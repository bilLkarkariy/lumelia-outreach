"""Human browser calls, call-audio imports, and signed Twilio callbacks.

This module creates call intents only. The paid outbound leg starts in the browser
through Twilio's Voice SDK, and its TwiML is restricted to the stored destination.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta
from functools import lru_cache
from html import escape
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import domain as d
from . import models as m
from .call_models import CallProviderEvent, CallRecording, CallSession, CallTranscript
from .call_storage import StorageUnavailable, delete_audio, read_audio, storage_available, store_audio
from .db import get_db
from .security import current_user

router = APIRouter(prefix="/api/calls", tags=["calls"])
UPLOAD_TYPES = {
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/vnd.wave",
    "audio/x-m4a",
    "audio/x-wav",
    "video/mp4",
}


class CallsSettings(BaseSettings):
    """Provider settings kept separate so central application config stays stable."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    public_url: str = Field(
        "http://127.0.0.1:8000", validation_alias=AliasChoices("LUMELIA_PUBLIC_URL", "PUBLIC_URL")
    )
    encryption_key: str = Field(
        "", validation_alias=AliasChoices("LUMELIA_ENCRYPTION_KEY", "CALLS_ENCRYPTION_KEY")
    )
    media_root: str = Field(
        "runtime/private-call-audio",
        validation_alias=AliasChoices("CALLS_MEDIA_ROOT", "LUMELIA_CALLS_MEDIA_ROOT"),
    )
    max_upload_bytes: int = Field(
        100 * 1024 * 1024,
        validation_alias=AliasChoices("CALLS_MAX_UPLOAD_BYTES", "LUMELIA_CALLS_MAX_UPLOAD_BYTES"),
    )
    twilio_account_sid: str = Field(
        "", validation_alias=AliasChoices("TWILIO_ACCOUNT_SID", "LUMELIA_TWILIO_ACCOUNT_SID")
    )
    twilio_auth_token: str = Field(
        "", validation_alias=AliasChoices("TWILIO_AUTH_TOKEN", "LUMELIA_TWILIO_AUTH_TOKEN")
    )
    twilio_api_key_sid: str = Field("", validation_alias=AliasChoices("TWILIO_API_KEY_SID", "TWILIO_API_KEY"))
    twilio_api_key_secret: str = Field(
        "", validation_alias=AliasChoices("TWILIO_API_KEY_SECRET", "TWILIO_API_SECRET")
    )
    twilio_twiml_app_sid: str = Field(
        "", validation_alias=AliasChoices("TWILIO_TWIML_APP_SID", "TWILIO_APP_SID")
    )
    deepgram_api_key: str = Field("", validation_alias="DEEPGRAM_API_KEY")
    deepgram_base_url: str = Field("https://api.eu.deepgram.com", validation_alias="DEEPGRAM_BASE_URL")
    # Default caller ID sits in the French range reserved for fiction: nothing real is dialled unconfigured.
    caller_id: str = Field("+33199000010", validation_alias="TWILIO_CALLER_ID")
    operator_number: str = Field("", validation_alias="TWILIO_OPERATOR_NUMBER")
    inbound_enabled: bool = Field(False, validation_alias="TWILIO_INBOUND_ENABLED")

    @property
    def mobile_ready(self) -> bool:
        return bool(
            self.public_url.startswith("https://")
            and self.twilio_account_sid
            and self.twilio_auth_token
            and re.fullmatch(r"\+[1-9]\d{7,14}", self.operator_number)
            and self.operator_number != CALLER_ID
        )

    @property
    def callback_base(self) -> str:
        return self.public_url.rstrip("/")

    @property
    def browser_ready(self) -> bool:
        return bool(
            self.public_url.startswith("https://")
            and self.twilio_account_sid
            and self.twilio_auth_token
            and self.twilio_api_key_sid
            and self.twilio_api_key_secret
            and self.twilio_twiml_app_sid
        )

    @property
    def upload_ready(self) -> bool:
        return storage_available(self.media_root, self.encryption_key)


@lru_cache
def call_settings() -> CallsSettings:
    return CallsSettings()


CALLER_ID = call_settings().caller_id


class CallIntent(BaseModel):
    account_id: str = Field(min_length=1, max_length=36)
    contact_id: str | None = Field(default=None, max_length=36)
    campaign_id: str | None = Field(default=None, max_length=36)
    mission_step_id: str | None = Field(default=None, max_length=36)
    destination: str = Field(min_length=3, max_length=40)
    recording_information_confirmed: Literal[True]
    information_proof: str = Field(min_length=3, max_length=1000)
    request_id: UUID


def _urls(cfg: CallsSettings, call_id: str) -> dict[str, str]:
    base = cfg.callback_base
    return {
        "notice": f"{base}/api/calls/provider/{call_id}/notice",
        "status": f"{base}/api/calls/provider/{call_id}/status",
        "recording": f"{base}/api/calls/provider/{call_id}/recording",
    }


def _recording_view(row: CallRecording) -> dict:
    return {
        **d.serialize(row),
        "media_url": f"/api/calls/{row.call_id}/recordings/{row.id}/media"
        if row.storage_status == "available" and row.blob_ref
        else None,
    }


def call_view(db: Session, row: CallSession, *, detail: bool = False) -> dict:
    result = d.serialize(row)
    contact = db.get(m.Contact, row.contact_id) if row.contact_id else None
    account = db.get(m.Account, row.account_id) if row.account_id else None
    result.update(
        contact_name=(contact.first_name + " " + contact.last_name).strip() if contact else "",
        account_name=account.name if account else "",
    )
    if detail:
        recordings = list(
            db.scalars(
                select(CallRecording)
                .where(CallRecording.call_id == row.id)
                .order_by(CallRecording.created_at)
            )
        )
        transcript = db.scalar(
            select(CallTranscript)
            .where(CallTranscript.call_id == row.id)
            .order_by(CallTranscript.version.desc())
            .limit(1)
        )
        result.update(
            recordings=[_recording_view(item) for item in recordings],
            transcript=d.serialize(transcript),
        )
    return result


def _linked_entities(db: Session, account_id: str, contact_id: str | None, campaign_id: str | None):
    account = d.get(db, m.Account, account_id)
    contact = d.get(db, m.Contact, contact_id) if contact_id else None
    campaign = d.get(db, m.Campaign, campaign_id) if campaign_id else None
    if contact and contact.account_id != account.id:
        raise HTTPException(422, "Le contact n’appartient pas à cette entreprise.")
    return account, contact, campaign


def _outbound_allowed(db: Session, account, contact) -> None:
    if account.lifecycle == "excluded" or account.outreach_paused:
        raise HTTPException(409, "Les appels sont suspendus pour cette entreprise.")
    if contact and (contact.status == "suppressed" or contact.paused or d.is_suppressed(db, contact)):
        raise HTTPException(409, "Les appels sont suspendus pour ce contact.")


def _event_key(kind: str, *parts: str) -> str:
    return ":".join([kind, *[part for part in parts if part]])[:180]


def remember_provider_event(
    db: Session, call_id: str | None, external_id: str, kind: str, data: dict
) -> tuple[CallProviderEvent, bool]:
    previous = db.scalar(select(CallProviderEvent).where(CallProviderEvent.external_id == external_id))
    if previous:
        return previous, False
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    event = CallProviderEvent(
        call_id=call_id,
        external_id=external_id,
        kind=kind,
        payload_checksum=hashlib.sha256(canonical.encode()).hexdigest(),
        data=data,
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
        return event, True
    except IntegrityError:
        previous = db.scalar(select(CallProviderEvent).where(CallProviderEvent.external_id == external_id))
        if not previous:
            raise
        return previous, False


@router.get("/status")
def calls_status(user=Depends(current_user), db: Session = Depends(get_db)):
    cfg = call_settings()
    enabled = d.state(db).settings.get("voice_routing", {}).get("enabled", True)
    return {
        "browser_call_available": cfg.browser_ready,
        "audio_upload_available": cfg.upload_ready,
        "transcription_available": bool(cfg.deepgram_api_key),
        "caller_id": CALLER_ID,
        "retention_days": 30,
        "mobile_call_available": cfg.mobile_ready,
        "operator_number": cfg.operator_number,
        "inbound_enabled": cfg.inbound_enabled and enabled,
        "inbound_configured": cfg.mobile_ready and cfg.inbound_enabled,
        "inbound_call_available": cfg.mobile_ready and cfg.inbound_enabled and enabled,
    }


@router.get("")
def list_calls(
    account_id: str = Query("", max_length=36),
    source: str = Query("", max_length=20),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(CallSession)
    if account_id:
        query = query.where(CallSession.account_id == account_id)
    if source:
        query = query.where(CallSession.source == source)
    rows = db.scalars(query.order_by(CallSession.created_at.desc()).limit(limit))
    return {"items": [call_view(db, row) for row in rows]}


@router.get("/{call_id}")
def call_detail(call_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    return call_view(db, d.get(db, CallSession, call_id), detail=True)


def _mission_binding(db, step_id, account_id, contact_id, campaign_id):
    from .mission_models import Mission, MissionStep

    step = d.get(db, MissionStep, step_id)
    mission = d.get(db, Mission, step.mission_id)
    if (
        step.kind != "callback"
        or step.account_id != account_id
        or (step.contact_id and contact_id and step.contact_id != contact_id)
        or (campaign_id and mission.campaign_id != campaign_id)
        or mission.status != "active"
        or step.status in {"completed", "cancelled"}
    ):
        raise HTTPException(409, "Ce rappel ne correspond pas à cette fiche active")
    return step.contact_id or contact_id, mission.campaign_id


@router.post("", status_code=201)
def create_call_intent(data: CallIntent, user=Depends(current_user), db: Session = Depends(get_db)):
    if data.information_proof.startswith("Annonce vocale automatique"):
        raise HTTPException(
            409,
            "Actualisez la page : l’annonce automatique a été retirée. Confirmez l’information préalable de l’interlocuteur.",
        )
    key = _event_key("intent", str(data.request_id))
    previous = db.scalar(select(CallProviderEvent).where(CallProviderEvent.external_id == key))
    if previous and previous.call_id:
        return call_view(db, d.get(db, CallSession, previous.call_id), detail=True)
    account, contact, _ = _linked_entities(db, data.account_id, data.contact_id, data.campaign_id)
    _outbound_allowed(db, account, contact)
    if data.mission_step_id:
        contact_id, campaign_id = _mission_binding(
            db, data.mission_step_id, account.id, data.contact_id, data.campaign_id
        )
        contact = db.get(m.Contact, contact_id) if contact_id else contact
        data.campaign_id = campaign_id
    destination = d.normalize_phone(data.destination, account.country)
    row = CallSession(
        account_id=account.id,
        contact_id=contact.id if contact else None,
        campaign_id=data.campaign_id,
        created_by=user.id,
        source="browser",
        destination=destination,
        caller_id=CALLER_ID,
        processing_status="awaiting_recording",
        data={
            "request_id": str(data.request_id),
            "mission_step_id": data.mission_step_id,
            "recording_information": {
                "confirmed": True,
                "proof": data.information_proof.strip(),
                "confirmed_at": m.now().isoformat(),
                "confirmed_by": user.id,
            },
        },
    )
    db.add(row)
    db.flush()
    remember_provider_event(db, row.id, key, "intent", {"request_id": str(data.request_id)})
    db.commit()
    return call_view(db, row, detail=True)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def voice_access_token(cfg: CallsSettings, *, identity: str, call_id: str, ttl: int = 300) -> str:
    now_epoch = int(time.time())
    header = {"alg": "HS256", "cty": "twilio-fpa;v=1", "typ": "JWT"}
    payload = {
        "jti": f"{cfg.twilio_api_key_sid}-{now_epoch}-{secrets.token_hex(4)}",
        "grants": {
            "identity": identity[:121],
            "voice": {
                "outgoing": {
                    "application_sid": cfg.twilio_twiml_app_sid,
                    "params": {"call_id": call_id},
                }
            },
        },
        "iat": now_epoch,
        "nbf": now_epoch,
        "exp": now_epoch + ttl,
        "iss": cfg.twilio_api_key_sid,
        "sub": cfg.twilio_account_sid,
    }
    body = (
        _b64(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64(json.dumps(payload, separators=(",", ":")).encode())
    )
    signature = hmac.new(cfg.twilio_api_key_secret.encode(), body.encode(), hashlib.sha256).digest()
    return body + "." + _b64(signature)


@router.post("/{call_id}/token")
def browser_token(call_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    cfg = call_settings()
    if not cfg.browser_ready:
        raise HTTPException(503, "La téléphonie navigateur n’est pas entièrement configurée.")
    row = d.get(db, CallSession, call_id)
    if row.source != "browser" or row.status not in {"intent", "connecting"}:
        raise HTTPException(409, "Cet appel ne peut plus être démarré.")
    if row.created_by != user.id:
        raise HTTPException(403, "Cet appel appartient à un autre opérateur")
    account, contact, _ = _linked_entities(db, row.account_id, row.contact_id, row.campaign_id)
    _outbound_allowed(db, account, contact)
    return {
        "token": voice_access_token(cfg, identity=f"operator-{user.id}", call_id=row.id),
        "expires_in": 300,
        "call_id": row.id,
    }


@router.post("/upload", status_code=201)
async def upload_recording(
    account_id: Annotated[str, Form(max_length=36)],
    recording_information_confirmed: Annotated[bool, Form()],
    information_proof: Annotated[str, Form(min_length=3, max_length=1000)],
    request_id: Annotated[UUID, Form()],
    audio: Annotated[UploadFile, File()],
    contact_id: Annotated[str | None, Form(max_length=36)] = None,
    campaign_id: Annotated[str | None, Form(max_length=36)] = None,
    mission_step_id: Annotated[str | None, Form(max_length=36)] = None,
    recorded_at: Annotated[datetime | None, Form()] = None,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if not recording_information_confirmed:
        raise HTTPException(422, "Confirmez que l’information sur l’enregistrement a été donnée")
    if recorded_at and (not recorded_at.tzinfo or recorded_at > m.now() + timedelta(minutes=5)):
        raise HTTPException(422, "Indiquez la date réelle de l’appel avec son fuseau horaire")
    cfg = call_settings()
    if not cfg.upload_ready:
        raise HTTPException(503, "Le stockage audio privé chiffré n’est pas configuré.")
    key = _event_key("upload", str(request_id))
    previous = db.scalar(select(CallProviderEvent).where(CallProviderEvent.external_id == key))
    if previous and previous.call_id:
        return call_view(db, d.get(db, CallSession, previous.call_id), detail=True)
    if mission_step_id:
        contact_id, campaign_id = _mission_binding(db, mission_step_id, account_id, contact_id, campaign_id)
    account, contact, _ = _linked_entities(db, account_id, contact_id, campaign_id)
    content_type = (audio.content_type or "application/octet-stream").split(";", 1)[0].lower()
    if content_type not in UPLOAD_TYPES:
        raise HTTPException(415, "Format audio non pris en charge.")
    content = await audio.read(cfg.max_upload_bytes + 1)
    if len(content) > cfg.max_upload_bytes:
        raise HTTPException(413, "Le fichier audio dépasse la taille autorisée.")
    if not content:
        raise HTTPException(422, "Le fichier audio est vide.")
    row = CallSession(
        account_id=account.id,
        contact_id=contact.id if contact else None,
        campaign_id=campaign_id,
        created_by=user.id,
        source="upload",
        status="completed",
        caller_id=CALLER_ID,
        ended_at=recorded_at,
        processing_status="queued",
        data={"request_id": str(request_id), "mission_step_id": mission_step_id},
    )
    db.add(row)
    db.flush()
    recording = CallRecording(
        call_id=row.id,
        provider="upload",
        original_filename=(audio.filename or "audio").rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:255],
        content_type=content_type,
        storage_status="pending",
        information_confirmed=True,
        information_proof=information_proof.strip(),
    )
    db.add(recording)
    db.flush()
    stored = store_audio(cfg.media_root, cfg.encryption_key, recording.id, content)
    recording.blob_ref = stored.blob_ref
    recording.checksum_sha256 = stored.checksum_sha256
    recording.byte_size = stored.byte_size
    recording.storage_status = "available"
    remember_provider_event(db, row.id, key, "upload", {"request_id": str(request_id)})
    try:
        db.commit()
    except Exception:
        delete_audio(cfg.media_root, stored.blob_ref)
        raise
    return call_view(db, row, detail=True)


@router.api_route("/{call_id}/recordings/{recording_id}/media", methods=["GET", "HEAD"])
def private_recording(
    call_id: str,
    recording_id: str,
    request: Request,
    download: bool = Query(False),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    recording = d.get(db, CallRecording, recording_id)
    if recording.call_id != call_id or recording.storage_status != "available" or not recording.blob_ref:
        raise HTTPException(404, "Audio indisponible.")
    if recording.retention_until <= m.now():
        raise HTTPException(410, "La durée de conservation de cet audio est terminée.")
    try:
        content = read_audio(call_settings().media_root, call_settings().encryption_key, recording.blob_ref)
    except StorageUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    name = recording.original_filename or f"appel-{call_id}.wav"
    safe_name = (
        "".join(ch for ch in name if ch.isascii() and (ch.isalnum() or ch in "._- "))[:120] or "appel-audio"
    )
    size = len(content)
    headers = {
        "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{safe_name}"',
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "ETag": '"' + recording.checksum_sha256 + '"',
        "Content-Length": str(size),
    }
    status = 200
    requested = request.headers.get("range", "") if request.method == "GET" else ""
    if request.headers.get("if-range", headers["ETag"]) != headers["ETag"]:
        requested = ""
    # Media players probe the header and seek through single byte ranges.
    # Unsupported multi-range requests receive the complete representation.
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
    if match:
        first, last = match.groups()
        try:
            start = int(first) if first else max(0, size - int(last))
            end = min(int(last), size - 1) if first and last else size - 1
        except ValueError:
            start, end = size, -1
        if start >= size or start > end:
            return Response(
                status_code=416,
                headers={
                    **headers,
                    "Content-Range": f"bytes */{size}",
                    "Content-Length": "0",
                },
            )
        content = content[start : end + 1]
        headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(len(content))})
        status = 206
    return Response(
        b"" if request.method == "HEAD" else content,
        status_code=status,
        media_type=recording.content_type,
        headers=headers,
    )


def twilio_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    signed = url + "".join(key + str(params[key]) for key in sorted(params))
    digest = hmac.new(auth_token.encode(), signed.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


async def signed_twilio_form(request: Request, cfg: CallsSettings | None = None) -> dict[str, str]:
    cfg = cfg or call_settings()
    if not cfg.twilio_auth_token:
        raise HTTPException(503, "Les callbacks Twilio ne sont pas configurés.")
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}
    url = cfg.callback_base + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    expected = twilio_signature(url, params, cfg.twilio_auth_token)
    if not hmac.compare_digest(request.headers.get("x-twilio-signature", ""), expected):
        raise HTTPException(403, "Signature fournisseur invalide.")
    return params


def dial_twiml(row: CallSession, urls: dict[str, str]) -> str:
    """Build TwiML exclusively from the server-side intent."""

    destination = escape(row.destination or "", quote=True)
    caller = escape(CALLER_ID, quote=True)
    status = escape(urls["status"], quote=True)
    recording = escape(urls["recording"], quote=True)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response><Dial answerOnBridge="true" timeout="30" '
        f'callerId="{caller}" action="{status}" method="POST" '
        'record="record-from-answer-dual" recordingChannels="dual" recordingTrack="both" '
        f'recordingStatusCallback="{recording}" recordingStatusCallbackMethod="POST" '
        'recordingStatusCallbackEvent="in-progress completed absent">'
        f'<Number statusCallback="{status}" '
        'statusCallbackMethod="POST" '
        'statusCallbackEvent="initiated ringing answered completed">'
        f"{destination}</Number>"
        "</Dial></Response>"
    )


def _provider_call(db: Session, call_id: str, call_sid: str) -> CallSession:
    row = db.get(CallSession, call_id, with_for_update=True)
    if not row or row.source not in {"browser", "mobile", "inbound"}:
        raise HTTPException(409, "Ce callback ne correspond pas à un appel téléphonique.")
    if not row.external_id or row.external_id != call_sid:
        raise HTTPException(409, "Identifiant fournisseur incohérent.")
    return row


@router.post("/provider/twiml")
@router.post("/provider/{call_id}/twiml")
async def provider_twiml(request: Request, call_id: str | None = None, db: Session = Depends(get_db)):
    params = await signed_twilio_form(request)
    call_id = call_id or params.get("call_id") or params.get("CallId")
    if not call_id:
        raise HTTPException(422, "Intention d’appel absente.")
    call_sid = params.get("CallSid", "")
    if not call_sid:
        raise HTTPException(422, "Identifiant d’appel fournisseur absent.")
    row = d.get(db, CallSession, call_id)
    if row.source != "browser" or (row.external_id and row.external_id != call_sid):
        raise HTTPException(409, "Cet appel navigateur ne correspond pas au fournisseur")
    if params.get("From") != "client:operator-" + row.created_by:
        raise HTTPException(403, "Identité de l’opérateur incorrecte")
    account, contact, _ = _linked_entities(db, row.account_id, row.contact_id, row.campaign_id)
    _outbound_allowed(db, account, contact)
    if row.status not in {"intent", "connecting"}:
        raise HTTPException(409, "Cet appel n’est plus en attente.")
    _, fresh = remember_provider_event(
        db,
        row.id,
        _event_key("twiml", call_sid),
        "twiml",
        {"call_sid": call_sid},
    )
    if fresh:
        row.external_id = call_sid
        row.status = "connecting"
        row.started_at = row.started_at or m.now()
    db.commit()
    return Response(dial_twiml(row, _urls(call_settings(), row.id)), media_type="application/xml")


@router.post("/provider/{call_id}/notice")
async def provider_notice(call_id: str, request: Request, db: Session = Depends(get_db)):
    params = await signed_twilio_form(request)
    row = d.get(db, CallSession, call_id)
    if row.source not in {"browser", "mobile"} or row.status not in {"connecting", "ringing", "in_progress"}:
        raise HTTPException(409, "Ce callback ne correspond pas à l’appel attendu.")
    remember_provider_event(
        db,
        row.id,
        _event_key("notice", params.get("CallSid", "")),
        "recording_notice",
        {"call_sid": params.get("CallSid", "")},
    )
    information = dict(row.data.get("recording_information", {}))
    row.data = {
        **row.data,
        "recording_information": {**information, "legacy_notice_skipped_at": m.now().isoformat()},
    }
    db.commit()
    xml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(xml, media_type="application/xml")


@router.post("/provider/{call_id}/status")
async def provider_status(call_id: str, request: Request, db: Session = Depends(get_db)):
    params = await signed_twilio_form(request)
    call_sid = params.get("CallSid", "")
    row = _provider_call(db, call_id, params.get("ParentCallSid") or call_sid)
    status = params.get("DialCallStatus") or params.get("CallStatus") or "unknown"
    if row.source != "browser":
        raise HTTPException(409, "Ce statut ne correspond pas à un appel navigateur")
    event_id = _event_key("status", call_sid, params.get("DialCallSid", ""), status)
    _, fresh = remember_provider_event(
        db,
        row.id,
        event_id,
        "call_status",
        {"status": status, "dial_call_sid": params.get("DialCallSid", "")},
    )
    if fresh:
        mapped = {
            "answered": "in_progress",
            "in-progress": "in_progress",
            "completed": "completed",
            "busy": "failed",
            "failed": "failed",
            "no-answer": "failed",
            "canceled": "canceled",
        }
        next_status = mapped.get(status)
        terminal = {"completed", "failed", "canceled"}
        if next_status and row.status not in terminal:
            row.status = next_status
        if next_status == "in_progress":
            row.answered_at = row.answered_at or m.now()
        if next_status in terminal:
            row.ended_at = row.ended_at or m.now()
            key = "call-ended:" + row.id
            if not db.scalar(select(m.Event.id).where(m.Event.external_id == key)):
                d.log(
                    db,
                    "call_ended",
                    actor_id=row.created_by,
                    contact=db.get(m.Contact, row.contact_id) if row.contact_id else None,
                    campaign_id=row.campaign_id,
                    channel="phone",
                    external_id=key,
                    summary="Appel terminé" if next_status == "completed" else "Appel non abouti",
                    data={"account_id": row.account_id, "call_id": row.id, "status": next_status},
                )
        duration = params.get("DialCallDuration") or params.get("CallDuration")
        if duration and duration.isdigit():
            row.duration_seconds = int(duration)
            if status == "completed" and row.ended_at and not row.answered_at and int(duration) > 0:
                row.answered_at = row.ended_at - timedelta(seconds=int(duration))
    db.commit()
    if "DialCallStatus" in params:
        return Response("<Response><Hangup/></Response>", media_type="application/xml")
    return {"ok": True, "duplicate": not fresh}


@router.post("/provider/{call_id}/recording")
async def provider_recording(call_id: str, request: Request, db: Session = Depends(get_db)):
    params = await signed_twilio_form(request)
    call_sid = params.get("CallSid", "")
    row = _provider_call(db, call_id, params.get("ParentCallSid") or call_sid)
    recording_sid = params.get("RecordingSid", "")
    status = params.get("RecordingStatus", "")
    if not recording_sid or status not in {"in-progress", "completed", "absent", "failed"}:
        raise HTTPException(422, "Callback d’enregistrement incomplet.")
    _, fresh = remember_provider_event(
        db,
        row.id,
        _event_key("recording", recording_sid, status),
        "recording_status",
        {"recording_sid": recording_sid, "status": status},
    )
    if not fresh:
        db.commit()
        return {"ok": True, "duplicate": True}
    recording = db.scalar(select(CallRecording).where(CallRecording.provider_ref == recording_sid))
    notice = row.data.get("recording_information", {})
    if not recording:
        recording = CallRecording(
            call_id=row.id,
            provider="twilio",
            provider_ref=recording_sid,
            content_type="audio/wav",
            original_filename=f"twilio-{recording_sid}.wav",
            information_confirmed=bool(notice.get("confirmed")),
            information_proof=str(notice.get("proof", "")),
        )
        db.add(recording)
    channels = params.get("RecordingChannels", "")
    if channels.isdigit():
        recording.channels = max(1, min(2, int(channels)))
    duration = params.get("RecordingDuration", "")
    if duration.isdigit():
        recording.duration_seconds = int(duration)
        row.duration_seconds = row.duration_seconds or int(duration)
    if status == "completed":
        recording.storage_status = "pending"
        row.processing_status = "queued"
        row.processing_error = ""
        row.next_processing_at = m.now()
    elif status in {"absent", "failed"}:
        recording.storage_status = "unavailable"
        row.processing_status = "unavailable"
        row.processing_error = "Twilio n’a fourni aucun audio exploitable."
    db.commit()
    return {"ok": True, "duplicate": False}


@router.post("/{call_id}/stop-recording")
def stop_recording(call_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    cfg = call_settings()
    row = d.get(db, CallSession, call_id)
    recording = db.scalar(
        select(CallRecording)
        .where(CallRecording.call_id == row.id, CallRecording.provider_ref.is_not(None))
        .order_by(CallRecording.created_at.desc())
        .limit(1)
    )
    if not recording or not recording.provider_ref:
        raise HTTPException(409, "L’enregistrement actif n’est pas encore identifié.")
    stop = dict(row.data.get("stop_recording", {}))
    if stop.get("state") in {"requested", "succeeded", "uncertain"}:
        return {"status": stop["state"], "replayed": True}
    if not cfg.twilio_account_sid or not cfg.twilio_auth_token:
        raise HTTPException(503, "Twilio n’est pas configuré.")
    stop = {"state": "requested", "requested_at": m.now().isoformat(), "requested_by": user.id}
    row.data = {**row.data, "stop_recording": stop}
    db.commit()  # Durable before the non-idempotent provider POST; it is never replayed automatically.
    url = (
        "https://api.twilio.com/2010-04-01/Accounts/"
        f"{cfg.twilio_account_sid}/Recordings/{recording.provider_ref}.json"
    )
    try:
        response = httpx.post(
            url,
            data={"Status": "stopped"},
            auth=(cfg.twilio_account_sid, cfg.twilio_auth_token),
            timeout=15,
        )
        response.raise_for_status()
        state = "succeeded"
    except (httpx.RequestError, httpx.HTTPStatusError):
        state = "uncertain"
    with db.begin():
        current = db.get(CallSession, row.id, with_for_update=True)
        current.data = {
            **current.data,
            "stop_recording": {**current.data.get("stop_recording", {}), "state": state},
        }
    if state == "uncertain":
        raise HTTPException(
            502,
            "L’arrêt a été demandé mais sa confirmation est incertaine ; aucune relance automatique ne sera faite.",
        )
    return {"status": state, "replayed": False}
