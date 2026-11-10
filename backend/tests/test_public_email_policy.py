from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models as m
from app.db import Base
from app.public_email import publication_review


def test_publication_review_is_bound_to_address_source_recency_and_policy():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account = m.Account(name="Fixture", domain="fixture.example")
        db.add(account)
        db.flush()
        contact = m.Contact(
            account_id=account.id, identity_key="contact@fixture.example", email="contact@fixture.example"
        )
        db.add(contact)
        db.flush()
        campaign = SimpleNamespace(config={"email_validation_policy": "official_publication_and_mx"})
        evidence = m.Evidence(
            account_id=account.id,
            contact_id=contact.id,
            field="email",
            value=contact.email,
            source_url="https://fixture.example/contact",
            source_type="official_website",
            observed_at=m.now() - timedelta(seconds=1),
        )
        review = {
            "email": contact.email,
            "email_domain": "fixture.example",
            "source_url": evidence.source_url,
            "mx_hosts": ["mx.fixture.example"],
            "mx_checked_at": (m.now() - timedelta(seconds=1)).isoformat(),
        }
        event = m.Event(
            contact_id=contact.id,
            account_id=account.id,
            kind="email_publication_reviewed",
            actor_id="reviewer",
            data=review,
            created_at=m.now() - timedelta(seconds=1),
        )
        db.add_all([evidence, event])
        db.flush()
        assert publication_review(db, contact, campaign)
        assert contact.email_status == "unknown" and contact.email_verified_at is None
        contact.email_status = "invalid"
        assert not publication_review(db, contact, campaign)
        contact.email_status = "risky"
        assert publication_review(db, contact, campaign)
        campaign.config = {}
        assert not publication_review(db, contact, campaign)
        campaign.config = {"email_validation_policy": "official_publication_and_mx"}
        contact.email = "changed@fixture.example"
        assert not publication_review(db, contact, campaign)
        contact.email = review["email"]
        for change in [
            {"mx_hosts": ["."]},
            {"email_domain": "other.example"},
            {"source_url": "https://other.example/contact"},
            {"mx_checked_at": (m.now() - timedelta(days=91)).isoformat()},
        ]:
            event.data = {**review, **change}
            db.flush()
            assert not publication_review(db, contact, campaign)
        event.data = review
        evidence.source_type = "provider"
        db.flush()
        assert not publication_review(db, contact, campaign)
        evidence.source_type = "official_website"
        event.actor_id = None
        db.flush()
        assert not publication_review(db, contact, campaign)
    engine.dispose()
