"""Central review of received conversations; handling never resumes outreach."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from . import domain as d
from . import models as m
from . import schemas as s
from .db import get_db
from .security import current_user
from .workspaces import page_result

router = APIRouter(prefix="/api/inbox", tags=["inbox"])
KINDS = {"reply", "positive_reply", "out_of_office", "bounce", "stop"}
Classification = Literal["reply", "positive_reply", "negative_reply", "out_of_office", "bounce", "stop"]


def state_expression():
    task_status = (
        select(m.Task.status)
        .where(m.Task.dedupe_key == "event:" + m.Event.id)
        .correlate(m.Event)
        .scalar_subquery()
    )
    return func.coalesce(
        m.Event.data["inbox"]["status"].as_string(),
        case((task_status.in_(["completed", "cancelled"]), "done"), else_="open"),
    )


def inbox_state(db, event):
    stored = event.data.get("inbox", {})
    if stored.get("status") in {"open", "done"}:
        return stored["status"]
    task = db.scalar(select(m.Task).where(m.Task.dedupe_key == "event:" + event.id))
    return "done" if task and task.status in {"completed", "cancelled"} else "open"


def get_event(db, event_id):
    event = d.get(db, m.Event, event_id)
    if event.kind not in KINDS:
        raise HTTPException(404, "Cette entrée n’est pas une réponse reçue.")
    return event


def event_view(db, event):
    contact = db.get(m.Contact, event.contact_id) if event.contact_id else None
    account = db.get(m.Account, event.account_id) if event.account_id else None
    message = db.get(m.Message, event.data.get("message_id")) if event.data.get("message_id") else None
    sender_id = message.sender_id if message else contact.email_sender_id if contact else None
    mailbox = db.get(m.SendingMailbox, sender_id) if sender_id else None
    campaign = db.get(m.Campaign, event.campaign_id) if event.campaign_id else None
    return {
        **d.serialize(event),
        "contact": d.serialize(contact),
        "account": d.serialize(account),
        "campaign": {"id": campaign.id, "group_id": campaign.group_id, "name": campaign.name}
        if campaign
        else None,
        "sender": {
            "id": sender_id,
            "from_email": message.sender_email or (mailbox.from_email if mailbox else ""),
        }
        if message
        else {"id": sender_id, "from_email": mailbox.from_email if mailbox else ""},
        "classification": event.data.get("inbox", {}).get("classification", event.kind),
        "review_status": inbox_state(db, event),
    }


@router.get("")
def inbox_list(
    status: Literal["open", "done", "all"] = "open",
    kind: str = "",
    channel: str = "",
    campaign_id: str = Query("", max_length=36),
    sender_id: str = Query("", max_length=36),
    q: str = Query("", max_length=120),
    page: int = Query(1, ge=1),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if kind and kind not in KINDS | {"negative_reply"}:
        raise HTTPException(422, "Type de réponse invalide.")
    if channel and channel not in {"email", "phone", "linkedin"}:
        raise HTTPException(422, "Canal invalide.")
    base = select(m.Event).where(m.Event.kind.in_(KINDS))
    totals = {
        key: db.scalar(select(func.count()).select_from(base.where(state_expression() == key).subquery()))
        or 0
        for key in ["open", "done"]
    }
    query = base.outerjoin(m.Contact, m.Contact.id == m.Event.contact_id).outerjoin(
        m.Account, m.Account.id == m.Event.account_id
    )
    if status != "all":
        query = query.where(state_expression() == status)
    if kind:
        query = query.where(
            func.coalesce(m.Event.data["inbox"]["classification"].as_string(), m.Event.kind) == kind
        )
    if channel:
        query = query.where(m.Event.channel == channel)
    if campaign_id:
        query = query.where(m.Event.campaign_id == campaign_id)
    if sender_id:
        query = query.outerjoin(m.Message, m.Message.id == m.Event.data["message_id"].as_string()).where(
            func.coalesce(m.Message.sender_id, m.Contact.email_sender_id) == sender_id
        )
    if q.strip():
        query = query.where(
            func.concat_ws(
                " ",
                m.Event.summary,
                m.Contact.first_name,
                m.Contact.last_name,
                m.Contact.email,
                m.Account.name,
            ).icontains(q.strip(), autoescape=True)
        )
    rows, total, page = page_result(db, query.order_by(m.Event.created_at.desc(), m.Event.id), page, 20)
    return {
        "items": [event_view(db, row) for row in rows],
        "total": total,
        "page": page,
        "page_size": 20,
        "totals": totals,
    }


@router.get("/{event_id}")
def inbox_detail(event_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    event = get_event(db, event_id)
    message = db.get(m.Message, event.data.get("message_id")) if event.data.get("message_id") else None
    history = (
        list(
            db.scalars(
                select(m.Event)
                .where(m.Event.contact_id == event.contact_id, m.Event.id != event.id)
                .order_by(m.Event.created_at.desc())
                .limit(20)
            )
        )
        if event.contact_id
        else []
    )
    opportunities = (
        list(
            db.scalars(
                select(m.Opportunity)
                .where(m.Opportunity.account_id == event.account_id)
                .order_by(m.Opportunity.updated_at.desc())
            )
        )
        if event.account_id
        else []
    )
    followup = (
        db.get(m.Task, event.data.get("inbox", {}).get("task_id"))
        if event.data.get("inbox", {}).get("task_id")
        else None
    )
    return {
        **event_view(db, event),
        "related_message": d.serialize(message),
        "history": [d.serialize(row) for row in history],
        "opportunities": [d.serialize(row) for row in opportunities],
        "followup": d.serialize(followup),
    }


class InboxHandle(s.Input):
    action: Literal["done", "reopen", "callback", "opportunity"]
    classification: Classification | None = None
    note: str = Field(default="", max_length=3000)
    due_at: datetime | None = None
    title: str = Field(default="", max_length=220)
    amount_cents: int = Field(default=0, ge=0, le=100000000)
    expected_updated_at: datetime
    request_id: UUID


@router.post("/{event_id}/handle")
def handle_reply(event_id: str, data: InboxHandle, user=Depends(current_user), db: Session = Depends(get_db)):
    event = get_event(db, event_id)
    key = f"inbox-handle:{event_id}:{data.request_id}"
    previous = db.scalar(select(m.Event).where(m.Event.external_id == key))
    if previous:
        return {**previous.data, "duplicate": True}
    if data.expected_updated_at.tzinfo is None or event.updated_at != data.expected_updated_at:
        raise HTTPException(409, "Cette réponse a changé. Rechargez-la avant de la traiter.")
    if data.action == "callback" and (
        not data.due_at or data.due_at.tzinfo is None or data.due_at <= m.now()
    ):
        raise HTTPException(422, "Choisissez une date future avec son fuseau pour le rappel.")
    contact = db.get(m.Contact, event.contact_id) if event.contact_id else None
    if not contact and data.action in {"callback", "opportunity"}:
        raise HTTPException(409, "Le contact a été effacé. Cette réponse peut seulement être classée.")
    original_kind = event.kind
    previous_triage = event.data.get("inbox", {})
    classification = data.classification or previous_triage.get("classification", event.kind)
    if data.action == "reopen":
        classification = previous_triage.get("classification", event.kind)
    if data.action == "callback" and (classification in {"stop", "bounce"} or d.is_suppressed(db, contact)):
        raise HTTPException(
            409, "Cette réponse nécessite une revue de la fiche avant de convenir d’un rappel."
        )
    if data.action != "reopen":
        if classification == "stop" and contact:
            d.add_suppression(
                db,
                "contact",
                contact.id,
                "all",
                data.note.strip() or "Opposition confirmée dans la boîte de réception",
                user.id,
            )
            if contact.email:
                d.add_suppression(
                    db, "email", contact.email, "all", data.note.strip() or "Opposition confirmée", user.id
                )
        elif classification == "bounce" and contact:
            contact.email_status = "invalid"
            d.cancel_pending(db, contact, "Email invalide confirmé dans la boîte de réception", ["email"])
        event.kind = "reply" if classification == "negative_reply" else classification
    task = db.scalar(select(m.Task).where(m.Task.dedupe_key == "event:" + event.id))
    if task:
        task.status = "pending" if data.action == "reopen" else "completed"
        task.outcome = "" if data.action == "reopen" else "done"
    result = {
        "event_id": event.id,
        "status": "open" if data.action == "reopen" else "done",
        "task_id": previous_triage.get("task_id"),
        "opportunity_id": previous_triage.get("opportunity_id"),
    }
    if data.action == "callback":
        assert contact is not None and data.due_at is not None  # both validated above
        followup = db.scalar(
            select(m.Task).where(
                m.Task.dedupe_key == "inbox-followup:" + event.id, m.Task.status == "pending"
            )
        )
        if not followup:
            followup = m.Task(
                contact_id=contact.id,
                campaign_id=event.campaign_id,
                owner_id=user.id,
                channel="review",
                title=data.title.strip()
                or "Rappel convenu — " + (contact.first_name + " " + contact.last_name).strip(),
                due_at=data.due_at,
                notes=data.note.strip() or event.summary,
                dedupe_key="inbox-followup:" + event.id,
            )
            # A completed callback remains historical; the new one has a fresh key.
            if db.scalar(select(m.Task.id).where(m.Task.dedupe_key == followup.dedupe_key)):
                followup.dedupe_key += ":" + str(data.request_id)
            db.add(followup)
        else:
            followup.due_at = data.due_at
            followup.notes = data.note.strip() or event.summary
            if data.title.strip():
                followup.title = data.title.strip()
        db.flush()
        result["task_id"] = followup.id
        d.pause_conversation(db, contact, "Rappel convenu après réponse")
    elif data.action == "opportunity":
        assert contact is not None  # validated above
        if not data.title.strip():
            raise HTTPException(422, "Donnez un titre à l’opportunité.")
        if classification in {"stop", "bounce"} or d.is_suppressed(db, contact):
            raise HTTPException(409, "Cette réponse exige une revue avant la création d’une opportunité.")
        opportunity = db.scalar(
            select(m.Opportunity).where(
                m.Opportunity.account_id == contact.account_id, m.Opportunity.stage.not_in(["won", "lost"])
            )
        )
        if not opportunity:
            opportunity = m.Opportunity(
                account_id=contact.account_id,
                contact_id=contact.id,
                campaign_id=event.campaign_id,
                owner_id=user.id,
                title=data.title.strip(),
                amount_cents=data.amount_cents,
                next_action=data.note.strip(),
            )
            db.add(opportunity)
            db.flush()
            d.pause_conversation(db, contact, "Opportunité active")
            d.log(
                db,
                "opportunity_created",
                contact=contact,
                actor_id=user.id,
                summary=opportunity.title,
                data={"opportunity_id": opportunity.id, "inbox_event_id": event.id},
            )
        result["opportunity_id"] = opportunity.id
    triage = {
        **previous_triage,
        **result,
        "classification": classification,
        "note": data.note.strip(),
        "handled_at": m.now().isoformat(),
        "handled_by": user.id,
    }
    event.data = {
        **event.data,
        "original_kind": event.data.get("original_kind", original_kind),
        "inbox": triage,
    }
    d.log(
        db,
        "inbox_reviewed",
        contact=contact,
        campaign_id=event.campaign_id,
        actor_id=user.id,
        summary=data.note.strip() or "Réponse classée dans la boîte de réception.",
        data=result,
        external_id=key,
    )
    db.commit()
    return {**result, "duplicate": False}
