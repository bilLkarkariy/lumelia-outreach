import os

os.environ["LUMELIA_DATABASE_URL"] = os.environ.get(
    "LUMELIA_TEST_DATABASE_URL", "postgresql+psycopg://lumelia@127.0.0.1:55439/lumelia_test"
)
from argon2 import PasswordHasher
from cryptography.fernet import Fernet

os.environ["LUMELIA_ACCESS_PASSWORD_HASH"] = PasswordHasher().hash("test-password-1234")
os.environ["LUMELIA_LIVE_ACTIONS_ENABLED"] = "false"
os.environ["LUMELIA_PROTECTED_SENDER_DOMAINS"] = "primary.example.net,corporate.example.net"
os.environ["LUMELIA_PROTECTED_SENDER_ADDRESSES"] = "owner@primary.example.net"
os.environ["LUMELIA_PRIVACY_CONTACT"] = "privacy@example.com"
# Throwaway key generated for the test suite only; it protects nothing.
os.environ["LUMELIA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import Base, engine
from app.main import app
from app.offers import PRIMARY_ID


@pytest.fixture(scope="session", autouse=True)
def migrated_test_database():
    from alembic import command
    from alembic.config import Config

    assert engine.url.host == "127.0.0.1" and engine.url.database.startswith("lumelia_test")
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture()
def client():
    assert engine.url.host == "127.0.0.1" and engine.url.database.startswith("lumelia_test")
    with engine.begin() as conn:
        names = ", ".join('"' + table.name + '"' for table in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE TABLE {names} CASCADE"))
    with TestClient(app) as client:
        result = client.post("/api/auth/login", json={"password": "test-password-1234"})
        assert result.status_code == 200, result.text
        client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        yield client


@pytest.fixture()
def flow(client):
    icp = next(row for row in client.get("/api/icps").json() if row["offer_id"] == PRIMARY_ID)
    campaign = client.post(
        "/api/campaigns",
        json={
            "name": "Test simulation",
            "icp_id": icp["id"],
            "offer_id": icp["offer_id"],
            "sequence": [
                {
                    "channel": "email",
                    "day": 0,
                    "subject": "Bonjour {{prenom}}",
                    "body": "Suivi pour {{societe}}",
                },
                {"channel": "phone", "day": 0, "body": "Appel de {{prenom}}"},
            ],
        },
    ).json()
    account = client.post(
        "/api/accounts", json={"name": "Entreprise Test", "domain": "test.example.com"}
    ).json()
    payload = {
        "account_id": account["id"],
        "campaign_id": campaign["id"],
        "first_name": "Alice",
        "last_name": "Test",
        "role": "Dirigeante",
        "email": "alice@example.com",
        "email_status": "valid",
        "phone": "+33199000042",
        "phone_status": "appropriate",
        "professional_relevance": "Suit les devis B2B",
        "sourcing_note": "Données fictives de recette",
        "information_status": "planned",
    }
    response = client.post("/api/contacts", json=payload)
    assert response.status_code == 201, response.text
    return {"contact": response.json(), "campaign": campaign, "account": account, "payload": payload}
