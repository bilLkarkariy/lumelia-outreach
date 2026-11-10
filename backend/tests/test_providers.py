import json

import httpx
import pytest
import respx

from app import providers


@respx.mock
def test_connection_checks_show_quota_without_enabling_live_actions(client):
    from app.config import settings

    assert settings().live_actions_enabled is False
    client.post("/api/integrations/hunter", json={"config": {"api_key": "hunter-secret"}})
    route = respx.get("https://api.hunter.io/v2/account").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "email": "private-account@example.org",
                    "plan_name": "Free",
                    "reset_date": "2026-09-09",
                    "requests": {"credits": {"used": 26.5, "available": 50, "remaining": 23.5}},
                }
            },
        )
    )
    result = client.post("/api/integrations/hunter/check")
    assert result.status_code == 200
    assert result.json()["state"]["quota"]["requests"]["credits"]["remaining"] == 23.5
    assert "private-account" not in result.text and "hunter-secret" not in result.text
    assert route.call_count == 1 and settings().live_actions_enabled is False
    assert client.get("/api/jobs").json() == []
    client.post("/api/integrations/hunter", json={"config": {"api_key": "replacement-secret"}})
    assert not client.get("/api/integrations").json()[0]["state"]


@respx.mock
def test_lemlist_plan_limit_is_reported_without_retries_or_secrets(client):
    client.post("/api/integrations/lemlist", json={"config": {"api_key": "lemlist-secret"}})
    route = respx.get("https://api.lemlist.com/api/team/credits").mock(
        return_value=httpx.Response(402, text="route is available starting emailPro plan; lemlist-secret")
    )
    result = client.post("/api/integrations/lemlist/check")
    assert result.json()["status"] == "error"
    assert "402" in result.json()["state"]["connection_error"]
    assert "lemlist-secret" not in result.text
    assert route.calls[0].request.headers["Authorization"].startswith("Basic ")
    assert "x-api-key" not in route.calls[0].request.headers
    assert route.call_count == 1


@respx.mock
def test_apollo_health_requires_authenticated_response():
    route = respx.get("https://api.apollo.io/api/v1/auth/health").mock(
        return_value=httpx.Response(200, json={"healthy": True, "is_logged_in": False})
    )
    assert providers.verify_connection("apollo", {"api_key": "key"}) == {"authenticated": False}
    route.mock(return_value=httpx.Response(200, json={"healthy": True, "is_logged_in": True}))
    quota = respx.post("https://api.apollo.io/api/v1/usage_stats/credit_usage_stats").mock(
        return_value=httpx.Response(
            200, json={"credit_usage_stats": {"lead_credit": {"limit": 85, "consumed": 1, "left_over": 84}}}
        )
    )
    search = respx.post("https://api.apollo.io/api/v1/mixed_people/api_search").mock(
        return_value=httpx.Response(403)
    )
    result = providers.verify_connection("apollo", {"api_key": "key"})
    assert result["authenticated"] is True
    assert result["quota"]["credit_usage_stats"]["lead_credit"]["left_over"] == 84
    assert quota.call_count == 1
    assert result["capabilities"]["people_search"] is False
    assert "403" in result["limitation"]
    assert search.calls[0].request.url.params["per_page"] == "1"
    quota.mock(return_value=httpx.Response(403))
    search.mock(return_value=httpx.Response(200, json={"people": [{"secret_contact": "not-retained"}]}))
    result = providers.verify_connection("apollo", {"api_key": "key"})
    assert result["authenticated"] is True and result["quota_unavailable"] is True
    assert result["capabilities"]["people_search"] is True
    assert "not-retained" not in json.dumps(result)


@respx.mock
def test_fullenrich_requests_only_work_data_and_matches_return():
    route = respx.post("https://app.fullenrich.com/api/v2/contact/enrich/bulk").mock(
        return_value=httpx.Response(200, json={"enrichment_id": "remote-123"})
    )
    result = providers.enrich(
        "fullenrich",
        {"api_key": "secret-test"},
        {"first_name": "Alice", "last_name": "Test", "domain": "example.com"},
        ["email", "phone"],
        "job-123",
    )
    body = json.loads(route.calls[0].request.content)
    assert body["data"][0]["enrich_fields"] == ["contact.work_emails", "contact.phones"]
    assert "personal" not in str(body)
    assert result["complete"] is False
    respx.get("https://app.fullenrich.com/api/v2/contact/enrich/bulk/remote-123").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "FINISHED",
                "data": [
                    {
                        "custom": {"job_id": "job-123"},
                        "contact_info": {
                            "most_probable_work_email": {
                                "email": "alice@example.com",
                                "status": "DELIVERABLE",
                            },
                            "most_probable_phone": {"number": "+33199000042", "ownership_match": "CONFIRMED"},
                        },
                    }
                ],
            },
        )
    )
    result = providers.poll_fullenrich({"api_key": "secret-test"}, "remote-123", "job-123")
    assert result["email_status"] == "valid"
    assert result["phone_status"] == "unknown"
    with pytest.raises(providers.ProviderError):
        providers.poll_fullenrich({"api_key": "secret-test"}, "remote-123", "wrong-job")


@respx.mock
def test_timeout_never_retries_paid_request_or_exposes_secret():
    route = respx.post("https://app.fullenrich.com/api/v2/contact/enrich/bulk").mock(
        side_effect=httpx.ReadTimeout("SECRET URL")
    )
    with pytest.raises(providers.ProviderError) as caught:
        providers.enrich(
            "fullenrich", {"api_key": "secret-test"}, {"domain": "example.com"}, ["phone"], "job"
        )
    assert caught.value.uncertain
    assert route.call_count == 1
    assert "SECRET" not in str(caught.value) and "secret-test" not in str(caught.value)


@respx.mock
def test_hunter_uncertain_email_stays_risky():
    route = respx.get("https://api.hunter.io/v2/email-verifier").mock(
        return_value=httpx.Response(200, json={"data": {"status": "accept_all"}})
    )
    result = providers.enrich(
        "hunter", {"api_key": "secret-test"}, {"email": "alice@example.com"}, ["email"], "job"
    )
    assert result["email_status"] == "risky"
    assert "api_key" not in route.calls[0].request.url.params


def test_integration_secrets_not_returned(client):
    result = client.post(
        "/api/integrations/fullenrich",
        json={"config": {"api_key": "never-return-me"}, "unit_cost_cents": 20, "monthly_limit_cents": 1000},
    )
    assert result.status_code == 200
    assert "never-return-me" not in result.text
    assert "encrypted_config" not in result.text
    assert "never-return-me" not in client.get("/api/integrations").text


def test_live_enrichment_disabled_by_default(client, flow):
    result = client.post(
        "/api/enrichments",
        json={"provider": "fullenrich", "contact_ids": [flow["contact"]["id"]], "fields": ["phone"]},
    )
    assert result.status_code == 409
    assert client.get("/api/jobs").json() == []


@respx.mock
def test_enrichment_worker_persists_pending_and_does_not_repurchase(client, flow, monkeypatch):
    from app import models as m
    from app.config import settings
    from app.db import SessionLocal
    from app.enrichment import tick

    monkeypatch.setattr(settings(), "live_actions_enabled", True)
    client.post(
        "/api/integrations/fullenrich",
        json={"config": {"api_key": "test-key"}, "unit_cost_cents": 30, "monthly_limit_cents": 1000},
    )
    client.patch("/api/state", json={"paused": False, "monthly_limit_cents": 1000})
    with SessionLocal() as db:
        campaign = db.get(m.Campaign, flow["campaign"]["id"])
        campaign.mode = "live"
        campaign.budget_cents = 1000
        campaign.budget_approved = True
        db.commit()
    body = {"provider": "fullenrich", "contact_ids": [flow["contact"]["id"]], "fields": ["phone"]}
    queued = client.post("/api/enrichments", json=body)
    assert queued.status_code == 201, queued.text
    job_id = queued.json()[0]["id"]
    post = respx.post("https://app.fullenrich.com/api/v2/contact/enrich/bulk").mock(
        return_value=httpx.Response(200, json={"enrichment_id": "remote1"})
    )
    assert tick() == 1
    assert client.get("/api/jobs").json()[0]["status"] == "waiting"
    repeated = client.post("/api/enrichments", json=body)
    assert repeated.json()[0]["id"] == job_id
    respx.get("https://app.fullenrich.com/api/v2/contact/enrich/bulk/remote1").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "FINISHED",
                "data": [
                    {
                        "custom": {"job_id": job_id},
                        "contact_info": {"most_probable_phone": {"number": "+33199000042"}},
                    }
                ],
            },
        )
    )
    with SessionLocal() as db:
        db.get(m.Job, job_id).run_at = m.now()
        db.commit()
    assert tick() == 1
    assert tick() == 0
    assert post.call_count == 1
    assert client.get("/api/jobs").json()[0]["status"] == "completed"
    assert client.get("/api/expenses").json()[0]["amount_cents"] == 30
    assert client.get("/api/contacts/" + flow["contact"]["id"]).json()["phone_status"] == "unknown"


@respx.mock
def test_discovery_is_bounded_deduplicated_and_requires_review(client, flow, monkeypatch):
    from app import models as m
    from app.config import settings
    from app.db import SessionLocal
    from app.discovery import tick

    monkeypatch.setattr(settings(), "live_actions_enabled", True)
    client.post(
        "/api/integrations/apollo",
        json={"config": {"api_key": "test-key"}, "unit_cost_cents": 20, "monthly_limit_cents": 1000},
    )
    client.patch("/api/state", json={"paused": False, "monthly_limit_cents": 1000})
    with SessionLocal() as db:
        campaign = db.get(m.Campaign, flow["campaign"]["id"])
        campaign.mode = "live"
        campaign.budget_cents = 1000
        campaign.budget_approved = True
        db.commit()
    body = {
        "campaign_id": flow["campaign"]["id"],
        "kind": "people",
        "filters": {"person_titles": ["CEO"], "organization_locations": ["France"]},
        "limit": 20,
    }
    queued = client.post("/api/discoveries", json=body)
    assert queued.status_code == 201, queued.text
    assert client.post("/api/discoveries", json=body).json()["id"] == queued.json()["id"]
    post = respx.post("https://api.apollo.io/api/v1/mixed_people/api_search").mock(
        return_value=httpx.Response(
            200,
            json={
                "people": [
                    {
                        "id": "candidate1",
                        "first_name": "Camille",
                        "last_name_obfuscated": "M***",
                        "title": "CEO",
                        "organization": {"name": "Example", "primary_domain": "example.org"},
                        "personal_email": "private@example.net",
                    }
                ]
            },
        )
    )
    assert tick() == 1
    assert tick() == 0
    job = client.get("/api/jobs").json()[0]
    assert job["status"] == "completed"
    assert job["result"]["candidates"][0]["review_required"]
    assert "private@example.net" not in str(job)
    assert client.get("/api/contacts").json()["total"] == 1
    assert post.call_count == 1
    assert post.calls[0].request.url.params["per_page"] == "20"
    assert len(client.get("/api/expenses").json()) == 1

    review = {
        "index": 0,
        "company": "Example",
        "domain": "example.org",
        "first_name": "Camille",
        "last_name": "M***",
        "role": "CEO",
        "review_note": "Identité vérifiée sur la source professionnelle.",
    }
    assert client.post("/api/discoveries/" + job["id"] + "/accept", json=review).status_code == 422
    review["last_name"] = "Martin"
    accepted = client.post("/api/discoveries/" + job["id"] + "/accept", json=review)
    assert accepted.status_code == 201, accepted.text
    contact_id = accepted.json()["contact_id"]
    assert (
        client.post("/api/discoveries/" + job["id"] + "/accept", json=review).json()["contact_id"]
        == contact_id
    )
    assert client.get("/api/contacts/" + contact_id).json()["approved"] is False
    assert (
        client.post("/api/contacts/" + contact_id + "/erase", json={"confirmation": "EFFACER"}).status_code
        == 200
    )
    assert client.get("/api/jobs").json()[0]["result"]["candidates"][0] == {"erased": True}
