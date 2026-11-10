from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app import models as m
from app.db import SessionLocal

from .test_agent_actions import proof


def test_priority_is_sorted_for_all_rows_and_keeps_unknown_facts_unknown(client, flow):
    cid = flow["contact"]["id"]
    response = client.get("/api/lists/contacts?sort=score&direction=desc&focus=active&page_size=10")
    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    assert 0 <= row["score"] <= 100
    assert row["last_call_at"] is None and row["last_email_at"] is None
    detail = client.get("/api/prospects/" + cid)
    assert detail.status_code == 200, detail.text
    assert detail.json()["score"] == row["score"]
    with SessionLocal() as db:
        db.get(m.Account, flow["account"]["id"]).lifecycle = "excluded"
        db.commit()
    assert client.get("/api/lists/contacts?focus=active").json()["total"] == 0
    assert client.get("/api/prospects/" + cid).json()["score"] == 0


def test_call_and_callback_are_recorded_once_without_sending_or_resuming(client, flow):
    cid = flow["contact"]["id"]
    state = client.get("/api/state").json()
    body = {
        "request_id": str(uuid4()),
        "outcome": "callback",
        "notes": "Rappeler demain.",
        "next_at": (m.now() + timedelta(days=1)).isoformat(),
    }
    first = client.post("/api/prospects/" + cid + "/calls", json=body)
    assert first.status_code == 201, first.text
    assert client.post("/api/prospects/" + cid + "/calls", json=body).json() == first.json()
    assert (
        client.post("/api/prospects/" + cid + "/calls", json={**body, "outcome": "stop"}).status_code == 409
    )
    row = client.get("/api/prospects/" + cid).json()
    assert row["last_call_at"] and row["last_email_at"] is None and row["paused"]
    assert len([e for e in row["timeline"] if e["kind"] == "call"]) == 1
    assert datetime.fromisoformat(row["next_task"]["due_at"]) == datetime.fromisoformat(body["next_at"])
    assert row["next_task"]["channel"] == "phone"
    from app import agent_actions

    with SessionLocal() as db:
        agent_actions.synchronize(db)
        db.commit()
        action = db.scalar(select(m.AgentAction).where(m.AgentAction.task_id == row["next_task"]["id"]))
        assert action.status == "waiting_human" and action.kind == "manual_task"
    assert client.get("/api/state").json()["paused"] == state["paused"]


def test_sourced_import_preview_and_replay_preserve_data(client, flow):
    lead = {
        "company": "Cabinet Exemple",
        "domain": "cabinet-import.example.org",
        "industry": "Conseil B2B",
        "first_name": "Camille",
        "last_name": "Exemple",
        "role": "Directrice",
        "phone": "+33199000042",
        "phone_type": "switchboard",
        "relevance": "Dirige les missions de conseil et les propositions commerciales B2B.",
        "next_action": "Vérifier le besoin et l’usage du CRM.",
        "proofs": [
            proof("identity", "Camille Exemple"),
            proof("role", "Directrice"),
            proof("phone", "+33199000042"),
        ],
    }
    body = {"campaign_id": flow["campaign"]["id"], "request_id": str(uuid4()), "leads": [lead]}
    before = client.get("/api/contacts").json()["total"]
    preview = client.post("/api/prospects/acquisition/imports", json={**body, "preview": True})
    assert preview.status_code == 200, preview.text
    assert preview.json()["created"] == 1 and client.get("/api/contacts").json()["total"] == before
    result = client.post("/api/prospects/acquisition/imports", json=body)
    assert result.status_code == 200, result.text
    assert client.post("/api/prospects/acquisition/imports", json=body).json() == result.json()
    assert client.get("/api/contacts").json()["total"] == before + 1
    row = client.get("/api/contacts/" + result.json()["contact_ids"][0]).json()
    assert row["phone_type"] == "switchboard" and row["email"] is None and not row["approved"]
    assert client.get("/api/prospects/acquisition/status").json()["imported"] == 1
