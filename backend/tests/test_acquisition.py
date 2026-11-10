import itertools
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app import acquisition as a
from app import domain as d
from app import models as m
from app import providers
from app.db import SessionLocal


def configured(client, monkeypatch):
    monkeypatch.setattr(a, "check", lambda config: {"authenticated": True, "quota": {"used_usd": 0}})
    monkeypatch.setattr(a, "usage", lambda config: {"used_usd": 0})
    assert (
        client.post("/api/integrations/apify", json={"config": {"api_key": "test-apify"}}).status_code == 200
    )
    assert client.post("/api/integrations/apify/check").json()["check"]["authenticated"]


def payload(flow):
    return {
        "request_id": str(uuid4()),
        "campaign_id": flow["campaign"]["id"],
        "search": "cabinet de conseil",
        "location": "Paris, France",
        "limit": 100,
    }


def test_acquisition_reserves_budget_and_replays_without_duplicate(client, flow, monkeypatch):
    configured(client, monkeypatch)
    body = payload(flow)
    first = client.post("/api/acquisition/apify", json=body)
    assert first.status_code == 202
    assert client.post("/api/acquisition/apify", json=body).json()["id"] == first.json()["id"]
    assert client.post("/api/acquisition/apify", json={**body, "limit": 25}).status_code == 409
    for _ in range(9):
        assert client.post("/api/acquisition/apify", json=payload(flow)).status_code == 202
    assert client.post("/api/acquisition/apify", json=payload(flow)).status_code == 409
    with SessionLocal() as db:
        jobs = list(db.scalars(select(m.Job).where(m.Job.kind == a.KIND).order_by(m.Job.run_at)))
        assert len(jobs) == 10
        assert all(b.run_at - a.run_at >= timedelta(hours=1) for a, b in itertools.pairwise(jobs))
        assert a.spent(db) == 500


def test_apify_partial_result_preserves_people_and_exclusions(client, flow, monkeypatch):
    configured(client, monkeypatch)
    excluded = client.post(
        "/api/accounts", json={"name": "Excluded", "domain": "excluded.example.com", "lifecycle": "excluded"}
    ).json()
    body = payload(flow)
    job = client.post("/api/acquisition/apify", json=body).json()
    calls = []

    def request(config, method, path, **kw):
        calls.append((method, path, kw))
        if method == "POST":
            assert kw["params"]["maxTotalChargeUsd"] == 0.5
            assert kw["payload"]["maxCrawledPlacesPerSearch"] == 100
            assert kw["payload"]["maximumLeadsEnrichmentRecords"] == 0
            assert kw["payload"]["scrapeContacts"] is False
            return {"data": {"id": "remote123"}}
        if path.startswith("/actor-runs/"):
            return {"data": {"status": "TIMED-OUT", "defaultDatasetId": "data123", "usageTotalUsd": 0.013}}
        base = {
            "title": "Cabinet nouveau",
            "website": "https://new.example.com",
            "countryCode": "FR",
            "phone": "01 99 00 00 42",
            "url": "https://www.google.com/maps/place/example",
        }
        return [
            base,
            base,
            {**base, "website": "https://excluded.example.com"},
            {**base, "website": ""},
            {**base, "website": "https://test.example.com"},
            {**base, "website": "https://closed.example.com", "permanentlyClosed": True},
        ]

    monkeypatch.setattr(a, "request", request)
    assert a.tick() == 1
    with SessionLocal() as db:
        row = db.get(m.Job, job["id"])
        row.run_at = m.now() - timedelta(seconds=1)
        db.commit()
    assert a.tick() == 1
    assert a.tick() == 0
    with SessionLocal() as db:
        row = db.get(m.Job, job["id"])
        assert row.status == "partial"
        assert {
            k: row.result[k]
            for k in [
                "created",
                "existing",
                "duplicates",
                "excluded",
                "without_domain",
                "closed",
                "charged_usd_cents",
            ]
        } == {
            "created": 1,
            "existing": 1,
            "duplicates": 1,
            "excluded": 1,
            "without_domain": 1,
            "closed": 1,
            "charged_usd_cents": 2,
        }
        assert db.scalar(select(func.count()).select_from(m.Contact)) == 1
        assert db.get(m.Contact, flow["contact"]["id"]).phone == flow["contact"]["phone"]
        assert db.get(m.Account, excluded["id"]).lifecycle == "excluded"
        assert (
            db.scalar(select(m.Evidence).where(m.Evidence.field == "company_phone")).confidence
            == "unverified"
        )
        assert d.state(db).paused
    assert sum(method == "POST" for method, _, _ in calls) == 1


def test_uncertain_start_never_retries_a_billable_post(client, flow, monkeypatch):
    configured(client, monkeypatch)
    row = client.post("/api/acquisition/apify", json=payload(flow)).json()
    count = 0

    def fail(*args, **kwargs):
        nonlocal count
        count += 1
        raise providers.ProviderError("apify", "Network failure", uncertain=True)

    monkeypatch.setattr(a, "request", fail)
    a.tick()
    a.tick()
    with SessionLocal() as db:
        assert db.get(m.Job, row["id"]).status == "uncertain"
        assert a.spent(db) == 50
    assert count == 1


def test_immediate_batch_uses_remaining_credit_without_waiting_an_hour(client, flow, monkeypatch):
    configured(client, monkeypatch)
    first = client.post("/api/acquisition/apify", json=payload(flow)).json()
    with SessionLocal() as db:
        row = db.get(m.Job, first["id"])
        row.status, row.locked_at = "completed", m.now()
        row.result = {"charged_usd": 4.41, "charged_usd_cents": 441}
        db.commit()
    assert (
        client.post(
            "/api/acquisition/apify", json={**payload(flow), "start_now": True, "max_charge_usd_cents": 8}
        ).status_code
        == 422
    )
    body = {**payload(flow), "start_now": True, "max_charge_usd_cents": 59, "limit": 147}
    created = client.post("/api/acquisition/apify", json=body)
    assert created.status_code == 202
    assert client.post("/api/acquisition/apify", json=body).json()["id"] == created.json()["id"]
    calls = []

    def request(config, method, path, **kwargs):
        calls.append(kwargs)
        assert method == "POST"
        assert kwargs["params"]["maxTotalChargeUsd"] == 0.59
        return {"data": {"id": "immediate-run"}}

    monkeypatch.setattr(a, "request", request)
    monkeypatch.setattr(a, "usage", lambda config: {"used_usd": 4.41})
    assert a.tick() == 1
    with SessionLocal() as db:
        assert a.spent(db) == 500
        assert db.get(m.Job, created.json()["id"]).status == "running"
    assert len(calls) == 1
    assert (
        client.post(
            "/api/acquisition/apify", json={**body, "request_id": str(uuid4()), "max_charge_usd_cents": 50}
        ).status_code
        == 409
    )
