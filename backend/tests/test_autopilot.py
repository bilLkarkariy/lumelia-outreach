from datetime import timedelta

import httpx
import respx
from sqlalchemy import select

from app import autopilot
from app import domain as d
from app import models as m
from app.db import SessionLocal
from tests.test_senders import healthy

BASE = "https://api.hunter.io/v2"


def setup(client, cap=20):
    client.post("/api/integrations/hunter", json={"config": {"api_key": "fictional-hunter-key"}})
    result = client.post("/api/autopilot/configure", json={"enabled": True, "free_credit_limit": cap})
    assert result.status_code == 200, result.text
    respx.post(BASE + "/discover").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"domain": "test.example.org", "organization": "Conseil Test"},
                ]
            },
        )
    )
    balance = respx.get(BASE + "/account").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "plan_name": "Free",
                    "requests": {"credits": {"remaining": 23, "available": 50, "used": 27}},
                }
            },
        )
    )
    domain = respx.get(BASE + "/domain-search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "domain": "test.example.org",
                    "webmail": False,
                    "emails": [
                        {
                            "value": "alice@test.example.org",
                            "first_name": "Alice",
                            "last_name": "Test",
                            "position": "Directrice commerciale",
                            "sources": [{"uri": "https://test.example.org/team"}],
                        },
                    ],
                }
            },
        )
    )
    verify = respx.get(BASE + "/email-verifier").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "status": "valid",
                    "webmail": False,
                    "disposable": False,
                    "accept_all": False,
                }
            },
        )
    )
    return result.json(), balance, domain, verify


@respx.mock
def test_automatic_pipeline_finds_verifies_and_prepares_without_per_contact_actions(client):
    config, balance, domain, verify = setup(client)
    assert autopilot.tick() == 1
    companies = client.get("/api/accounts").json()
    assert len(companies) == 1 and companies[0]["contact_count"] == 0
    assert companies[0]["evidence_count"] == 1 and domain.call_count == 0
    assert (
        client.get("/api/accounts/" + companies[0]["id"]).json()["evidence"][0]["confidence"] == "unverified"
    )
    assert autopilot.tick() == 1
    assert autopilot.tick() == 1
    state = client.get("/api/autopilot").json()
    assert state["contacts"] == state["verified"] == 1 and state["prepared"] == 3
    assert state["credits_reserved_or_used"] == 1.5 and not state["sending_enabled"]
    assert "fictional-hunter-key" not in str(state)
    assert balance.call_count == 2 and domain.call_count == verify.call_count == 1
    with SessionLocal() as db:
        contact = db.scalar(select(m.Contact))
        account = db.get(m.Account, contact.account_id)
        assert contact.approved and contact.qualification["validation"] == "automatic_email_eligibility"
        assert account.hubspot_status == "unknown" and account.employee_count is None
        assert db.query(m.Task).count() == 0
        assert db.query(m.Job).filter(m.Job.kind == "send").count() == 0
    assert autopilot.tick() == 0
    assert domain.call_count == verify.call_count == 1  # Restart/poll cannot rebuy the same contact.


@respx.mock
def test_automatic_approval_selects_only_one_contact_per_company(client):
    setup(client)
    respx.get(BASE + "/domain-search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "emails": [
                        {
                            "value": "alice@test.example.org",
                            "first_name": "Alice",
                            "last_name": "Test",
                            "position": "CEO",
                        },
                        {
                            "value": "bob@test.example.org",
                            "first_name": "Bob",
                            "last_name": "Test",
                            "position": "Sales Director",
                        },
                    ]
                }
            },
        )
    )
    for _ in range(4):
        assert autopilot.tick() == 1
    with SessionLocal() as db:
        rows = list(db.scalars(select(m.Contact).order_by(m.Contact.created_at)))
        assert len(rows) == 2 and sum(row.approved for row in rows) == 1
        assert rows[0].approved
        rows[1].approved = True  # A legacy or concurrent approval cannot bypass the send guard.
        db.flush()
        assert "Un autre interlocuteur de cette entreprise est déjà actif" in d.eligibility(db, rows[1])


@respx.mock
def test_automatic_approval_rejects_a_known_company_size_mismatch(client):
    setup(client)
    autopilot.tick()
    autopilot.tick()
    with SessionLocal() as db:
        account = db.scalar(select(m.Account))
        account.employee_count = 200
        db.commit()
    autopilot.tick()
    with SessionLocal() as db:
        contact = db.scalar(select(m.Contact))
        assert contact.email_status == "valid" and not contact.approved


@respx.mock
def test_paid_plan_or_exhausted_free_budget_never_triggers_paid_lookup(client):
    config, balance, domain, verify = setup(client, cap=0)
    autopilot.tick()
    autopilot.tick()
    assert domain.call_count == 0 and balance.call_count == 0
    client.post("/api/autopilot/configure", json={"enabled": True, "free_credit_limit": 20})
    with SessionLocal() as db:
        db.scalar(select(m.Job).where(m.Job.status == "queued")).run_at = m.now()
        db.commit()
    balance.mock(
        return_value=httpx.Response(
            200, json={"data": {"plan_name": "Starter", "requests": {"credits": {"remaining": 1000}}}}
        )
    )
    autopilot.tick()
    assert domain.call_count == 0 and "forfait" in client.get("/api/autopilot").json()["last_error"]


@respx.mock
def test_interrupted_credit_operation_is_never_repeated(client):
    _, _, domain, _ = setup(client)
    autopilot.tick()

    def timeout(request):
        with SessionLocal() as db:
            job = db.scalar(select(m.Job).where(m.Job.status == "running"))
            assert job.payload["credit_units"] == 2
        raise httpx.ReadTimeout("private response", request=request)

    domain.mock(side_effect=timeout)
    autopilot.tick()
    autopilot.tick()
    assert domain.call_count == 1
    with SessionLocal() as db:
        assert db.scalar(select(m.Job).where(m.Job.status == "uncertain"))
    assert client.get("/api/autopilot").json()["credits_reserved_or_used"] == 1


@respx.mock
def test_verification_polling_reserves_once_and_doubtful_email_gets_no_sequence(client):
    _, _, _, verify = setup(client)
    autopilot.tick()
    autopilot.tick()
    verify.mock(return_value=httpx.Response(202, json={"data": None}))
    autopilot.tick()
    with SessionLocal() as db:
        job = db.scalar(select(m.Job).where(m.Job.status == "waiting"))
        job.run_at = m.now()
        db.commit()
    verify.mock(return_value=httpx.Response(200, json={"data": {"status": "accept_all"}}))
    autopilot.tick()
    state = client.get("/api/autopilot").json()
    assert state["credits_reserved_or_used"] == 1.5 and state["prepared"] == 0 and state["verified"] == 0


@respx.mock
def test_domain_optout_between_discovery_and_purchase_is_respected(client):
    _, _, domain, _ = setup(client)
    autopilot.tick()
    with SessionLocal() as db:
        db.add(m.Suppression(kind="domain", value="test.example.org", scope="all", reason="Test"))
        db.commit()
    autopilot.tick()
    assert domain.call_count == 0


@respx.mock
def test_ready_sender_queues_sequence_automatically_and_stop_never_requeues(client, monkeypatch):
    config, _, _, _ = setup(client)
    for _ in range(3):
        autopilot.tick()
    with SessionLocal() as db:
        healthy(db)
        state = db.get(m.AppState, "global")
        state.paused = False
        db.commit()
    monkeypatch.setattr(autopilot.settings(), "live_actions_enabled", True)
    monkeypatch.setattr(autopilot.settings(), "public_url", "https://example.org")
    autopilot.tick()
    with SessionLocal() as db:
        jobs = list(db.scalars(select(m.Job).where(m.Job.kind == "send")))
        assert len(jobs) == 3
        messages = list(db.scalars(select(m.Message).order_by(m.Message.step)))
        assert messages[1].due_at - messages[0].due_at >= timedelta(days=5)
        assert len({message.sender_id for message in messages}) == 1
        contact = db.scalar(select(m.Contact))
        from app import domain as d

        d.receive_event(
            db, contact, "reply", channel="email", summary="Réponse fictive", external_id="auto-reply-test"
        )
        db.commit()
    autopilot.tick()
    with SessionLocal() as db:
        assert all(job.status == "cancelled" for job in db.scalars(select(m.Job).where(m.Job.kind == "send")))


@respx.mock
def test_stop_between_intent_and_provider_call_cancels_purchase(client, monkeypatch):
    _, _, domain, _ = setup(client)
    autopilot.tick()
    real_session = autopilot.SessionLocal
    count = 0

    def session():
        nonlocal count
        count += 1
        if count == 2:
            with real_session() as db:
                state = db.get(m.AppState, "global")
                autopilot.store(state, {**autopilot.config(state), "enabled": False})
                db.commit()
        return real_session()

    monkeypatch.setattr(autopilot, "SessionLocal", session)
    autopilot.tick()
    assert domain.call_count == 0
