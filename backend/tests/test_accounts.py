from fastapi.testclient import TestClient

from app.main import app


def test_company_without_contacts_has_reviewable_evidence_and_replay_does_not_duplicate(client):
    account = client.post(
        "/api/accounts", json={"name": "Conseil à revoir", "domain": "conseil.example.org"}
    ).json()
    path = f"/api/accounts/{account['id']}"
    evidence = {
        "field": "activité",
        "value": "Le site présente une offre de conseil pour les entreprises.",
        "source_type": "official_website",
        "source_url": "https://conseil.example.org/services",
        "confidence": "confirmed",
    }
    first = client.post(path + "/evidence", json=evidence)
    assert first.status_code == 201, first.text
    assert client.post(path + "/evidence", json=evidence).json()["id"] == first.json()["id"]
    detail = client.get(path).json()
    assert detail["contacts"] == []
    assert len(detail["evidence"]) == 1 and detail["evidence"][0]["source_url"] == evidence["source_url"]
    listed = client.get("/api/accounts").json()[0]
    assert listed["contact_count"] == 0 and listed["evidence_count"] == 1
    assert client.get("/api/contacts").json()["total"] == 0


def test_company_review_requires_auth_csrf_valid_company_and_safe_source(client, flow):
    path = f"/api/accounts/{flow['account']['id']}"
    evidence = {
        "field": "activité",
        "value": "Observation sourcée",
        "source_url": "https://example.org",
        "source_type": "official_website",
    }
    with TestClient(app) as anonymous:
        assert anonymous.get(path).status_code == 401
        assert anonymous.post(path + "/evidence", json=evidence).status_code == 401
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post(path + "/evidence", json=evidence).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.get("/api/accounts/missing").status_code == 404
    assert client.post("/api/accounts/missing/evidence", json=evidence).status_code == 404
    evidence["source_url"] = "javascript:alert(1)"
    assert client.post(path + "/evidence", json=evidence).status_code == 422
    detail = client.get(path).json()
    assert detail["contacts"][0]["id"] == flow["contact"]["id"]
    assert "email" not in detail["contacts"][0]
    assert client.get("/api/accounts").json()[0]["contact_count"] == 1
