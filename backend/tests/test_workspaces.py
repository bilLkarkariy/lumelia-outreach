from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app import models as m
from app.db import SessionLocal


def test_assignment_requires_new_review_and_preserves_history(client, flow):
    contact_id = flow["contact"]["id"]
    client.post("/api/batches/approve", json={"contact_ids": [contact_id]})
    client.post("/api/batches/prepare", json={"contact_ids": [contact_id]})
    campaign = client.post(
        "/api/campaigns",
        json={
            "name": "Autre sélection de recette",
            "offer_id": flow["campaign"]["offer_id"],
            "icp_id": flow["campaign"]["icp_id"],
            "sequence": flow["campaign"]["sequence"],
        },
    ).json()
    path = "/api/campaigns/" + campaign["id"] + "/contacts"
    assigned = client.post(path, json={"contact_ids": [contact_id, contact_id]})
    assert assigned.status_code == 200 and assigned.json()["assigned"] == [contact_id]
    row = client.get("/api/contacts/" + contact_id).json()
    assert row["campaign_id"] == campaign["id"] and not row["approved"] and row["status"] == "candidate"
    messages = client.get("/api/messages").json()
    assert messages and all(
        item["status"] == "cancelled" and item["campaign_id"] == flow["campaign"]["id"] for item in messages
    )
    assert client.post(path, json={"contact_ids": [contact_id]}).json()["assigned"] == []
    client.post(
        "/api/contacts/" + contact_id + "/events",
        json={"kind": "stop", "channel": "email", "summary": "Opposition fictive documentée."},
    )
    assert client.post(path, json={"contact_ids": [contact_id]}).status_code == 409


def review(row, decision="qualified", **extra):
    return {
        "decision": decision,
        "reason": "Revue fictive des preuves et de la cible.",
        "expected_updated_at": row["updated_at"],
        "request_id": str(uuid4()),
        **extra,
    }


def reply(client, flow, text="Une réponse reçue pour la recette"):
    response = client.post(
        f"/api/contacts/{flow['contact']['id']}/events",
        json={"kind": "reply", "channel": "email", "summary": text},
    )
    assert response.status_code == 200, response.text
    return response.json()["event"]


def handle(event, action="done", **extra):
    return {"action": action, "expected_updated_at": event["updated_at"], "request_id": str(uuid4()), **extra}


def test_account_workspace_sources_history_and_idempotent_notes(client, flow):
    aid = flow["account"]["id"]
    client.post(
        f"/api/contacts/{flow['contact']['id']}/evidence",
        json={"field": "Fonction", "value": "Directrice", "source_type": "manual"},
    )
    note = {"text": "Note de recette sur cette entreprise.", "request_id": str(uuid4())}
    first = client.post(f"/api/workspaces/accounts/{aid}/notes", json=note)
    again = client.post(f"/api/workspaces/accounts/{aid}/notes", json=note)
    assert first.status_code == 201 and first.json()["id"] == again.json()["id"]
    detail = client.get(f"/api/workspaces/accounts/{aid}").json()
    assert detail["contacts"][0]["id"] == flow["contact"]["id"]
    assert detail["evidence"][0]["field"] == "Fonction"
    assert "Effectif à vérifier" in detail["missing"]
    assert sum(e["summary"] == note["text"] for e in detail["events"]) == 1
    history = client.get(f"/api/workspaces/accounts/{aid}/events?page=999").json()
    assert history["total"] == detail["events_total"] and history["page"] == 1


def test_company_review_is_target_scoped_and_rejects_stale_data(client, flow):
    account, campaign = flow["account"], flow["campaign"]
    payload = review(account, "rejected", campaign_id=campaign["id"])
    url = f"/api/qualification/accounts/{account['id']}"
    assert client.post(url, json=payload).status_code == 200
    assert client.post(url, json=payload).json()["duplicate"]
    assert client.get("/api/qualification/queue", params={"campaign_id": campaign["id"]}).json()["total"] == 0
    assert client.get("/api/qualification/queue").json()["total"] == 1
    assert client.get(f"/api/accounts/{account['id']}").json()["lifecycle"] == "prospect"
    edit = {
        key: account[key]
        for key in [
            "name",
            "domain",
            "country",
            "industry",
            "employee_count",
            "hubspot_status",
            "lifecycle",
            "notes",
        ]
    }
    edit["industry"] = "Conseil"
    assert client.put(f"/api/accounts/{account['id']}", json=edit).status_code == 200
    assert client.get("/api/qualification/queue", params={"campaign_id": campaign["id"]}).json()["total"] == 1
    assert client.post(url, json=review(account)).status_code == 409


def test_contact_review_preserves_eligibility_and_records_reason(client, flow):
    row = client.get(f"/api/contacts/{flow['contact']['id']}").json()
    url = f"/api/qualification/contacts/{row['id']}"
    payload = review(row)
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    assert client.get(f"/api/contacts/{row['id']}").json()["approved"]
    assert client.get("/api/qualification/queue?resource=contacts").json()["total"] == 0
    assert client.post(url, json=payload).json()["duplicate"]
    changed = client.patch(f"/api/contacts/{row['id']}", json={"professional_relevance": ""}).json()
    assert client.post(url, json=review(changed)).status_code == 409
    assert not client.get(f"/api/contacts/{row['id']}").json()["approved"]


def test_rejected_contact_cancels_pending_without_opposition(client, flow):
    client.post("/api/batches/prepare", json={"contact_ids": [flow["contact"]["id"]]})
    row = client.get(f"/api/contacts/{flow['contact']['id']}").json()
    result = client.post(f"/api/qualification/contacts/{row['id']}", json=review(row, "rejected"))
    assert result.status_code == 200
    contact = client.get(f"/api/contacts/{row['id']}").json()
    assert contact["paused"] and not contact["approved"]
    assert all(msg["status"] == "cancelled" for msg in client.get("/api/messages").json())
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Suppression)) == 0


def test_campaign_workspace_and_scoped_lists_exclude_other_groups(client, flow):
    other = client.post(
        "/api/accounts", json={"name": "Autre entreprise", "domain": "other.example.org"}
    ).json()
    client.post(
        "/api/contacts", json={"account_id": other["id"], "first_name": "Autre", "last_name": "Contact"}
    )
    gid = flow["campaign"]["group_id"]
    detail = client.get("/api/workspaces/campaigns/" + gid).json()
    assert detail["metrics"]["contacts"] == 1 and detail["versions"][0]["readiness"]
    listed = client.get("/api/lists/contacts", params={"group_id": gid}).json()
    assert listed["total"] == 1 and listed["total_unfiltered"] == 1
    assert (
        client.get("/api/lists/contacts", params={"account_id": other["id"]}).json()["items"][0]["first_name"]
        == "Autre"
    )
    assert client.get("/api/lists/contacts?group_id=missing").status_code == 404


def test_inbox_positive_handling_is_idempotent_and_keeps_outreach_paused(client, flow):
    event = reply(client, flow)
    inbox = client.get("/api/inbox").json()
    assert inbox["total"] == 1 and inbox["items"][0]["contact"]["id"] == flow["contact"]["id"]
    payload = handle(event, classification="positive_reply", note="Intérêt confirmé par la réponse.")
    url = f"/api/inbox/{event['id']}/handle"
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    assert client.post(url, json=payload).json()["duplicate"]
    assert client.get("/api/inbox").json()["total"] == 0
    assert client.get("/api/inbox?status=done&kind=positive_reply").json()["total"] == 1
    assert client.get("/api/dashboard").json()["positive_replies"] == 1
    contact = client.get(f"/api/contacts/{flow['contact']['id']}").json()
    assert contact["paused"] and contact["account"]["outreach_paused"]
    with SessionLocal() as db:
        assert (
            db.scalar(select(m.Task).where(m.Task.dedupe_key == "event:" + event["id"])).status == "completed"
        )
        assert db.scalar(select(func.count()).select_from(m.Job)) == 0


def test_inbox_callback_is_dated_idempotent_and_reopenable(client, flow):
    event = reply(client, flow)
    url = f"/api/inbox/{event['id']}/handle"
    assert client.post(url, json=handle(event, "callback")).status_code == 422
    payload = handle(
        event,
        "callback",
        due_at=(m.now() + timedelta(days=2)).isoformat(),
        note="Rappel convenu avec le contact.",
    )
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    assert client.post(url, json=payload).json()["task_id"] == task_id
    detail = client.get("/api/inbox/" + event["id"]).json()
    assert detail["followup"]["channel"] == "review" and detail["followup"]["status"] == "pending"
    assert client.post(url, json=handle(detail, "reopen")).status_code == 200
    assert client.get("/api/inbox").json()["total"] == 1
    assert client.get(f"/api/contacts/{flow['contact']['id']}").json()["paused"]


def test_inbox_opportunity_and_stop_preserve_existing_guards(client, flow):
    event = reply(client, flow)
    response = client.post(
        f"/api/inbox/{event['id']}/handle",
        json=handle(event, "opportunity", title="Projet de recette", amount_cents=750000),
    )
    assert response.status_code == 200, response.text
    second = reply(client, flow, "Autre précision pour le même projet")
    same = client.post(
        f"/api/inbox/{second['id']}/handle", json=handle(second, "opportunity", title="Autre titre")
    )
    assert same.json()["opportunity_id"] == response.json()["opportunity_id"]
    assert len(client.get("/api/opportunities").json()) == 1
    third = reply(client, flow, "Ne me contactez plus")
    url = f"/api/inbox/{third['id']}/handle"
    assert client.post(url, json=handle(third, classification="stop")).status_code == 200
    current = client.get("/api/inbox/" + third["id"]).json()
    callback = client.post(
        url, json=handle(current, "callback", due_at=(m.now() + timedelta(days=1)).isoformat())
    )
    assert callback.status_code == 409
    assert client.get(f"/api/contacts/{flow['contact']['id']}").json()["status"] == "suppressed"


def test_existing_completed_review_tasks_appear_as_done(client, flow):
    event = reply(client, flow)
    with SessionLocal() as db:
        db.scalar(select(m.Task).where(m.Task.dedupe_key == "event:" + event["id"])).status = "completed"
        db.commit()
    assert client.get("/api/inbox").json()["total"] == 0
    assert client.get("/api/inbox?status=done").json()["total"] == 1
    assert client.get("/api/inbox?status=all&q=%").json()["total"] == 0


def test_workspace_auth_and_admin_boundary(client, flow):
    with SessionLocal() as db:
        db.scalar(select(m.User)).role = "operator"
        db.commit()
    assert client.get("/api/qualification/queue").status_code == 200
    assert (
        client.post(
            f"/api/qualification/contacts/{flow['contact']['id']}", json=review(flow["contact"])
        ).status_code
        == 403
    )
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anonymous:
        assert anonymous.get("/api/inbox").status_code == 401
        assert anonymous.get("/api/workspaces/accounts/" + flow["account"]["id"]).status_code == 401
