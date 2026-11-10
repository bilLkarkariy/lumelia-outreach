"""Focused routing contracts. Telephony I/O is mocked; no real numbers are called."""

from datetime import timedelta
from uuid import uuid4
from xml.etree import ElementTree as ET

import httpx
import pytest
from sqlalchemy import func, select

from app import calls
from app import models as m
from app import phone_routing as phone
from app.call_models import CallSession, CallTranscript
from app.calls import CALLER_ID, CallsSettings, twilio_signature
from app.db import SessionLocal

ROOT = "CA" + "1" * 32
CHILD = "CA" + "2" * 32


@pytest.fixture
def telephony(monkeypatch):
    cfg = CallsSettings(
        _env_file=None,
        LUMELIA_PUBLIC_URL="https://voice.example.test",
        TWILIO_ACCOUNT_SID="AC" + "3" * 32,
        TWILIO_AUTH_TOKEN="unit-test-secret",
        TWILIO_OPERATOR_NUMBER="+33199000061",
        TWILIO_INBOUND_ENABLED=True,
    )
    monkeypatch.setattr(calls, "call_settings", lambda: cfg)
    monkeypatch.setattr(phone, "call_settings", lambda: cfg)

    def unexpected(*args, **kwargs):
        raise AssertionError("Unexpected external HTTP request")

    monkeypatch.setattr(httpx, "post", unexpected)
    monkeypatch.setattr(httpx, "get", unexpected)
    return cfg


def webhook(client, cfg, path, **params):
    params = {"AccountSid": cfg.twilio_account_sid, **params}
    return client.post(
        path,
        data=params,
        headers={
            "X-Twilio-Signature": twilio_signature(cfg.callback_base + path, params, cfg.twilio_auth_token)
        },
    )


def intent(flow):
    return {
        "account_id": flow["account"]["id"],
        "contact_id": flow["contact"]["id"],
        "destination": flow["contact"]["phone"],
        "request_id": str(uuid4()),
        "recording_information_confirmed": True,
        "information_proof": "Choix confirmé au téléphone",
    }


def test_mobile_acceptance_idempotency_opt_out_and_out_of_order_callbacks(
    client, flow, telephony, monkeypatch
):
    sent = []

    def create(url, **kwargs):
        sent.append(kwargs["data"])
        return httpx.Response(201, json={"sid": ROOT})

    monkeypatch.setattr(httpx, "post", create)
    data = intent(flow)
    response = client.post("/api/calls/mobile", json=data)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["status"] == "operator_ringing"
    assert client.post("/api/calls/mobile", json=data).json()["id"] == row["id"]
    assert len(sent) == 1 and sent[0]["To"] == telephony.operator_number
    base = f"/api/calls/provider/{row['id']}"
    operator = dict(CallSid=ROOT, To=telephony.operator_number, From=CALLER_ID)
    answer = webhook(client, telephony, base + "/mobile/answer", **operator)
    assert ET.fromstring(answer.text).find("Dial") is None
    connect = webhook(client, telephony, base + "/mobile/connect", Digits="2", **operator)
    dial = ET.fromstring(connect.text).find("Dial")
    assert dial.attrib["record"] == "do-not-record"
    assert dial.find("Number").text == data["destination"]
    assert "url" not in dial.find("Number").attrib
    replay = webhook(client, telephony, base + "/mobile/connect", Digits="2", **operator)
    assert ET.fromstring(replay.text).find("Dial") is None
    webhook(client, telephony, base + "/mobile/status", CallStatus="completed", **operator)
    assert client.get("/api/calls/" + row["id"]).json()["status"] == "connecting"
    for _ in range(2):
        result = webhook(
            client,
            telephony,
            base + "/bridge-status",
            CallSid=ROOT,
            DialCallSid=CHILD,
            DialCallStatus="completed",
            DialCallDuration="23",
        )
        assert result.status_code == 200 and ET.fromstring(result.text).find("Hangup") is not None
    result = client.get("/api/calls/" + row["id"]).json()
    assert result["status"] == "completed" and result["duration_seconds"] == 23
    assert result["processing_status"] == "not_recorded"
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(m.Event)
                .where(m.Event.external_id == "call-ended:" + row["id"])
            )
            == 1
        )


def test_uncertain_mobile_post_is_not_replayed_and_operator_loop_is_rejected(
    client, flow, telephony, monkeypatch
):
    sent = []

    def timeout(url, **kwargs):
        sent.append(url)
        raise httpx.ReadTimeout("provider timeout")

    monkeypatch.setattr(httpx, "post", timeout)
    data = intent(flow)
    assert (
        client.post("/api/calls/mobile", json={**data, "destination": telephony.operator_number}).status_code
        == 422
    )
    row = client.post("/api/calls/mobile", json=data).json()
    assert row["status"] == "uncertain"
    assert client.post("/api/calls/mobile", json=data).json()["id"] == row["id"]
    assert client.post("/api/calls/mobile", json={**data, "request_id": str(uuid4())}).status_code == 409
    assert len(sent) == 1
    canceled = client.post(f"/api/calls/{row['id']}/cancel")
    assert canceled.json()["status"] == "canceled"
    late = webhook(
        client,
        telephony,
        f"/api/calls/provider/{row['id']}/mobile/answer",
        CallSid=ROOT,
        To=telephony.operator_number,
        From=CALLER_ID,
    )
    assert ET.fromstring(late.text).find("Hangup") is not None
    assert ET.fromstring(late.text).find("Dial") is None


def test_incoming_opt_out_acceptance_and_missed_followup_are_idempotent(client, flow, telephony):
    assert client.post("/api/calls/routing", json={"enabled": False}).status_code == 200
    paused = client.get("/api/calls/status").json()
    assert paused["inbound_configured"] and not paused["inbound_call_available"]
    assert client.post("/api/calls/routing", json={"enabled": True}).status_code == 200
    original = dict(CallSid=ROOT, To=CALLER_ID, From=flow["contact"]["phone"], Direction="inbound")
    assert client.post("/api/calls/provider/incoming", data=original).status_code == 403
    for _ in range(2):
        assert webhook(client, telephony, "/api/calls/provider/incoming", **original).status_code == 200
    rows = client.get("/api/calls?source=inbound").json()["items"]
    assert len(rows) == 1 and rows[0]["contact_id"] == flow["contact"]["id"]
    base = f"/api/calls/provider/{rows[0]['id']}"
    connected = webhook(client, telephony, base + "/incoming/connect", Digits="2", **original)
    assert ET.fromstring(connected.text).find("Dial").attrib["record"] == "do-not-record"
    accept = dict(CallSid=CHILD, ParentCallSid=ROOT, To=telephony.operator_number, From=CALLER_ID)
    assert (
        ET.fromstring(webhook(client, telephony, base + "/incoming/accept", **accept).text).find("Gather")
        is not None
    )
    assert webhook(client, telephony, base + "/incoming/accept", Digits="1", **accept).status_code == 200
    webhook(client, telephony, "/api/calls/provider/incoming/status", CallStatus="completed", **original)
    assert client.get("/api/calls/" + rows[0]["id"]).json()["status"] == "completed"
    missed_sid = "CA" + "4" * 32
    webhook(client, telephony, "/api/calls/provider/incoming", **{**original, "CallSid": missed_sid})
    for _ in range(2):
        webhook(
            client,
            telephony,
            "/api/calls/provider/incoming/status",
            CallStatus="completed",
            **{**original, "CallSid": missed_sid},
        )
    with SessionLocal() as db:
        tasks = list(db.scalars(select(m.Task).where(m.Task.dedupe_key.like("missed-call:%"))))
        assert len(tasks) == 1 and tasks[0].contact_id == flow["contact"]["id"]


def test_unknown_incoming_keeps_transcript_until_linked_without_fake_company(client, flow, telephony):
    original = dict(CallSid=ROOT, To=CALLER_ID, From="+33199000098", Direction="inbound")
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(m.Account))
    webhook(client, telephony, "/api/calls/provider/incoming", **original)
    row = client.get("/api/calls?source=inbound").json()["items"][0]
    assert row["account_id"] is None and row["contact_id"] is None
    with SessionLocal() as db:
        from app.call_processing import enqueue_report

        call = db.get(CallSession, row["id"])
        call.status = "completed"
        transcript = CallTranscript(call_id=call.id, version=1, status="ready", segments=[{"text": "Test"}])
        db.add(transcript)
        db.flush()
        enqueue_report(db, call, transcript)
        db.commit()
        assert call.processing_status == "unassigned"
        assert db.scalar(select(func.count()).select_from(m.Account)) == before
        assert (
            db.scalar(
                select(func.count())
                .select_from(m.AgentAction)
                .where(m.AgentAction.kind == "post_call_report")
            )
            == 0
        )
    candidates = client.get("/api/calls/link-candidates?q=Alice").json()["items"]
    assert candidates[0]["id"] == flow["contact"]["id"]
    for _ in range(2):
        result = client.post(f"/api/calls/{row['id']}/link", json={"contact_id": flow["contact"]["id"]})
        assert result.status_code == 200, result.text
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(m.AgentAction)
                .where(m.AgentAction.kind == "post_call_report")
            )
            == 1
        )
        assert db.get(CallSession, row["id"]).processing_status == "transcribed"


def test_missing_bridge_callback_reconciles_with_reads_only(client, flow, telephony, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(201, json={"sid": ROOT}))
    row = client.post("/api/calls/mobile", json=intent(flow)).json()
    with SessionLocal() as db:
        call = db.get(CallSession, row["id"])
        call.created_at = m.now() - timedelta(minutes=3)
        call.status = "connecting"
        call.data = {**call.data, "bridge_started": True}
        db.commit()

    def read(url, **kwargs):
        payload = (
            {"status": "completed"}
            if ROOT in url
            else {"calls": [{"to": flow["contact"]["phone"], "status": "completed", "duration": "14"}]}
        )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", read)
    assert phone.reconcile_tick() == {"checked": 1}
    assert client.get("/api/calls/" + row["id"]).json()["status"] == "completed"
