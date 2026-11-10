from datetime import datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app import mail, senders, worker
from app import models as m
from app import native_warmup as nw
from app.db import SessionLocal
from app.providers import ProviderError
from app.warmup_policy import reserve
from tests.test_senders import healthy


def setup(client):
    with SessionLocal() as db:
        rows = [
            healthy(db, address) for address in ("one@example.org", "two@example.net", "three@example.net")
        ]
        for row in rows:
            row.paused = True  # Only campaigns are paused.
        db.commit()
        ids = [row.id for row in rows]
    result = client.post("/api/mailbox-warmup/start", json={"mailbox_ids": ids, "confirm_owned": True})
    assert result.status_code == 200, result.text
    return ids


def receipt(job):
    email = EmailMessage()
    email["Message-ID"] = job.external_id
    email["From"] = job.payload["sender_email"]
    email["To"] = job.payload["recipient_email"]
    email["X-Lumelia-Preparation"] = job.id
    email.set_content("Fictional controlled test")
    return email


def test_native_start_requires_two_owned_healthy_mailboxes_and_admin(client):
    with SessionLocal() as db:
        row = healthy(db)
        db.commit()
        key = row.id
    assert (
        client.post(
            "/api/mailbox-warmup/start", json={"mailbox_ids": [key, key], "confirm_owned": True}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/mailbox-warmup/start", json={"mailbox_ids": [key, key], "confirm_owned": False}
        ).status_code
        == 422
    )
    with SessionLocal() as db:
        other = healthy(db, "different@example.net")
        other.status = "error"
        db.commit()
        key2 = other.id
    assert (
        client.post(
            "/api/mailbox-warmup/start", json={"mailbox_ids": [key, key2], "confirm_owned": True}
        ).status_code
        == 409
    )
    assert not any(r["enabled"] for r in client.get("/api/mailbox-warmup").json()["mailboxes"])
    with SessionLocal() as db:
        db.scalar(select(m.User)).role = "operator"
        db.commit()
    assert client.post("/api/mailbox-warmup/pause").status_code == 403


def test_native_sends_while_campaigns_paused_but_only_between_selected_mailboxes(client, monkeypatch):
    ids = setup(client)
    captured = []

    def transport(job, sender):
        with SessionLocal() as db:
            persisted = db.get(m.Job, job.id)
            assert persisted.status == "running" and persisted.attempts == 1
        assert sender.id in ids and job.payload["recipient_id"] in ids
        assert job.payload["recipient_id"] != sender.id
        captured.append(job.id)

    monkeypatch.setattr(nw, "transmit", transport)
    assert nw.tick() == 1
    assert nw.tick() == 1  # Another mailbox may progress while the first receipt is pending.
    assert len(captured) == 2
    with SessionLocal() as db:
        sent = list(db.scalars(select(m.Job).where(m.Job.kind == nw.KIND)))
        assert len({job.payload["sender_id"] for job in sent}) == 2
    state = client.get("/api/state").json()
    assert state["paused"] and not state["live_actions_enabled"]
    data = client.get("/api/mailbox-warmup").json()
    assert data["paid_service"] is False
    assert sum(r["accepted_7d"] for r in data["mailboxes"]) == 2
    assert all(r["received_7d"] == 0 for r in data["mailboxes"])
    with SessionLocal() as db:
        assert db.query(m.Contact).count() == db.query(m.Message).count() == db.query(m.Expense).count() == 0


def test_imap_receipt_is_required_and_does_not_pollute_prospect_inbox(client, monkeypatch):
    setup(client)
    monkeypatch.setattr(nw, "transmit", lambda *args: None)
    nw.tick()
    with SessionLocal() as db:
        job = db.scalar(select(m.Job).where(m.Job.kind == nw.KIND))
        raw = receipt(job).as_bytes()
        assert job.status == "waiting"
        events_before = db.query(m.Event).count()
        mail.parse_reply(db, raw, "imap:test:1", job.payload["recipient_id"], "INBOX")
        assert job.status == "completed" and job.result["received"]
        observed = job.result["received_at"]
        mail.parse_reply(db, raw, "imap:test:1", job.payload["recipient_id"], "INBOX")
        assert job.result["received_at"] == observed
        assert db.query(m.Event).count() == events_before
        assert not nw.record_receipt(db, receipt(job), "wrong-box", "INBOX", "wrong")
        db.commit()
    data = client.get("/api/mailbox-warmup").json()
    assert sum(r["received_7d"] for r in data["mailboxes"]) == 1


def test_junk_receipt_is_an_incident_and_never_queues_a_reply(client, monkeypatch):
    setup(client)
    monkeypatch.setattr(nw, "transmit", lambda *args: None)
    nw.tick()
    with SessionLocal() as db:
        original = db.scalar(select(m.Job).where(m.Job.kind == nw.KIND))
        assert nw.record_receipt(db, receipt(original), original.payload["recipient_id"], "Junk", "junk:imap")
        assert original.status == "failed"
        assert original.result["placement"] == "spam"
        assert not db.scalar(select(m.Job.id).where(m.Job.kind == nw.KIND, m.Job.status == "queued"))
        db.commit()
    assert any(row["status"] == "blocked" for row in client.get("/api/mailbox-warmup").json()["mailboxes"])


def test_snapshot_exposes_the_same_due_reply_the_worker_will_send(client, monkeypatch):
    setup(client)
    monkeypatch.setattr(nw, "transmit", lambda *args: None)
    nw.tick()
    with SessionLocal() as db:
        original = db.scalar(select(m.Job).where(m.Job.kind == nw.KIND))
        nw.record_receipt(db, receipt(original), original.payload["recipient_id"], "INBOX", "original:imap")
        reply = db.scalar(select(m.Job).where(m.Job.kind == nw.KIND, m.Job.status == "queued"))
        reply.created_at = m.now() - timedelta(minutes=16)
        reply_sender = reply.payload["sender_id"]
        db.commit()
    account = next(
        row for row in client.get("/api/mailbox-warmup").json()["mailboxes"] if row["id"] == reply_sender
    )
    assert account["next_kind"] == "reply"
    assert datetime.fromisoformat(account["next_at"]) <= m.now()
    assert nw.tick() == 1
    with SessionLocal() as db:
        assert db.get(m.Job, reply.id).status == "waiting"


def test_uncertain_smtp_is_never_repeated_and_stale_intent_is_not_replayed(client, monkeypatch):
    setup(client)
    calls = []

    def fail(job, row):
        calls.append(job.id)
        raise ProviderError("smtp", "ambiguous", uncertain=True)

    monkeypatch.setattr(nw, "transmit", fail)
    for _ in range(8):
        nw.tick()
    assert len(calls) == 3 and len(set(calls)) == 3
    with SessionLocal() as db:
        job = db.scalar(select(m.Job).where(m.Job.kind == nw.KIND))
        job.status = "running"
        job.created_at = m.now() - timedelta(minutes=5)
        db.commit()
    nw.tick()
    assert len(calls) == 3
    assert any(r["status"] == "blocked" for r in client.get("/api/mailbox-warmup").json()["mailboxes"])


def test_pause_keeps_reserve_and_disallows_new_sends(client, monkeypatch):
    setup(client)
    monkeypatch.setattr(nw, "transmit", lambda *args: None)
    nw.tick()
    assert client.post("/api/mailbox-warmup/pause").status_code == 200
    assert nw.tick() == 0
    with SessionLocal() as db:
        assert all(reserve(row) == 10 for row in db.scalars(select(m.SendingMailbox)))
        assert all(row.paused for row in db.scalars(select(m.SendingMailbox)))


def test_native_reservation_applies_to_campaign_and_provider_capacity(client):
    setup(client)
    with SessionLocal() as db:
        row = db.scalar(select(m.SendingMailbox))
        row.external_daily_reserve = 20
        row.total_daily_limit = 31
        assert senders.ramp.assess(db, row)["effective_limit"] == 1
        used, reserved = senders.group_usage(db, "provider", row.provider_group)
        assert reserved == 90 and used == 0
        policy = db.scalar(select(m.EmailLimit).where(m.EmailLimit.scope == "provider"))
        policy.daily_limit = 89
        db.commit()
    assert nw.tick() == 0


def test_configuration_change_revokes_preparation_and_preserves_capacity(client):
    ids = setup(client)
    with SessionLocal() as db:
        row = db.get(m.SendingMailbox, ids[0])
        from tests.test_senders import sender_payload

        senders.configure(db, senders.SenderCreate.model_validate(sender_payload(row.from_email)), row)
        assert not nw.config(row)["enabled"] and reserve(row) == 10
        db.commit()


def test_cadence_counts_received_paris_days_for_current_connection_only(client):
    ids = setup(client)
    with SessionLocal() as db:
        row = db.get(m.SendingMailbox, ids[0])
        row.state = {
            **row.state,
            "native_warmup": {
                **nw.config(row),
                "program_started_at": (m.now() - timedelta(days=6)).isoformat(),
            },
        }
        paris = ZoneInfo("Europe/Paris")
        local = m.now().astimezone(paris)
        same_day = local.date() - timedelta(days=1)
        first = datetime.combine(same_day, datetime.min.time(), tzinfo=paris) + timedelta(minutes=30)
        second = datetime.combine(same_day, datetime.min.time(), tzinfo=paris) + timedelta(
            hours=23, minutes=30
        )
        jobs = [
            m.Job(
                kind=nw.KIND,
                status="completed",
                attempts=1,
                created_at=at,
                payload={"sender_id": row.id, "sender_version": row.config_version},
                result={"received": True, "received_at": at.isoformat()},
            )
            for at in [first, second]
        ]
        wrong_version = m.Job(
            kind=nw.KIND,
            status="completed",
            attempts=1,
            created_at=m.now() - timedelta(days=2),
            payload={"sender_id": row.id, "sender_version": row.config_version + 1},
            result={"received": True, "received_at": (m.now() - timedelta(days=2)).isoformat()},
        )
        assert nw.cadence_details(row, [*jobs, wrong_version])["healthy_days"] == 1
        assert nw.cadence(row, [*jobs, wrong_version]) == 2
        for job in jobs:
            job.status = "waiting"
        assert nw.cadence(row, jobs) == 2


def test_missing_receipt_stops_sender_after_two_hours(client, monkeypatch):
    setup(client)
    monkeypatch.setattr(nw, "transmit", lambda *args: None)
    nw.tick()
    with SessionLocal() as db:
        job = db.scalar(select(m.Job).where(m.Job.kind == nw.KIND))
        job.created_at = m.now() - timedelta(hours=3)
        key = job.id
        db.commit()
    nw.tick()
    with SessionLocal() as db:
        assert db.get(m.Job, key).status == "failed"


def test_worker_has_native_lane_and_no_paid_warmup(monkeypatch, client):
    import inspect

    source = inspect.getsource(worker.main)
    assert '"warmup": native_warmup.tick' in source
    assert "safe(warmup.tick)" not in source


def test_alerts_group_agent_measurements_and_suppress_paused_campaign_noise(client):
    ids = setup(client)
    with SessionLocal() as db:
        db.get(m.AppState, "global").worker_seen_at = m.now()
        for key in ids:
            row = db.get(m.SendingMailbox, key)
            senders.ramp.enable(row)
        for i in range(4):
            db.add(m.Job(kind="agent_usage", status="uncertain", idempotency_key="usage-test-" + str(i)))
        db.add(m.Job(kind="reply", status="uncertain", idempotency_key="reply-test"))
        for i in range(3):
            db.add(
                m.Job(
                    kind="apify_discovery",
                    provider="apify",
                    status="failed",
                    error="apify: HTTP 400",
                    idempotency_key="apify-rejected-" + str(i),
                )
            )
        db.commit()
    items = client.get("/api/alerts").json()["items"]
    usage = [a for a in items if a["id"] == "agent-usage"]
    assert len(usage) == 1 and usage[0]["count"] == 4 and usage[0]["severity"] == "warning"
    assert sum(a["title"] == "Envoi à rapprocher" for a in items) == 1
    assert not any("warming:" in a["id"] for a in items)
    collection = [a for a in items if a["id"] == "apify-rejected"]
    assert len(collection) == 1 and collection[0]["count"] == 3


def test_observed_original_queues_one_reply_and_reply_receipt_never_loops(client, monkeypatch):
    setup(client)
    outgoing = []
    monkeypatch.setattr(nw, "transmit", lambda job, row: outgoing.append(job.id))
    nw.tick()
    with SessionLocal() as db:
        original = db.scalar(select(m.Job).where(m.Job.kind == nw.KIND))
        nw.record_receipt(db, receipt(original), original.payload["recipient_id"], "INBOX", "original:imap")
        db.commit()
        nw.record_receipt(db, receipt(original), original.payload["recipient_id"], "INBOX", "original:imap")
        db.commit()
        assert db.query(m.Job).filter(m.Job.kind == nw.KIND).count() == 2
        reply = db.scalar(select(m.Job).where(m.Job.kind == nw.KIND, m.Job.status == "queued"))
        assert reply.payload["in_reply_to"] == original.external_id
        assert reply.payload["recipient_id"] == original.payload["sender_id"]
        reply.created_at = m.now() - timedelta(minutes=16)
        key = reply.id
        db.commit()
    assert nw.tick() == 1
    assert outgoing[-1] == key  # Replies take priority over new original tests.
    with SessionLocal() as db:
        reply = db.get(m.Job, key)
        assert reply.status == "waiting"
        nw.record_receipt(db, receipt(reply), reply.payload["recipient_id"], "INBOX", "reply:imap")
        db.commit()
        assert db.query(m.Job).filter(m.Job.kind == nw.KIND).count() == 2
    assert sum(r["replies_received_7d"] for r in client.get("/api/mailbox-warmup").json()["mailboxes"]) == 1


def test_reply_transport_is_threaded_and_transparently_automatic(client, monkeypatch):
    ids = setup(client)
    captured = []

    class SMTP:
        def send_message(self, email, **kwargs):
            captured.append((email, kwargs))
            return {}

        def quit(self):
            pass

    monkeypatch.setattr(mail, "connect_smtp", lambda cfg: SMTP())
    with SessionLocal() as db:
        row = db.get(m.SendingMailbox, ids[0])
        job = m.Job(
            id=m.uid(),
            kind=nw.KIND,
            external_id="<lumelia-preparation-test@example.org>",
            payload={
                "recipient_email": "two@example.net",
                "reply_to_job_id": "original-id",
                "in_reply_to": "<original@example.net>",
            },
        )
        nw.transmit(job, row)
    email, kwargs = captured[0]
    assert email["Auto-Submitted"] == "auto-replied"
    assert email["In-Reply-To"] == email["References"] == "<original@example.net>"
    assert kwargs["to_addrs"] == ["two@example.net"]


def test_queued_reply_consumes_the_shared_daily_cap(client, monkeypatch):
    setup(client)
    monkeypatch.setattr(nw, "transmit", lambda *args: None)
    nw.tick()
    with SessionLocal() as db:
        original = db.scalar(select(m.Job).where(m.Job.kind == nw.KIND))
        recipient = db.get(m.SendingMailbox, original.payload["recipient_id"])
        db.add(
            m.Job(
                kind=nw.KIND,
                status="completed",
                attempts=1,
                created_at=m.now() - timedelta(hours=1),
                idempotency_key="cap-fixture:" + recipient.id,
                payload={"sender_id": recipient.id, "sender_version": recipient.config_version},
                result={"received": True, "received_at": (m.now() - timedelta(hours=1)).isoformat()},
            )
        )
        db.flush()
        nw.record_receipt(db, receipt(original), original.payload["recipient_id"], "INBOX", original.id)
        reply = db.scalar(select(m.Job).where(m.Job.kind == nw.KIND, m.Job.status == "queued"))
        reply.created_at = m.now() - timedelta(minutes=16)
        db.commit()
    assert nw.tick() == 1  # The reply is the second, and last, initial-day transmission for its sender.
    with SessionLocal() as db:
        recipient = db.get(m.SendingMailbox, original.payload["recipient_id"])
        jobs = nw.history(db, recipient)
        assert (
            len([job for job in jobs if job.attempts and job.created_at > m.now() - timedelta(hours=24)]) == 2
        )
        assert nw.next_transmission_at(db, recipient, jobs) > m.now()


def test_reduced_cap_waits_for_enough_transmissions_to_leave_rolling_window(client, monkeypatch):
    key = setup(client)[0]
    now = m.now()
    monkeypatch.setattr(m, "now", lambda: now)
    with SessionLocal() as db:
        row = db.get(m.SendingMailbox, key)
        jobs = [
            m.Job(
                attempts=1,
                created_at=now - timedelta(hours=hours),
                status="completed",
                payload={"sender_id": key, "sender_version": row.config_version},
                result={},
            )
            for hours in (23, 20, 12, 9, 2, 1)
        ]
        assert nw.cadence(row, jobs) == 2
        assert nw.next_transmission_at(db, row, jobs) == now + timedelta(hours=22)


def test_reply_delay_is_not_bypassed_when_sender_has_no_history(client, monkeypatch):
    key = setup(client)[0]
    now = m.now()
    monkeypatch.setattr(m, "now", lambda: now)
    with SessionLocal() as db:
        row = db.get(m.SendingMailbox, key)
        reply = m.Job(created_at=now - timedelta(minutes=3))
        assert nw.next_transmission_at(db, row, [], reply) == now + timedelta(minutes=12)
