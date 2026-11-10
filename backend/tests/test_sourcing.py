import json

import httpx
import respx
from sqlalchemy import select

from app import autopilot
from app import models as m
from app.db import SessionLocal
from tests.test_autopilot import BASE, setup
from tests.test_campaign_groups import group, niche


def configured(client, campaign, **values):
    payload = {
        "enabled": True,
        "keywords": ["agency"],
        "free_credit_limit": 5,
        "contacts_per_day": 1,
        **values,
    }
    result = client.put("/api/sourcing/" + campaign["id"], json=payload)
    assert result.status_code == 200, result.text
    return result.json()


def test_global_control_does_not_create_a_campaign_or_sequence(client):
    assert client.post("/api/sourcing/control", json={"enabled": True}).status_code == 200
    assert client.get("/api/sourcing").json()["enabled"]
    assert not client.get("/api/campaigns").json()
    assert not client.get("/api/messages").json()


@respx.mock
def test_niches_apply_distinct_filters_and_share_one_provider_ceiling(client):
    setup(client)
    parent = group(client)
    first, _ = niche(client, parent, "Agences", 20, 100)
    second, _ = niche(client, parent, "Conseil", 1000, 5000)
    a = configured(client, first, industries=["Advertising Services"], keywords=["agency"])
    b = configured(client, second, keywords=["consulting"], excluded_keywords=["retail"])
    assert a["filters"]["headcount"] == ["11-50", "51-200"]
    assert b["filters"]["headcount"] == ["501-1000", "1001-5000"]
    discovery = respx.post(BASE + "/discover").mock(return_value=httpx.Response(200, json={"data": []}))
    assert autopilot.tick(first["id"]) == autopilot.tick(second["id"]) == 1
    payloads = [json.loads(call.request.content) for call in discovery.calls]
    assert payloads[0]["keywords"]["include"] == ["agency"]
    assert payloads[1]["keywords"]["include"] == ["consulting"]
    assert payloads[1]["keywords"]["exclude"] == ["retail"]
    assert payloads[0]["headquarters_location"]["include"] == [{"country": "FR"}]
    assert (
        client.put(
            "/api/sourcing/" + first["id"],
            json={"enabled": True, "keywords": ["agency"], "free_credit_limit": 21},
        ).status_code
        == 422
    )
    assert client.put("/api/sourcing/" + first["id"], json={"enabled": True}).status_code == 422
    assert (
        client.put("/api/sourcing/" + first["id"], json={"industries": ["Invented sector"]}).status_code
        == 422
    )
    with SessionLocal() as db:
        campaigns = [db.get(m.Campaign, c["id"]) for c in [first, second]]
        assert campaigns[0].config["sourcing"]["filters"] != campaigns[1].config["sourcing"]["filters"]


@respx.mock
def test_one_contact_per_day_is_supported_and_never_prepares_or_approves_new_niche_copy(client):
    _, _, domain, verify = setup(client)
    campaign, _ = niche(client, group(client))
    configured(client, campaign)
    assert autopilot.tick(campaign["id"]) == 1
    assert autopilot.tick(campaign["id"]) == 1
    assert domain.calls.last.request.url.params["limit"] == "1"
    assert autopilot.tick(campaign["id"]) == 1
    with SessionLocal() as db:
        row = db.scalar(select(m.Contact))
        assert row.campaign_id == campaign["id"] and row.email_status == "valid"
        assert not row.approved and row.status == "candidate"
        assert db.query(m.Message).count() == db.query(m.Task).count() == 0
        assert len(list(db.scalars(select(m.Contact)))) == 1
    assert verify.call_count == 1
    assert client.get("/api/sourcing").json()["messages"] == {
        "drafts": 0,
        "validated": 0,
        "cancelled": 0,
        "sent": 0,
        "historical": 0,
    }


@respx.mock
def test_same_domain_is_not_purchased_in_two_niches_and_global_reservations_apply(client):
    _, balance, domain, _ = setup(client, cap=2)
    a, _ = niche(client, group(client), "Agences")
    b, _ = niche(client, group(client), "Conseil")
    configured(client, a, free_credit_limit=2)
    configured(client, b, free_credit_limit=2)
    autopilot.tick(a["id"])
    autopilot.tick(b["id"])
    with SessionLocal() as db:
        jobs = list(db.scalars(select(m.Job).where(m.Job.payload["stage"].as_string() == "domain")))
        assert len(jobs) == 1 and jobs[0].campaign_id == a["id"]
        db.add(
            m.Job(
                kind="autopilot",
                provider="hunter",
                campaign_id=b["id"],
                status="uncertain",
                idempotency_key="prior-unresolved-cost",
                payload={"stage": "domain", "credit_units": 2},
            )
        )
        db.commit()
    autopilot.tick(a["id"])
    assert balance.call_count == domain.call_count == 0
    assert (
        "Limite de crédits"
        in next(c for c in client.get("/api/sourcing").json()["campaigns"] if c["campaign_id"] == a["id"])[
            "last_error"
        ]
    )


@respx.mock
def test_filter_change_cancels_queued_work_and_midflight_change_prevents_provider_call(client, monkeypatch):
    _, balance, domain, _ = setup(client)
    campaign, _ = niche(client, group(client))
    configured(client, campaign)
    autopilot.tick(campaign["id"])
    configured(client, campaign, keywords=["changed"])
    with SessionLocal() as db:
        assert (
            db.scalar(select(m.Job).where(m.Job.payload["stage"].as_string() == "domain")).status
            == "cancelled"
        )
    openings = 0

    def session():
        nonlocal openings
        openings += 1
        if openings == 2:
            with SessionLocal() as db:
                row = db.get(m.Campaign, campaign["id"])
                row.config = {
                    **row.config,
                    "sourcing": {**row.config["sourcing"], "enabled": False, "version": 99},
                }
                db.commit()
        return SessionLocal()

    monkeypatch.setattr(autopilot, "SessionLocal", session)
    discovery = respx.post(BASE + "/discover")
    previous = discovery.call_count
    assert autopilot.tick(campaign["id"]) == 0
    assert discovery.call_count == previous and balance.call_count == domain.call_count == 0


@respx.mock
def test_round_robin_visits_each_enabled_niche_without_activating_sends(client):
    legacy, _, _, _ = setup(client)
    a, _ = niche(client, group(client), "Agences")
    b, _ = niche(client, group(client), "Conseil")
    configured(client, a)
    configured(client, b)
    respx.post(BASE + "/discover").mock(return_value=httpx.Response(200, json={"data": []}))
    for _ in range(3):
        assert autopilot.tick() == 1
    rows = client.get("/api/sourcing").json()["campaigns"]
    assert all(
        next(c for c in rows if c["campaign_id"] == key)["last_tick_at"]
        for key in [legacy["campaign_id"], a["id"], b["id"]]
    )
    assert client.get("/api/state").json()["paused"]


def test_prepared_count_excludes_cancelled_history(client, flow):
    client.post("/api/batches/prepare", json={"contact_ids": [flow["contact"]["id"]]})
    with SessionLocal() as db:
        for row in db.scalars(select(m.Message)):
            row.status = "cancelled"
        db.commit()
    counts = client.get("/api/sourcing").json()["messages"]
    assert counts["cancelled"] == counts["historical"] == 1
    assert counts["drafts"] == counts["validated"] == 0


def test_contact_language_is_explicit_and_sequence_versions_remain_independent(client, flow):
    payload = {
        "name": "US English",
        "offer_id": flow["campaign"]["offer_id"],
        "icp_id": flow["campaign"]["icp_id"],
        "language": "en",
        "timezone": "America/New_York",
        "localized_footer": "Lumelia — business process consulting. Reply to opt out.",
        "sequence": [
            {
                "channel": "email",
                "day": 0,
                "subject": "A question for {{societe}}",
                "body": "Hello {{prenom}}, a fictional test.",
            }
        ],
    }
    created = client.post("/api/campaigns", json=payload)
    assert created.status_code == 201, created.text
    campaign = created.json()
    endpoint = "/api/contacts/" + flow["contact"]["id"]
    client.patch(endpoint, json={"campaign_id": campaign["id"]})

    def prepare():
        return client.post("/api/batches/prepare", json={"contact_ids": [flow["contact"]["id"]]})

    assert prepare().status_code == 422
    assert (
        client.post(
            endpoint + "/language",
            json={"language": "de", "note": "Fictional German language confirmed from profile."},
        ).status_code
        == 200
    )
    assert prepare().status_code == 422
    assert (
        client.post(
            endpoint + "/language",
            json={"language": "en", "note": "Fictional English conversation confirming preference."},
        ).status_code
        == 200
    )
    assert prepare().status_code == 200
    from app import languages, mail

    with SessionLocal() as db:
        row = db.get(m.Contact, flow["contact"]["id"])
        assert languages.reviewed(db, row)["language"] == "en"
        current = db.get(m.Campaign, campaign["id"])
        config = mail.campaign_config(
            {"legal_footer": "Ancien pied français", "privacy_url": "https://example.org/privacy"}, current
        )
        message = db.scalar(select(m.Message))
        body, _ = mail.content(message, row, config)
        assert "Privacy information" in body and "Stop receiving messages" in body
        assert "Ancien pied français" not in body and payload["localized_footer"] in body
    version = client.post(
        "/api/campaigns/" + campaign["id"] + "/versions",
        json={
            **payload,
            "language": "de",
            "localized_footer": "Lumelia — Beratung. Antworten Sie zum Abmelden.",
        },
    )
    assert version.status_code == 201
    rows = {c["id"]: c for c in client.get("/api/campaigns").json()}
    assert rows[campaign["id"]]["config"]["language"] == "en"
    assert rows[version.json()["id"]]["config"]["language"] == "de"
    client.post(
        endpoint + "/language",
        json={"language": "", "note": "Language no longer confirmed; requires review."},
    )
    assert client.get("/api/messages").json()[0]["status"] == "cancelled"


def test_non_french_campaign_never_falls_back_to_a_french_footer(client, flow):
    response = client.post(
        "/api/campaigns",
        json={
            "name": "German draft",
            "offer_id": flow["campaign"]["offer_id"],
            "icp_id": flow["campaign"]["icp_id"],
            "language": "de",
            "sequence": [{"channel": "email", "day": 0, "subject": "Test", "body": "Fiktive Daten."}],
        },
    )
    campaign = response.json()
    endpoint = "/api/contacts/" + flow["contact"]["id"]
    client.patch(endpoint, json={"campaign_id": campaign["id"]})
    client.post(
        endpoint + "/language", json={"language": "de", "note": "Fictional German preference reviewed."}
    )
    with SessionLocal() as db:
        from app import domain as d

        row = db.get(m.Contact, flow["contact"]["id"])
        assert any("Pied d’email" in r for r in d.eligibility(db, row))
    assert (
        client.post("/api/batches/prepare", json={"contact_ids": [flow["contact"]["id"]]}).status_code == 422
    )
