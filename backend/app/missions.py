"""Persistent business objectives. Completion is derived from saved effects."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, model_validator
from sqlalchemy import func, select

from . import domain as d
from . import models as m
from . import schemas as s
from .db import SessionLocal, get_db
from .mission_models import Mission, MissionStep
from .security import admin, current_user, digest

router = APIRouter(prefix="/api/missions", tags=["missions"])
STEP_KINDS = (
    "qualify",
    "identify",
    "enrich",
    "prepare_call",
    "callback",
    "draft_email",
    "draft_campaign",
    "draft_sequence",
    "send_email",
    "assign_contact",
    "update_opportunity",
    "human_decision",
)
TERMINAL = {"completed", "cancelled", "not_fit", "unresolved"}


class CreateMission(s.Input):
    request_id: UUID
    title: str = Field(min_length=5, max_length=220)
    objective: str = Field(min_length=10, max_length=5000)
    campaign_id: str
    kind: Literal["prepare_prospects", "business"] = "prepare_prospects"
    target_count: int = Field(default=10, ge=1, le=1000)
    account_ids: list[str] = Field(default_factory=list, max_length=1000)
    due_at: datetime | None = None

    @model_validator(mode="after")
    def dates(self):
        if self.due_at and self.due_at.tzinfo is None:
            raise ValueError("Une échéance doit inclure son fuseau horaire")
        return self


class ChangeMission(s.Input):
    action: Literal["pause", "resume", "cancel"]
    expected_updated_at: datetime


class StepDecision(s.Input):
    action: Literal["complete", "cancel", "resume"]
    note: str = Field(min_length=5, max_length=3000)
    expected_updated_at: datetime


def view(db, mission, detail=False):
    result = d.serialize(mission)
    counts = dict(
        db.execute(
            select(MissionStep.status, func.count())
            .where(MissionStep.mission_id == mission.id)
            .group_by(MissionStep.status)
        ).all()
    )
    result["counts"] = counts
    if detail:
        result["steps"] = [
            d.serialize(x)
            for x in db.scalars(
                select(MissionStep)
                .where(MissionStep.mission_id == mission.id)
                .order_by(MissionStep.created_at, MissionStep.id)
            )
        ]
    return result


@router.get("")
def listing(
    campaign_id: str = "",
    group_id: str = "",
    account_id: str = "",
    page: int = Query(1, ge=1),
    user=Depends(current_user),
    db=Depends(get_db),
):
    query = select(Mission)
    if group_id:
        query = query.where(
            Mission.campaign_id.in_(select(m.Campaign.id).where(m.Campaign.group_id == group_id))
        )
    if campaign_id:
        query = query.where(Mission.campaign_id == campaign_id)
    if account_id:
        query = query.where(
            (Mission.account_id == account_id)
            | Mission.id.in_(select(MissionStep.mission_id).where(MissionStep.account_id == account_id))
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Mission.created_at.desc()).offset((page - 1) * 20).limit(20))
    return {"items": [view(db, r) for r in rows], "total": total, "page": page, "page_size": 20}


@router.post("", status_code=201)
def create(data: CreateMission, user=Depends(admin), db=Depends(get_db)):
    campaign = d.get(db, m.Campaign, data.campaign_id)
    key = "mission:" + str(data.request_id)
    fingerprint = digest(data.model_dump_json())
    prior = db.scalar(select(Mission).where(Mission.request_key == key))
    if prior:
        if prior.config.get("fingerprint") != fingerprint:
            raise HTTPException(409, "Cette demande existe avec un autre contenu")
        return view(db, prior, True)
    for aid in data.account_ids:
        d.get(db, m.Account, aid)
    mission = Mission(
        campaign_id=campaign.id,
        kind=data.kind,
        owner_id=user.id,
        title=data.title,
        objective=data.objective,
        target_count=data.target_count,
        due_at=data.due_at,
        request_key=key,
        config={
            "account_ids": list(dict.fromkeys(data.account_ids)),
            "fingerprint": fingerprint,
            "acceptance": "ready_to_call",
            "allow_sending": False,
        },
    )
    db.add(mission)
    db.flush()
    replenish(db, mission)
    d.log(
        db,
        "mission_created",
        actor_id=user.id,
        campaign_id=campaign.id,
        summary=mission.title,
        data={"mission_id": mission.id},
    )
    db.commit()
    return view(db, mission, True)


@router.get("/{mission_id}")
def detail(mission_id: str, user=Depends(current_user), db=Depends(get_db)):
    return view(db, d.get(db, Mission, mission_id), True)


@router.patch("/{mission_id}")
def change(mission_id: str, data: ChangeMission, user=Depends(current_user), db=Depends(get_db)):
    mission = d.get(db, Mission, mission_id)
    if mission.updated_at != data.expected_updated_at:
        raise HTTPException(409, "La mission a changé, rechargez-la")
    if mission.status == "completed":
        raise HTTPException(409, "Cette mission est déjà terminée")
    mission.status = {"pause": "paused", "resume": "active", "cancel": "cancelled"}[data.action]
    for step in db.scalars(
        select(MissionStep).where(MissionStep.mission_id == mission.id, MissionStep.status.notin_(TERMINAL))
    ):
        action = db.get(m.AgentAction, step.action_id) if step.action_id else None
        if (
            action
            and action.status in {"queued", "running", "retry_at", "waiting_human"}
            and data.action != "resume"
        ):
            action.status = "cancelled"
            action.lease_hash = ""
            action.lease_until = None
            step.action_id = None
        if data.action == "cancel":
            step.status = "cancelled"
        elif data.action == "resume" and step.status == "running":
            step.status = "pending"
    db.commit()
    return view(db, mission, True)


@router.post("/{mission_id}/steps/{step_id}/decision")
def decide(mission_id: str, step_id: str, data: StepDecision, user=Depends(current_user), db=Depends(get_db)):
    mission = d.get(db, Mission, mission_id)
    step = d.get(db, MissionStep, step_id)
    if step.mission_id != mission.id or step.updated_at != data.expected_updated_at:
        raise HTTPException(409, "L’action a changé, rechargez-la")
    if data.action == "complete" and (step.executor != "human" or step.kind == "callback"):
        raise HTTPException(409, "Consignez le résultat métier dans la fiche ou le parcours d’appel")
    step.status = {"complete": "completed", "cancel": "cancelled", "resume": "pending"}[data.action]
    step.result = {**step.result, "human_resolution": data.note, "actor_id": user.id}
    step.completed_at = m.now() if step.status in TERMINAL else None
    if data.action == "resume":
        step.action_id = None
        step.attempts = 0
    refresh(db, mission)
    db.commit()
    return view(db, mission, True)


def add_step(
    db,
    mission,
    key,
    kind,
    title,
    *,
    account_id=None,
    contact_id=None,
    dependencies=None,
    payload=None,
    due_at=None,
    executor="agent",
):
    previous = db.scalar(select(MissionStep).where(MissionStep.dedupe_key == key))
    if previous:
        return previous
    if kind not in STEP_KINDS:
        raise HTTPException(422, "Action métier inconnue")
    deps = dependencies or []
    for dep_id in deps:
        dep = db.get(MissionStep, dep_id)
        if not dep or dep.mission_id != mission.id:
            raise HTTPException(422, "Dépendance hors de la mission")
    step = MissionStep(
        mission_id=mission.id,
        account_id=account_id,
        contact_id=contact_id,
        kind=kind,
        title=title[:220],
        dependencies=deps,
        payload=payload or {},
        dedupe_key=key,
        due_at=due_at or m.now(),
        executor=executor,
    )
    db.add(step)
    db.flush()
    return step


def replenish(db, mission):
    if mission.kind != "prepare_prospects" or mission.status != "active":
        return
    from .prospect_pipeline import allowed, people, sync

    sync(db, mission.campaign_id)
    existing = set(db.scalars(select(MissionStep.account_id).where(MissionStep.mission_id == mission.id)))
    unfinished = (
        db.scalar(
            select(func.count())
            .select_from(MissionStep)
            .where(
                MissionStep.mission_id == mission.id,
                MissionStep.kind == "prepare_call",
                MissionStep.status.notin_(TERMINAL),
            )
        )
        or 0
    )
    missing = max(0, mission.target_count - mission.accepted_count - unfinished)
    if not missing:
        return
    query = (
        select(m.Account)
        .join(m.ProspectProfile)
        .where(
            m.Account.lifecycle == "prospect",
            m.Account.outreach_paused.is_(False),
            m.Account.id.notin_(existing),
        )
    )
    scope = mission.config.get("account_ids", [])
    if scope:
        query = query.where(m.Account.id.in_(scope))
    candidates = db.scalars(
        query.order_by(m.ProspectProfile.score.desc(), m.Account.created_at).limit(min(missing * 3, 100))
    )
    added = 0
    for account in candidates:
        if not allowed(db, account, people(db, account.id)):
            continue
        previous = []
        for kind, label in [
            ("qualify", "Qualifier"),
            ("identify", "Identifier l’interlocuteur"),
            ("enrich", "Compléter les coordonnées"),
            ("prepare_call", "Préparer l’appel"),
        ]:
            step = add_step(
                db,
                mission,
                f"{mission.id}:{account.id}:{kind}",
                kind,
                f"{label} · {account.name}",
                account_id=account.id,
                dependencies=previous,
            )
            previous = [step.id]
        added += 1
        if added >= missing:
            break
    if not added and not unfinished:
        mission.result = {
            **mission.result,
            "shortfall": missing,
            "next_action": "Compléter la collecte ou élargir la cible pour atteindre l’objectif.",
        }


def complete(step, result):
    step.status, step.completed_at, step.result, step.last_error = "completed", m.now(), result, ""


def refresh(db, mission):
    db.flush()
    query = select(MissionStep).where(MissionStep.mission_id == mission.id)
    steps = list(db.scalars(query))
    finals = (
        [s for s in steps if s.kind == "prepare_call"]
        if mission.kind == "prepare_prospects"
        else [s for s in steps if s.status != "cancelled"]
    )
    mission.accepted_count = sum(s.status == "completed" for s in finals)
    if mission.kind in {"post_call", "business"}:
        mission.target_count = len(finals)
    if mission.status == "active" and finals and mission.accepted_count >= mission.target_count:
        mission.status, mission.completed_at = "completed", m.now()
    mission.result = {
        **mission.result,
        "completed_steps": sum(s.status == "completed" for s in steps),
        "total_steps": len(steps),
        "shortfall": max(0, mission.target_count - mission.accepted_count),
    }


def run_step(db, mission, step):
    from .agent_actions import enqueue
    from .campaign_qualification import save
    from .prospect_pipeline import allowed, people, phone_for

    if step.account_id:
        account = db.get(m.Account, step.account_id)
        contacts = people(db, account.id) if account else []
        if not account or not allowed(db, account, contacts):
            step.status, step.last_error = "cancelled", "Entreprise exclue ou en pause"
            return
    else:
        account, contacts = None, []
    if step.kind == "callback":
        from .call_models import CallSession

        performed = db.scalar(
            select(CallSession)
            .where(
                CallSession.account_id == step.account_id,
                CallSession.data["mission_step_id"].as_string() == step.id,
                CallSession.status == "completed",
                CallSession.ended_at.is_not(None),
            )
            .order_by(CallSession.ended_at.desc())
            .limit(1)
        )
        if performed:
            complete(step, {"call_id": performed.id, "outcome": performed.outcome})
            if step.task_id:
                task = db.get(m.Task, step.task_id)
                if task and task.status != "completed":
                    task.status = "completed"
                    d.log(
                        db,
                        "task_completed",
                        actor_id=performed.created_by,
                        contact=db.get(m.Contact, step.contact_id) if step.contact_id else None,
                        campaign_id=mission.campaign_id,
                        channel="phone",
                        external_id="callback-done:" + step.id,
                        summary="Rappel effectué depuis l’espace d’appel",
                        data={"task_id": task.id, "call_id": performed.id, "account_id": step.account_id},
                    )
            if step.action_id:
                action = db.get(m.AgentAction, step.action_id)
                if action:
                    action.status, action.completed_at = "completed", m.now()
                    action.result = step.result
                    action.lease_hash, action.lease_until = "", None
            return
        if step.due_at > m.now():
            return
    if step.task_id:
        task = db.get(m.Task, step.task_id)
        if task and task.status == "completed":
            actual = db.scalar(
                select(m.Event.id).where(
                    m.Event.kind == "task_completed", m.Event.data["task_id"].as_string() == task.id
                )
            )
            if actual:
                complete(step, {"task_id": task.id, "event_id": actual})
                return
    if step.action_id and step.kind not in {"qualify", "identify", "enrich", "prepare_call"}:
        action = db.get(m.AgentAction, step.action_id)
        if action and action.status in {"queued", "running", "retry_at", "waiting_external"}:
            step.status = (
                "running"
                if action.status == "running"
                else "waiting_external"
                if action.status == "waiting_external"
                else "queued"
            )
            return
        if action and action.status in {"failed", "uncertain", "waiting_human"}:
            step.status, step.last_error = "waiting_human", action.human_reason or action.last_error
            return
        if action and action.status == "completed":
            if step.kind == "human_decision":
                complete(step, {"action_id": action.id, "resolution": action.result})
                return
            step.action_id = None
        elif action and action.status == "cancelled":
            step.status, step.last_error = "waiting_human", "Action annulée : vérifier la suite"
            return
    if step.kind in {"qualify", "identify", "enrich", "prepare_call"}:
        if not mission.campaign_id:
            step.status, step.last_error = (
                "waiting_data",
                "Associer une campagne pour qualifier cette entreprise",
            )
            return
        profile = db.get(m.ProspectProfile, account.id)
        assessment = save(db, account, profile, mission.campaign_id)
        if assessment.status in {"not_fit", "excluded"}:
            step.status = "not_fit"
            step.result = {"qualification_id": assessment.id, "reason": assessment.next_action}
            return
        found = next((c for c in contacts if c.first_name and c.last_name and c.role), None)
        phone, label = phone_for(db, account, contacts)
        if step.kind == "qualify" and assessment.status in {"ready", "qualified"}:
            complete(step, {"qualification_id": assessment.id, "score": assessment.score})
            return
        if step.kind == "identify" and found:
            step.contact_id = found.id
            complete(step, {"contact_id": found.id})
            return
        if step.kind == "enrich" and phone:
            complete(step, {"phone": phone, "phone_label": label})
            return
        if step.kind == "prepare_call" and phone and assessment.status == "ready":
            complete(
                step,
                {
                    "qualification_id": assessment.id,
                    "brief": assessment.call_brief,
                    "phone": phone,
                    "contact_id": found.id if found else None,
                },
            )
            return
        if not profile or not profile.checked_at:
            step.status, step.last_error = "waiting_data", "Lecture des sources publiques en cours"
            return
        previous_action = db.get(m.AgentAction, step.action_id) if step.action_id else None
        if previous_action and previous_action.status in {
            "queued",
            "running",
            "retry_at",
            "waiting_external",
        }:
            step.status = (
                "running"
                if previous_action.status == "running"
                else "waiting_external"
                if previous_action.status == "waiting_external"
                else "queued"
            )
            return
        if previous_action and previous_action.status in {"waiting_human", "failed", "uncertain"}:
            step.status, step.last_error = (
                "waiting_human",
                previous_action.human_reason or previous_action.last_error,
            )
            return
        if step.attempts >= 3:
            step.status = "unresolved"
            step.last_error = (
                "Informations insuffisantes après trois recherches ; une nouvelle source est nécessaire."
            )
            step.due_at = m.now() + timedelta(days=7)
            return
        action = db.scalar(
            select(m.AgentAction)
            .where(
                m.AgentAction.kind == "qualify_batch",
                m.AgentAction.status == "queued",
                m.AgentAction.payload["mission_id"].as_string() == mission.id,
            )
            .order_by(m.AgentAction.created_at.desc())
            .limit(1)
        )
        if action and len(action.payload.get("account_ids", [])) >= 10:
            action = None
        if not action:
            action = enqueue(
                db,
                "mission-batch:" + mission.id + ":" + m.uid(),
                "qualify_batch",
                "Qualifier les entreprises de la mission",
                campaign_id=mission.campaign_id,
                priority=25,
                target_url="/contacts",
                payload={
                    "mission_id": mission.id,
                    "account_ids": [],
                    "account_revisions": {},
                    "limit": 10,
                    "brief": mission.objective
                    + ". Réutiliser les faits et preuves existants. Chercher le décideur et les coordonnées professionnelles. "
                    + (
                        "Stratégie : site officiel et registre public."
                        if step.attempts == 0
                        else "Stratégie : annuaire professionnel et autre source officielle. Éviter de répéter les recherches infructueuses."
                    ),
                },
            )
        ids = list(action.payload.get("account_ids", []))
        if account.id not in ids:
            ids.append(account.id)
        action.payload = {
            **action.payload,
            "account_ids": ids,
            "account_revisions": {
                **action.payload.get("account_revisions", {}),
                account.id: account.updated_at.isoformat(),
            },
        }
    elif step.kind == "callback":
        if not step.task_id and step.contact_id:
            task = m.Task(
                contact_id=step.contact_id,
                campaign_id=mission.campaign_id,
                title=step.title,
                channel="phone",
                due_at=step.due_at,
                dedupe_key="callback:mission:" + step.id,
                notes=step.payload.get("reason", ""),
            )
            db.add(task)
            db.flush()
            step.task_id = task.id
        step.status, step.executor = "waiting_human", "human"
        action = enqueue(
            db,
            "mission-call:" + step.id,
            "manual_task",
            step.title,
            human_reason="Réaliser l’appel convenu et consigner son résultat.",
            account_id=step.account_id,
            contact_id=step.contact_id,
            campaign_id=mission.campaign_id,
            task_id=step.task_id,
            due_at=step.due_at,
            target_url="/calendar",
        )
    elif step.kind == "human_decision":
        step.status, step.executor = "waiting_human", "human"
        action = enqueue(
            db,
            "mission-human:" + step.id,
            "mission_decision",
            step.title,
            human_reason=step.payload.get("reason", "Décision commerciale nécessaire"),
            account_id=step.account_id,
            contact_id=step.contact_id,
            campaign_id=mission.campaign_id,
            target_url="/agent?view=human",
        )
    else:
        from .business_tools import execute_step

        action = execute_step(db, mission, step)
        if action is None:
            return
    step.action_id = action.id
    step.attempts += 1
    if step.executor != "human":
        step.status = "running" if action.status == "running" else "queued"


def tick():
    with SessionLocal() as db:
        d.state(db, lock=True)
        for mission in db.scalars(
            select(Mission).where(Mission.status == "active").order_by(Mission.created_at).limit(20)
        ):
            steps = list(db.scalars(select(MissionStep).where(MissionStep.mission_id == mission.id)))
            by_id = {s.id: s for s in steps}
            handled = 0
            for step in steps:
                if step.status in TERMINAL or (step.due_at > m.now() and step.kind != "callback"):
                    continue
                deps = [by_id.get(x) for x in step.dependencies]
                if any(dep and dep.status in {"cancelled", "not_fit", "unresolved"} for dep in deps):
                    step.status = (
                        "unresolved" if any(dep and dep.status == "unresolved" for dep in deps) else "not_fit"
                    )
                    continue
                if any(not dep or dep.status != "completed" for dep in deps):
                    continue
                before = (step.status, step.action_id, step.task_id, step.attempts, step.result)
                try:
                    with db.begin_nested():
                        run_step(db, mission, step)
                except HTTPException as exc:
                    step.status = "waiting_human"
                    step.last_error = str(exc.detail)[:1000]
                after = (step.status, step.action_id, step.task_id, step.attempts, step.result)
                handled += int(before != after)
                if handled >= 15:
                    break
            refresh(db, mission)
            replenish(db, mission)
        db.commit()


class CreateStep(s.Input):
    request_id: UUID
    kind: Literal[
        "qualify",
        "identify",
        "enrich",
        "prepare_call",
        "callback",
        "draft_campaign",
        "draft_sequence",
        "assign_contact",
        "update_opportunity",
        "human_decision",
    ]
    title: str = Field(min_length=5, max_length=220)
    reason: str = Field(min_length=10, max_length=3000)
    account_id: str
    contact_id: str | None = None
    due_at: datetime | None = None
    dependencies: list[str] = Field(default_factory=list, max_length=30)


@router.post("/{mission_id}/steps", status_code=201)
def create_step(mission_id: str, data: CreateStep, user=Depends(admin), db=Depends(get_db)):
    mission = d.get(db, Mission, mission_id)
    if mission.status != "active":
        raise HTTPException(409, "La mission doit être active")
    d.get(db, m.Account, data.account_id)
    if data.contact_id and d.get(db, m.Contact, data.contact_id).account_id != data.account_id:
        raise HTTPException(422, "Contact hors de cette entreprise")
    if data.due_at and not data.due_at.tzinfo:
        raise HTTPException(422, "Échéance avec fuseau horaire requise")
    step = add_step(
        db,
        mission,
        "manual-step:" + str(data.request_id),
        data.kind,
        data.title,
        account_id=data.account_id,
        contact_id=data.contact_id,
        due_at=data.due_at,
        dependencies=data.dependencies,
        payload={"reason": data.reason},
        executor="human" if data.kind in {"callback", "human_decision"} else "agent",
    )
    if step.mission_id != mission.id:
        raise HTTPException(409, "Demande déjà utilisée")
    refresh(db, mission)
    db.commit()
    return d.serialize(step)
