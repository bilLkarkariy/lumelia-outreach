from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app import missions
from app import models as m
from app.call_models import CallSession, CallTranscript
from app.call_reports import ReportInput, save
from app.db import SessionLocal
from app.mission_models import Mission, MissionStep


def call_fixture(db, flow):
    call = CallSession(
        account_id=flow["account"]["id"],
        contact_id=flow["contact"]["id"],
        campaign_id=flow["campaign"]["id"],
        created_by=db.scalar(select(m.User.id)),
        source="upload",
        status="completed",
        ended_at=m.now(),
        processing_status="transcribed",
    )
    db.add(call)
    db.flush()
    transcript = CallTranscript(
        call_id=call.id,
        version=1,
        status="ready",
        segments=[
            {
                "start": 5,
                "end": 12,
                "speaker": 1,
                "text": "Rappelez-moi vendredi. Envoyez-moi un récapitulatif.",
            },
            {"start": 15, "end": 19, "speaker": 1, "text": "Ne me contactez plus."},
        ],
    )
    db.add(transcript)
    db.flush()
    return call, transcript


def test_report_revision_preserves_effects_updates_pending_and_rejects_unproven_action(client, flow):
    with SessionLocal() as db:
        call, transcript = call_fixture(db, flow)
        data = ReportInput(
            summary="Un rappel et un email sont demandés.",
            actions=[
                {
                    "kind": "callback",
                    "title": "Rappeler vendredi",
                    "reason": "Engagement explicite",
                    "segment_index": 0,
                    "quote": "Rappelez-moi vendredi.",
                    "due_at": m.now() + timedelta(days=1),
                },
                {
                    "kind": "draft_email",
                    "title": "Préparer le récapitulatif",
                    "reason": "Demande explicite",
                    "segment_index": 0,
                    "quote": "Envoyez-moi un récapitulatif.",
                },
            ],
        )
        first = save(db, call, transcript, data, "report:first")
        draft = db.get(MissionStep, first.actions[1]["step_id"])
        missions.complete(draft, {"message_id": "existing-effect", "effect": "draft_saved"})
        new_due = m.now() + timedelta(days=2)
        data.actions[0].due_at = new_due
        second = save(db, call, transcript, data, "report:second")
        assert first.actions[0]["step_id"] == second.actions[0]["step_id"]
        assert db.get(MissionStep, second.actions[0]["step_id"]).due_at == new_due
        assert draft.result["message_id"] == "existing-effect" and draft.status == "completed"
        assert db.scalar(select(func.count()).select_from(MissionStep)) == 2
        assert second.version == 2
        data.actions[0].quote = "Une promesse qui ne figure pas dans cet appel."
        with pytest.raises(HTTPException):
            save(db, call, transcript, data, "report:unproven")
        db.rollback()


def test_ambiguous_commitment_requires_decision_and_opposition_cancels_work(client, flow):
    with SessionLocal() as db:
        call, transcript = call_fixture(db, flow)
        data = ReportInput(
            summary="Un rappel est demandé sans échéance certaine.",
            actions=[
                {
                    "kind": "callback",
                    "title": "Clarifier le rappel",
                    "reason": "Date ambiguë",
                    "segment_index": 0,
                    "quote": "Rappelez-moi vendredi.",
                }
            ],
        )
        first = save(db, call, transcript, data, "ambiguous")
        step = db.get(MissionStep, first.actions[0]["step_id"])
        assert step.kind == "human_decision" and step.executor == "human"
        data.outcome, data.outcome_segment_index, data.outcome_quote = "stop", 1, "Ne me contactez plus."
        save(db, call, transcript, data, "stop")
        assert db.get(m.Account, call.account_id).outreach_paused
        assert step.status == "cancelled"
        assert db.scalar(select(func.count()).select_from(m.Suppression)) > 0
        db.rollback()


def test_mission_retains_seven_results_on_resume(client, flow):
    body = {
        "request_id": str(uuid4()),
        "title": "Préparer dix prospects",
        "objective": "Dix entreprises avec un appel préparé",
        "campaign_id": flow["campaign"]["id"],
        "target_count": 10,
    }
    r = client.post("/api/missions", json=body)
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert client.post("/api/missions", json=body).json()["id"] == mid
    with SessionLocal() as db:
        mission = db.get(Mission, mid)
        for i in range(7):
            step = missions.add_step(
                db,
                mission,
                f"accepted:{i}",
                "prepare_call",
                f"Appel prêt {i}",
                account_id=flow["account"]["id"],
            )
            missions.complete(step, {"qualification_id": str(i), "phone": "+33199000042"})
        missions.refresh(db, mission)
        assert mission.accepted_count == 7 and mission.status == "active"
        db.commit()
    latest = client.get("/api/missions/" + mid).json()
    paused = client.patch(
        "/api/missions/" + mid, json={"action": "pause", "expected_updated_at": latest["updated_at"]}
    )
    resumed = client.patch(
        "/api/missions/" + mid, json={"action": "resume", "expected_updated_at": paused.json()["updated_at"]}
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["accepted_count"] == 7


def test_post_call_uses_independent_lane_when_research_budget_is_reserved(client, flow):
    from app import agent_budget

    from .test_agent_actions import connected

    connected(client)
    with SessionLocal() as db:
        agent = db.scalar(select(m.AgentClient))
        agent.state = {**agent.state, "per_task_tokens": 200000, "daily_tokens": 200000}
        research = m.AgentAction(kind="qualify_batch", dedupe_key="research-budget", title="Recherche")
        postcall = m.AgentAction(kind="post_call_report", dedupe_key="post-budget", title="Compte rendu")
        db.add_all([research, postcall])
        db.flush()
        assert agent_budget.reserve(db, agent, research)["per_task_tokens"] == 200000
        assert agent_budget.reserve(db, agent, research) is None
        result = agent_budget.reserve(db, agent, postcall)
        assert result["per_task_tokens"] is None and result["max_output_tokens"] is None
        assert result["max_turns"] is None
        usage = db.get(m.Job, result["run_id"])
        usage.status, usage.result = "completed", {"total_tokens": 350000}
        current = agent_budget.usage(db, agent)
        assert current["post_call_tokens"] == 350000
        assert current["reserved_tokens"] == 200000 and current["used_tokens"] == 0
        db.rollback()


def test_qualification_is_specific_to_campaign_without_universal_size_or_crm(client, flow):
    from app.campaign_qualification import save as qualify
    from app.prospect_pipeline import sync

    with SessionLocal() as db:
        sync(db, flow["campaign"]["id"])
        account = db.get(m.Account, flow["account"]["id"])
        account.employee_count = 5
        profile = db.get(m.ProspectProfile, account.id)
        profile.checked_at = m.now()
        profile.facts = {"b2b": "match"}
        first = qualify(db, account, profile, flow["campaign"]["id"])
        icp = m.ICP(name="Toute taille professionnelle", offer_id=flow["campaign"]["offer_id"], criteria={})
        db.add(icp)
        db.flush()
        second_campaign = m.Campaign(
            name="Autre offre", icp_id=icp.id, offer_id=icp.offer_id, owner_id=db.scalar(select(m.User.id))
        )
        db.add(second_campaign)
        db.flush()
        second = qualify(db, account, profile, second_campaign.id)
        assert first.status == "not_fit"
        assert second.status == "ready"
        assert not any(c["key"] in {"size", "hubspot"} for c in second.facts["checks"])
        assert first.campaign_id != second.campaign_id
        db.rollback()


def test_two_commitments_in_one_segment_are_distinct_and_call_closes_its_reminder(client, flow):
    with SessionLocal() as db:
        call, transcript = call_fixture(db, flow)
        data = ReportInput(
            summary="Deux rappels distincts ont été convenus.",
            actions=[
                {
                    "kind": "callback",
                    "title": "Premier rappel convenu",
                    "reason": "Premier engagement",
                    "segment_index": 0,
                    "quote": "Rappelez-moi vendredi.",
                    "due_at": m.now() - timedelta(minutes=2),
                },
                {
                    "kind": "callback",
                    "title": "Second rappel convenu",
                    "reason": "Second engagement",
                    "segment_index": 0,
                    "quote": "Rappelez-moi vendredi.",
                    "due_at": m.now() + timedelta(days=7),
                    "depends_on": [0],
                },
            ],
        )
        report = save(db, call, transcript, data, "two-commitments")
        assert len({a["step_id"] for a in report.actions}) == 2
        first, second = [db.get(MissionStep, a["step_id"]) for a in report.actions]
        assert second.dependencies == [first.id]
        mission = db.get(Mission, report.mission_id)
        missions.run_step(db, mission, first)
        assert db.get(m.Task, first.task_id).status == "pending"
        performed, _ = call_fixture(db, flow)
        performed.data = {"mission_step_id": first.id}
        db.flush()
        missions.run_step(db, mission, first)
        assert first.status == "completed"
        assert db.get(m.Task, first.task_id).status == "completed"
        assert db.get(m.AgentAction, first.action_id).status == "completed"
        db.rollback()


def test_removed_report_commitment_cancels_unsent_derived_email(client, flow):
    with SessionLocal() as db:
        call, transcript = call_fixture(db, flow)
        data = ReportInput(
            summary="Un compte rendu par email a été demandé.",
            actions=[
                {
                    "kind": "draft_email",
                    "title": "Envoyer le récapitulatif",
                    "reason": "Demande du contact",
                    "segment_index": 0,
                    "quote": "Envoyez-moi un récapitulatif.",
                }
            ],
        )
        report = save(db, call, transcript, data, "email-report")
        mission = db.get(Mission, report.mission_id)
        parent = db.get(MissionStep, report.actions[0]["step_id"])
        message = m.Message(
            contact_id=call.contact_id,
            campaign_id=call.campaign_id,
            dedupe_key="test-followup",
            subject="Récapitulatif",
            body="Résumé factuel.",
            status="queued",
            purpose="reply",
        )
        action = m.AgentAction(
            kind="send_reply", title="Envoyer le résumé", dedupe_key="test-send", status="queued"
        )
        db.add_all([message, action])
        db.flush()
        missions.complete(parent, {"message_id": message.id, "effect": "draft_saved"})
        child = missions.add_step(
            db,
            mission,
            "derived",
            "send_email",
            "Transmettre le résumé",
            dependencies=[parent.id],
            payload={"message_id": message.id},
        )
        child.action_id = action.id
        data.actions = []
        save(db, call, transcript, data, "withdrawn-email")
        assert message.status == "cancelled" and action.status == "cancelled"
        assert child.status == "cancelled" and parent.status == "completed"
        db.rollback()


def test_qualification_refresh_handles_external_control_text_and_reports_worker_recovery(client, flow):
    from app import campaign_qualification, prospect_pipeline, runtime_ops

    with SessionLocal() as db:
        prospect_pipeline.sync(db, flow["campaign"]["id"])
        profile = db.get(m.ProspectProfile, flow["account"]["id"])
        profile.facts = {"sources": [{"excerpt": "Texte externe\x00encodé"}]}
        profile.checked_at = m.now()
        db.commit()
        # PostgreSQL JSON text extraction must remain usable on the whole stored object.
        assert db.scalar(
            select(m.ProspectProfile.facts["sources"].as_string()).where(
                m.ProspectProfile.account_id == profile.account_id
            )
        )
    campaign_qualification.tick()
    for _ in range(3):
        runtime_ops.record_worker_health("test.operation", "AttributeError")
    with SessionLocal() as db:
        health = db.scalar(select(m.Job).where(m.Job.idempotency_key == "worker-health:test.operation"))
        aid = health.result["action_id"]
        assert health.status == "failed" and db.get(m.AgentAction, aid).status == "waiting_human"
    runtime_ops.record_worker_health("test.operation")
    with SessionLocal() as db:
        assert db.get(m.AgentAction, aid).result["health_restored"] is True
