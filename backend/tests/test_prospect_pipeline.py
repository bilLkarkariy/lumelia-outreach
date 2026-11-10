from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app import agent_actions as agent
from app import models as m
from app import prospect_pipeline as p
from app.db import SessionLocal


def test_every_company_is_visible_without_fabricating_contacts(client, flow):
    a = client.post(
        "/api/accounts", json={"name": "Sans interlocuteur", "domain": "orphan.example.com"}
    ).json()
    excluded = client.post(
        "/api/accounts", json={"name": "Excluded", "domain": "excluded.example.com", "lifecycle": "excluded"}
    ).json()
    configured = client.post("/api/prospect-pipeline/config", json={"campaign_id": flow["campaign"]["id"]})
    assert configured.status_code == 200
    rows = client.get("/api/lists/prospects").json()
    assert rows["total"] == 3
    assert next(x for x in rows["items"] if x["id"] == a["id"])["contact"] is None
    assert next(x for x in rows["items"] if x["id"] == excluded["id"])["can_call"] is False
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Contact)) == 1


def test_commercial_status_is_derived_from_messages_and_reply_events(client, flow):
    untouched = client.post(
        "/api/accounts", json={"name": "Jamais contactée", "domain": "never.example.com"}
    ).json()
    with SessionLocal() as db:
        contact = db.get(m.Contact, flow["contact"]["id"])
        db.add(
            m.Message(
                contact_id=contact.id,
                campaign_id=flow["campaign"]["id"],
                subject="Brouillon",
                body="Bonjour",
                status="draft",
                dedupe_key="commercial-status-draft",
            )
        )
        db.commit()
    rows = {row["id"]: row for row in client.get("/api/lists/prospects").json()["items"]}
    assert rows[flow["account"]["id"]]["commercial_status"] == "email_pending"
    assert rows[flow["account"]["id"]]["commercial_status_label"] == "Email préparé / en attente"
    assert rows[untouched["id"]]["commercial_status"] == "never_contacted"

    with SessionLocal() as db:
        contact = db.get(m.Contact, flow["contact"]["id"])
        db.add(
            m.Event(
                contact_id=contact.id,
                account_id=contact.account_id,
                campaign_id=flow["campaign"]["id"],
                kind="positive_reply",
                channel="email",
                summary="Intéressé",
                created_at=m.now() + timedelta(seconds=1),
            )
        )
        db.commit()
    row = next(
        row
        for row in client.get("/api/lists/prospects").json()["items"]
        if row["id"] == flow["account"]["id"]
    )
    assert row["commercial_status"] == "interested"
    assert row["commercial_status_label"] == "Intérêt manifesté"


def test_public_registry_import_is_idempotent_and_preserves_existing_phone(client, flow):
    client.post("/api/prospect-pipeline/config", json={"campaign_id": flow["campaign"]["id"]})
    public = {
        "pages": [
            {"url": "https://test.example.com", "text": "Services pour les entreprises. SIREN 552100554."}
        ],
        "sirens": {"552100554": "https://test.example.com/legal"},
        "emails": {"contact@test.example.com": "https://test.example.com"},
        "errors": [],
    }
    registry = {
        "siren": "552100554",
        "active": True,
        "size_code": "12",
        "size_year": m.now().year - 1,
        "source_url": "https://annuaire-entreprises.data.gouv.fr/entreprise/552100554",
        "directors": [
            {"first_name": "Paul", "last_name": "Exemple", "role": "Président"},
            {"first_name": "Sans", "last_name": "Fonction", "role": None},
            {"first_name": None, "last_name": "Incomplet", "role": "Gérant"},
        ],
    }
    with SessionLocal() as db:
        p.apply_public(db, flow["account"]["id"], public, registry)
        db.commit()
        p.apply_public(db, flow["account"]["id"], public, registry)
        db.commit()
        assert db.scalar(select(func.count()).select_from(m.Contact)) == 2
        assert db.get(m.Contact, flow["contact"]["id"]).phone == flow["contact"]["phone"]
        new = db.scalar(select(m.Contact).where(m.Contact.first_name == "Paul"))
        assert new.phone == "" and new.email is None
        profile = db.get(m.ProspectProfile, flow["account"]["id"])
        assert profile.status == "ready"
        assert profile.facts["checks"][-1]["status"] == "unknown"
        assert db.get(m.Account, flow["account"]["id"]).employee_count is None
        assert db.scalar(select(m.Evidence).where(m.Evidence.field == "company_email")).contact_id is None


def test_batch_rejects_other_company_and_unproven_exclusion(client, flow):
    client.post("/api/prospect-pipeline/config", json={"campaign_id": flow["campaign"]["id"]})
    review = agent.AccountReview(account_id="other-account", summary="Aucune information disponible")
    with SessionLocal() as db:
        action = SimpleNamespace(payload={"account_ids": [flow["account"]["id"]]})
        with pytest.raises(HTTPException):
            p.apply_agent_batch(db, action, [review])
        review.account_id = flow["account"]["id"]
        review.decision = "not_fit"
        with pytest.raises(HTTPException):
            p.apply_agent_batch(db, action, [review])


def test_apify_accounts_keep_separate_credit_ledgers(client, flow):
    from app import acquisition as a
    from app.security import encrypt_config

    with SessionLocal() as db:
        db.add(
            m.Integration(
                provider="apify",
                encrypted_config=encrypt_config({"api_key": "fake"}),
                state={"account_bucket": "old"},
                status="authenticated",
            )
        )
        db.add(
            m.Job(
                kind=a.KIND,
                provider="apify",
                idempotency_key="old",
                status="completed",
                payload={"account_bucket": "old"},
                result={"charged_usd": 4.9926},
            )
        )
        db.commit()
        assert a.spent(db) == 500
        integration = db.scalar(select(m.Integration).where(m.Integration.provider == "apify"))
        integration.state = {"account_bucket": "new"}
        db.commit()
        assert a.spent(db) == 0
        db.add(
            m.Job(
                kind=a.KIND,
                provider="apify",
                idempotency_key="new",
                status="queued",
                payload={"account_bucket": "new", "max_charge_usd_cents": 500},
            )
        )
        db.commit()
        assert a.spent(db) == 500


def test_discovery_review_does_not_skip_immediate_qualification(client, flow):
    with SessionLocal() as db:
        contact = db.get(m.Contact, flow["contact"]["id"])
        contact.qualification = {
            "agent_review": {
                "decision": "exploratory",
                "reviewed_at": m.now().isoformat(),
                "review_phase": "discovery",
            }
        }
        db.commit()
        agent.synchronize(db)
        db.commit()
        row = db.scalar(
            select(m.AgentAction).where(
                m.AgentAction.contact_id == contact.id, m.AgentAction.kind == "qualify_contact"
            )
        )
        assert row and row.status == "queued"
        agent.synchronize(db)
        db.commit()
        assert (
            db.scalar(
                select(func.count())
                .select_from(m.AgentAction)
                .where(m.AgentAction.contact_id == contact.id, m.AgentAction.kind == "qualify_contact")
            )
            == 1
        )


def test_unresolved_account_enters_batch_and_resolves_official_domain(client, flow):
    client.post("/api/prospect-pipeline/config", json={"campaign_id": flow["campaign"]["id"]})
    with SessionLocal() as db:
        for existing in db.scalars(select(m.ProspectProfile)):
            existing.facts = {**existing.facts, "agent_reviewed_at": m.now().isoformat()}
        account = m.Account(
            name="Entreprise sans domaine", domain="import-9999.unresolved.invalid", country="FR"
        )
        db.add(account)
        db.flush()
        db.add(
            m.ProspectProfile(
                account_id=account.id,
                campaign_id=flow["campaign"]["id"],
                status="review",
                facts={},
            )
        )
        db.commit()

        p.enqueue_batch(db)
        db.commit()
        action = db.scalar(select(m.AgentAction).where(m.AgentAction.kind == "qualify_batch"))
        assert action and account.id in action.payload["account_ids"]

        review = agent.AccountReview(
            account_id=account.id,
            summary="Site officiel retrouvé et rattaché à cette entreprise.",
            proofs=[
                agent.Proof(
                    field="domain",
                    value="Domaine officiel : resolved.example.com",
                    source_url="https://resolved.example.com/about",
                    excerpt="La page officielle présente clairement cette entreprise.",
                    observed_at=m.now(),
                )
            ],
            account_updates=agent.AccountUpdate(domain="resolved.example.com"),
        )
        p.apply_agent_batch(db, action, [review])
        db.commit()

        updated = db.get(m.Account, account.id)
        profile = db.get(m.ProspectProfile, account.id)
        assert updated.domain == "resolved.example.com"
        assert profile.facts["domain_resolution"] == {
            "verified": True,
            "resolved_domain": "resolved.example.com",
        }
