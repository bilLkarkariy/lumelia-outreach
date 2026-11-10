from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import models as m
from app.db import SessionLocal
from app.main import app


def listing(client, resource, **params):
    response = client.get("/api/lists/" + resource, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_contact_pagination_search_beyond_old_limit_and_stable_pages(client, flow):
    with SessionLocal() as db:
        stamp = m.now()
        for i in range(225):
            db.add(
                m.Contact(
                    account_id=flow["account"]["id"],
                    identity_key=f"list-{i}",
                    first_name="Recette",
                    last_name=f"{i:03}",
                    role="Direction commerciale",
                    email=f"list-{i}@example.org",
                    created_at=stamp,
                )
            )
        db.commit()
    first = listing(client, "contacts", page_size=100, sort="created")
    second = listing(client, "contacts", page=2, page_size=100, sort="created")
    third = listing(client, "contacts", page=3, page_size=100, sort="created")
    assert first["total"] == 226 and len(third["items"]) == 26
    assert len({row["id"] for result in [first, second, third] for row in result["items"]}) == 226
    assert [row["id"] for row in second["items"]] == [
        row["id"] for row in listing(client, "contacts", page=2, page_size=100, sort="created")["items"]
    ]
    result = listing(client, "contacts", q="list-224@example.org", page=20)
    assert result["page"] == 1 and result["total"] == 1 and result["items"][0]["last_name"] == "224"
    assert listing(client, "contacts", q="Direction commerciale")["total"] == 225
    assert listing(client, "contacts", q="%")["total"] == 0
    assert len(client.get("/api/contacts?limit=200").json()["items"]) == 200


def test_contact_filters_and_account_counters_include_unassigned_contacts(client, flow):
    with SessionLocal() as db:
        known = db.get(m.Contact, flow["contact"]["id"])
        known.phone_type = "direct"
        known.approved = True
        account = db.get(m.Account, flow["account"]["id"])
        account.employee_count = 42
        account.hubspot_status = "signal"
        db.add(m.Evidence(account_id=account.id, field="activité", value="fictif", source_type="manual"))
        empty = m.Account(
            name="Sans interlocuteur", domain="empty-list.example.org", country="GB", employee_count=None
        )
        db.add(empty)
        db.flush()
        db.add(
            m.Contact(
                account_id=account.id,
                id="00000000-0000-4000-8000-000000000001",
                identity_key="no-email",
                first_name="Sans",
                last_name="Email",
                paused=True,
            )
        )
        db.commit()
    result = listing(
        client,
        "contacts",
        phone="direct",
        email_status="valid",
        qualification="approved",
        campaign_id=flow["campaign"]["id"],
    )
    assert result["total"] == 1 and result["items"][0]["id"] == flow["contact"]["id"]
    assert listing(client, "contacts", email_status="missing", campaign_id="none")["total"] == 1
    assert listing(client, "contacts", status="paused")["total"] == 1
    assert listing(client, "contacts", status="candidate")["total"] == 1
    ordered = listing(client, "contacts", sort="status")["items"]
    assert [row["paused"] for row in ordered] == [False, True]
    result = listing(client, "accounts", employees="20-49", crm="signal", has_contacts="yes")
    assert result["total"] == 1 and result["items"][0]["contact_count"] == 2
    assert result["items"][0]["evidence_count"] >= 1
    result = listing(client, "accounts", has_contacts="no", country="gb", employees="unknown")
    assert result["total"] == 1 and result["items"][0]["domain"] == "empty-list.example.org"
    assert result["facets"]["countries"] == ["FR", "GB"]


def test_task_filters_keep_status_date_and_contact_together(client, flow):
    with SessionLocal() as db:
        for status, channel, days in [
            ("pending", "phone", -1),
            ("completed", "phone", -1),
            ("pending", "review", 3),
        ]:
            db.add(
                m.Task(
                    contact_id=flow["contact"]["id"],
                    campaign_id=flow["campaign"]["id"],
                    title="Action de recette",
                    status=status,
                    channel=channel,
                    due_at=m.now() + timedelta(days=days),
                )
            )
        db.commit()
    result = listing(client, "tasks", period="overdue", channel="phone", campaign_id=flow["campaign"]["id"])
    assert result["total"] == 1 and result["summary"]["pending"] == 2
    assert result["items"][0]["contact"]["id"] == flow["contact"]["id"]
    assert "checks" in result["items"][0]["contact"]["fit"]
    assert result["items"][0]["sent_count"] == 0
    assert result["items"][0]["account"]["name"] == flow["account"]["name"]
    assert listing(client, "tasks", period="overdue", status="completed")["total"] == 0
    assert listing(client, "tasks", period="upcoming")["total"] == 1


def test_opportunities_without_contact_and_amount_sort(client, flow):
    with SessionLocal() as db:
        user = db.scalar(select(m.User))
        for amount, contact in [(15000, None), (75000, flow["contact"]["id"])]:
            db.add(
                m.Opportunity(
                    account_id=flow["account"]["id"],
                    contact_id=contact,
                    owner_id=user.id,
                    title="Affaire fictive",
                    amount_cents=amount,
                )
            )
        db.commit()
    result = listing(client, "opportunities", sort="amount", direction="desc")
    assert [row["amount_cents"] for row in result["items"]] == [75000, 15000]
    assert result["items"][1]["contact"] is None and result["summary"]["amount_cents"] == 90000
    assert listing(client, "opportunities", period="undated")["total"] == 2


def test_messages_are_filtered_without_preparing_or_sending(client, flow):
    with SessionLocal() as db:
        for i, status in enumerate(["draft", "cancelled"]):
            db.add(
                m.Message(
                    contact_id=flow["contact"]["id"],
                    campaign_id=flow["campaign"]["id"],
                    subject="Fiche test",
                    body="Recette",
                    status=status,
                    dedupe_key=f"list-message-{i}",
                )
            )
        db.commit()
    result = listing(client, "messages", status="cancelled", q="alice@example.com")
    assert result["total"] == 1 and result["items"][0]["attempted_at"] is None
    assert result["items"][0]["campaign"]["id"] == flow["campaign"]["id"]
    assert listing(client, "messages")["summary"]["sent"] == 0


def test_lists_require_auth_and_reject_invalid_page_or_sort(client):
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/lists/contacts").status_code == 401
    for params in [
        {"page": 0},
        {"page_size": 10000},
        {"sort": "not-a-column"},
        {"direction": "random"},
        {"phone": "maybe"},
    ]:
        assert client.get("/api/lists/contacts", params=params).status_code == 422
