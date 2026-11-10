from uuid import uuid4

from app import models as m
from app.db import SessionLocal


def payload(row, **extra):
    return {
        "name": row["name"],
        "language": "fr",
        "sequence": [
            {
                "channel": "email",
                "day": 0,
                "subject": "Bonjour {{prenom}}",
                "body": "Une question pour {{societe}}.",
            },
            {"channel": "phone", "day": 2, "body": "Échanger avec {{prenom}}."},
        ],
        "expected_updated_at": row["updated_at"],
        "request_id": str(uuid4()),
        **extra,
    }


def test_preview_missing_variables_does_not_create_messages(client, flow):
    cid = flow["campaign"]["id"]
    response = client.post(
        f"/api/campaigns/{cid}/sequence/preview",
        json={
            "contact_id": flow["contact"]["id"],
            "sequence": [
                {
                    "channel": "email",
                    "day": 0,
                    "subject": "Bonjour {{prenom}}",
                    "body": "Pour {{societe}} et {{inconnue}}",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    step = response.json()["steps"][0]
    assert step["subject"] == "Bonjour Alice" and "Entreprise Test" in step["body"]
    assert step["missing"] == ["inconnue"] and response.json()["issues"]
    assert client.get("/api/messages").json() == []
    assert client.get("/api/campaigns").json()[0]["sequence"] == flow["campaign"]["sequence"]


def test_used_campaign_edit_creates_draft_preserving_originals(client, flow):
    cid = flow["campaign"]["id"]
    client.post("/api/batches/prepare", json={"contact_ids": [flow["contact"]["id"]]})
    messages = client.get("/api/messages").json()
    assert not client.get(f"/api/campaigns/{cid}/sequence").json()["edit_in_place"]
    request = payload(flow["campaign"])
    response = client.put(f"/api/campaigns/{cid}/sequence", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["saved_as"] == "new_version" and result["campaign"]["id"] != cid
    assert result["campaign"]["status"] == "draft" and not result["campaign"]["budget_approved"]
    assert result["campaign"]["config"]["parent_campaign_id"] == cid
    assert (
        client.put(f"/api/campaigns/{cid}/sequence", json=request).json()["campaign"]["id"]
        == result["campaign"]["id"]
    )
    assert client.get(f"/api/contacts/{flow['contact']['id']}").json()["campaign_id"] == cid
    assert client.get("/api/messages").json() == messages
    assert (
        client.get(f"/api/campaigns/{cid}/sequence").json()["campaign"]["sequence"]
        == flow["campaign"]["sequence"]
    )


def test_unused_draft_updates_in_place_and_rejects_stale_write(client, flow):
    with SessionLocal() as db:
        base = db.get(m.Campaign, flow["campaign"]["id"])
        row = m.Campaign(
            name="Séquence vierge",
            offer_id=base.offer_id,
            group_id=base.group_id,
            niche=base.niche,
            icp_id=base.icp_id,
            owner_id=base.owner_id,
            sequence=[],
        )
        db.add(row)
        db.commit()
        cid = row.id
    before = client.get(f"/api/campaigns/{cid}/sequence").json()
    assert before["edit_in_place"]
    response = client.put(f"/api/campaigns/{cid}/sequence", json=payload(before["campaign"]))
    assert response.status_code == 200, response.text
    assert response.json()["saved_as"] == "draft" and response.json()["campaign"]["id"] == cid
    assert client.put(f"/api/campaigns/{cid}/sequence", json=payload(before["campaign"])).status_code == 409


def test_invalid_sequence_does_not_publish_or_mutate(client, flow):
    cid = flow["campaign"]["id"]
    for steps in [
        [{"channel": "email", "day": 0, "subject": "", "body": "Message"}],
        [{"channel": "email", "day": 0, "subject": "Bonjour", "body": "Variable {{inconnue}}"}],
        [{"channel": "email", "day": 0, "subject": "Bonjour", "body": "Variable {{prenom}"}],
        [
            {"channel": "phone", "day": 5, "body": "Appeler"},
            {"channel": "email", "day": 2, "subject": "Bonjour", "body": "Message"},
        ],
        [{"channel": "email", "day": 0, "subject": "Bonjour", "body": "Notre tarif est disponible"}],
    ]:
        response = client.put(
            f"/api/campaigns/{cid}/sequence", json=payload(flow["campaign"], sequence=steps)
        )
        assert response.status_code == 422, response.text
    assert (
        client.put(
            f"/api/campaigns/{cid}/sequence", json=payload(flow["campaign"], language="en")
        ).status_code
        == 422
    )
    assert len(client.get("/api/campaigns").json()) == 1
