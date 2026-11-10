"""Bounded post-call audio retrieval and transcription worker."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, or_, select

from . import models as m
from .call_models import CallRecording, CallSession, CallTranscript
from .call_storage import StorageUnavailable, delete_audio, read_audio, store_audio
from .calls import call_settings
from .db import SessionLocal


class ProcessingUnavailable(RuntimeError):
    pass


class ProcessingFailed(RuntimeError):
    pass


class ProcessingUncertain(ProcessingFailed):
    pass


@dataclass(frozen=True)
class ClaimedCall:
    call_id: str
    recording_id: str


def delete_recording_objects(blob_ref=None, provider_ref=None):
    cfg = call_settings()
    if provider_ref:
        if not cfg.twilio_account_sid or not cfg.twilio_auth_token:
            raise StorageUnavailable("Twilio indisponible pour supprimer l’audio")
        try:
            response = httpx.delete(
                f"https://api.twilio.com/2010-04-01/Accounts/{cfg.twilio_account_sid}/Recordings/{provider_ref}.json",
                auth=(cfg.twilio_account_sid, cfg.twilio_auth_token),
                timeout=20,
            )
        except httpx.RequestError as exc:
            raise StorageUnavailable("Suppression fournisseur à reprendre") from exc
        if response.status_code not in {204, 404}:
            raise StorageUnavailable("Suppression fournisseur à reprendre")
    if blob_ref:
        delete_audio(cfg.media_root, blob_ref)


def purge_expired_recordings(limit: int = 20) -> int:
    removed = 0
    with SessionLocal() as db:
        for job in db.scalars(
            select(m.Job).where(m.Job.kind == "erase_audio", m.Job.status == "queued").limit(limit)
        ):
            try:
                delete_recording_objects(**job.payload)
            except StorageUnavailable:
                continue
            job.status, job.payload = "completed", {}
        rows = list(
            db.scalars(
                select(CallRecording)
                .where(CallRecording.retention_until <= m.now(), CallRecording.storage_status != "deleted")
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        for row in rows:
            try:
                delete_recording_objects(row.blob_ref, row.provider_ref)
            except StorageUnavailable:
                continue
            row.blob_ref, row.storage_status = None, "deleted"
            removed += 1
        db.commit()
    return removed


def claim_one() -> ClaimedCall | None:
    cfg = call_settings()
    cutoff = m.now() - timedelta(minutes=20)
    with SessionLocal() as db, db.begin():
        uncertain = db.scalar(
            select(CallSession)
            .where(
                CallSession.processing_status == "transcribing",
                CallSession.processing_locked_at < cutoff,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if uncertain:
            uncertain.processing_status = "uncertain"
            uncertain.processing_locked_at = None
            uncertain.processing_error = (
                "La transcription a été interrompue après sa soumission possible ; "
                "elle n’est pas relancée automatiquement."
            )
            return None
        if cfg.deepgram_api_key:
            for waiting in db.scalars(
                select(CallSession)
                .where(
                    CallSession.processing_status == "unavailable",
                    CallSession.processing_error == "Deepgram n’est pas configuré.",
                )
                .limit(10)
            ):
                waiting.processing_status, waiting.processing_error = "queued", ""
        row = db.scalar(
            select(CallSession)
            .where(
                or_(
                    CallSession.processing_status == "queued",
                    (
                        (CallSession.processing_status == "processing")
                        & (CallSession.processing_locked_at < cutoff)
                    ),
                ),
                CallSession.next_processing_at <= m.now(),
                CallSession.status == "completed",
            )
            .order_by(CallSession.next_processing_at, CallSession.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not row:
            return None
        recording = db.scalar(
            select(CallRecording)
            .where(CallRecording.call_id == row.id)
            .order_by(CallRecording.created_at.desc())
            .limit(1)
        )
        if not recording:
            row.processing_status = "unavailable"
            row.processing_error = "Aucun enregistrement n’est associé à cet appel."
            return None
        row.processing_status = "processing"
        row.processing_locked_at = m.now()
        row.processing_attempts += 1
        row.processing_error = ""
        return ClaimedCall(row.id, recording.id)


def _retrieve_twilio(provider_ref: str | None, channels: int) -> bytes:
    cfg = call_settings()
    if not provider_ref:
        raise ProcessingUnavailable("La référence de l’enregistrement Twilio est absente.")
    if not cfg.twilio_account_sid or not cfg.twilio_auth_token:
        raise ProcessingUnavailable("Twilio n’est pas configuré pour récupérer l’enregistrement.")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{cfg.twilio_account_sid}/Recordings/{provider_ref}.wav"
    try:
        response = httpx.get(
            url,
            params={"RequestedChannels": 2 if channels > 1 else 1},
            auth=(cfg.twilio_account_sid, cfg.twilio_auth_token),
            timeout=30,
            follow_redirects=False,
        )
    except httpx.RequestError as exc:
        raise ProcessingFailed("La récupération Twilio a échoué avant confirmation.") from exc
    if response.status_code != 200:
        raise ProcessingFailed(f"Twilio a refusé la récupération audio (HTTP {response.status_code}).")
    if not response.content:
        raise ProcessingUnavailable("Twilio a renvoyé un enregistrement vide.")
    if len(response.content) > cfg.max_upload_bytes:
        raise ProcessingFailed("L’enregistrement Twilio dépasse la taille autorisée.")
    return response.content


def _load_audio(claim: ClaimedCall) -> tuple[bytes, str, int]:
    cfg = call_settings()
    with SessionLocal() as db:
        recording = db.get(CallRecording, claim.recording_id)
        if not recording or recording.call_id != claim.call_id:
            raise ProcessingUnavailable("L’enregistrement associé a disparu.")
        blob_ref = recording.blob_ref
        content_type = recording.content_type
        channels = recording.channels
        provider = recording.provider
        provider_ref = recording.provider_ref
    if blob_ref:
        try:
            return read_audio(cfg.media_root, cfg.encryption_key, blob_ref), content_type, channels
        except StorageUnavailable as exc:
            raise ProcessingUnavailable(str(exc)) from exc
    if provider != "twilio":
        raise ProcessingUnavailable("Le fichier audio importé n’est plus disponible.")
    audio = _retrieve_twilio(provider_ref, channels)
    with SessionLocal() as db:
        recording = db.get(CallRecording, claim.recording_id)
        if not recording or recording.call_id != claim.call_id:
            raise ProcessingUnavailable("L’enregistrement associé a disparu.")
        try:
            stored = store_audio(cfg.media_root, cfg.encryption_key, recording.id, audio)
        except StorageUnavailable as exc:
            raise ProcessingUnavailable(str(exc)) from exc
        recording.blob_ref = stored.blob_ref
        recording.checksum_sha256 = stored.checksum_sha256
        recording.byte_size = stored.byte_size
        recording.storage_status = "available"
        try:
            db.commit()
        except Exception:
            delete_audio(cfg.media_root, stored.blob_ref)
            raise
        return audio, recording.content_type, recording.channels


def _mark_transcribing(claim: ClaimedCall) -> None:
    """Make a Deepgram POST non-replayable before sending any audio."""

    with SessionLocal() as db, db.begin():
        row = db.get(CallSession, claim.call_id, with_for_update=True)
        if not row or row.processing_status != "processing":
            raise ProcessingFailed("Le traitement de cet appel a été remplacé.")
        row.processing_status = "transcribing"


def _as_number(value, default=0.0) -> float:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return default


def transcript_parts(payload: dict, *, multichannel: bool) -> tuple[list[dict], list[dict]]:
    """Normalize Deepgram channels into stable speaker/timestamp records."""

    normalized_words: list[dict] = []
    streams: list[list[dict]] = []
    channels = payload.get("results", {}).get("channels", [])
    for channel_index, channel in enumerate(channels):
        alternatives = channel.get("alternatives") or []
        if not alternatives:
            continue
        alternative = alternatives[0]
        words: list[dict[str, Any]] = []
        for raw in alternative.get("words") or []:
            text = str(raw.get("punctuated_word") or raw.get("word") or "").strip()
            if not text:
                continue
            word: dict[str, Any] = {
                "start": _as_number(raw.get("start")),
                "end": _as_number(raw.get("end")),
                "speaker": channel_index if multichannel else int(raw.get("speaker", 0) or 0),
                "text": text,
            }
            if raw.get("confidence") is not None:
                word["confidence"] = round(float(raw["confidence"]), 4)
            words.append(word)
            normalized_words.append(word)
        if not words:
            text = str(alternative.get("transcript") or "").strip()
            if text:
                duration = _as_number(payload.get("metadata", {}).get("duration"))
                words = [
                    {
                        "start": 0.0,
                        "end": duration,
                        "speaker": channel_index if multichannel else 0,
                        "text": text,
                    }
                ]
        streams.append(words)

    segments: list[dict] = []
    for words in streams:
        current: dict[str, Any] | None = None
        for word in words:
            if (
                current is None
                or current["speaker"] != word["speaker"]
                or word["start"] - current["end"] > 1.25
            ):
                current = {
                    "start": word["start"],
                    "end": word["end"],
                    "speaker": word["speaker"],
                    "text": word["text"],
                    "_confidences": [word["confidence"]] if "confidence" in word else [],
                }
                segments.append(current)
            else:
                current["end"] = max(current["end"], word["end"])
                current["text"] += " " + word["text"]
                if "confidence" in word:
                    current["_confidences"].append(word["confidence"])
    segments.sort(key=lambda part: (part["start"], part["speaker"]))
    for segment in segments:
        confidence = segment.pop("_confidences")
        if confidence:
            segment["confidence"] = round(sum(confidence) / len(confidence), 4)
    normalized_words.sort(key=lambda word: (word["start"], word["speaker"]))
    return segments, normalized_words


def _transcribe(audio: bytes, content_type: str, channels: int) -> tuple[dict, list, list]:
    cfg = call_settings()
    if not cfg.deepgram_api_key:
        raise ProcessingUnavailable("Deepgram n’est pas configuré.")
    multichannel = channels > 1
    params: dict[str, str] = {
        "model": "nova-3",
        "language": "fr",
        "smart_format": "true",
        "punctuate": "true",
    }
    if multichannel:
        params["multichannel"] = "true"
    else:
        params["diarize_model"] = "latest"
    try:
        response = httpx.post(
            cfg.deepgram_base_url.rstrip("/") + "/v1/listen",
            params=params,
            headers={
                "Authorization": "Token " + cfg.deepgram_api_key,
                "Content-Type": content_type or "application/octet-stream",
            },
            content=audio,
            timeout=120,
        )
    except httpx.RequestError as exc:
        # This paid POST may have reached Deepgram. Keep the uncertainty durable and do not replay it.
        raise ProcessingUncertain(
            "La réponse Deepgram est incertaine ; la transcription n’est pas relancée automatiquement."
        ) from exc
    if response.status_code != 200:
        raise ProcessingFailed(f"Deepgram a refusé la transcription (HTTP {response.status_code}).")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProcessingUncertain("Deepgram a renvoyé une réponse illisible.") from exc
    segments, words = transcript_parts(payload, multichannel=multichannel)
    if not segments:
        raise ProcessingUnavailable("Aucune parole transcrite n’a été détectée dans l’audio.")
    return payload, segments, words


def enqueue_report(db, row: CallSession, transcript: CallTranscript) -> None:
    """Keep unknown inbound audio usable; analyse only after the correct CRM link exists."""
    row.processing_locked_at = None
    row.processing_error = ""
    if not row.account_id:
        from .phone_routing import attention

        row.processing_status = "unassigned"
        attention(
            db,
            row,
            "link_call",
            "Identifier l’appelant entrant",
            "La transcription est disponible. Rattacher l’appel à la bonne fiche pour préparer le compte rendu.",
        )
        return
    key = f"post-call-report:{row.id}:{transcript.version}"
    if db.scalar(select(m.AgentAction.id).where(m.AgentAction.dedupe_key == key)):
        return
    row.processing_status = "transcribed"
    db.add(
        m.AgentAction(
            dedupe_key=key,
            kind="post_call_report",
            title="Analyser l’appel téléphonique",
            contact_id=row.contact_id,
            account_id=row.account_id,
            campaign_id=row.campaign_id,
            target_url=f"/contacts?company={row.account_id}&call={row.id}",
            payload={
                "call_id": row.id,
                "transcript_id": transcript.id,
                "transcript_version": transcript.version,
            },
        )
    )


def _complete(claim: ClaimedCall, payload: dict, segments: list, words: list) -> CallTranscript:
    with SessionLocal() as db, db.begin():
        row = db.get(CallSession, claim.call_id, with_for_update=True)
        recording = db.get(CallRecording, claim.recording_id)
        if not row or row.processing_status != "transcribing":
            raise ProcessingFailed("Le traitement de cet appel a été remplacé.")
        version = (
            db.scalar(select(func.max(CallTranscript.version)).where(CallTranscript.call_id == row.id)) or 0
        ) + 1
        duration = payload.get("metadata", {}).get("duration")
        transcript = CallTranscript(
            call_id=row.id,
            version=version,
            status="ready",
            channels=recording.channels,
            duration_seconds=round(float(duration)) if duration is not None else row.duration_seconds,
            segments=segments,
            words=words,
            raw=payload,
        )
        db.add(transcript)
        db.flush()
        enqueue_report(db, row, transcript)
        return transcript


def _fail(claim: ClaimedCall, exc: Exception) -> None:
    status = (
        "unavailable"
        if isinstance(exc, ProcessingUnavailable)
        else "uncertain"
        if isinstance(exc, ProcessingUncertain)
        else "failed"
    )
    with SessionLocal() as db, db.begin():
        row = db.get(CallSession, claim.call_id, with_for_update=True)
        if row and row.processing_status in {"processing", "transcribing"}:
            row.processing_status = status
            row.processing_locked_at = None
            row.processing_error = str(exc)[:2000]
            from .agent_actions import enqueue

            enqueue(
                db,
                "call-processing:" + row.id + ":" + str(row.processing_attempts),
                "provider_issue",
                "Traitement d’appel à reprendre",
                account_id=row.account_id,
                contact_id=row.contact_id,
                human_reason=row.processing_error,
                target_url=f"/contacts?company={row.account_id}&call={row.id}",
            )


def tick() -> dict:
    """Claim and process at most one call recording."""

    claim = claim_one()
    if not claim:
        return {"processed": 0}
    try:
        audio, content_type, channels = _load_audio(claim)
        if not call_settings().deepgram_api_key:
            raise ProcessingUnavailable("Deepgram n’est pas configuré.")
        _mark_transcribing(claim)
        payload, segments, words = _transcribe(audio, content_type, channels)
        transcript = _complete(claim, payload, segments, words)
        return {"processed": 1, "call_id": claim.call_id, "transcript_id": transcript.id}
    except (ProcessingUnavailable, ProcessingFailed) as exc:
        _fail(claim, exc)
        return {"processed": 1, "call_id": claim.call_id, "error": str(exc)}
    except Exception:
        failure = ProcessingUncertain(
            "Le traitement a été interrompu et ne sera pas relancé automatiquement."
        )
        _fail(claim, failure)
        return {"processed": 1, "call_id": claim.call_id, "error": str(failure)}
