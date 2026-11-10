import httpx
import pytest
import respx

from app import email_policy, mail
from app import models as m
from app.db import SessionLocal
from app.providers import ProviderError
from app.worker import tick
from tests.test_workflows import prepare


def test_catalogue_campaign_dependency_and_offer_usage(client, flow):
    catalogue = client.get("/api/offers").json()
    assert [o["status"] for o in catalogue] == ["primary", "network", "historical", "experimental"]
    control = catalogue[3]
    assert [p["min_cents"] for p in control["pricing"]] == [200000, 300000, 40000]
    icps = client.get("/api/icps").json()
    control_icp = next(i for i in icps if i["offer_id"] == control["id"])
    base = {"name": "Campagne contrôle", "offer_id": control["id"], "icp_id": control_icp["id"]}
    assert (
        client.post("/api/campaigns", json={k: v for k, v in base.items() if k != "offer_id"}).status_code
        == 422
    )
    assert client.post("/api/campaigns", json={**base, "offer_id": "unknown"}).status_code == 422
    assert (
        client.post("/api/campaigns", json={**base, "icp_id": flow["campaign"]["icp_id"]}).status_code == 422
    )
    created = client.post("/api/campaigns", json=base)
    assert created.status_code == 201
    assert created.json()["sequence"] == [] and created.json()["status"] == "draft"
    assert client.post("/api/campaigns/" + created.json()["id"] + "/activate").status_code == 422
    assert (
        client.post(
            "/api/campaigns/" + flow["campaign"]["id"] + "/versions",
            json={**base, "icp_id": flow["campaign"]["icp_id"]},
        ).status_code
        == 422
    )
    network = next(i for i in icps if i["offer_id"] == catalogue[1]["id"])
    payload = {"name": "Réseau", "offer_id": network["offer_id"], "icp_id": network["id"]}
    assert client.post("/api/campaigns", json=payload).status_code == 422
    assert client.post("/api/campaigns", json={**payload, "purpose": "network"}).status_code == 201
    historical = client.post("/api/icps", json={"name": "Historique", "offer_id": catalogue[2]["id"]}).json()
    assert (
        client.post(
            "/api/campaigns", json={**base, "icp_id": historical["id"], "offer_id": catalogue[2]["id"]}
        ).status_code
        == 422
    )
    assert client.post("/api/icps", json={"name": "Sans offre"}).status_code == 422
    assert client.get("/api/contacts").json()["total"] == 1
    client.post("/api/auth/logout")
    assert client.get("/api/offers").status_code == 401


def test_control_target_does_not_inherit_hubspot_or_employee_filters(client, flow):
    icp = next(i for i in client.get("/api/icps").json() if i["offer_id"] == "automation-audit-v1")
    campaign = client.post(
        "/api/campaigns", json={"name": "Contrôle", "offer_id": icp["offer_id"], "icp_id": icp["id"]}
    ).json()
    assert (
        client.patch(
            "/api/contacts/" + flow["contact"]["id"], json={"campaign_id": campaign["id"]}
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        account = db.get(m.Account, flow["account"]["id"])
        account.hubspot_status, account.employee_count = "absent", 500
        db.commit()
    result = client.get("/api/contacts/" + flow["contact"]["id"]).json()
    assert {check["criterion"] for check in result["fit"]["checks"]} == {"Pays", "Rôle et pertinence"}
    opportunity = client.post(
        "/api/opportunities", json={"contact_id": flow["contact"]["id"], "title": "Audit"}
    )
    assert opportunity.status_code == 201 and opportunity.json()["amount_cents"] == 200000


@pytest.mark.parametrize(
    "copy",
    ["7 500 €", "7500 HT", "EUR 2,900", "sept mille euros", "Notre tarif", "Prix : 2900", "1 490 / mois"],
)
def test_price_guard_normalizes_common_formats(copy):
    assert email_policy.contains_price(copy)
    assert not email_policy.contains_price(
        "10 jours, 3 automatisations. Réunion le 15/09. +33 1 99 00 00 42."
    )


def test_price_guard_rejects_templates_rendered_fields_and_manual_edits(client, flow):
    campaign = flow["campaign"]
    payload = {
        "name": "Prix refusé",
        "offer_id": campaign["offer_id"],
        "icp_id": campaign["icp_id"],
        "sequence": [{"channel": "email", "day": 0, "subject": "Bonjour", "body": "Audit à 2 900 €"}],
    }
    assert client.post("/api/campaigns", json=payload).status_code == 422
    assert client.post("/api/campaigns/" + campaign["id"] + "/versions", json=payload).status_code == 422
    with SessionLocal() as db:
        db.get(m.Campaign, campaign["id"]).sequence = [
            {"channel": "email", "day": 0, "subject": "Suivi", "body": "{{pertinence}}"}
        ]
        db.get(m.Contact, flow["contact"]["id"]).professional_relevance = "2 900 EUR"
        db.commit()
    assert (
        client.post("/api/batches/prepare", json={"contact_ids": [flow["contact"]["id"]]}).status_code == 422
    )
    assert client.get("/api/messages").json() == []
    with SessionLocal() as db:
        db.get(m.Contact, flow["contact"]["id"]).professional_relevance = "Suivi des devis"
        db.commit()
    assert (
        client.post("/api/batches/prepare", json={"contact_ids": [flow["contact"]["id"]]}).status_code == 200
    )
    message = client.get("/api/messages").json()[0]
    assert (
        client.patch(
            "/api/messages/" + message["id"], json={"subject": "7 500 €", "body": "Bonjour"}
        ).status_code
        == 422
    )
    with SessionLocal() as db:
        db.get(m.Message, message["id"]).body = "Audit : 2 900 €"
        db.commit()
    assert client.post("/api/messages/" + message["id"] + "/approve").status_code == 422


def test_price_guard_blocks_previously_queued_copy_and_smtp_footer(client, flow, monkeypatch):
    message = prepare(client, flow)
    with SessionLocal() as db:
        row = db.get(m.Message, message["id"])
        row.body = "7 500 €"
        db.commit()
    client.patch("/api/state", json={"paused": False})
    assert tick() == 1
    result = client.get("/api/messages").json()[0]
    assert result["status"] == "cancelled" and result["attempted_at"] is None
    with SessionLocal() as db:
        row = db.get(m.Message, message["id"])
        row.body = "Bonjour"
        contact = db.get(m.Contact, flow["contact"]["id"])

        def no_smtp(config):
            pytest.fail("SMTP must never open for rejected copy")

        monkeypatch.setattr(mail, "connect_smtp", no_smtp)
        with pytest.raises(ProviderError, match="prix"):
            mail.send(
                row,
                contact,
                {
                    "legal_footer": "Audit : 2 900 €",
                    "privacy_url": "https://example.org/privacy",
                    "from_name": "Lumelia",
                },
            )


@respx.mock
def test_fullenrich_balance_check_is_read_only_and_keeps_no_secret(client):
    client.post("/api/integrations/fullenrich", json={"config": {"api_key": "private-fullenrich"}})
    key = respx.get("https://app.fullenrich.com/api/v2/account/keys/verify").mock(
        return_value=httpx.Response(200, json={})
    )
    balance = respx.get("https://app.fullenrich.com/api/v2/account/credits").mock(
        return_value=httpx.Response(200, json={"balance": 50, "owner": "private-owner"})
    )
    result = client.post("/api/integrations/fullenrich/check")
    assert result.json()["state"]["quota"]["credits"] == 50
    assert "private-" not in result.text
    assert key.call_count == balance.call_count == 1
    assert client.get("/api/jobs").json() == []
