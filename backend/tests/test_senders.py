from datetime import timedelta

from sqlalchemy import select

from app import models as m
from app import senders
from app.db import SessionLocal
from app.security import encrypt_config


def sender_payload(address="team@example.org", username=None, reply_to=None):
    return {
        "config": {
            "host": "smtp.example.org",
            "port": 465,
            "security": "ssl",
            "username": username or address,
            "password": "fictional-smtp-password",
            "from_email": address,
            "from_name": "Recette",
            "reply_to": reply_to,
            "legal_footer": "Données fictives de recette, contact professionnel et opposition possible.",
            "privacy_url": "https://example.org/privacy",
            "dns_verified": True,
            "mailboxes": [
                {
                    "host": "imap.example.org",
                    "username": address,
                    "password": "fictional-imap-password",
                    "address": address,
                }
            ],
        }
    }


def healthy(db, address="second@example.org"):
    row = senders.configure(db, senders.SenderCreate.model_validate(sender_payload(address)))
    row.status, row.paused, row.warmup_status = "authenticated", False, "ready"
    row.ready_at, row.last_event_at = m.now(), m.now()
    row.readiness_note = "Contrôles fictifs documentés pour la recette"
    db.flush()
    for policy in db.scalars(select(m.EmailLimit)):
        policy.daily_limit = 1000
    return row


def test_mailbox_secrets_are_encrypted_and_aliases_do_not_multiply_capacity(client):
    result = client.post("/api/senders", json=sender_payload())
    assert result.status_code == 201, result.text
    row = result.json()
    assert row["paused"] and row["warmup_status"] == "warming"
    for response in [result, client.get("/api/senders"), client.get("/api/senders/" + row["id"])]:
        assert "fictional-smtp-password" not in response.text
        assert "fictional-imap-password" not in response.text
        assert "identity_hash" not in response.text
    with SessionLocal() as db:
        assert "fictional-smtp-password" not in db.get(m.SendingMailbox, row["id"]).encrypted_config
    duplicate = client.post("/api/senders", json=sender_payload("alias@example.org", "team@example.org"))
    assert duplicate.status_code == 409
    assert len(client.get("/api/senders").json()) == 1
    assert all(p["daily_limit"] == 0 for p in client.get("/api/email-limits").json())


def test_protected_business_address_cannot_be_sender_or_reply_to(client):
    assert client.post("/api/senders", json=sender_payload("owner@primary.example.net")).status_code == 422
    assert (
        client.post("/api/senders", json=sender_payload(reply_to="Owner@primary.example.net")).status_code
        == 422
    )
    assert client.get("/api/senders").json() == []


def test_preparation_and_reserve_require_real_configuration(client):
    row = client.post("/api/senders", json=sender_payload()).json()
    policy = {
        "paused": False,
        "warmup_status": "ready",
        "readiness_note": "",
        "daily_limit": 60,
        "total_daily_limit": 80,
        "external_daily_reserve": 20,
        "min_interval_seconds": 600,
    }
    assert client.patch("/api/senders/" + row["id"] + "/policy", json=policy).status_code == 422
    policy["readiness_note"] = "Préparation attestée dans la recette fictive"
    policy["total_daily_limit"] = 70
    assert client.patch("/api/senders/" + row["id"] + "/policy", json=policy).status_code == 422
    policy["total_daily_limit"] = 80
    result = client.patch("/api/senders/" + row["id"] + "/policy", json=policy)
    assert result.status_code == 200, result.text
    assert "Connexion SMTP/IMAP à vérifier" in result.json()["blocking_reasons"]


def test_assignment_distributes_contacts_and_preserves_followup_identity(client, flow):
    with SessionLocal() as db:
        first, second = healthy(db), healthy(db, "third@example.org")
        contact = db.get(m.Contact, flow["contact"]["id"])
        a = senders.assign(db, contact)
        other = m.Contact(
            account_id=contact.account_id, identity_key="second-person", email="other@example.com"
        )
        db.add(other)
        db.flush()
        b = senders.assign(db, other)
        assert a.id != b.id
        assert senders.assign(db, contact).id == a.id
        a.paused = True
        assert senders.assign(db, contact).id == a.id  # Never switch a conversation to a healthy alternative.
        assert {a.id, b.id} == {first.id, second.id}


def test_limits_aggregate_domains_providers_and_reserve_on_rolling_window(client, flow):
    with SessionLocal() as db:
        row = healthy(db)
        healthy(db, "third@example.org")
        for policy in db.scalars(select(m.EmailLimit)):
            policy.daily_limit = 40  # Two reserves of 20 already consume the entire allowance.
        reasons = senders.limits_reasons(db, row)
        assert any("domaine" in r for r in reasons) and any("fournisseur" in r for r in reasons)
        for policy in db.scalars(select(m.EmailLimit)):
            policy.daily_limit = 41
        message = m.Message(
            contact_id=flow["contact"]["id"],
            campaign_id=flow["campaign"]["id"],
            subject="Recette",
            body="Fictif",
            dedupe_key="past-attempt",
            sender_id=row.id,
            sender_domain=row.domain,
            sender_provider=row.provider_group,
            status="uncertain",
            attempted_at=m.now() - timedelta(hours=23),
            updated_at=m.now() - timedelta(days=5),
        )
        db.add(message)
        db.flush()
        assert senders.count(db) == 1
        assert any("domaine" in r for r in senders.limits_reasons(db, row))
        message.attempted_at = m.now() - timedelta(hours=25)
        message.updated_at = m.now()
        db.flush()
        assert senders.count(db) == 0  # Later edits do not extend the 24 h accounting window.


def test_ready_pool_cannot_burst_or_send_from_an_unprepared_box(client, flow, monkeypatch):
    from tests.test_mail import live_fixture

    worker = live_fixture(client, flow, monkeypatch)
    calls = []
    monkeypatch.setattr(worker.mail, "send", lambda *args: calls.append(1) or {"accepted": True})
    with SessionLocal() as db:
        sender = db.scalar(select(m.SendingMailbox))
        sender.warmup_status = "warming"
        db.commit()
    assert worker.tick() == 0
    assert not calls
    with SessionLocal() as db:
        sender = db.scalar(select(m.SendingMailbox))
        sender.warmup_status = "ready"
        db.scalar(select(m.Job).where(m.Job.kind == "send")).run_at = m.now()
        db.commit()
    assert worker.tick() == 1
    with SessionLocal() as db:
        sender = db.scalar(select(m.SendingMailbox))
        assert "Intervalle entre deux emails à respecter" in senders.limits_reasons(db, sender)
    assert calls == [1]


def test_blocked_mailbox_does_not_block_another_and_sent_affinity_cannot_change(client, flow, monkeypatch):
    from tests.test_mail import live_fixture

    worker = live_fixture(client, flow, monkeypatch)
    calls = []
    monkeypatch.setattr(
        worker.mail,
        "send",
        lambda message, contact, config: calls.append(config["from_email"]) or {"accepted": True},
    )
    with SessionLocal() as db:
        first = db.scalar(select(m.SendingMailbox))
        first.paused = True
        second = healthy(db)
        other_account = m.Account(name="Autre société fictive", domain="other.example.com")
        db.add(other_account)
        db.flush()
        other = m.Contact(
            account_id=other_account.id,
            campaign_id=flow["campaign"]["id"],
            identity_key="other",
            first_name="Autre",
            email="other@example.com",
            email_status="valid",
            email_verified_at=m.now(),
            approved=True,
            role="Direction",
            professional_relevance="Pertinence fictive",
            sourcing_note="Source fictive",
            information_status="planned",
            email_sender_id=second.id,
        )
        db.add(other)
        db.flush()
        message = m.Message(
            contact_id=other.id,
            campaign_id=other.campaign_id,
            subject="Fictif",
            body="Fictif",
            dedupe_key="second-contact",
            status="queued",
            sender_id=second.id,
            sender_version=second.config_version,
        )
        db.add(message)
        db.flush()
        db.add(
            m.Job(
                kind="send",
                provider="smtp",
                contact_id=other.id,
                campaign_id=other.campaign_id,
                message_id=message.id,
                idempotency_key="second-send",
                run_at=m.now(),
            )
        )
        other_id, first_id = other.id, first.id
        db.commit()
    assert worker.tick() == 0
    assert worker.tick() == 1
    assert calls == ["second@example.org"]
    assert (
        client.post("/api/contacts/" + other_id + "/sender", json={"sender_id": first_id}).status_code == 409
    )


def test_connection_change_between_intent_and_network_prevents_sending(client, flow, monkeypatch):
    from tests.test_mail import live_fixture

    worker = live_fixture(client, flow, monkeypatch)
    calls, opens = [], 0
    monkeypatch.setattr(worker.mail, "send", lambda *args: calls.append(1))

    def session():
        nonlocal opens
        opens += 1
        if opens == 2:
            with SessionLocal() as db:
                db.scalar(select(m.SendingMailbox)).config_version += 1
                db.commit()
        return SessionLocal()

    monkeypatch.setattr(worker, "SessionLocal", session)
    assert worker.tick() == 1
    assert not calls
    assert client.get("/api/messages").json()[0]["status"] == "cancelled"
    with SessionLocal() as db:
        assert senders.count(db) == 0


def test_failed_collector_rotates_to_other_accounts_and_keeps_global_reply_stop(client, flow, monkeypatch):
    from tests.test_mail import live_fixture

    worker = live_fixture(client, flow, monkeypatch)
    with SessionLocal() as db:
        healthy(db)
        db.commit()
    calls = []

    def collect(db, row, config):
        calls.append(row.id)
        if len(calls) == 1:
            raise RuntimeError("Fictitious unavailable mailbox")
        row.last_event_at = m.now()

    monkeypatch.setattr(worker.mail, "collect", collect)
    worker.collection_tick()
    worker.collection_tick()
    assert len(set(calls)) == 2
    with SessionLocal() as db:
        assert db.get(m.SendingMailbox, calls[0]).last_event_at is None
        assert db.get(m.SendingMailbox, calls[1]).last_event_at is not None


def test_legacy_migration_is_idempotent_paused_and_protected_reply_to_is_excluded(client, flow):
    with SessionLocal() as db:
        legacy = m.Integration(
            provider="smtp",
            status="authenticated",
            encrypted_config=encrypt_config(sender_payload()["config"]),
        )
        db.add(legacy)
        db.flush()
        senders.migrate_legacy(db)
        senders.migrate_legacy(db)
        rows = list(db.scalars(select(m.SendingMailbox)))
        assert len(rows) == 1 and rows[0].paused and rows[0].warmup_status == "not_started"
        assert rows[0].status == "configured"
    # Rollback the fixture additions, then check that the old Reply-To cannot enter the pool.
    with SessionLocal() as db:
        db.add(
            m.Integration(
                provider="smtp",
                encrypted_config=encrypt_config(
                    sender_payload(reply_to="owner@primary.example.net")["config"]
                ),
            )
        )
        db.flush()
        senders.migrate_legacy(db)
        assert not db.scalar(select(m.SendingMailbox))
