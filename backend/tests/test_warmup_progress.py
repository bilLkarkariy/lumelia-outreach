from datetime import timedelta

from sqlalchemy import select

from app import models as m
from app import native_warmup as nw
from app import senders, warmup_progress
from app.db import SessionLocal
from app.warmup_policy import cold_validation_reasons, config
from tests.test_native_warmup import setup


def progress(client, key):
    return next(
        r["progress"] for r in client.get("/api/mailbox-warmup").json()["mailboxes"] if r["id"] == key
    )


def seed_receipts(db, key, *, count=50, days=14, received=True, version=None):
    row = db.get(m.SendingMailbox, key)
    beginning = m.now() - timedelta(days=120)
    row.state = {**row.state, "native_warmup": {**config(row), "program_started_at": beginning.isoformat()}}
    for i in range(count):
        at = m.now() - timedelta(days=i % days, minutes=5)
        db.add(
            m.Job(
                kind=nw.KIND,
                provider="smtp",
                status="completed" if received else "waiting",
                attempts=1,
                created_at=at,
                locked_at=at,
                idempotency_key="progress:" + m.uid(),
                payload={
                    "sender_id": key,
                    "sender_version": version or row.config_version,
                    "recipient_email": "other@example.net",
                },
                result={
                    "accepted": True,
                    "received": received,
                    "received_at": at.isoformat() if received else None,
                    "folder": "INBOX",
                },
            )
        )
    db.commit()
    return row


def checks(db, key):
    row = db.get(m.SendingMailbox, key)
    row.state = {
        **row.state,
        "delivery_checks": {
            provider: {"outcome": "inbox", "observed_at": m.now().isoformat()}
            for provider in ["gmail", "outlook"]
        },
    }
    db.commit()


def validate(client, key):
    p = progress(client, key)
    return client.post(
        "/api/mailbox-warmup/" + key + "/validate",
        json={
            "expected_plan_key": p["plan_key"],
            "note": "Revue fictive des deux réceptions externes et de la connexion.",
        },
    )


def test_progress_uses_the_slower_goal_and_never_elapsed_days_alone(client):
    key = setup(client)[0]
    with SessionLocal() as db:
        seed_receipts(db, key, count=1, days=1)
    p = progress(client, key)
    assert p["percent"] == 2 and p["active_days"] == 1 and p["received"] == 1
    assert p["elapsed_days"] > 100 and not p["complete"]
    assert p["readiness"] == "in_progress"


def test_volume_alone_cannot_complete_the_programme(client):
    key = setup(client)[0]
    with SessionLocal() as db:
        seed_receipts(db, key, count=200, days=1)
    p = progress(client, key)
    assert p["percent"] == 7.1 and not p["complete"]
    assert validate(client, key).status_code == 409


def test_smtp_acceptance_without_imap_receipt_does_not_count(client):
    key = setup(client)[0]
    with SessionLocal() as db:
        seed_receipts(db, key, received=False)
    p = progress(client, key)
    assert p["accepted"] == 50 and p["percent"] == p["received"] == p["active_days"] == 0


def test_full_progress_requires_validation_but_not_external_delivery_checks(client):
    key = setup(client)[0]
    with SessionLocal() as db:
        seed_receipts(db, key)
    p = progress(client, key)
    assert p["percent"] == 100 and p["complete"] and p["readiness"] == "awaiting_validation"
    assert not p["checks"]["gmail"]["valid"] and not p["checks"]["outlook"]["valid"]
    assert validate(client, key).status_code == 200
    assert progress(client, key)["readiness"] == "validated"
    state = client.get("/api/state").json()
    assert state["paused"] and not state["live_actions_enabled"]
    with SessionLocal() as db:
        row = db.get(m.SendingMailbox, key)
        assert row.paused and row.daily_limit == 50
        assert not cold_validation_reasons(row)


def test_cold_gate_does_not_block_ordinary_replies_or_reduce_physical_capacity(client):
    key = setup(client)[0]
    with SessionLocal() as db:
        row = db.get(m.SendingMailbox, key)
        row.paused = False
        assert cold_validation_reasons(row)
        assert any("valider" in reason for reason in senders.readiness(row))
        assert senders.readiness(row, cold=False) == []
        state = db.get(m.AppState, "global")
        state.settings = {**state.settings, "daily_limit_mode": "mailboxes"}
        assert senders.global_limit(db, state) == 40


def test_old_external_checks_do_not_expire_validated_preparation(client):
    key = setup(client)[0]
    with SessionLocal() as db:
        seed_receipts(db, key)
        checks(db, key)
    assert validate(client, key).status_code == 200
    with SessionLocal() as db:
        row = db.get(m.SendingMailbox, key)
        row.state = {
            **row.state,
            "delivery_checks": {
                "gmail": {"outcome": "inbox", "observed_at": (m.now() - timedelta(days=8)).isoformat()}
            },
        }
        db.commit()
        assert not cold_validation_reasons(row)
    p = progress(client, key)
    assert p["percent"] == 100 and p["readiness"] == "validated"


def test_plan_changes_preserve_results_and_require_new_validation(client):
    key = setup(client)[0]
    with SessionLocal() as db:
        seed_receipts(db, key)
        checks(db, key)
    assert validate(client, key).status_code == 200
    before = progress(client, key)
    response = client.patch(
        "/api/mailbox-warmup/" + key + "/plan",
        json={"target_active_days": 14, "target_received": 200, "expected_plan_key": before["plan_key"]},
    )
    assert response.status_code == 200
    after = progress(client, key)
    assert after["received"] == 50 and after["percent"] == 25 and after["active_days"] == 14
    assert after["validation"]["invalidated_at"]
    assert (
        client.patch(
            "/api/mailbox-warmup/" + key + "/plan",
            json={"target_active_days": 14, "target_received": 50, "expected_plan_key": before["plan_key"]},
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        assert cold_validation_reasons(db.get(m.SendingMailbox, key))


def test_pause_and_resume_preserve_programme_and_goals(client):
    ids = setup(client)
    with SessionLocal() as db:
        seed_receipts(db, ids[0], count=1)
    before = progress(client, ids[0])
    with SessionLocal() as db:
        row = db.get(m.SendingMailbox, ids[0])
        cadence_before = nw.cadence(row, nw.history(db, row))
    assert client.post("/api/mailbox-warmup/pause").status_code == 200
    assert (
        client.post("/api/mailbox-warmup/start", json={"mailbox_ids": ids, "confirm_owned": True}).status_code
        == 200
    )
    after = progress(client, ids[0])
    assert before["started_at"] == after["started_at"] and after["received"] == 1
    with SessionLocal() as db:
        row = db.get(m.SendingMailbox, ids[0])
        assert nw.cadence(row, nw.history(db, row)) == cadence_before


def test_new_connection_does_not_inherit_old_progress(client):
    key = setup(client)[0]
    with SessionLocal() as db:
        row = seed_receipts(db, key)
        row.config_version += 1
        db.commit()
        assert warmup_progress.assess(db, row)["percent"] == 0
        assert cold_validation_reasons(row)


def test_incident_blocks_validation_even_at_one_hundred_percent(client):
    key = setup(client)[0]
    with SessionLocal() as db:
        seed_receipts(db, key)
        checks(db, key)
        db.add(
            m.Job(
                kind=nw.KIND,
                provider="smtp",
                status="uncertain",
                attempts=1,
                idempotency_key="uncertain-progress",
                payload={"sender_id": key, "sender_version": 1},
            )
        )
        db.commit()
    assert progress(client, key)["percent"] == 100
    assert validate(client, key).status_code == 409


def test_validation_and_plan_endpoints_require_admin(client):
    key = setup(client)[0]
    with SessionLocal() as db:
        db.scalar(select(m.User)).role = "operator"
        db.commit()
    assert validate(client, key).status_code == 403
    p = progress(client, key)
    assert (
        client.patch(
            "/api/mailbox-warmup/" + key + "/plan",
            json={"target_active_days": 14, "target_received": 50, "expected_plan_key": p["plan_key"]},
        ).status_code
        == 403
    )
