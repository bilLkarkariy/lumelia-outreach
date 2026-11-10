from sqlalchemy import select

from app import models as m
from app import senders
from app.db import SessionLocal
from app.providers import ProviderError
from app.security import decrypt_config
from tests.test_senders import healthy


def setup_source():
    with SessionLocal() as db:
        row = healthy(db, "source@example.org")
        row.state = {"delivery_checks": {"gmail": {"outcome": "inbox"}}, "native_warmup": {"enabled": True}}
        db.commit()
        return row.id


def request(address="other@example.net", **extra):
    return {"address": address, "from_name": "Boîte existante", "confirm_owned_shared_access": True, **extra}


def test_connect_existing_keeps_secrets_and_pauses_cold_with_independent_checks(client, monkeypatch):
    source = setup_source()
    calls = []
    monkeypatch.setattr(senders.mail, "verify", lambda cfg: calls.append(cfg) or {"authenticated": True})
    before = client.get("/api/email-limits").json()
    response = client.post(f"/api/senders/{source}/existing-mailbox", json=request())
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["paused"] and row["status"] == "authenticated"
    assert row["last_event_at"] is None and row["ready_at"] is None
    assert "delivery_checks" not in row["state"] and "native_warmup" not in row["state"]
    assert row["domain"] == "example.net" and row["provider_group"] == "smtp.example.org"
    assert calls[0]["host"] == "smtp.example.org"
    assert calls[0]["username"] == calls[0]["mailboxes"][0]["username"] == "other@example.net"
    assert calls[0]["mailboxes"][0]["host"] == "imap.example.org"
    for r in [response, client.get("/api/senders/" + row["id"])]:
        assert "fictional-smtp-password" not in r.text and "fictional-imap-password" not in r.text
    with SessionLocal() as db:
        added = db.get(m.SendingMailbox, row["id"])
        config = decrypt_config(added.encrypted_config)
        assert config["password"] == "fictional-smtp-password"
        assert config["mailboxes"][0]["password"] == "fictional-imap-password"
        assert config["dns_verified"] is False  # A different domain cannot inherit DNS approval.
    after = client.get("/api/email-limits").json()
    assert all(
        next(a for a in after if a["id"] == b["id"])["daily_limit"] == b["daily_limit"] for b in before
    )
    assert next(a for a in after if a["key"] == "example.net")["daily_limit"] == 0
    assert client.post(f"/api/senders/{source}/existing-mailbox", json=request()).status_code == 409
    assert len(calls) == 1


def test_failed_auth_does_not_create_or_leak_credentials(client, monkeypatch):
    source = setup_source()

    def fail(_):
        raise ProviderError("smtp", "fictional-smtp-password")

    monkeypatch.setattr(senders.mail, "verify", fail)
    result = client.post(f"/api/senders/{source}/existing-mailbox", json=request())
    assert result.status_code == 422 and "fictional-smtp-password" not in result.text
    assert len(client.get("/api/senders").json()) == 1
    assert all(r["key"] != "example.net" for r in client.get("/api/email-limits").json())


def test_existing_requires_admin_owned_confirmation_and_excludes_protected(client, monkeypatch):
    source = setup_source()
    calls = []
    monkeypatch.setattr(senders.mail, "verify", lambda cfg: calls.append(cfg) or {"authenticated": True})
    path = f"/api/senders/{source}/existing-mailbox"
    assert client.post(path, json=request(confirm_owned_shared_access=False)).status_code == 422
    assert client.post(path, json=request("hello@primary.example.net")).status_code == 422
    assert client.post(path, json=request(host="attacker.example.org")).status_code == 422
    with SessionLocal() as db:
        user = db.scalar(select(m.User))
        user.role = "operator"
        db.commit()
    assert client.post(path, json=request()).status_code == 403
    assert not calls
