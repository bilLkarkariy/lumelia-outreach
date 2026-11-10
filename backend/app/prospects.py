"""Prospect priority and actual call/email history, without changing sending permissions."""

from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import and_, case, func, or_, select

from . import activity
from . import domain as d
from . import models as m
from . import schemas as s
from .db import get_db
from .security import admin, current_user

router = APIRouter(prefix="/api/prospects", tags=["prospects"])


def score_expression():
    def criterion(key, integer=False):
        field = m.ICP.criteria[key]
        return (
            select(field.as_integer() if integer else field.as_string())
            .join(m.Campaign, m.Campaign.icp_id == m.ICP.id)
            .where(m.Campaign.id == m.Contact.campaign_id)
            .correlate(m.Contact)
            .scalar_subquery()
        )

    country, low, high, crm = (
        criterion("country"),
        criterion("min_employees", True),
        criterion("max_employees", True),
        criterion("crm"),
    )
    country_set = and_(country.is_not(None), country != "")
    size_set = or_(low.is_not(None), high.is_not(None))
    crm_set = func.lower(crm) == "hubspot"
    role = and_(m.Contact.role != "", m.Contact.professional_relevance != "")
    total = 1 + case((country_set, 1), else_=0) + case((size_set, 1), else_=0) + case((crm_set, 1), else_=0)
    matches = case((role, 1), else_=0) + case((and_(country_set, m.Account.country == country), 1), else_=0)
    matches += case(
        (
            and_(
                size_set,
                m.Account.employee_count >= func.coalesce(low, 0),
                m.Account.employee_count <= func.coalesce(high, 10000000),
            ),
            1,
        ),
        else_=0,
    )
    matches += case((and_(crm_set, m.Account.hubspot_status == "confirmed"), 1), else_=0)
    fit = case((m.Contact.campaign_id.is_not(None), func.floor(60.0 * matches / total)), else_=0)
    phone = case(
        (and_(m.Contact.phone != "", m.Contact.phone_status == "appropriate"), 10),
        (and_(m.Contact.phone != "", m.Contact.phone_status != "invalid"), 5),
        else_=0,
    )
    email = case(
        (and_(m.Contact.email.is_not(None), m.Contact.email_status == "valid"), 15),
        (and_(m.Contact.email.is_not(None), m.Contact.email_status == "unknown"), 5),
        else_=0,
    )
    proof = (
        select(m.Evidence.id)
        .where(
            m.Evidence.account_id == m.Account.id,
            m.Evidence.confidence == "confirmed",
            m.Evidence.source_url != "",
            m.Evidence.observed_at >= m.now() - timedelta(days=90),
        )
        .correlate(m.Account)
        .exists()
    )
    return case(
        (or_(m.Contact.status == "suppressed", m.Account.lifecycle != "prospect"), 0),
        else_=fit + phone + email + case((proof, 15), else_=0),
    )


def last_call_expression():
    return (
        select(func.max(m.Event.created_at))
        .where(
            m.Event.contact_id == m.Contact.id,
            m.Event.channel == "phone",
            m.Event.kind.in_(["task_completed", "reply", "positive_reply", "meeting"]),
        )
        .correlate(m.Contact)
        .scalar_subquery()
    )


def last_email_expression():
    return (
        select(func.max(m.Message.sent_at))
        .where(m.Message.contact_id == m.Contact.id, m.Message.status == "sent")
        .correlate(m.Contact)
        .scalar_subquery()
    )


@router.get("/{contact_id}")
def detail(contact_id: str, page: int = Query(1, ge=1), user=Depends(current_user), db=Depends(get_db)):
    contact = d.get(db, m.Contact, contact_id)
    account = d.get(db, m.Account, contact.account_id)
    score, last_call, last_email = db.execute(
        select(score_expression(), last_call_expression(), last_email_expression())
        .select_from(m.Contact)
        .join(m.Account)
        .where(m.Contact.id == contact.id)
    ).one()
    events = list(
        db.scalars(
            select(m.Event)
            .where(
                m.Event.contact_id == contact.id,
                m.Event.kind.in_(
                    ["task_completed", "note", "reply", "positive_reply", "stop", "meeting", "bounce"]
                ),
            )
            .order_by(m.Event.created_at.desc())
            .limit(page * 20 + 1)
        )
    )
    messages = list(
        db.scalars(
            select(m.Message)
            .where(m.Message.contact_id == contact.id, m.Message.status == "sent")
            .order_by(m.Message.sent_at.desc())
            .limit(page * 20 + 1)
        )
    )
    timeline = [
        {
            "id": e.id,
            "at": e.created_at,
            "kind": "call" if e.channel == "phone" and e.kind == "task_completed" else e.kind,
            "channel": e.channel,
            "summary": e.summary,
            "outcome": e.data.get("outcome", ""),
        }
        for e in events
    ]
    timeline += [
        {
            "id": message.id,
            "at": message.sent_at or message.created_at,
            "kind": "email_sent",
            "channel": "email",
            "summary": message.subject,
            "outcome": "",
        }
        for message in messages
    ]
    timeline.sort(key=lambda row: (row["at"], row["id"]), reverse=True)
    task = db.scalar(
        select(m.Task)
        .where(m.Task.contact_id == contact.id, m.Task.status == "pending")
        .order_by(m.Task.due_at)
        .limit(1)
    )
    conversation = db.scalar(
        select(m.Event.id)
        .where(
            m.Event.contact_id == contact.id,
            m.Event.channel == "email",
            m.Event.kind.in_(["reply", "positive_reply"]),
        )
        .order_by(m.Event.created_at.desc())
        .limit(1)
    )
    return {
        **d.serialize(contact),
        "account": d.serialize(account),
        "fit": d.score_contact(db, contact),
        "score": int(score),
        "last_call_at": last_call,
        "last_email_at": last_email,
        "next_task": d.serialize(task),
        "conversation_id": conversation,
        "timeline": timeline[(page - 1) * 20 : page * 20],
        "has_more": len(timeline) > page * 20,
        "page": page,
    }


class CallLog(s.TaskComplete):
    request_id: UUID
    task_id: str | None = None
    occurred_at: datetime | None = None


@router.post("/{contact_id}/calls", status_code=201)
def log_call(contact_id: str, data: CallLog, user=Depends(current_user), db=Depends(get_db)):
    import hashlib
    import json

    fingerprint = hashlib.sha256(
        json.dumps(data.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    contact = db.scalar(select(m.Contact).where(m.Contact.id == contact_id).with_for_update())
    if not contact:
        raise HTTPException(404, "Prospect introuvable.")
    key = "call:" + contact.id + ":" + str(data.request_id)
    previous = db.scalar(select(m.Event).where(m.Event.external_id == key))
    if previous:
        if previous.data.get("request_hash") != fingerprint:
            raise HTTPException(
                409, "Cet appel a déjà été enregistré avec un autre résultat. Rechargez la fiche."
            )
        return {"event_id": previous.id, "recorded": True}
    at = data.occurred_at or m.now()
    if at.tzinfo is None or at > m.now() + timedelta(minutes=5):
        raise HTTPException(422, "Indiquez la date d’un appel déjà effectué avec son fuseau horaire.")
    if data.task_id:
        task = d.get(db, m.Task, data.task_id)
        if task.contact_id != contact.id or task.channel != "phone":
            raise HTTPException(409, "Cette tâche ne correspond pas à l’appel de ce prospect.")
    else:
        task = m.Task(
            contact_id=contact.id,
            campaign_id=contact.campaign_id,
            owner_id=user.id,
            title="Appel effectué",
            channel="phone",
            status="pending",
            due_at=at,
            dedupe_key=key,
        )
        db.add(task)
        db.flush()
    activity.complete(db, task.id, data, user.id)
    event = db.scalar(
        select(m.Event)
        .where(m.Event.kind == "task_completed", m.Event.data["task_id"].as_string() == task.id)
        .order_by(m.Event.created_at.desc())
        .limit(1)
    )
    event.external_id, event.created_at = key, at
    event.data = {**event.data, "request_hash": fingerprint}
    db.commit()
    return {"event_id": event.id, "recorded": True}


@router.get("/acquisition/status")
def acquisition_status(user=Depends(current_user), db=Depends(get_db)):
    actions = list(
        db.scalars(
            select(m.AgentAction)
            .where(m.AgentAction.kind == "discover_leads")
            .order_by(m.AgentAction.created_at.desc())
        )
    )
    imports = list(db.scalars(select(m.Job).where(m.Job.kind == "lead_import", m.Job.status == "completed")))
    client = db.scalar(select(m.AgentClient).limit(1))
    return {
        "imported": sum(len(row.result.get("leads", [])) for row in actions)
        + sum(row.result.get("created", 0) for row in imports),
        "pending": sum(row.status in {"queued", "retry_at"} for row in actions),
        "running": sum(row.status == "running" for row in actions),
        "next_at": client.state.get("next_scheduled_at") if client else None,
        "latest": [
            {"id": row.id, "count": len(row.result.get("leads", [])), "at": row.completed_at}
            for row in actions
            if row.status == "completed"
        ][:5],
    }


class LeadImport(s.Input):
    campaign_id: str
    request_id: UUID
    leads: list[dict] = Field(min_length=1, max_length=500)
    preview: bool = False


@router.post("/acquisition/imports")
def import_records(data: LeadImport, user=Depends(admin), db=Depends(get_db)):
    import hashlib
    import json
    from types import SimpleNamespace

    from pydantic import ValidationError

    from .agent_actions import Lead, import_leads

    d.state(db, lock=True)
    d.get(db, m.Campaign, data.campaign_id)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"campaign": data.campaign_id, "leads": data.leads}, sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()
    key = "lead-import:" + str(data.request_id)
    previous = db.scalar(select(m.Job).where(m.Job.idempotency_key == key))
    if previous:
        if previous.payload.get("fingerprint") != fingerprint:
            raise HTTPException(409, "Cet identifiant correspond à un autre import.")
        return previous.result
    try:
        leads = [Lead.model_validate(value) for value in data.leads]
    except ValidationError as error:
        raise HTTPException(422, "Import invalide : " + str(error)) from None
    records = import_leads(db, SimpleNamespace(campaign_id=data.campaign_id, payload={"limit": 500}), leads)
    result = {
        "created": len(records),
        "duplicates": len(leads) - len(records),
        "contact_ids": [row["id"] for row in records],
    }
    if data.preview:
        db.rollback()
        return {"created": result["created"], "duplicates": result["duplicates"], "preview": True}
    job = m.Job(
        kind="lead_import",
        provider="public_sources",
        status="completed",
        idempotency_key=key,
        payload={"fingerprint": fingerprint, "count": len(leads), "campaign_id": data.campaign_id},
        result=result,
    )
    db.add(job)
    d.log(
        db,
        "leads_imported",
        actor_id=user.id,
        summary=str(len(records)) + " prospects sourcés importés.",
        data={"import_id": key, "created": len(records)},
    )
    db.commit()
    return result
