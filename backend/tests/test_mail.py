from datetime import timedelta
from email.message import EmailMessage

from sqlalchemy import select

from app import mail
from app import models as m
from app.db import SessionLocal
from app.worker import tick

from .test_workflows import prepare


def test_unsubscribe_get_is_read_only_and_post_idempotent(client, flow):
    token = mail.unsubscribe_token(flow["contact"]["id"])
    assert client.get("/api/unsubscribe/" + token).status_code == 200
    assert client.get("/api/suppressions").json() == []
    assert client.post("/api/unsubscribe/" + token).status_code == 200
    assert client.post("/api/unsubscribe/" + token).status_code == 200
    assert len(client.get("/api/suppressions").json()) == 2
    assert client.post("/api/unsubscribe/invalid").status_code == 404


def test_reply_parser_correlates_message_id_and_stops_other_channels(client, flow):
    message = prepare(client, flow)
    raw = EmailMessage()
    raw["From"] = "alice@example.com"
    raw["To"] = "team@example.org"
    raw["Subject"] = "Re: devis"
    raw["In-Reply-To"] = "<test-id@example.org>"
    raw.set_content("Oui, échangeons.")
    with SessionLocal() as db:
        db.get(m.Message, message["id"]).provider_id = "<test-id@example.org>"
        db.flush()
        assert mail.parse_reply(db, raw.as_bytes(), "imap:test:1")
        db.commit()
    assert client.get("/api/messages").json()[0]["status"] == "cancelled"
    assert client.get("/api/tasks?channel=phone").json()[0]["status"] == "cancelled"


def test_interrupted_send_becomes_uncertain_never_requeued(client, flow):
    prepare(client, flow)
    client.patch("/api/state", json={"paused": False})
    with SessionLocal() as db:
        job = db.query(m.Job).first()
        job.status = "running"
        job.locked_at = m.now() - timedelta(minutes=4)
        db.get(m.Message, job.message_id).status = "sending"
        db.commit()
    assert tick() == 0
    assert client.get("/api/jobs").json()[0]["status"] == "uncertain"
    assert client.get("/api/messages").json()[0]["status"] == "uncertain"
    assert tick() == 0


def test_smtp_transport_headers_and_no_false_one_click_claim(client, flow, monkeypatch):
    from app import mail

    captured = []

    class SMTP:
        def send_message(self, message, **kwargs):
            captured.append(message)
            return {}

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(mail, "connect_smtp", lambda config: SMTP())
    with SessionLocal() as db:
        contact = db.get(m.Contact, flow["contact"]["id"])
        message = m.Message(subject="Objet relu", body="Bonjour Alice", provider_id="<stable-id@example.org>")
        config = {
            "from_name": "Lumelia",
            "from_email": "team@example.org",
            "reply_to": "reply@example.org",
            "legal_footer": "Lumelia — contact professionnel relatif au suivi des devis",
            "privacy_url": "https://example.org/privacy",
        }
        assert mail.send(message, contact, config)["accepted"]
    assert captured[0]["Message-ID"] == "<stable-id@example.org>"
    assert captured[0]["Reply-To"] == "reply@example.org"
    assert captured[0]["List-Unsubscribe-Post"] is None
    assert "Ne plus être contacté" in captured[0].get_content()


def test_dsn_without_references_uses_final_recipient(client, flow):
    message = prepare(client, flow)
    with SessionLocal() as db:
        db.get(m.Message, message["id"]).status = "sent"
        db.commit()
    raw = b"""From: postmaster@example.org\r
Subject: Delivery failed\r
MIME-Version: 1.0\r
Content-Type: multipart/report; report-type=delivery-status; boundary="dsn"\r
\r
--dsn\r
Content-Type: text/plain\r
\r
Delivery failed.\r
--dsn\r
Content-Type: message/delivery-status\r
\r
Reporting-MTA: dns; example.org\r
\r
Final-Recipient: rfc822; alice@example.com\r
Action: failed\r
Status: 5.1.1\r
\r
--dsn--\r
"""
    with SessionLocal() as db:
        assert mail.parse_reply(db, raw, "dsn:1")
        db.commit()
    assert client.get("/api/contacts/" + flow["contact"]["id"]).json()["email_status"] == "invalid"


def live_fixture(client, flow, monkeypatch):
    from app import worker
    from app.config import settings

    prepare(client, flow)
    monkeypatch.setattr(settings(), "live_actions_enabled", True)
    monkeypatch.setattr(settings(), "public_url", "https://example.org")
    monkeypatch.setattr(worker.d, "in_sending_window", lambda c: True)
    client.patch("/api/state", json={"paused": False})
    with SessionLocal() as db:
        campaign = db.get(m.Campaign, flow["campaign"]["id"])
        campaign.mode = "live"
        campaign.budget_approved = True
        from app import senders
        from tests.test_senders import sender_payload

        sender = senders.configure(db, senders.SenderCreate.model_validate(sender_payload()))
        sender.status, sender.paused, sender.warmup_status = "authenticated", False, "ready"
        sender.ready_at, sender.last_event_at = m.now(), m.now()
        sender.readiness_note = "Contrôles fictifs de recette validés"
        for policy in db.scalars(select(m.EmailLimit)):
            policy.daily_limit = 1000
        db.get(m.Contact, flow["contact"]["id"]).email_sender_id = sender.id
        for message in db.scalars(select(m.Message)):
            message.sender_id, message.sender_version = sender.id, sender.config_version
        db.commit()
    return worker


def test_pause_between_persisted_intent_and_smtp_prevents_transmission(client, flow, monkeypatch):
    worker = live_fixture(client, flow, monkeypatch)
    sent = []
    monkeypatch.setattr(worker.mail, "send", lambda *args: sent.append(args))
    calls = 0

    def session():
        nonlocal calls
        calls += 1
        if calls == 2:
            with SessionLocal() as db:
                db.get(m.AppState, "global").paused = True
                db.commit()
        return SessionLocal()

    monkeypatch.setattr(worker, "SessionLocal", session)
    assert worker.tick() == 1
    assert not sent
    assert client.get("/api/messages").json()[0]["status"] == "cancelled"


def test_smtp_timeout_after_intent_never_retries(client, flow, monkeypatch):
    worker = live_fixture(client, flow, monkeypatch)
    calls = []

    def timeout(*args):
        from app.providers import ProviderError

        calls.append(1)
        raise ProviderError("smtp", "Unknown acceptance", uncertain=True)

    monkeypatch.setattr(worker.mail, "send", timeout)
    assert worker.tick() == 1
    assert worker.tick() == 0
    assert calls == [1]
    assert client.get("/api/jobs").json()[0]["status"] == "uncertain"
