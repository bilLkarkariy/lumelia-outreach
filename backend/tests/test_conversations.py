from datetime import timedelta
from email.message import EmailMessage
from uuid import uuid4

from sqlalchemy import func, select

from app import conversations, mail
from app import models as m
from app.config import settings
from app.db import SessionLocal
from app.providers import ProviderError

from .test_senders import healthy


def conversation_fixture(client, flow):
    with SessionLocal() as db:
        box = healthy(db, "reply@example.org")
        contact = db.get(m.Contact, flow["contact"]["id"])
        contact.email_sender_id = box.id
        db.add(
            m.Message(
                contact_id=contact.id,
                campaign_id=flow["campaign"]["id"],
                subject="Les devis",
                body="Question fictive",
                dedupe_key="conversation-seed",
                status="sent",
                sent_at=m.now() - timedelta(days=2),
                attempted_at=m.now() - timedelta(days=2),
                sender_id=box.id,
                sender_email=box.from_email,
                provider_id="<original@example.org>",
            )
        )
        db.commit()
        sender_id = box.id
    raw = EmailMessage()
    raw["From"] = flow["contact"]["email"]
    raw["To"] = "reply@example.org"
    raw["Subject"] = "Re: Les devis"
    raw["In-Reply-To"] = "<original@example.org>"
    raw["Message-ID"] = "<incoming@example.net>"
    raw.set_content("Bonjour, parlons du suivi de nos propositions.")
    with SessionLocal() as db:
        assert mail.parse_reply(db, raw.as_bytes(), "imap:test:1", sender_id)
        db.commit()
    with SessionLocal() as db:
        return db.scalar(select(m.Event).where(m.Event.kind == "reply")).id, raw, sender_id


def save_draft(client, event_id, **extra):
    context = client.get("/api/inbox/" + event_id + "/conversation").json()
    payload = {
        "subject": "Re: Les devis",
        "body": "Bonjour, je peux vous proposer un échange jeudi.",
        "request_id": str(uuid4()),
        "expected_event_updated_at": context["latest_updated_at"],
        **extra,
    }
    response = client.post("/api/inbox/" + event_id + "/reply-draft", json=payload)
    assert response.status_code == 200, response.text
    return response.json(), payload


def test_full_html_content_reference_priority_grouping_and_dedup(client, flow):
    event_id, raw, box = conversation_fixture(client, flow)
    with SessionLocal() as db:
        assert mail.parse_reply(db, raw.as_bytes(), "imap:test:other-folder", box)
        assert db.scalar(select(func.count()).select_from(m.Event).where(m.Event.kind == "reply")) == 1
        db.add(
            m.Message(
                contact_id=flow["contact"]["id"],
                campaign_id=flow["campaign"]["id"],
                step=1,
                subject="Plus récent",
                body="Texte",
                status="sent",
                dedupe_key="newer",
                provider_id="<newer@example.org>",
                sender_id=box,
            )
        )
        db.commit()
    html = EmailMessage()
    html["From"] = flow["contact"]["email"]
    html["In-Reply-To"] = "<original@example.org>"
    html["References"] = "<original@example.org> <newer@example.org>"
    html["Message-ID"] = "<html@example.net>"
    html.set_content("<p>" + ("Texte complet " * 800) + "</p><script>secret script</script>", subtype="html")
    with SessionLocal() as db:
        assert mail.parse_reply(db, html.as_bytes(), "imap:test:html", box)
        db.commit()
        event = db.scalar(
            select(m.Event).where(m.Event.data["email"]["message_id"].as_string() == "<html@example.net>")
        )
        assert len(event.data["email"]["body"]) > 5500 and "secret script" not in event.data["email"]["body"]
        assert db.get(m.Message, event.data["message_id"]).step == 0
    listing = client.get("/api/conversations").json()
    assert listing["total"] == 1 and listing["items"][0]["reply_count"] == 2
    assert client.get("/api/inbox/" + event_id + "/conversation").json()["total"] == 4
    raw.replace_header("From", "unrelated@example.org")
    raw.replace_header("Message-ID", "<wrong@example.net>")
    with SessionLocal() as db:
        assert not mail.parse_reply(db, raw.as_bytes(), "wrong", box)


def test_draft_persists_during_pause_and_rejects_sequence_approval(client, flow):
    event_id, raw, box = conversation_fixture(client, flow)
    draft, payload = save_draft(client, event_id)
    assert client.post("/api/inbox/" + event_id + "/reply-draft", json=payload).json()["id"] == draft["id"]
    assert client.get("/api/inbox/" + event_id + "/conversation").json()["draft"]["body"] == draft["body"]
    assert client.post("/api/messages/" + draft["id"] + "/approve").status_code == 409
    queue = client.post(
        "/api/reply-messages/" + draft["id"] + "/send",
        json={"expected_updated_at": draft["updated_at"], "request_id": str(uuid4())},
    )
    assert queue.status_code == 409 and "pause" in queue.text
    assert conversations.reply_tick() == 0


def queue_live(client, flow, monkeypatch):
    event_id, raw, box = conversation_fixture(client, flow)
    draft, payload = save_draft(client, event_id)
    monkeypatch.setattr(settings(), "live_actions_enabled", True)
    monkeypatch.setattr(settings(), "public_url", "https://example.org")
    client.patch("/api/state", json={"paused": False})
    payload = {"expected_updated_at": draft["updated_at"], "request_id": str(uuid4())}
    result = client.post("/api/reply-messages/" + draft["id"] + "/send", json=payload)
    assert result.status_code == 200, result.text
    assert (
        client.post("/api/reply-messages/" + draft["id"] + "/send", json=payload).json()["id"]
        == result.json()["id"]
    )
    return event_id, draft, box


def test_reply_headers_exact_content_and_preserved_pause(client, flow, monkeypatch):
    event_id, draft, box = queue_live(client, flow, monkeypatch)
    captured = []

    class SMTP:
        def send_message(self, email, **kwargs):
            captured.append((email, kwargs))
            return {}

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(mail, "connect_smtp", lambda config: SMTP())
    assert conversations.reply_tick() == 1 and conversations.reply_tick() == 0
    assert captured[0][0]["In-Reply-To"] == "<incoming@example.net>"
    assert "<original@example.org>" in captured[0][0]["References"]
    assert captured[0][1]["to_addrs"] == [flow["contact"]["email"]]
    contact = client.get("/api/contacts/" + flow["contact"]["id"]).json()
    assert contact["paused"] and contact["account"]["outreach_paused"]
    outgoing = next(
        item
        for item in client.get("/api/inbox/" + event_id + "/conversation").json()["items"]
        if item["id"] == draft["id"]
    )
    assert outgoing["status"] == "sent" and outgoing["body"].strip() == captured[0][0].get_content().strip()


def test_reply_uncertain_never_retries_and_is_alerted(client, flow, monkeypatch):
    event_id, draft, box = queue_live(client, flow, monkeypatch)
    calls = []

    def timeout(*args):
        calls.append(1)
        raise ProviderError("smtp", "Transmission incertaine", uncertain=True)

    monkeypatch.setattr(mail, "send", timeout)
    assert conversations.reply_tick() == 1 and conversations.reply_tick() == 0 and calls == [1]
    assert any(row["title"] == "Envoi à rapprocher" for row in client.get("/api/alerts").json()["items"])


def test_pause_after_intent_and_suppression_block_reply(client, flow, monkeypatch):
    event_id, draft, box = queue_live(client, flow, monkeypatch)
    calls = []
    monkeypatch.setattr(mail, "send", lambda *args: calls.append(1))
    opened = 0

    def session():
        nonlocal opened
        opened += 1
        if opened == 2:
            with SessionLocal() as db:
                db.get(m.AppState, "global").paused = True
                db.commit()
        return SessionLocal()

    monkeypatch.setattr(conversations, "SessionLocal", session)
    assert conversations.reply_tick() == 1 and not calls
    client.post(
        "/api/contacts/" + flow["contact"]["id"] + "/events",
        json={"kind": "stop", "channel": "email", "summary": "Opposition fictive documentée."},
    )
    assert client.get("/api/inbox/" + event_id + "/conversation").json()["draft_reasons"]


def test_draft_stale_and_header_injection_rejected(client, flow):
    event_id, raw, box = conversation_fixture(client, flow)
    draft, payload = save_draft(client, event_id)
    update = {
        **payload,
        "draft_id": draft["id"],
        "expected_updated_at": draft["updated_at"],
        "request_id": str(uuid4()),
        "subject": "Re: suite\nBcc: evil@example.org",
    }
    assert client.post("/api/inbox/" + event_id + "/reply-draft", json=update).status_code == 422
    update["subject"] = "Re: suite"
    assert client.post("/api/inbox/" + event_id + "/reply-draft", json=update).status_code == 200
    update["request_id"] = str(uuid4())
    assert client.post("/api/inbox/" + event_id + "/reply-draft", json=update).status_code == 409
    assert client.get("/api/conversations?q=Alice").json()["total"] == 1
    assert client.get("/api/conversations?status=done").json()["total"] == 0


def test_reconciled_reply_edits_and_paginated_history_keep_latest(client, flow, monkeypatch):
    event_id, draft, box = queue_live(client, flow, monkeypatch)
    assert client.get("/api/inbox/" + event_id + "/conversation").json()["draft"]["status"] == "queued"

    def fail(*args):
        raise ProviderError("smtp", "Connexion refusée avant transmission", uncertain=False)

    monkeypatch.setattr(mail, "send", fail)
    assert conversations.reply_tick() == 1
    job = next(row for row in client.get("/api/jobs").json() if row["message_id"] == draft["id"])
    assert (
        client.post(
            "/api/jobs/" + job["id"] + "/reconcile",
            json={"outcome": "not_sent", "evidence": "Fournisseur contrôlé, aucun envoi accepté."},
        ).status_code
        == 200
    )
    current = client.get("/api/inbox/" + event_id + "/conversation").json()["draft"]
    changed, _ = save_draft(
        client,
        event_id,
        draft_id=current["id"],
        expected_updated_at=current["updated_at"],
        body="Nouveau contenu après correction.",
    )
    assert changed["sent_body"] == "" and changed["failure_reason"] == ""
    with SessionLocal() as db:
        for index in range(12):
            db.add(
                m.Message(
                    contact_id=flow["contact"]["id"],
                    campaign_id=flow["campaign"]["id"],
                    dedupe_key="history:" + str(index),
                    subject="Historique",
                    body="Texte",
                    status="sent",
                    sent_at=m.now() - timedelta(days=10 + index),
                )
            )
        db.add(
            m.Message(
                contact_id=flow["contact"]["id"],
                campaign_id=flow["campaign"]["id"],
                dedupe_key="future-not-conversation",
                subject="Brouillon futur",
                body="Planifié",
            )
        )
        db.commit()
    history = client.get("/api/inbox/" + event_id + "/conversation?page=2").json()
    assert history["total"] == 15 and history["page_size"] == 10 and len(history["items"]) == 5
    assert history["latest_message"]["id"] == event_id
    assert history["draft"]["body"] == "Nouveau contenu après correction."
