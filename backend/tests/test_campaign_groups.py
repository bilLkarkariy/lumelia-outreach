from copy import deepcopy

from app import models as m
from app.db import SessionLocal


def group(client, name="Comparaison des niches", offer_id="pipeline-review-v1"):
    response = client.post("/api/campaign-groups", json={"name": name, "offer_id": offer_id})
    assert response.status_code == 201, response.text
    return response.json()


def niche(client, parent, label="Agences", minimum=20, maximum=100):
    payload = {
        "name": label + " — première approche",
        "group_id": parent["id"],
        "offer_id": parent["offer_id"],
        "niche": label,
        "budget_cents": 1000,
        "target_profile": {
            "name": label,
            "offer_id": parent["offer_id"],
            "criteria": {"country": "FR", "niche": label, "min_employees": minimum, "max_employees": maximum},
        },
        "sequence": [{"channel": "email", "day": 0, "subject": label, "body": "Recette fictive"}],
    }
    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 201, response.text
    return response.json(), payload


def test_subcampaigns_in_same_niche_have_independent_targets_versions_and_budgets(client):
    parent = group(client)
    agencies, payload = niche(client, parent)
    consultants, _ = niche(client, parent, "Agences", 100, 200)
    assert consultants["niche"] == agencies["niche"]
    assert agencies["icp_id"] != consultants["icp_id"]
    profiles = {row["id"]: row for row in client.get("/api/icps").json()}
    assert profiles[agencies["icp_id"]]["criteria"]["max_employees"] == 100
    assert profiles[consultants["icp_id"]]["criteria"]["max_employees"] == 200
    # A new version can refine one niche without changing its siblings or the previous version.
    updated = deepcopy(payload)
    updated["target_profile"]["criteria"]["max_employees"] = 60
    updated["budget_cents"] = 2000
    response = client.post(f"/api/campaigns/{agencies['id']}/versions", json=updated)
    assert response.status_code == 201
    version = response.json()
    assert version["group_id"] == parent["id"] and version["niche"] == "Agences"
    assert version["status"] == "draft" and not version["budget_approved"]
    rows = {row["id"]: row for row in client.get("/api/campaigns").json()}
    assert rows[version["id"]]["family_id"] == agencies["id"]
    assert rows[consultants["id"]]["family_id"] == consultants["id"]
    assert rows[agencies["id"]]["budget_cents"] == rows[consultants["id"]]["budget_cents"] == 1000
    assert client.get("/api/contacts").json()["total"] == 0
    assert client.get("/api/messages").json() == []


def test_niche_rejects_wrong_offer_and_rolls_back_profile_creation(client):
    parent = group(client)
    _, payload = niche(client, parent)
    before = len(client.get("/api/icps").json())
    payload["offer_id"] = "automation-audit-v1"
    payload["target_profile"]["offer_id"] = payload["offer_id"]
    assert client.post("/api/campaigns", json=payload).status_code == 422
    assert len(client.get("/api/icps").json()) == before
    payload["offer_id"] = parent["offer_id"]
    assert client.post("/api/campaigns", json=payload).status_code == 422
    payload["target_profile"]["offer_id"] = parent["offer_id"]
    payload["niche"] = "  "
    assert client.post("/api/campaigns", json=payload).status_code == 422
    assert (
        client.patch(
            f"/api/campaign-groups/{parent['id']}",
            json={
                "name": "Autre offre",
                "offer_id": "automation-audit-v1",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/campaign-groups",
            json={
                "name": "Archivée",
                "offer_id": "inbound-desk-v1",
            },
        ).status_code
        == 422
    )


def test_pausing_group_leaves_other_campaigns_and_drafts_unchanged(client):
    first, second = group(client, "Premier"), group(client, "Second")
    a, _ = niche(client, first)
    b, _ = niche(client, first, "Conseil")
    c, _ = niche(client, second, "Informatique")
    for row in [a, c]:
        assert client.post(f"/api/campaigns/{row['id']}/activate").status_code == 200
    assert client.post(f"/api/campaign-groups/{first['id']}/pause").json()["paused_subcampaigns"] == 1
    rows = {row["id"]: row for row in client.get("/api/campaigns").json()}
    assert rows[a["id"]]["status"] == "paused"
    assert rows[b["id"]]["status"] == "draft"
    assert rows[c["id"]]["status"] == "active"


def test_results_aggregate_niches_without_double_counting_customer(client, flow):
    parent, empty = group(client), group(client, "Vide")
    a, _ = niche(client, parent)
    b, _ = niche(client, parent, "Conseil")
    key = flow["contact"]["id"]
    assert client.patch(f"/api/contacts/{key}", json={"campaign_id": a["id"]}).status_code == 200
    second = client.post(
        "/api/contacts",
        json={
            **flow["payload"],
            "first_name": "Bob",
            "email": "bob@example.com",
            "campaign_id": b["id"],
        },
    ).json()
    # One company can have historical results in multiple niches; its win counts once in the group.
    with SessionLocal() as db:
        for campaign, contact in [(a, key), (b, second["id"])]:
            db.add(
                m.Opportunity(
                    account_id=flow["account"]["id"],
                    contact_id=contact,
                    campaign_id=campaign["id"],
                    title="Recette",
                    owner_id=flow["contact"]["owner_id"],
                    stage="won",
                )
            )
            db.add(
                m.Expense(
                    campaign_id=campaign["id"],
                    category="software",
                    amount_cents=1000,
                    status="spent",
                    note="Recette",
                )
            )
        db.commit()
    totals = client.get("/api/dashboard", params={"group_id": parent["id"]}).json()
    assert totals["contacts"] == 2 and totals["customers_won"] == 1
    assert totals["cost_cents"] == 2000 and totals["cost_per_customer_cents"] == 2000
    isolated = client.get("/api/dashboard", params={"campaign_id": a["id"]}).json()
    assert isolated["contacts"] == 1 and isolated["cost_cents"] == 1000
    assert client.get("/api/dashboard", params={"group_id": empty["id"]}).json()["contacts"] == 0
    assert client.get("/api/dashboard", params={"group_id": "missing"}).status_code == 404
    assert (
        client.get("/api/dashboard", params={"group_id": parent["id"], "campaign_id": a["id"]}).status_code
        == 422
    )
    summary = next(row for row in client.get("/api/campaign-groups").json() if row["id"] == parent["id"])
    assert summary["metrics"]["companies"] == 1 and summary["metrics"]["customers_won"] == 1


def test_niches_share_company_suppression_and_single_decider_rule(client, flow):
    parent = group(client)
    a, _ = niche(client, parent)
    b, _ = niche(client, parent, "Conseil")
    key = flow["contact"]["id"]
    assert client.patch(f"/api/contacts/{key}", json={"campaign_id": a["id"]}).status_code == 200
    second = client.post(
        "/api/contacts",
        json={
            **flow["payload"],
            "first_name": "Bob",
            "email": "bob@example.com",
            "campaign_id": b["id"],
        },
    ).json()
    for contact in [key, second["id"]]:
        assert client.post("/api/batches/prepare", json={"contact_ids": [contact]}).status_code == 200
    assert client.post("/api/batches/approve", json={"contact_ids": [key]}).status_code == 200
    assert client.post("/api/batches/approve", json={"contact_ids": [second["id"]]}).status_code == 409
    assert (
        client.post(
            f"/api/contacts/{key}/events", json={"kind": "reply", "summary": "Échange fictif"}
        ).status_code
        == 200
    )
    assert all(row["status"] == "cancelled" for row in client.get("/api/messages").json())
    assert client.get(f"/api/contacts/{second['id']}").json()["account"]["outreach_paused"]
