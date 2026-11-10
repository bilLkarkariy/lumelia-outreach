from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app import agent_actions as a
from app import models as m
from app.db import SessionLocal
from app.security import digest


def connected(client):
    credential = client.post("/api/agent/credentials").json()
    client.post("/api/agent/configure", json={"enabled": True})
    return {"Authorization": "Bearer " + credential["token"]}


def queued(client, flow, scope="existing"):
    response = client.post(
        "/api/agent/batches",
        json={
            "scope": scope,
            "campaign_id": flow["campaign"]["id"],
            "count": 5,
            "brief": "Entreprises françaises de conseil avec un dirigeant nommé sur le site officiel.",
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["action_ids"][0]


def claimed(client, headers):
    row = client.post("/api/agent/runtime/claim", headers=headers).json()
    assert row["action"]
    return row["action"], {**headers, "X-Agent-Lease": row["lease"]}


def proof(field="research", value="Contexte public de la société"):
    return {
        "field": field,
        "value": value,
        "source_url": "https://company.example.org/team",
        "excerpt": "Notre équipe accompagne les entreprises sur leurs projets de conseil.",
        "observed_at": m.now().isoformat(),
    }


def result(**extra):
    return {
        "outcome": "completed",
        "summary": "Recherche terminée sur le site officiel de l’entreprise.",
        "proofs": [proof()],
        "next_action": "Vérifier le suivi commercial existant avant toute prise de contact.",
        "request_id": str(uuid4()),
        **extra,
    }


def test_credentials_are_scoped_and_revocation_invalidates_lease(client, flow):
    headers = connected(client)
    queued(client, flow)
    row, leased = claimed(client, headers)
    client.cookies.clear()
    assert client.get("/api/contacts", headers=headers).status_code == 401
    assert (
        client.get("/api/agent/runtime/actions/" + row["id"] + "/context", headers=headers).status_code == 409
    )
    assert (
        client.get("/api/agent/runtime/actions/" + row["id"] + "/context", headers=leased).status_code == 200
    )
    with SessionLocal() as db:
        db.scalar(select(m.AgentClient)).token_hash = digest("replacement-token")
        db.commit()
    assert (
        client.get("/api/agent/runtime/actions/" + row["id"] + "/context", headers=leased).status_code == 401
    )


def test_exclusive_claim_completion_replay_and_historical_preservation(client, flow):
    headers = connected(client)
    queued(client, flow)
    row, lease = claimed(client, headers)
    assert client.post("/api/agent/runtime/claim", headers=headers).json()["action"] is None
    context = client.get("/api/agent/runtime/actions/" + row["id"] + "/context", headers=lease).json()
    assert context["offer"] and context["icp"] and "token_hash" not in str(context)
    payload = result(relevance="Dirigeante impliquée dans les propositions commerciales.")
    first = client.post("/api/agent/runtime/actions/" + row["id"] + "/result", headers=lease, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"
    assert (
        client.post(
            "/api/agent/runtime/actions/" + row["id"] + "/result", headers=lease, json=payload
        ).json()["id"]
        == row["id"]
    )
    contact = client.get("/api/contacts/" + flow["contact"]["id"]).json()
    assert contact["email"] == flow["contact"]["email"]
    assert contact["qualification"]["agent_review"]["next_action"]
    assert not client.get("/api/agent/notifications").json()["items"]


def test_expired_lease_requeues_pause_rejects_and_stale_record_blocks(client, flow):
    headers = connected(client)
    queued(client, flow)
    row, lease = claimed(client, headers)
    with SessionLocal() as db:
        db.get(m.AgentAction, row["id"]).lease_until = m.now() - timedelta(seconds=1)
        db.commit()
    assert (
        client.post(
            "/api/agent/runtime/actions/" + row["id"] + "/result", headers=lease, json=result()
        ).status_code
        == 409
    )
    a.tick()
    with SessionLocal() as db:
        action = db.get(m.AgentAction, row["id"])
        assert action.status == "retry_at"
        action.next_attempt_at = m.now()
        db.commit()
    row, lease = claimed(client, headers)
    client.patch("/api/contacts/" + flow["contact"]["id"], json={"role": "Nouveau rôle"})
    assert (
        client.post(
            "/api/agent/runtime/actions/" + row["id"] + "/result", headers=lease, json=result()
        ).status_code
        == 409
    )
    client.post("/api/agent/configure", json={"enabled": False})
    assert (
        client.post(
            "/api/agent/runtime/actions/" + row["id"] + "/result", headers=lease, json=result()
        ).status_code
        == 409
    )


def test_human_notification_is_deduplicated_and_resolution_acknowledges(client, flow):
    headers = connected(client)
    queued(client, flow)
    row, lease = claimed(client, headers)
    payload = result(
        outcome="waiting_human",
        human_category="private_access",
        human_reason="Connecter la boîte Outlook de test pour mesurer la réception.",
    )
    for _ in range(2):
        response = client.post(
            "/api/agent/runtime/actions/" + row["id"] + "/result", headers=lease, json=payload
        )
        assert response.status_code == 200, response.text
    notices = client.get("/api/agent/runtime/notifications", headers=headers).json()["items"]
    assert len(notices) == 1
    client.post(
        "/api/agent/runtime/notifications/" + notices[0]["id"] + "/delivered", headers=headers, json={}
    )
    assert not client.get("/api/agent/runtime/notifications", headers=headers).json()["items"]
    current = client.get("/api/agent/actions/" + row["id"]).json()
    assert (
        client.post(
            "/api/agent/actions/" + row["id"] + "/decision",
            json={
                "action": "resume",
                "note": "Accès corrigé, reprendre la tâche.",
                "expected_updated_at": current["updated_at"],
            },
        ).status_code
        == 200
    )
    assert not client.get("/api/agent/notifications").json()["items"]


def test_discovery_requires_identity_sources_and_preserves_unknown_email_status(client, flow):
    headers = connected(client)
    queued(client, flow, "discover")
    row, lease = claimed(client, headers)
    lead = {
        "company": "Conseil Nouveau",
        "domain": "conseil-nouveau.example.org",
        "industry": "Conseil B2B",
        "first_name": "Camille",
        "last_name": "Exemple",
        "role": "Présidente",
        "relevance": "Pilote les propositions commerciales et le suivi de projets de conseil B2B.",
        "next_action": "Vérifier le processus actuel de suivi des propositions.",
        "proofs": [proof("identity", "Camille Exemple"), proof("role", "Présidente")],
    }
    payload = result(leads=[lead])
    payload["leads"][0]["email"] = "camille@conseil-nouveau.example.org"
    assert (
        client.post(
            "/api/agent/runtime/actions/" + row["id"] + "/result", headers=lease, json=payload
        ).status_code
        == 422
    )
    payload["leads"][0]["proofs"].append(proof("email", "camille@conseil-nouveau.example.org"))
    response = client.post("/api/agent/runtime/actions/" + row["id"] + "/result", headers=lease, json=payload)
    assert response.status_code == 200, response.text
    contact = client.get("/api/contacts/" + response.json()["result"]["leads"][0]["id"]).json()
    assert (
        contact["email_status"] == "unknown"
        and not contact["approved"]
        and contact["qualification"]["agent_review"]["next_action"]
    )
    assert (
        client.post(
            "/api/agent/runtime/actions/" + row["id"] + "/result", headers=lease, json=payload
        ).status_code
        == 200
    )
    assert client.get("/api/contacts").json()["total"] == 2
    pending = client.get("/api/agent/actions?view=agent").json()["items"]
    remainder = [action for action in pending if action["dedupe_key"] == "discover-remaining:" + row["id"]]
    assert len(remainder) == 1 and remainder[0]["payload"]["limit"] == 4


def test_manual_calls_are_human_and_worker_sync_reconciles_completion(client, flow):
    headers = connected(client)
    with SessionLocal() as db:
        task_row = m.Task(
            contact_id=flow["contact"]["id"],
            campaign_id=flow["campaign"]["id"],
            channel="phone",
            title="Appeler le décideur",
            due_at=m.now(),
        )
        db.add(task_row)
        db.commit()
        task = {"id": task_row.id}
    client.post("/api/agent/runtime/claim", headers=headers)
    rows = client.get("/api/agent/actions?view=human").json()["items"]
    assert rows[0]["task_id"] == task["id"]
    assert rows[0]["executor"] == "human"
    with SessionLocal() as db:
        db.get(m.Task, task["id"]).status = "completed"
        db.commit()
    a.tick()
    assert not client.get("/api/agent/actions?view=human").json()["items"]
    assert not client.get("/api/agent/notifications").json()["items"]


def test_ordinary_reply_waits_for_policy_without_human_then_queues_once(client, flow, monkeypatch):
    from app.config import settings

    from .test_conversations import conversation_fixture

    event_id, _, _ = conversation_fixture(client, flow)
    headers = connected(client)
    row, lease = claimed(client, headers)
    assert row["kind"] == "reply_draft"
    response = client.post(
        "/api/agent/runtime/actions/" + row["id"] + "/result",
        headers=lease,
        json=result(
            reply_subject="Re: Les devis",
            reply_body="Bonjour, pouvez-vous préciser votre organisation actuelle pour le suivi des devis ?",
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    a.tick()
    assert not client.get("/api/agent/notifications").json()["items"]
    with SessionLocal() as db:
        action = db.scalar(select(m.AgentAction).where(m.AgentAction.kind == "send_reply"))
        assert action.status == "retry_at" and "pause" in action.last_error
        action.next_attempt_at = m.now()
        db.commit()
    monkeypatch.setattr(settings(), "live_actions_enabled", True)
    monkeypatch.setattr(settings(), "public_url", "https://example.org")
    client.patch("/api/state", json={"paused": False})
    a.tick()
    a.tick()
    with SessionLocal() as db:
        jobs = list(db.scalars(select(m.Job).where(m.Job.kind == "reply")))
        assert len(jobs) == 1 and jobs[0].status == "queued"
        assert db.get(m.Contact, flow["contact"]["id"]).paused
        assert db.get(m.Message, jobs[0].message_id).reply_event_id == event_id


def test_editing_agent_draft_cancels_automatic_send(client, flow):
    from .test_conversations import conversation_fixture

    conversation_fixture(client, flow)
    headers = connected(client)
    row, lease = claimed(client, headers)
    response = client.post(
        "/api/agent/runtime/actions/" + row["id"] + "/result",
        headers=lease,
        json=result(
            reply_subject="Re: Les devis", reply_body="Bonjour, quel est votre fonctionnement actuel ?"
        ),
    )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        draft = db.get(m.Message, response.json()["result"]["message_id"])
        draft.body = "Modification humaine à conserver."
        db.commit()
    a.tick()
    with SessionLocal() as db:
        action = db.scalar(select(m.AgentAction).where(m.AgentAction.kind == "send_reply"))
        assert action.status == "cancelled"
        assert not db.scalar(select(m.Job.id).where(m.Job.kind == "reply"))


def test_send_reply_action_reconciles_real_delivery_state_without_replaying_cancelled_history(client, flow):
    from .test_conversations import conversation_fixture

    conversation_fixture(client, flow)
    headers = connected(client)
    row, lease = claimed(client, headers)
    response = client.post(
        "/api/agent/runtime/actions/" + row["id"] + "/result",
        headers=lease,
        json=result(
            reply_subject="Re: Les devis",
            reply_body="Bonjour, pouvez-vous préciser votre processus actuel de validation ?",
        ),
    )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        action = db.scalar(select(m.AgentAction).where(m.AgentAction.kind == "send_reply"))
        message = db.get(m.Message, action.payload["message_id"])
        action.status, action.completed_at = "completed", m.now()
        action.result = {"summary": "Ancienne prise en charge considérée comme terminée."}
        message.status = "queued"
        db.scalar(select(m.AgentClient)).enabled = False
        action_id, message_id = action.id, message.id
        db.commit()

    a.tick()
    with SessionLocal() as db:
        action = db.get(m.AgentAction, action_id)
        assert action.status == "waiting_external" and action.completed_at is None
        assert db.get(m.Message, message_id).status == "queued"

        message = db.get(m.Message, message_id)
        message.status = "uncertain"
        message.failure_reason = "Résultat SMTP inconnu."
        db.commit()
    a.tick()
    human = client.get("/api/agent/actions?view=human").json()["items"]
    current = next(item for item in human if item["id"] == action_id)
    assert current["status"] == "uncertain"
    assert "aucun nouvel envoi" in current["human_reason"]
    assert client.get("/api/agent/notifications").json()["items"]
    assert (
        client.post(
            f"/api/agent/actions/{action_id}/decision",
            json={
                "action": "complete",
                "note": "Considérer la réponse comme envoyée.",
                "expected_updated_at": current["updated_at"],
            },
        ).status_code
        == 409
    )

    with SessionLocal() as db:
        message = db.get(m.Message, message_id)
        message.status, message.sent_at = "sent", m.now()
        db.commit()
    a.tick()
    with SessionLocal() as db:
        action = db.get(m.AgentAction, action_id)
        assert action.status == "completed" and action.result["sent_at"]
        action.status, action.completed_at = "cancelled", m.now()
        db.commit()
    a.tick()
    with SessionLocal() as db:
        assert db.get(m.AgentAction, action_id).status == "cancelled"


def test_review_action_records_examination_without_completing_business_task(client, flow):
    headers = connected(client)
    with SessionLocal() as db:
        task = m.Task(
            contact_id=flow["contact"]["id"],
            campaign_id=flow["campaign"]["id"],
            channel="review",
            title="Vérifier le dossier commercial",
            due_at=m.now(),
        )
        db.add(task)
        db.commit()
        task_id = task.id
    a.tick()
    row, lease = claimed(client, headers)
    assert row["kind"] == "review_task" and row["title"].startswith("Examiner : ")
    response = client.post(
        "/api/agent/runtime/actions/" + row["id"] + "/result",
        headers=lease,
        json=result(next_action="Appeler la dirigeante pour confirmer le circuit de validation."),
    )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        task = db.get(m.Task, task_id)
        assert task.status == "pending" and task.outcome == ""
        assert "Agent :" in task.notes and "Suite :" in task.notes

        # Reproduce the exact historical false completion made by this agent action.
        task.status, task.outcome = "completed", "done"
        db.commit()
    a.tick()
    with SessionLocal() as db:
        task = db.get(m.Task, task_id)
        assert task.status == "pending" and task.outcome == ""

    manual = client.post(
        f"/api/tasks/{task_id}/complete",
        json={"outcome": "done", "notes": "Examen confirmé manuellement."},
    )
    assert manual.status_code == 200, manual.text
    a.tick()
    with SessionLocal() as db:
        task = db.get(m.Task, task_id)
        assert task.status == "completed" and task.outcome == "done"
        assert db.scalar(
            select(m.Event.id).where(
                m.Event.kind == "task_completed",
                m.Event.data["task_id"].as_string() == task_id,
            )
        )


def test_quota_delay_survives_runner_heartbeat_and_missing_data_is_not_human(client, flow):
    headers = connected(client)
    row, lease = claimed(client, headers)
    endpoint = "/api/agent/runtime/actions/" + row["id"]
    response = client.post(
        endpoint + "/result",
        headers=lease,
        json=result(
            outcome="waiting_human", human_reason="Information publique non trouvée, vérifier la fiche."
        ),
    )
    assert response.status_code == 422
    client.post(
        endpoint + "/failure",
        headers=lease,
        json={"reason": "quota", "message": "Limite d’usage atteinte.", "retry_minutes": 60},
    )
    client.post("/api/agent/runtime/heartbeat", headers=headers, json={"status": "idle"})
    assert client.post("/api/agent/runtime/claim", headers=headers).json()["action"] is None
    status = client.get("/api/agent/status").json()
    assert status["client"]["state"]["runtime_status"] == "quota" and status["client"]["next_run_at"]
    assert not client.get("/api/agent/notifications").json()["items"]


def test_discovery_result_is_scrubbed_on_contact_erasure(client, flow):
    from app.privacy import erase

    with SessionLocal() as db:
        contact = db.get(m.Contact, flow["contact"]["id"])
        action = a.enqueue(
            db,
            "privacy-result",
            "discover_leads",
            "Recherche",
            result={"summary": "Alice Test trouvée", "leads": [{"id": contact.id, "name": "Alice Test"}]},
        )
        identifier = action.id
        db.commit()
        erase(db, contact)
        db.commit()
        db.expire_all()
        assert "Alice" not in str(db.get(m.AgentAction, identifier).result)
