"""Scoped agent work, durable leases and explicit human handoffs.

Agent credentials never authorize the ordinary admin or email-send endpoints.
"""

import secrets
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import EmailStr, Field, model_validator
from sqlalchemy import case, false, func, or_, select
from sqlalchemy.orm import Session

from . import domain as d
from . import inbox
from . import models as m
from . import schemas as s
from .business_tools import BusinessResult
from .call_reports import ReportInput
from .db import SessionLocal, get_db
from .security import admin, current_user, digest
from .workspaces import page_result

router = APIRouter(prefix="/api", tags=["agent"])
ACTIVE = ["queued", "running", "retry_at", "waiting_human"]
HUMAN_STATUSES = ["waiting_human", "failed", "uncertain"]
AGENT_STATUSES = ["queued", "running", "retry_at", "waiting_external"]
HISTORY_STATUSES = ["completed", "cancelled"]
AGENT_KINDS = {
    "post_call_report",
    "mission_step",
    "qualify_batch",
    "qualify_contact",
    "qualify_account",
    "discover_leads",
    "reply_draft",
    "review_task",
    "operations_check",
}


def lane_expression():
    return case(
        (m.AgentAction.kind == "post_call_report", "post_call"),
        else_=func.coalesce(m.AgentAction.payload["lane"].as_string(), "research"),
    )


def notify(db, action):
    state = ":" + action.status if action.status in {"failed", "uncertain"} else ""
    key = f"human:{action.id}:{action.attempts}{state}"
    if not db.scalar(select(m.ActionNotification.id).where(m.ActionNotification.dedupe_key == key)):
        db.add(
            m.ActionNotification(
                action_id=action.id,
                dedupe_key=key,
                title=action.title,
                body=action.human_reason or "Cette action nécessite votre intervention.",
            )
        )


def enqueue(db, key, kind, title, *, human_reason="", **values):
    previous = db.scalar(select(m.AgentAction).where(m.AgentAction.dedupe_key == key))
    if previous:
        return previous
    action = m.AgentAction(
        dedupe_key=key,
        kind=kind,
        title=title,
        executor="human" if human_reason else "agent",
        status="waiting_human" if human_reason else "queued",
        human_reason=human_reason,
        **values,
    )
    db.add(action)
    db.flush()
    if human_reason:
        notify(db, action)
    return action


def eligible_contact(db, contact):
    account = db.get(m.Account, contact.account_id)
    return bool(
        account
        and account.lifecycle == "prospect"
        and contact.status != "suppressed"
        and not d.is_suppressed(db, contact)
    )


def requested_callback(db, task, contact):
    if (
        not task
        or not contact
        or task.status != "pending"
        or task.channel != "phone"
        or not (task.dedupe_key or "").startswith("callback:")
    ):
        return False
    account = db.get(m.Account, contact.account_id)
    return bool(
        account
        and account.lifecycle == "prospect"
        and not account.outreach_paused
        and contact.status != "suppressed"
    )


def synchronize(db):
    from .prospect_pipeline import enqueue_batch

    reconcile_review_tasks(db)
    enqueue_batch(db)
    # Routine quality and operations checks replenish themselves without a human click.
    for contact in db.scalars(
        select(m.Contact)
        .where(m.Contact.paused.is_(False), m.Contact.campaign_id.is_not(None))
        .order_by(m.Contact.updated_at)
        .limit(250)
    ):
        review_data = contact.qualification.get("agent_review", {})
        review = review_data.get("reviewed_at", "")
        examined = review_data.get("review_phase") == "qualification"
        delay = 30 if contact.approved or review_data.get("decision") == "not_fit" else 1
        if not eligible_contact(db, contact) or (
            examined and review and review > (m.now() - timedelta(days=delay)).isoformat()
        ):
            continue
        key = (
            "qualify-contact:" + contact.id + (":" + m.now().strftime("%Y-%m-%d") if examined else ":initial")
        )
        enqueue(
            db,
            key,
            "qualify_contact",
            "Qualifier " + (contact.first_name + " " + contact.last_name).strip(),
            contact_id=contact.id,
            account_id=contact.account_id,
            campaign_id=contact.campaign_id,
            target_url="/contacts/" + contact.id + "?tab=qualification",
        )
    if not db.scalar(
        select(m.AgentAction.id).where(
            m.AgentAction.kind == "operations_check", m.AgentAction.status.in_(ACTIVE)
        )
    ):
        enqueue(
            db,
            "operations:" + m.now().date().isoformat(),
            "operations_check",
            "Contrôler les boîtes et les traitements",
            priority=90,
            target_url="/alerts",
            payload={
                "brief": "Contrôler les boîtes avec l’outil check_mailbox_connection et examiner les alertes. Vérifier les prérequis réels du warming et les sources contractuelles du fournisseur. Conserver toute pause explicite et les plafonds. Les quotas consommés attendent sans achat. Si une décision contractuelle ou un accès privé indispensable manque, préparer une demande précise regroupant uniquement ces interventions humaines. Ne pas prétendre au warming lorsque aucun envoi accepté n’est enregistré."
            },
        )
    # Existing manual work retains its original outcome workflow.
    for task in db.scalars(select(m.Task).where(m.Task.status == "pending").limit(500)):
        if (task.dedupe_key or "").startswith(("event:", "callback:mission:")):
            continue
        contact = db.get(m.Contact, task.contact_id)
        callback = requested_callback(db, task, contact)
        if not eligible_contact(db, contact) and not callback:
            continue
        human = task.channel in {"phone", "linkedin"}
        enqueue(
            db,
            "task:" + task.id,
            "manual_task" if human else "review_task",
            task.title if human else ("Examiner : " + task.title)[:220],
            task_id=task.id,
            contact_id=contact.id,
            account_id=contact.account_id,
            campaign_id=task.campaign_id,
            due_at=task.due_at,
            priority=20,
            target_url="/calendar",
            human_reason="Réaliser l’appel et consigner son résultat."
            if task.channel == "phone"
            else "Réaliser cette prise de contact LinkedIn."
            if human
            else "",
        )
    for event in db.scalars(
        select(m.Event)
        .where(m.Event.kind.in_(inbox.KINDS), m.Event.channel == "email")
        .order_by(m.Event.created_at.desc())
        .limit(200)
    ):
        if inbox.inbox_state(db, event) == "done":
            continue
        contact = db.get(m.Contact, event.contact_id) if event.contact_id else None
        if not contact or not eligible_contact(db, contact):
            continue
        if event.kind in {"stop", "bounce", "out_of_office"}:
            continue  # Existing deterministic receive_event handles these.
        newer = db.scalar(
            select(m.Event.id)
            .where(
                m.Event.contact_id == contact.id,
                m.Event.channel == "email",
                m.Event.kind.in_(inbox.KINDS),
                m.Event.created_at > event.created_at,
            )
            .limit(1)
        )
        if newer:
            continue
        enqueue(
            db,
            "reply:" + event.id,
            "reply_draft",
            "Préparer la réponse à " + contact.first_name,
            contact_id=contact.id,
            account_id=contact.account_id,
            campaign_id=event.campaign_id,
            event_id=event.id,
            priority=10,
            target_url="/inbox/" + event.id,
        )
    for action in db.scalars(select(m.AgentAction).where(m.AgentAction.status.in_(ACTIVE))):
        if action.task_id:
            task = db.get(m.Task, action.task_id)
            if task and task.status in {"completed", "cancelled"}:
                action.status = task.status
                action.completed_at = m.now()
                action.lease_hash = ""
        if action.contact_id and action.kind != "post_call_report":
            contact = db.get(m.Contact, action.contact_id)
            callback = (
                action.kind == "manual_task"
                and action.task_id
                and requested_callback(db, db.get(m.Task, action.task_id), contact)
            )
            if not contact or (not eligible_contact(db, contact) and not callback):
                action.status = "cancelled"
                action.lease_hash = ""
                action.completed_at = m.now()
        if action.status == "running" and action.lease_until and action.lease_until < m.now():
            action.lease_hash = ""
            action.last_error = "Exécution interrompue, reprise de la tâche."
            if action.attempts >= 6:
                action.status = "waiting_human"
                action.executor = "human"
                action.human_reason = "Six exécutions interrompues malgré les reprises automatiques. Vérifier le journal de l’agent avant de relancer."
                notify(db, action)
            else:
                action.status = "retry_at"
                action.next_attempt_at = m.now() + timedelta(minutes=min(360, 2**action.attempts))
    reconcile_send_replies(db)
    for notification in db.scalars(
        select(m.ActionNotification)
        .join(m.AgentAction)
        .where(
            m.ActionNotification.read_at.is_(None),
            m.AgentAction.status.not_in(HUMAN_STATUSES),
        )
    ):
        notification.read_at = m.now()


def tick():
    with SessionLocal() as db:
        d.state(db, lock=True)
        if db.scalar(select(m.AgentClient.id)):
            synchronize(db)
            dispatch_replies(db)
        else:
            reconcile_send_replies(db)
        db.commit()


def reconcile_review_tasks(db):
    """Undo only the historical task completion produced by the old review action."""
    for action in db.scalars(select(m.AgentAction).where(m.AgentAction.kind == "review_task")):
        task = db.get(m.Task, action.task_id) if action.task_id else None
        if not task:
            continue
        action.title = ("Examiner : " + task.title)[:220]
        if (
            action.status != "completed"
            or not action.completion_key
            or task.status != "completed"
            or task.outcome != "done"
        ):
            continue
        completed_event = db.scalar(
            select(m.Event.id)
            .where(
                m.Event.kind == "task_completed",
                m.Event.data["task_id"].as_string() == task.id,
            )
            .limit(1)
        )
        if not completed_event:
            task.status = "pending"
            task.outcome = ""


def reconcile_send_reply(db, row, message):
    """Mirror an automatic reply action from its durable message truth."""
    if row.status == "cancelled":
        return
    previous = row.status
    row.lease_hash = ""
    row.lease_until = None
    if not message or message.status == "cancelled":
        row.status, row.completed_at = "cancelled", m.now()
        row.executor = "agent"
        row.human_reason = ""
        row.result = {"summary": "Brouillon annulé : l’envoi automatique est arrêté."}
    elif message.status == "sent" and message.sent_at:
        row.status = "completed"
        row.completed_at = row.completed_at or m.now()
        row.executor = "agent"
        row.human_reason = ""
        row.last_error = ""
        row.result = {
            "summary": "Réponse envoyée et horodatée.",
            "message_id": message.id,
            "sent_at": message.sent_at.isoformat(),
        }
    elif message.status in {"queued", "sending"}:
        row.status = "waiting_external"
        row.completed_at = None
        row.executor = "agent"
        row.human_reason = ""
        row.last_error = ""
        row.result = {
            "summary": "Réponse confiée à la file d’envoi ; confirmation en attente.",
            "message_id": message.id,
        }
    elif message.status == "failed":
        row.status = "failed"
        row.completed_at = None
        row.executor = "human"
        row.human_reason = (
            "L’envoi a échoué avant confirmation. Rapprochez l’opération dans le journal "
            "avant toute nouvelle tentative."
        )
        row.last_error = message.failure_reason or "Échec d’envoi confirmé."
        row.result = {"summary": "Échec d’envoi à rapprocher.", "message_id": message.id}
    elif message.status == "uncertain" or (message.status == "sent" and not message.sent_at):
        row.status = "uncertain"
        row.completed_at = None
        row.executor = "human"
        row.human_reason = (
            "Le résultat de l’envoi est incertain. Vérifiez le fournisseur puis rapprochez "
            "l’opération dans le journal ; aucun nouvel envoi ne sera lancé automatiquement."
        )
        row.last_error = message.failure_reason or "Envoi indiqué sans horodatage fiable."
        row.result = {"summary": "Résultat d’envoi incertain à rapprocher.", "message_id": message.id}
    elif message.status == "draft" and row.status not in {"queued", "retry_at"}:
        row.status, row.completed_at = "cancelled", m.now()
        row.executor = "agent"
        row.human_reason = ""
        row.result = {
            "summary": "Envoi rapproché comme non effectué ; le brouillon reste à relire.",
            "message_id": message.id,
        }
    if row.status in {"failed", "uncertain"} and row.status != previous:
        notify(db, row)


def reconcile_send_replies(db):
    for row in db.scalars(select(m.AgentAction).where(m.AgentAction.kind == "send_reply")):
        if row.status == "cancelled":
            continue  # A historical cancellation is an explicit stop, even if the message later changed.
        reconcile_send_reply(db, row, db.get(m.Message, row.payload.get("message_id", "")))


def dispatch_replies(db):
    client = db.scalar(select(m.AgentClient).limit(1))
    if not client or not client.enabled:
        return
    from .conversations import QueueReply, schedule_reply

    for row in db.scalars(
        select(m.AgentAction)
        .where(
            m.AgentAction.kind == "send_reply",
            m.AgentAction.status.in_(["queued", "retry_at"]),
            m.AgentAction.next_attempt_at <= m.now(),
        )
        .limit(20)
    ):
        message = db.get(m.Message, row.payload.get("message_id", ""))
        if (
            not message
            or message.status == "cancelled"
            or (
                message.status == "draft"
                and message.updated_at != datetime.fromisoformat(row.payload["draft_revision"])
            )
        ):
            row.status, row.completed_at = "cancelled", m.now()
            row.result = {"summary": "Brouillon modifié ou annulé : l’envoi automatique est arrêté."}
            continue
        if message.status != "draft":
            reconcile_send_reply(db, row, message)
            continue
        try:
            job = schedule_reply(
                message.id,
                QueueReply(
                    expected_updated_at=message.updated_at,
                    request_id=uuid5(NAMESPACE_URL, "lumelia-agent-reply:" + message.id),
                ),
                db.get(m.User, "workspace"),
                db,
            )
        except HTTPException as error:
            row.status = "retry_at"
            row.last_error = str(error.detail)[:1000]
            row.next_attempt_at = m.now() + timedelta(minutes=15)
            continue
        reconcile_send_reply(db, row, message)
        row.result = {**row.result, "job_id": job["id"]}


def action_view(db, row):
    values = d.serialize(row)
    for key in ["lease_hash", "completion_key"]:
        values.pop(key, None)
    values["contact_name"] = ""
    if row.contact_id:
        contact = db.get(m.Contact, row.contact_id)
        values["contact_name"] = (contact.first_name + " " + contact.last_name).strip() if contact else ""
    account = db.get(m.Account, row.account_id) if row.account_id else None
    values["company"] = account.name if account else ""
    return values


@router.get("/agent/status")
def status(user=Depends(current_user), db: Session = Depends(get_db)):
    client = db.scalar(select(m.AgentClient).order_by(m.AgentClient.created_at).limit(1))
    values = d.serialize(client)
    if values:
        values.pop("token_hash", None)
    return {
        "client": values,
        "counts": {
            key: db.scalar(
                select(func.count()).select_from(m.AgentAction).where(m.AgentAction.status.in_(statuses))
            )
            or 0
            for key, statuses in {
                "human": HUMAN_STATUSES,
                "agent": AGENT_STATUSES,
                "history": HISTORY_STATUSES,
            }.items()
        },
        "unread": db.scalar(
            select(func.count())
            .select_from(m.ActionNotification)
            .where(m.ActionNotification.read_at.is_(None))
        )
        or 0,
    }


@router.get("/agent/actions")
def actions(
    view: Literal["human", "agent", "history"] = "human",
    q: str = Query("", max_length=120),
    page: int = Query(1, ge=1),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    statuses = {
        "human": HUMAN_STATUSES,
        "agent": AGENT_STATUSES,
        "history": HISTORY_STATUSES,
    }[view]
    query = select(m.AgentAction).where(m.AgentAction.status.in_(statuses))
    if q.strip():
        query = query.where(m.AgentAction.title.icontains(q.strip(), autoescape=True))
    query = (
        query.order_by(m.AgentAction.completed_at.desc())
        if view == "history"
        else query.order_by(
            m.AgentAction.priority, m.AgentAction.due_at.asc().nullslast(), m.AgentAction.created_at
        )
    )
    rows, total, page = page_result(db, query, page, 6)
    return {"items": [action_view(db, row) for row in rows], "page": page, "page_size": 6, "total": total}


@router.get("/agent/actions/{action_id}")
def action_detail(action_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    return action_view(db, d.get(db, m.AgentAction, action_id))


class Configure(s.Input):
    enabled: bool


@router.post("/agent/configure")
def configure(data: Configure, user=Depends(admin), db: Session = Depends(get_db)):
    client = db.scalar(select(m.AgentClient).limit(1))
    if not client:
        raise HTTPException(409, "Connectez d’abord l’exécuteur de l’agent.")
    client.enabled = data.enabled
    if not data.enabled:
        for row in db.scalars(select(m.AgentAction).where(m.AgentAction.status == "running")):
            row.status = "queued"
            row.lease_hash = ""
            row.lease_until = None
    synchronize(db)
    db.commit()
    return status(user, db)


@router.post("/agent/credentials", status_code=201)
def credentials(user=Depends(admin), db: Session = Depends(get_db)):
    token = secrets.token_urlsafe(48)
    client = db.scalar(select(m.AgentClient).limit(1))
    if not client:
        client = m.AgentClient(token_hash=digest(token))
        db.add(client)
    else:
        client.token_hash = digest(token)
    for row in db.scalars(select(m.AgentAction).where(m.AgentAction.status == "running")):
        row.status = "queued"
        row.lease_hash = ""
        row.lease_until = None
    db.commit()
    return {"token": token, "client_id": client.id, "model": client.model}


class StartBatch(s.Input):
    scope: Literal["existing", "discover"]
    campaign_id: str
    count: int = Field(default=25, ge=1, le=50)
    brief: str = Field(default="", max_length=3000)
    request_id: UUID


@router.post("/agent/batches", status_code=201)
def batch(data: StartBatch, user=Depends(admin), db: Session = Depends(get_db)):
    campaign = d.get(db, m.Campaign, data.campaign_id)
    if data.scope == "existing":
        rows = list(
            db.scalars(
                select(m.Contact)
                .join(m.Account)
                .where(
                    m.Account.lifecycle == "prospect",
                    m.Contact.status != "suppressed",
                    m.Contact.paused.is_(False),
                    m.Contact.campaign_id == campaign.id,
                )
                .order_by(m.Contact.created_at)
                .limit(data.count)
            )
        )
        ids = [
            enqueue(
                db,
                "qualify-contact:" + row.id,
                "qualify_contact",
                "Qualifier " + (row.first_name + " " + row.last_name).strip(),
                contact_id=row.id,
                account_id=row.account_id,
                campaign_id=campaign.id,
                target_url="/contacts/" + row.id + "?tab=qualification",
            ).id
            for row in rows
            if eligible_contact(db, row)
        ]
    else:
        if len(data.brief.strip()) < 20:
            raise HTTPException(422, "Précisez le lot à rechercher.")
        ids = []
        for index in range((data.count + 4) // 5):
            action = enqueue(
                db,
                f"discover:{data.request_id}:{index}",
                "discover_leads",
                f"Identifier les décideurs · lot {index + 1}",
                campaign_id=campaign.id,
                priority=40,
                payload={
                    "brief": data.brief,
                    "limit": min(5, data.count - index * 5),
                    "batch_id": str(data.request_id),
                    "batch_index": index,
                },
                target_url="/contacts",
            )
            ids.append(action.id)
    db.commit()
    return {"action_ids": ids, "total": len(ids)}


class HumanDecision(s.Input):
    action: Literal["resume", "complete", "cancel"]
    note: str = Field(min_length=5, max_length=3000)
    expected_updated_at: datetime


@router.post("/agent/actions/{action_id}/decision")
def decide(action_id: str, data: HumanDecision, user=Depends(current_user), db: Session = Depends(get_db)):
    row = d.get(db, m.AgentAction, action_id)
    if row.updated_at != data.expected_updated_at:
        raise HTTPException(409, "L’action a changé. Rechargez-la.")
    if row.kind == "send_reply":
        raise HTTPException(
            409,
            "Rapprochez l’opération d’envoi depuis le journal. Cette action suivra le résultat enregistré.",
        )
    if data.action == "resume":
        if row.kind not in AGENT_KINDS or row.status not in {"waiting_human", "retry_at", "cancelled"}:
            raise HTTPException(409, "Cette action ne peut pas être confiée à l’agent.")
        row.status = "queued"
        row.executor = "agent"
        row.next_attempt_at = m.now()
        row.attempts = 0
        row.completed_at = None
        row.payload = {**row.payload, "human_answer": data.note}
    else:
        if data.action == "complete" and row.task_id:
            raise HTTPException(409, "Consignez le résultat depuis l’action d’origine.")
        row.status = "completed" if data.action == "complete" else "cancelled"
        row.completed_at = m.now()
        row.result = {**row.result, "human_resolution": data.note}
    row.lease_hash = ""
    row.lease_until = None
    for n in db.scalars(
        select(m.ActionNotification).where(
            m.ActionNotification.action_id == row.id, m.ActionNotification.read_at.is_(None)
        )
    ):
        n.read_at = m.now()
    db.commit()
    return action_view(db, row)


@router.get("/agent/notifications")
def notifications(user=Depends(current_user), db: Session = Depends(get_db)):
    rows = list(
        db.scalars(
            select(m.ActionNotification)
            .where(m.ActionNotification.read_at.is_(None))
            .order_by(m.ActionNotification.created_at.desc())
            .limit(50)
        )
    )
    return {
        "items": [{**d.serialize(row), "url": "/agent?view=human&action=" + row.action_id} for row in rows]
    }


@router.post("/agent/notifications/{notification_id}/read")
def read_notification(notification_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    row = d.get(db, m.ActionNotification, notification_id)
    row.read_at = m.now()
    db.commit()
    return {"ok": True}


def agent_auth(request: Request, db: Session = Depends(get_db)):
    raw = request.headers.get("authorization", "")
    if not raw.startswith("Bearer "):
        raise HTTPException(401, "Identifiant agent requis.")
    client = db.scalar(select(m.AgentClient).where(m.AgentClient.token_hash == digest(raw[7:])))
    if not client:
        raise HTTPException(401, "Identifiant agent révoqué ou invalide.")
    expected_host = client.state.get("execution_host")
    if request.headers.get("x-agent-host") == "hosted" and expected_host != "hosted":
        raise HTTPException(409, "Configurez la cadence et le budget du service avant son démarrage.")
    if expected_host and request.headers.get("x-agent-host", "local") != expected_host:
        raise HTTPException(409, "Cet exécuteur a été remplacé. Utilisez le service autorisé.")
    if request.method != "GET":
        d.state(db, lock=True)
    return client


class Pulse(s.Input):
    status: Literal["idle", "running", "quota", "error", "budget"] = "idle"
    message: str = Field(default="", max_length=300)
    retry_at: datetime | None = None


@router.post("/agent/runtime/heartbeat")
def pulse(data: Pulse, client=Depends(agent_auth), db: Session = Depends(get_db)):
    client.last_seen_at = m.now()
    if data.retry_at or not client.next_run_at or client.next_run_at <= m.now():
        client.next_run_at = data.retry_at
        client.state = {**client.state, "runtime_status": data.status, "message": data.message}
    db.commit()
    return {"enabled": client.enabled, "model": client.model}


@router.post("/agent/runtime/claim")
def claim(
    lane: Literal["research", "post_call"] = "research",
    client=Depends(agent_auth),
    db: Session = Depends(get_db),
):
    synchronize(db)
    client.last_seen_at = m.now()
    scheduled = client.state.get("next_scheduled_at")
    if (
        not client.enabled
        or (
            client.next_run_at
            and client.next_run_at > m.now()
            and (lane == "research" or client.state.get("runtime_status") in {"quota", "auth"})
        )
        or (lane == "research" and scheduled and datetime.fromisoformat(scheduled) > m.now())
    ):
        db.commit()
        return {"action": None}
    if db.scalar(
        select(m.AgentAction.id).where(
            m.AgentAction.status == "running", m.AgentAction.client_id == client.id, lane_expression() == lane
        )
    ):
        db.commit()
        return {"action": None}
    row = db.scalar(
        select(m.AgentAction)
        .where(
            m.AgentAction.status.in_(["queued", "retry_at"]),
            m.AgentAction.next_attempt_at <= m.now(),
            m.AgentAction.kind.in_(AGENT_KINDS),
            lane_expression() == lane,
        )
        .order_by(m.AgentAction.priority, m.AgentAction.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not row:
        db.commit()
        return {"action": None}
    from .agent_budget import reserve

    budget = reserve(db, client, row)
    if budget is None:
        client.state = {
            **client.state,
            "runtime_status": "budget",
            "message": "Budget quotidien atteint, reprise le lendemain.",
        }
        client.next_run_at = (m.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        db.commit()
        return {"action": None, "reason": "daily_token_budget"}
    secret = secrets.token_urlsafe(36)
    row.status = "running"
    row.client_id = client.id
    row.attempts += 1
    row.lease_hash = digest(secret)
    row.lease_until = m.now() + timedelta(minutes=12)
    row.last_error = ""
    row.payload = {**row.payload, "revision": target_revision(db, row)}
    if row.kind == "post_call_report":
        from .call_models import CallSession

        call_row = d.get(db, CallSession, row.payload["call_id"])
        row.payload = {**row.payload, "report_version": call_row.report_version}
    if lane == "research" and client.state.get("execution_host") == "hosted":
        client.state = {
            **client.state,
            "next_scheduled_at": (
                m.now() + timedelta(minutes=client.state.get("interval_minutes", 60))
            ).isoformat(),
        }
    db.commit()
    return {"action": action_view(db, row), "lease": secret, "budget": budget}


def target_revision(db, row):
    return {
        name: db.get(model, identifier).updated_at.isoformat()
        for name, model, identifier in [
            ("contact", m.Contact, row.contact_id),
            ("account", m.Account, row.account_id),
            ("event", m.Event, row.event_id),
            ("task", m.Task, row.task_id),
        ]
        if identifier and db.get(model, identifier)
    }


def lease_action(db, client, action_id, secret):
    row = d.get(db, m.AgentAction, action_id)
    if (
        not client.enabled
        or row.status != "running"
        or row.client_id != client.id
        or not row.lease_until
        or row.lease_until < m.now()
        or not secrets.compare_digest(row.lease_hash, digest(secret))
    ):
        raise HTTPException(409, "Bail expiré, action arrêtée ou déjà traitée.")
    if (
        row.kind != "post_call_report"
        and row.contact_id
        and not eligible_contact(db, d.get(db, m.Contact, row.contact_id))
    ):
        raise HTTPException(409, "Fiche désormais exclue.")
    return row


@router.get("/agent/runtime/actions/{action_id}/context")
def context(action_id: str, request: Request, client=Depends(agent_auth), db: Session = Depends(get_db)):
    row = lease_action(db, client, action_id, request.headers.get("x-agent-lease", ""))
    campaign = db.get(m.Campaign, row.campaign_id) if row.campaign_id else None
    contact = db.get(m.Contact, row.contact_id) if row.contact_id else None
    evidence = list(
        db.scalars(
            select(m.Evidence)
            .where(
                or_(
                    m.Evidence.contact_id == row.contact_id if row.contact_id else false(),
                    m.Evidence.account_id == row.account_id if row.account_id else false(),
                )
            )
            .order_by(m.Evidence.observed_at.desc())
            .limit(50)
        )
    )
    result = {
        "action": action_view(db, row),
        "contact": d.serialize(contact),
        "account": d.serialize(db.get(m.Account, row.account_id)) if row.account_id else None,
        "campaign": d.serialize(campaign),
        "offer": d.serialize(db.get(m.Offer, campaign.offer_id)) if campaign else None,
        "icp": d.serialize(db.get(m.ICP, campaign.icp_id)) if campaign else None,
        "evidence": [d.serialize(e) for e in evidence],
        "task": d.serialize(db.get(m.Task, row.task_id)) if row.task_id else None,
    }
    if row.kind == "post_call_report":
        from .call_reports import context as report_context

        result["post_call"] = report_context(db, row)
    if row.kind == "mission_step":
        from .business_tools import context as business_context

        result["business"] = business_context(db, row)
    if row.kind == "qualify_batch":
        from .prospect_pipeline import agent_context

        result["accounts"] = agent_context(db, row)
    if row.kind == "discover_leads":
        result["existing_domains"] = list(db.scalars(select(m.Account.domain)))
        result["existing_contacts"] = [
            {"name": c.first_name + " " + c.last_name, "account_id": c.account_id}
            for c in db.scalars(select(m.Contact).limit(1000))
        ]
    if row.kind == "operations_check":
        from . import native_warmup, operations, senders

        result["mailboxes"] = [senders.view(db, box) for box in db.scalars(select(m.SendingMailbox))]
        result["alerts"] = operations.alerts("all", None, db)
        result["warmup"] = native_warmup.snapshot(db)
        result["sending"] = {
            "paused": d.state(db).paused,
            "limits": "Ne pas modifier les plafonds autorisés.",
            "contract_evidence": client.state.get(
                "contract_evidence", "Aucune condition particulière documentée dans le contexte de l’agent."
            ),
        }
    if row.event_id:
        from .conversations import conversation

        thread = conversation(row.event_id, 1, None, db)
        for page in range(2, (thread["total"] + 9) // 10 + 1):
            thread["items"] = conversation(row.event_id, page, None, db)["items"] + thread["items"]
        result["conversation"] = thread
    result["policy"] = {
        "model": client.model,
        "ordinary_replies": "La réponse ordinaire enregistrée sera envoyée automatiquement lorsque les règles existantes autorisent l’envoi. Les engagements hors cadre nécessitent une décision humaine.",
        "paid_provider_calls": False,
        "change_cadence": False,
        "primary_sources_required": True,
        "human_for": "Appels, reconnexion privée, choix commercial exceptionnel. Ne pas demander la validation d’une recherche ou d’un brouillon ordinaire.",
    }
    return result


class MailboxCheck(s.Input):
    sender_id: str


@router.post("/agent/runtime/actions/{action_id}/check-mailbox")
def check_mailbox(
    action_id: str,
    data: MailboxCheck,
    request: Request,
    client=Depends(agent_auth),
    db: Session = Depends(get_db),
):
    row = lease_action(db, client, action_id, request.headers.get("x-agent-lease", ""))
    if row.kind != "operations_check":
        raise HTTPException(403, "Ce contrôle appartient à une mission d’exploitation.")
    from .senders import check_sender, view

    box = d.get(db, m.SendingMailbox, data.sender_id)
    if box.status in {"excluded", "disabled"}:
        raise HTTPException(409, "Boîte exclue des contrôles.")
    if box.last_checked_at and box.last_checked_at > m.now() - timedelta(minutes=10):
        return view(db, box)
    return check_sender(box.id, db.get(m.User, "workspace"), db)


class Proof(s.Input):
    field: Literal[
        "identity",
        "role",
        "industry",
        "employee_count",
        "hubspot_status",
        "domain",
        "email",
        "phone",
        "linkedin_url",
        "relevance",
        "research",
        "b2b",
    ]
    value: str = Field(min_length=2, max_length=4000)
    source_url: str = Field(min_length=8, max_length=1000)
    excerpt: str = Field(min_length=10, max_length=1600)
    observed_at: datetime

    @model_validator(mode="after")
    def validate_proof(self):
        d.validate_url(self.source_url)
        parts = urlsplit(self.source_url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError("Une URL publique HTTPS est requise.")
        if (
            self.observed_at.tzinfo is None
            or self.observed_at > m.now() + timedelta(minutes=10)
            or self.observed_at < m.now() - timedelta(days=30)
        ):
            raise ValueError("La date doit correspondre à la consultation récente de cette source.")
        return self


class Lead(s.Input):
    company: str = Field(min_length=2, max_length=220)
    domain: str = Field(min_length=3, max_length=253)
    country: Literal["FR"] = "FR"
    industry: str = Field(min_length=3, max_length=180)
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=3, max_length=180)
    email: EmailStr | None = None
    phone: str = Field(default="", max_length=50)
    phone_type: Literal["unknown", "direct", "switchboard"] = "unknown"
    linkedin_url: str = Field(default="", max_length=600)
    relevance: str = Field(min_length=30, max_length=2000)
    next_action: str = Field(min_length=10, max_length=1000)
    proofs: list[Proof] = Field(min_length=2, max_length=10)


class ContactUpdate(s.Input):
    email: EmailStr | None = None
    phone: str = Field(default="", max_length=50)
    linkedin_url: str = Field(default="", max_length=600)
    role: str = Field(default="", max_length=180)


class AccountUpdate(s.Input):
    domain: str = Field(default="", max_length=253)
    employee_count: int | None = Field(default=None, ge=1, le=10000000)
    hubspot_status: Literal["", "confirmed", "absent"] = ""


class AccountReview(s.Input):
    account_id: str
    decision: Literal["fit", "exploratory", "not_fit"] = "exploratory"
    business_type: Literal["b2b", "b2c", "unknown"] = "unknown"
    summary: str = Field(min_length=10, max_length=2000)
    proofs: list[Proof] = Field(default_factory=list, max_length=10)
    leads: list[Lead] = Field(default_factory=list, max_length=3)
    account_updates: AccountUpdate | None = None


class AgentResult(s.Input):
    post_call_report: ReportInput | None = None
    business_result: BusinessResult | None = None
    account_reviews: list[AccountReview] = Field(default_factory=list, max_length=10)
    outcome: Literal["completed", "waiting_human", "retry"]
    summary: str = Field(min_length=10, max_length=3000)
    human_reason: str = Field(default="", max_length=2000)
    human_category: Literal[
        "", "private_access", "commercial_commitment", "legal_decision", "manual_channel"
    ] = ""
    retry_minutes: int = Field(default=15, ge=2, le=1440)
    request_id: UUID
    proofs: list[Proof] = Field(default_factory=list, max_length=15)
    leads: list[Lead] = Field(default_factory=list, max_length=5)
    relevance: str = Field(default="", max_length=2000)
    decision: Literal["fit", "exploratory", "not_fit"] = "exploratory"
    next_action: str = Field(default="", max_length=1500)
    contact_updates: ContactUpdate | None = None
    account_updates: AccountUpdate | None = None
    reply_subject: str = Field(default="", max_length=500)
    reply_body: str = Field(default="", max_length=20000)


def add_proofs(db, proofs, account_id, contact_id=None):
    ids = []
    for proof in proofs:
        value = proof.value + "\n\nExtrait observé : " + proof.excerpt
        existing = db.scalar(
            select(m.Evidence)
            .where(
                m.Evidence.contact_id == contact_id,
                m.Evidence.account_id == account_id,
                m.Evidence.field == proof.field,
                m.Evidence.value == value,
                m.Evidence.source_url == proof.source_url,
            )
            .limit(1)
        )
        if existing:
            ids.append(existing.id)
            continue
        record = m.Evidence(
            account_id=account_id,
            contact_id=contact_id,
            field=proof.field,
            value=value,
            source_url=proof.source_url,
            source_type="agent_public_source",
            confidence="signal",
            observed_at=proof.observed_at,
        )
        db.add(record)
        db.flush()
        ids.append(record.id)
    return ids


def apply_account_domain(db, account, updates, proofs):
    if not updates or not updates.domain.strip():
        return False
    candidate = d.normalize_domain(updates.domain)
    if candidate == account.domain:
        return False
    if not account.domain.endswith(".invalid"):
        raise HTTPException(409, "Le domaine de cette entreprise est déjà établi.")
    if not any(
        proof.field == "domain" and d.normalize_domain(proof.source_url) == candidate for proof in proofs
    ):
        raise HTTPException(422, "Le domaine officiel doit être prouvé par une source située sur ce domaine.")
    if db.scalar(
        select(m.Account.id).where(m.Account.domain == candidate, m.Account.id != account.id).limit(1)
    ):
        raise HTTPException(409, "Ce domaine appartient déjà à une autre entreprise.")
    account.domain = candidate
    return True


def import_leads(db, row, leads):
    if not leads or len(leads) > row.payload.get("limit", 5):
        raise HTTPException(422, "Fournissez le nombre de décideurs demandé avec leurs preuves.")
    result = []
    for lead in leads:
        fields = {p.field for p in lead.proofs}
        if not {"identity", "role"}.issubset(fields):
            raise HTTPException(422, "L’identité et la fonction du décideur doivent être sourcées.")
        for value, field in [
            (lead.email, "email"),
            (lead.phone, "phone"),
            (lead.linkedin_url, "linkedin_url"),
        ]:
            if value and not any(
                p.field == field and str(value).casefold() in p.value.casefold() for p in lead.proofs
            ):
                raise HTTPException(422, "Coordonnée sans preuve explicite : " + field)
        domain = d.normalize_domain(lead.domain)
        account, created = d.create_account(
            db, s.AccountCreate(name=lead.company, domain=domain, country="FR", industry=lead.industry)
        )
        if account.lifecycle != "prospect":
            raise HTTPException(409, "Une entreprise de ce lot est déjà exclue ou cliente.")
        named = db.scalar(
            select(m.Contact).where(
                m.Contact.account_id == account.id,
                func.lower(m.Contact.first_name) == lead.first_name.strip().lower(),
                func.lower(m.Contact.last_name) == lead.last_name.strip().lower(),
            )
        )
        if named:
            if not eligible_contact(db, named):
                raise HTTPException(409, "Décideur exclu de la prospection.")
            # Existing contacts do not count as newly found leads.
            continue
        if (
            db.scalar(
                select(m.Suppression.id).where(
                    m.Suppression.kind == "email", m.Suppression.value == str(lead.email)
                )
            )
            if lead.email
            else False
        ):
            raise HTTPException(409, "Adresse exclue de la prospection.")
        contact, new = d.create_contact(
            db,
            s.ContactCreate(
                account_id=account.id,
                campaign_id=row.campaign_id,
                first_name=lead.first_name,
                last_name=lead.last_name,
                role=lead.role,
                email=lead.email,
                phone=lead.phone,
                phone_type=lead.phone_type,
                phone_status="unknown",
                linkedin_url=lead.linkedin_url,
                professional_relevance=lead.relevance,
                sourcing_note="Collecte sur sources professionnelles publiques. Coordonnées non vérifiées techniquement.",
                source_url=lead.proofs[0].source_url,
                source_type="official_website",
                information_status="planned",
            ),
            "workspace",
        )
        if not eligible_contact(db, contact):
            raise HTTPException(409, "Ce contact appartient aux exclusions.")
        if not new:
            continue
        ids = add_proofs(db, lead.proofs, account.id, contact.id)
        contact.qualification = {
            "agent_review": {
                "decision": "exploratory",
                "review_phase": "discovery",
                "reason": lead.relevance,
                "next_action": lead.next_action,
                "evidence_ids": ids,
                "reviewed_at": m.now().isoformat(),
            }
        }
        result.append(
            {
                "id": contact.id,
                "account_id": account.id,
                "name": lead.first_name + " " + lead.last_name,
                "company": lead.company,
                "new": new,
                "evidence_ids": ids,
            }
        )
    if not result:
        raise HTTPException(
            409, "Le lot ne contient aucun nouveau décideur. Recherchez des entreprises supplémentaires."
        )
    return result


@router.post("/agent/runtime/actions/{action_id}/result")
def finish(
    action_id: str,
    data: AgentResult,
    request: Request,
    client=Depends(agent_auth),
    db: Session = Depends(get_db),
):
    row = d.get(db, m.AgentAction, action_id)
    key = digest(str(data.request_id))
    if row.client_id == client.id and row.completion_key == key:
        return action_view(db, row)
    row = lease_action(db, client, action_id, request.headers.get("x-agent-lease", ""))
    if row.payload.get("revision") != target_revision(db, row):
        raise HTTPException(
            409, "La fiche a changé depuis la prise en charge. Reprendre avec le nouveau contexte."
        )
    result = {
        "summary": data.summary,
        "model": client.model,
        "next_action": data.next_action,
        "decision": data.decision,
        "evidence_ids": [],
        "leads": [],
        "public_proofs": [p.model_dump(mode="json") for p in data.proofs]
        if row.kind == "operations_check"
        else [],
    }
    batch_applied = False
    if row.kind == "qualify_batch" and data.account_reviews:
        from .prospect_pipeline import apply_agent_batch

        saved = apply_agent_batch(db, row, data.account_reviews)
        done = {r["account_id"] for r in saved}
        remaining = [aid for aid in row.payload.get("account_ids", []) if aid not in done]
        checkpoints = row.result.get("account_reviews", []) + saved
        result["account_reviews"] = checkpoints
        result["remaining_accounts"] = remaining
        row.payload = {**row.payload, "account_ids": remaining}
        batch_applied = True
        if remaining:
            data.outcome = "retry"
            data.retry_minutes = 2
            result["summary"] = (
                f"{len(checkpoints)} entreprises examinées ; {len(remaining)} restent à traiter."
            )
        elif data.outcome == "retry":
            data.outcome = "completed"
    if data.outcome == "retry":
        row.status = "retry_at"
        row.next_attempt_at = m.now() + timedelta(minutes=data.retry_minutes)
        row.last_error = data.summary
    elif data.outcome == "waiting_human":
        if len(data.human_reason.strip()) < 15 or not data.human_category:
            raise HTTPException(
                422,
                "Précisez la catégorie critique et l’intervention humaine indispensable. Une recherche ou une erreur temporaire doit être traitée par l’agent.",
            )
        row.status = "waiting_human"
        row.executor = "human"
        row.human_reason = data.human_reason
        result["evidence_ids"] = (
            add_proofs(db, data.proofs, row.account_id, row.contact_id) if row.account_id else []
        )
        notify(db, row)
    else:
        if row.kind == "post_call_report":
            from .call_reports import apply_agent_result

            result.update(apply_agent_result(db, row, data.post_call_report))
        elif row.kind == "mission_step":
            from .business_tools import apply_result

            if data.business_result is None:
                raise HTTPException(422, "Livrable métier structuré requis")
            result.update(apply_result(db, row, data.business_result))
        elif row.kind == "qualify_batch":
            from .prospect_pipeline import apply_agent_batch

            if not batch_applied:
                result["account_reviews"] = apply_agent_batch(db, row, data.account_reviews)
        elif row.kind == "discover_leads":
            result["leads"] = import_leads(db, row, data.leads)
            remaining = row.payload.get("limit", 5) - len(result["leads"])
            if remaining > 0:
                enqueue(
                    db,
                    "discover-remaining:" + row.id,
                    "discover_leads",
                    "Poursuivre la recherche · " + str(remaining) + " décideur(s)",
                    campaign_id=row.campaign_id,
                    priority=row.priority,
                    payload={**row.payload, "limit": remaining},
                    target_url="/contacts",
                )
        elif row.kind in {"qualify_contact", "qualify_account"}:
            if not data.proofs or len(data.next_action.strip()) < 10:
                raise HTTPException(422, "La qualification nécessite des preuves et une prochaine action.")
            result["evidence_ids"] = add_proofs(db, data.proofs, row.account_id, row.contact_id)
            account = d.get(db, m.Account, row.account_id)
            apply_account_domain(db, account, data.account_updates, data.proofs)
            if data.account_updates:
                for field, value in data.account_updates.model_dump(exclude_none=True).items():
                    if field == "domain":
                        continue
                    if value:
                        if not any(p.field == field for p in data.proofs):
                            raise HTTPException(422, "Une preuve est requise pour modifier " + field)
                        setattr(account, field, value)
            if row.contact_id:
                contact = d.get(db, m.Contact, row.contact_id)
                if data.contact_updates:
                    for field, value in data.contact_updates.model_dump(exclude_none=True).items():
                        if value and value != getattr(contact, field):
                            if not any(
                                p.field == field and str(value).casefold() in p.value.casefold()
                                for p in data.proofs
                            ):
                                raise HTTPException(
                                    422, "Coordonnée ou rôle sans preuve explicite : " + field
                                )
                            if field == "email" and contact.email:
                                raise HTTPException(
                                    409,
                                    "Conservez l’adresse existante ; une modification de destinataire nécessite une revue.",
                                )
                            if field == "email":
                                value = str(value).lower()
                                if db.scalar(
                                    select(m.Contact.id).where(
                                        m.Contact.email == value, m.Contact.id != contact.id
                                    )
                                ):
                                    raise HTTPException(409, "Cette adresse est déjà rattachée à un contact.")
                                contact.email_status, contact.email_verified_at = "unknown", None
                            if field == "phone":
                                value = d.normalize_phone(value)
                                contact.phone_status, contact.phone_type = "unknown", "unknown"
                            if field == "linkedin_url":
                                d.validate_url(value)
                            setattr(contact, field, value)
                    if not eligible_contact(db, contact):
                        raise HTTPException(409, "La nouvelle coordonnée figure dans les exclusions.")
                if data.relevance:
                    contact.professional_relevance = data.relevance
                score = d.score_contact(db, contact)
                if (
                    data.decision == "fit"
                    and score["unknown"] == 0
                    and all(check["status"] == "match" for check in score["checks"])
                    and not contact.approved
                ):
                    try:
                        d.approve_contact(db, contact, "workspace")
                        d.prepare_contact(db, contact, "workspace")
                        result["automatically_qualified"] = True
                    except HTTPException as error:
                        result["qualification_pending"] = error.detail
                contact.qualification = {
                    **contact.qualification,
                    "agent_review": {
                        **result,
                        "review_phase": "qualification",
                        "reviewed_at": m.now().isoformat(),
                    },
                }
            d.log(
                db,
                "agent_qualification",
                contact=db.get(m.Contact, row.contact_id) if row.contact_id else None,
                campaign_id=row.campaign_id,
                summary=data.summary,
                source="agent",
                data={"action_id": row.id, "decision": data.decision, "evidence_ids": result["evidence_ids"]},
            )
        elif row.kind == "reply_draft":
            from .conversations import ReplyDraft, persist_reply

            event = d.get(db, m.Event, row.event_id)
            existing = db.scalar(
                select(m.Message)
                .where(
                    m.Message.reply_event_id == event.id,
                    m.Message.purpose == "reply",
                    m.Message.status.in_(["draft", "queued", "sending", "uncertain"]),
                )
                .limit(1)
            )
            if existing:
                raise HTTPException(409, "Une réponse est déjà préparée pour cette conversation.")
            if not data.reply_body.strip() or not data.reply_subject.strip():
                raise HTTPException(422, "Rédigez l’objet et le corps de la réponse.")
            draft = persist_reply(
                event.id,
                ReplyDraft(
                    subject=data.reply_subject,
                    body=data.reply_body,
                    request_id=data.request_id,
                    expected_event_updated_at=event.updated_at,
                ),
                db.get(m.User, "workspace"),
                db,
            )
            result["message_id"] = draft["id"]
            enqueue(
                db,
                "send-reply:" + draft["id"],
                "send_reply",
                "Envoyer la réponse à " + d.get(db, m.Contact, row.contact_id).first_name,
                contact_id=row.contact_id,
                account_id=row.account_id,
                campaign_id=row.campaign_id,
                event_id=row.event_id,
                priority=15,
                target_url=row.target_url,
                payload={"message_id": draft["id"], "draft_revision": draft["updated_at"]},
            )
        elif row.kind == "operations_check":
            if len(data.next_action.strip()) < 10:
                raise HTTPException(422, "Précisez le résultat des contrôles et la prochaine vérification.")
        elif row.kind == "review_task":
            if not data.next_action.strip() or not data.proofs:
                raise HTTPException(
                    422, "Consignez le travail réalisé et ses preuves avant de terminer l’examen."
                )
            result["evidence_ids"] = add_proofs(db, data.proofs, row.account_id, row.contact_id)
            task = d.get(db, m.Task, row.task_id)
            task.notes = (
                task.notes + "\n\nAgent : " + data.summary + "\nSuite : " + data.next_action
            ).strip()
        else:
            raise HTTPException(409, "Action réservée à une personne.")
        row.status = "completed"
        row.completed_at = m.now()
    row.result = result
    row.completion_key = key
    row.lease_hash = ""
    row.lease_until = None
    client.last_seen_at = m.now()
    db.commit()
    return action_view(db, row)


class Failure(s.Input):
    reason: Literal["quota", "timeout", "error", "budget", "auth"]
    message: str = Field(min_length=5, max_length=400)
    retry_minutes: int = Field(default=30, ge=2, le=1440)


@router.post("/agent/runtime/actions/{action_id}/failure")
def failure(
    action_id: str, data: Failure, request: Request, client=Depends(agent_auth), db: Session = Depends(get_db)
):
    row = lease_action(db, client, action_id, request.headers.get("x-agent-lease", ""))
    row.lease_hash = ""
    row.lease_until = None
    row.last_error = data.message
    if data.reason in {"quota", "budget"} or row.attempts < 6:
        row.status = "retry_at"
        row.next_attempt_at = m.now() + timedelta(minutes=max(data.retry_minutes, min(360, 2**row.attempts)))
        if data.reason == "quota":
            client.next_run_at = row.next_attempt_at
            client.state = {
                **client.state,
                "runtime_status": "quota",
                "message": "Limite de la souscription atteinte ; reprise différée.",
            }
    else:
        row.status = "waiting_human"
        row.executor = "human"
        row.human_reason = "L’agent a rencontré six échecs malgré les reprises automatiques. Examiner l’action et relancer après correction."
        notify(db, row)
    db.commit()
    return action_view(db, row)


@router.get("/agent/runtime/notifications")
def pending_notifications(client=Depends(agent_auth), db: Session = Depends(get_db)):
    rows = list(
        db.scalars(
            select(m.ActionNotification)
            .join(m.AgentAction)
            .where(
                m.ActionNotification.read_at.is_(None),
                m.ActionNotification.delivered_at.is_(None),
                m.AgentAction.status.in_(HUMAN_STATUSES),
            )
            .order_by(m.ActionNotification.created_at)
            .limit(20)
        )
    )
    return {
        "items": [
            {
                "id": row.id,
                "title": row.title,
                "body": row.body,
                "url": "/agent?view=human&action=" + row.action_id,
            }
            for row in rows
        ]
    }


@router.post("/agent/runtime/notifications/{notification_id}/delivered")
def delivered(notification_id: str, client=Depends(agent_auth), db: Session = Depends(get_db)):
    row = d.get(db, m.ActionNotification, notification_id)
    row.delivered_at = m.now()
    db.commit()
    return {"ok": True}
