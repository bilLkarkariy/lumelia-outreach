from datetime import datetime, timedelta

from sqlalchemy import select

from app import domain as d
from app import models as m
from app import privacy
from app.db import SessionLocal


def test_recipient_notice_is_public_and_contains_no_workspace_access(client):
    client.cookies.clear()
    response = client.get("/privacy")
    assert response.status_code == 200
    assert "privacy@example.com" in response.text
    assert "désinscription" in response.text
    assert "test-password" not in response.text and "csrf" not in response.text
    assert client.get("/api/contacts").status_code == 401


def test_expiry_preserves_uncertain_operations_then_erases_and_prevents_reimport(client, flow):
    key = flow["contact"]["id"]
    with SessionLocal() as db:
        campaign = db.get(m.Campaign, flow["campaign"]["id"])
        campaign.config = {"automatic": True}
        contact = db.get(m.Contact, key)
        contact.retention_until = m.now() - timedelta(days=1)
        db.add(
            m.Job(
                kind="autopilot",
                contact_id=key,
                campaign_id=campaign.id,
                idempotency_key="expiry-uncertain",
                status="uncertain",
                payload={"email": contact.email},
            )
        )
        db.commit()
    privacy.tick()
    with SessionLocal() as db:
        assert db.get(m.Contact, key) is not None
        job = db.scalar(select(m.Job).where(m.Job.idempotency_key == "expiry-uncertain"))
        job.status = "completed"
        db.commit()
    privacy.tick()
    with SessionLocal() as db:
        assert db.get(m.Contact, key) is None
        job = db.scalar(select(m.Job).where(m.Job.idempotency_key == "expiry-uncertain"))
        assert job.payload == {} and job.result == {}
        assert db.scalar(
            select(m.Suppression.id).where(
                m.Suppression.kind == "email", m.Suppression.value == "alice@example.com"
            )
        )
    recreated = client.post("/api/contacts", json=flow["payload"])
    assert recreated.status_code == 201
    assert (
        client.post("/api/batches/approve", json={"contact_ids": [recreated.json()["id"]]}).status_code == 409
    )


def test_only_real_incoming_contact_extends_automatic_retention(client, flow):
    with SessionLocal() as db:
        campaign = db.get(m.Campaign, flow["campaign"]["id"])
        campaign.config = {"automatic": True}
        contact = db.get(m.Contact, flow["contact"]["id"])
        original = contact.retention_until = m.now() + timedelta(days=10)
        d.receive_event(db, contact, "out_of_office", "email", "Absence automatique")
        assert contact.retention_until == original
        d.receive_event(db, contact, "reply", "email", "Réponse réelle de recette")
        assert contact.retention_until > m.now() + timedelta(days=364)
        db.commit()


def test_manual_and_csv_contacts_expire_without_reimport_extending_retention(client, flow, monkeypatch):
    manual = flow["contact"]
    assert datetime.fromisoformat(manual["retention_until"]) > m.now() + timedelta(days=364)
    csv = "company,domain,first_name,email\nRecette,recette.example.org,Sam,sam@example.org\n"
    imported = client.post("/api/import/csv", content=csv).json()
    assert imported["created"] == 1
    csv_id = imported["contact_ids"][0]
    original = client.get(f"/api/contacts/{csv_id}").json()["retention_until"]
    assert datetime.fromisoformat(original) > m.now() + timedelta(days=364)

    later = m.now() + timedelta(days=20)
    monkeypatch.setattr(m, "now", lambda: later)
    repeated_manual = client.post("/api/contacts", json=flow["payload"]).json()
    repeated_csv = client.post("/api/import/csv", content=csv).json()
    assert repeated_manual["new"] is False
    assert datetime.fromisoformat(repeated_manual["retention_until"]) == datetime.fromisoformat(
        manual["retention_until"]
    )
    assert repeated_csv["duplicates"] == 1
    assert client.get(f"/api/contacts/{csv_id}").json()["retention_until"] == original
