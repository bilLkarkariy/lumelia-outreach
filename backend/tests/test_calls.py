import base64
import io
import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import call_storage
from app import models as m
from app.call_models import CallProviderEvent, CallSession
from app.call_processing import transcript_parts
from app.call_storage import StorageUnavailable, read_audio, safe_blob_path, store_audio
from app.calls import (
    CallsSettings,
    _outbound_allowed,
    dial_twiml,
    remember_provider_event,
    twilio_signature,
    voice_access_token,
)
from app.db import Base

RECORDING_ID = "00000000-0000-4000-8000-000000000001"


def test_private_audio_supports_browser_playback_ranges_and_retention(client, flow, monkeypatch, tmp_path):
    from datetime import timedelta
    from uuid import uuid4

    from app import calls
    from app.call_models import CallRecording
    from app.db import SessionLocal

    config = SimpleNamespace(
        upload_ready=True,
        media_root=str(tmp_path),
        encryption_key=Fernet.generate_key().decode(),
        max_upload_bytes=1024,
    )
    monkeypatch.setattr(calls, "call_settings", lambda: config)
    audio = b"RIFF" + bytes(range(64))
    denied = client.post(
        "/api/calls/upload",
        data={
            "account_id": flow["account"]["id"],
            "request_id": str(uuid4()),
            "recording_information_confirmed": "false",
            "information_proof": "Audio fictif de recette",
        },
        files={"audio": ("test.wav", audio, "audio/wav")},
    )
    assert denied.status_code == 422
    result = client.post(
        "/api/calls/upload",
        data={
            "account_id": flow["account"]["id"],
            "request_id": str(uuid4()),
            "recording_information_confirmed": "true",
            "information_proof": "Audio fictif de recette",
        },
        files={"audio": ("test.wav", audio, "audio/wav")},
    )
    assert result.status_code == 201, result.text
    recording = result.json()["recordings"][0]
    url = recording["media_url"]
    response = client.get(url)
    assert response.content == audio
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["accept-ranges"] == "bytes"
    assert "no-store" in response.headers["cache-control"]
    probe = client.get(url, headers={"Range": "bytes=0-1"})
    assert probe.status_code == 206 and probe.content == b"RI"
    assert probe.headers["content-range"] == f"bytes 0-1/{len(audio)}"
    assert client.get(url, headers={"Range": "bytes=-4"}).content == audio[-4:]
    assert client.get(url, headers={"Range": "bytes=4-"}).content == audio[4:]
    assert client.get(url, headers={"Range": "bytes=99999-"}).status_code == 416
    head = client.head(url)
    assert head.status_code == 200 and not head.content
    assert int(head.headers["content-length"]) == len(audio)
    assert client.get(url + "?download=true").headers["content-disposition"].startswith("attachment;")
    with SessionLocal() as db:
        db.get(CallRecording, recording["id"]).retention_until = m.now() - timedelta(seconds=1)
        db.commit()
    assert client.get(url).status_code == 410
    client.cookies.clear()
    assert client.get(url).status_code == 401


def test_twilio_signature_matches_published_algorithm_and_rejects_changed_payload():
    url = "https://example.com/myapp.php?foo=1&bar=2"
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+14158675310",
        "Digits": "1234",
        "From": "+14158675310",
        "To": "+18005551212",
    }
    assert twilio_signature(url, params, "12345") == "L/OH5YylLD5NRKLltdqwSvS0BnU="
    assert twilio_signature(url, {**params, "Digits": "9999"}, "12345") != "L/OH5YylLD5NRKLltdqwSvS0BnU="


def test_provider_event_replay_is_idempotent():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[m.User.__table__, m.Account.__table__, CallSession.__table__, CallProviderEvent.__table__],
    )
    with Session(engine) as db:
        db.add_all(
            [
                m.User(id="user", email="user@example.test", name="User", password_hash="hash"),
                m.Account(id="account", name="Account", domain="account.example.test"),
            ]
        )
        call = CallSession(
            id="call", account_id="account", created_by="user", source="upload", caller_id="+33199000010"
        )
        db.add(call)
        db.flush()
        first, created = remember_provider_event(
            db, call.id, "recording:RE123:completed", "recording_status", {"status": "completed"}
        )
        replay, replay_created = remember_provider_event(
            db, call.id, "recording:RE123:completed", "recording_status", {"status": "completed"}
        )
        assert created and not replay_created and first.id == replay.id
        assert db.scalar(select(func.count()).select_from(CallProviderEvent)) == 1


def test_twiml_uses_stored_destination_and_outbound_exclusions_are_enforced():
    call = CallSession(
        id="call",
        account_id="account",
        created_by="user",
        source="browser",
        destination="+33199000042",
        caller_id="+33199000010",
    )
    xml = dial_twiml(
        call,
        {
            "notice": "https://calls.example.test/notice",
            "status": "https://calls.example.test/status",
            "recording": "https://calls.example.test/recording",
        },
    )
    assert "+33199000042" in xml and "+33199000010" in xml
    assert "+33199000099" not in xml
    assert 'recordingChannels="dual"' in xml
    assert "<Number url=" not in xml and "<Say" not in xml
    with pytest.raises(HTTPException) as blocked:
        _outbound_allowed(None, SimpleNamespace(lifecycle="excluded", outreach_paused=False), None)
    assert blocked.value.status_code == 409


def test_private_audio_path_is_confined_and_payload_is_encrypted(tmp_path):
    key = Fernet.generate_key().decode()
    stored = store_audio(str(tmp_path), key, RECORDING_ID, b"raw-audio")
    encrypted = safe_blob_path(str(tmp_path), stored.blob_ref).read_bytes()
    assert b"raw-audio" not in encrypted
    assert read_audio(str(tmp_path), key, stored.blob_ref) == b"raw-audio"
    with pytest.raises(StorageUnavailable):
        safe_blob_path(str(tmp_path), "../public/audio.wav")


def test_s3_audio_is_private_encrypted_and_confined(monkeypatch):
    objects = {}

    class Body(io.BytesIO):
        pass

    class FakeS3:
        def put_object(self, **values):
            assert "ACL" not in values
            objects[(values["Bucket"], values["Key"])] = values["Body"]

        def get_object(self, **values):
            content = objects[(values["Bucket"], values["Key"])]
            return {"ContentLength": len(content), "Body": Body(content)}

        def delete_object(self, **values):
            objects.pop((values["Bucket"], values["Key"]), None)

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://objects.example.test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-3")
    monkeypatch.setattr(call_storage, "_s3_client", lambda config: FakeS3())
    monkeypatch.setattr(call_storage.importlib.util, "find_spec", lambda name: object())
    key = Fernet.generate_key().decode()
    root = "s3://private-calls/lumelia/audio"
    stored = store_audio(root, key, RECORDING_ID, b"raw-audio")
    assert stored.blob_ref == f"lumelia/audio/{RECORDING_ID}.fernet"
    assert b"raw-audio" not in objects[("private-calls", stored.blob_ref)]
    assert read_audio(root, key, stored.blob_ref) == b"raw-audio"
    with pytest.raises(StorageUnavailable):
        read_audio(root, key, "lumelia/audio/../../public.fernet")
    delete_audio = call_storage.delete_audio
    delete_audio(root, stored.blob_ref)
    assert not objects


def test_voice_token_is_short_lived_and_bound_to_call_intent():
    cfg = CallsSettings(
        LUMELIA_PUBLIC_URL="https://calls.example.test",
        TWILIO_ACCOUNT_SID="AC123",
        TWILIO_AUTH_TOKEN="auth",
        TWILIO_API_KEY_SID="SK123",
        TWILIO_API_KEY_SECRET="secret",
        TWILIO_TWIML_APP_SID="AP123",
    )
    token = voice_access_token(cfg, identity="operator-user", call_id="call-123")
    payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    assert payload["exp"] - payload["iat"] == 300
    assert payload["grants"]["voice"]["outgoing"]["params"] == {"call_id": "call-123"}
    assert "auth" not in token


def test_deepgram_multichannel_segments_preserve_speakers_and_timestamps():
    payload = {
        "metadata": {"duration": 3.0},
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "words": [
                                {"start": 0, "end": 0.5, "word": "bonjour", "confidence": 0.9},
                                {"start": 0.6, "end": 1, "word": "madame", "confidence": 0.8},
                            ]
                        }
                    ]
                },
                {
                    "alternatives": [
                        {"words": [{"start": 1.1, "end": 1.5, "word": "bonjour", "confidence": 0.95}]}
                    ]
                },
            ]
        },
    }
    segments, words = transcript_parts(payload, multichannel=True)
    assert segments == [
        {"start": 0.0, "end": 1.0, "speaker": 0, "text": "bonjour madame", "confidence": 0.85},
        {"start": 1.1, "end": 1.5, "speaker": 1, "text": "bonjour", "confidence": 0.95},
    ]
    assert [word["speaker"] for word in words] == [0, 0, 1]
    assert transcript_parts({"results": {"channels": []}}, multichannel=False) == ([], [])
