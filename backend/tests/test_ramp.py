from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app import models as m
from app import ramp, senders
from app.db import SessionLocal
from tests.test_senders import healthy, sender_payload


def activity(db, flow, sender, at, status="sent"):
    message = m.Message(
        contact_id=flow["contact"]["id"],
        campaign_id=flow["campaign"]["id"],
        sender_id=sender.id,
        status=status,
        subject="Test",
        body="Données fictives",
        dedupe_key=str(uuid4()),
        attempted_at=at,
        sent_at=at if status == "sent" else None,
    )
    db.add(message)
    return message


def test_new_mailbox_uses_small_automatic_limit_and_time_alone_does_not_warm_it(client):
    result = client.post("/api/senders", json=sender_payload()).json()
    assert result["ramp"]["enabled"] and result["ramp"]["effective_limit"] == 5
    assert result["paused"] and result["status"] != "authenticated"
    with SessionLocal() as db:
        row = db.get(m.SendingMailbox, result["id"])
        row.state = {"ramp": {"enabled": True, "started_at": (m.now() - timedelta(days=60)).isoformat()}}
        assert ramp.assess(db, row)["effective_limit"] == 5
        assert "Préparation de la boîte non validée" not in senders.readiness(row)
        assert "Connexion SMTP/IMAP à vérifier" in senders.readiness(row)
    assert client.post("/api/senders", json=sender_payload("contact@primary.example.net")).status_code == 422


def test_progress_requires_observed_successful_days_and_resets_after_inactivity(client, flow):
    with SessionLocal() as db:
        row = healthy(db)
        ramp.enable(row)
        row.state = {"ramp": {"enabled": True, "started_at": (m.now() - timedelta(days=40)).isoformat()}}
        for ago in [3, 2, 1]:
            for _ in range(3):
                activity(db, flow, row, m.now() - timedelta(days=ago))
        db.flush()
        report = ramp.assess(db, row)
        assert report["effective_limit"] == 5 and report["activity_limit"] == 10
        assert report["accepted_7d"] == 9 and report["review_required"]
        assert report["placement"] == "unmeasured"
        for message in db.scalars(select(m.Message)):
            message.attempted_at -= timedelta(days=10)
        db.flush()
        assert ramp.assess(db, row)["effective_limit"] == 5


def test_failed_attempt_stops_other_messages_without_erasing_attempt_quota(client, flow):
    with SessionLocal() as db:
        row = healthy(db)
        ramp.enable(row)
        activity(db, flow, row, m.now(), "uncertain")
        db.flush()
        assert ramp.assess(db, row)["cooldown_until"]
        assert any("suspendue" in reason for reason in senders.limits_reasons(db, row))
        assert senders.count(db, m.Message.sender_id == row.id) == 1


def test_bounce_for_same_box_stops_send_between_committed_intent_and_smtp(client, flow, monkeypatch):
    from tests.test_mail import live_fixture

    worker = live_fixture(client, flow, monkeypatch)
    calls, opens = [], 0
    monkeypatch.setattr(worker.mail, "send", lambda *args: calls.append(1))
    with SessionLocal() as db:
        ramp.enable(db.scalar(select(m.SendingMailbox)))
        db.commit()

    def session():
        nonlocal opens
        opens += 1
        if opens == 2:
            with SessionLocal() as db:
                db.add(
                    m.Event(
                        contact_id=flow["contact"]["id"],
                        channel="email",
                        kind="bounce",
                        data={"message_id": "earlier-message"},
                    )
                )
                db.commit()
        return SessionLocal()

    monkeypatch.setattr(worker, "SessionLocal", session)
    assert worker.tick() == 1
    assert not calls
    assert client.get("/api/messages").json()[0]["status"] == "cancelled"


def test_reply_collection_remains_active_when_prospect_sends_are_disabled(client, monkeypatch):
    from app import worker
    from app.config import settings

    assert settings().live_actions_enabled is False
    with SessionLocal() as db:
        healthy(db)
        db.commit()
    calls = []
    monkeypatch.setattr(worker.mail, "collect", lambda db, row, cfg: calls.append(row.id))
    worker.collection_tick()
    assert len(calls) == 1


def test_connection_is_checked_automatically_without_changing_the_send_pause(client, monkeypatch):
    from app import worker

    result = client.post("/api/senders", json=sender_payload()).json()
    calls = []
    monkeypatch.setattr(worker.mail, "verify", lambda cfg: calls.append(1) or {"authenticated": True})
    worker.connection_tick()
    worker.connection_tick()
    row = client.get("/api/senders/" + result["id"]).json()
    assert calls == [1] and row["status"] == "authenticated" and row["paused"]


def checks(client, sender_id, ago=0, outcome="inbox"):
    for provider in ["gmail", "outlook"]:
        result = client.post(
            "/api/senders/" + sender_id + "/delivery-check",
            json={
                "provider": provider,
                "outcome": outcome,
                "observed_at": (m.now() - timedelta(days=ago, seconds=1)).isoformat(),
                "note": "Observation fictive du message test, vérifiée dans la recette.",
            },
        )
        assert result.status_code == 200, result.text


def test_progress_requires_recent_external_placement_and_explicit_review(client, flow):
    with SessionLocal() as db:
        row = healthy(db)
        row.state = {"ramp": {"enabled": True, "started_at": (m.now() - timedelta(days=20)).isoformat()}}
        for ago in [3, 2, 1]:
            for _ in range(3):
                activity(db, flow, row, m.now() - timedelta(days=ago))
        db.commit()
        sender_id = row.id
    endpoint = "/api/senders/" + sender_id
    assert client.post(endpoint + "/approve-ramp-step").status_code == 422
    checks(client, sender_id, ago=8)
    assert client.post(endpoint + "/approve-ramp-step").status_code == 422
    checks(client, sender_id)
    row = client.get(endpoint).json()
    assert row["ramp"]["external_checks_current"] and row["ramp"]["effective_limit"] == 5
    assert row["ramp"]["next_limit"] == 10
    approved = client.post(endpoint + "/approve-ramp-step")
    assert approved.status_code == 200, approved.text
    assert approved.json()["ramp"]["effective_limit"] == 10
    assert client.post(endpoint + "/approve-ramp-step").status_code == 422
    checks(client, sender_id, outcome="spam")
    row = client.get(endpoint).json()
    assert row["available_24h"] == 0 and row["ramp"]["effective_limit"] == 5
    assert any("spam" in reason for reason in row["blocking_reasons"])


def test_delivery_observations_reject_future_and_stale_overwrites(client):
    row = client.post("/api/senders", json=sender_payload()).json()
    endpoint = "/api/senders/" + row["id"] + "/delivery-check"
    payload = {
        "provider": "gmail",
        "outcome": "inbox",
        "observed_at": (m.now() + timedelta(days=1)).isoformat(),
        "note": "Données fictives de test.",
    }
    assert client.post(endpoint, json=payload).status_code == 422
    payload["observed_at"] = m.now().replace(tzinfo=None).isoformat()
    assert client.post(endpoint, json=payload).status_code == 422
    checks(client, row["id"])
    payload["observed_at"] = (m.now() - timedelta(days=1)).isoformat()
    assert client.post(endpoint, json=payload).status_code == 409


def test_global_capacity_follows_eligible_mailboxes_but_pause_means_zero_available(client):
    with SessionLocal() as db:
        a, b = healthy(db), healthy(db, "third@example.org")
        ramp.enable(a)
        ramp.enable(b)
        db.commit()
        first = a.id
    result = client.patch("/api/state", json={"daily_limit_mode": "mailboxes"})
    assert result.status_code == 200, result.text
    assert client.get("/api/state").json()["effective_daily_limit"] == 10
    boxes = client.get("/api/senders").json()
    assert all(box["available_24h"] == 0 and box["global_sending_paused"] for box in boxes)
    with SessionLocal() as db:
        db.get(m.SendingMailbox, first).paused = True
        db.commit()
    assert client.get("/api/state").json()["effective_daily_limit"] == 5
    client.patch("/api/state", json={"daily_limit_mode": "manual", "daily_limit": 7})
    assert client.get("/api/state").json()["effective_daily_limit"] == 7
