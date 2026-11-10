"""Conversation history and explicit, durable replies. Never resumes a sequence."""

import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import domain as d
from . import inbox, mail, senders
from . import models as m
from . import schemas as s
from .config import settings
from .db import SessionLocal, get_db
from .providers import ProviderError
from .security import current_user, decrypt_config
from .workspaces import page_result

router = APIRouter(prefix="/api", tags=["conversations"])


def received():
    return select(m.Event).where(m.Event.kind.in_(inbox.KINDS))


@router.get("/conversations")
def conversations(
    status: Literal["open", "done", "all"] = "open",
    q: str = Query("", max_length=120),
    kind: str = "",
    channel: str = "",
    campaign_id: str = "",
    sender_id: str = "",
    page: int = Query(1, ge=1),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    # Group before paginating: a contact with ten replies occupies one row.
    grouping = func.coalesce(m.Event.contact_id, m.Event.id)
    candidates = (
        received()
        .outerjoin(m.Contact, m.Contact.id == m.Event.contact_id)
        .outerjoin(m.Account, m.Account.id == m.Event.account_id)
    )
    if q.strip():
        candidates = candidates.where(
            func.concat_ws(
                " ",
                m.Contact.first_name,
                m.Contact.last_name,
                m.Contact.email,
                m.Account.name,
                m.Event.summary,
            ).icontains(q.strip(), autoescape=True)
        )
    if kind:
        candidates = candidates.where(
            func.coalesce(m.Event.data["inbox"]["classification"].as_string(), m.Event.kind) == kind
        )
    if channel:
        candidates = candidates.where(m.Event.channel == channel)
    if campaign_id:
        candidates = candidates.where(m.Event.campaign_id == campaign_id)
    if sender_id:
        candidates = candidates.outerjoin(
            m.Message, m.Message.id == m.Event.data["message_id"].as_string()
        ).where(
            func.coalesce(
                m.Event.data["sender_id"].as_string(), m.Message.sender_id, m.Contact.email_sender_id
            )
            == sender_id
        )
    ranked = candidates.with_only_columns(
        m.Event.id.label("id"),
        func.row_number()
        .over(partition_by=grouping, order_by=(m.Event.created_at.desc(), m.Event.id))
        .label("position"),
    ).subquery()
    open_contacts = (
        received().where(inbox.state_expression() == "open").with_only_columns(grouping).subquery()
    )
    query = received().where(m.Event.id.in_(select(ranked.c.id).where(ranked.c.position == 1)))
    totals = {
        key: db.scalar(
            select(func.count()).select_from(
                query.where(
                    grouping.in_(select(open_contacts))
                    if key == "open"
                    else grouping.not_in(select(open_contacts))
                ).subquery()
            )
        )
        or 0
        for key in ["open", "done"]
    }
    if status != "all":
        query = query.where(
            grouping.in_(select(open_contacts))
            if status == "open"
            else grouping.not_in(select(open_contacts))
        )
    rows, total, page = page_result(db, query.order_by(m.Event.created_at.desc(), m.Event.id), page, 20)
    items = []
    for row in rows:
        count = (
            db.scalar(
                select(func.count()).select_from(
                    received().where(m.Event.contact_id == row.contact_id).subquery()
                )
            )
            if row.contact_id
            else 1
        )
        items.append({**inbox.event_view(db, row), "reply_count": count})
    return {"items": items, "total": total, "page": page, "page_size": 20, "totals": totals}


def response_event(db, event_id):
    event = d.get(db, m.Event, event_id)
    if event.kind == "call_followup_request":
        from .call_models import CallSession
        from .mission_models import CallReport

        report = db.get(CallReport, event.data.get("report_id", ""))
        call = db.get(CallSession, event.data.get("call_id", ""))
        if (
            not report
            or not call
            or report.call_id != call.id
            or call.status != "completed"
            or event.account_id != call.account_id
        ):
            raise HTTPException(409, "Appel source du suivi indisponible")
        return event
    return inbox.get_event(db, event_id)


def reply_context(db, event):
    contact = db.get(m.Contact, event.contact_id) if event.contact_id else None
    linked = db.get(m.Message, event.data.get("message_id")) if event.data.get("message_id") else None
    sender_id = event.data.get("sender_id") or (
        linked.sender_id if linked else contact.email_sender_id if contact else None
    )
    sender = db.get(m.SendingMailbox, sender_id) if sender_id else None
    campaign = db.get(
        m.Campaign,
        linked.campaign_id if linked else event.campaign_id or (contact.campaign_id if contact else None),
    )
    return contact, linked, sender, campaign


def reply_reasons(db, event, *, sending=False, message=None):
    contact, linked, sender, campaign = reply_context(db, event)
    reasons = []
    if not contact or not contact.email:
        reasons.append("Contact ou adresse email indisponible.")
    elif d.is_suppressed(db, contact) or contact.status == "suppressed":
        reasons.append("Ce contact a demandé à ne plus être contacté.")
    elif contact.email_status == "invalid":
        reasons.append("L’adresse email est invalide.")
    if contact and db.get(m.Account, contact.account_id).lifecycle == "excluded":
        reasons.append("Cette entreprise est exclue.")
    if (event.channel != "email" and event.kind != "call_followup_request") or event.kind in {
        "stop",
        "bounce",
        "out_of_office",
    }:
        reasons.append("Choisissez une réponse email humaine pour poursuivre l’échange.")
    incoming = event.data.get("email", {})
    if (
        contact
        and incoming.get("from_email")
        and incoming["from_email"].casefold() != contact.email.casefold()
    ):
        reasons.append("L’adresse du contact a changé depuis la réception. Vérifiez la conversation.")
    if not sender:
        reasons.append("La boîte de cette conversation n’est pas identifiée.")
    if not campaign:
        reasons.append("La campagne de cette conversation n’est pas identifiée.")
    if event.kind == "call_followup_request":
        try:
            response_event(db, event.id)
        except HTTPException:
            reasons.append("Le compte rendu source n’est plus disponible.")
        account = db.get(m.Account, contact.account_id) if contact else None
        if not account or account.outreach_paused or (contact and contact.paused):
            reasons.append("Le prospect est en pause.")
    if sending:
        state = d.state(db)
        if state.paused:
            reasons.append("Les envois sont en pause générale.")
        if not settings().live_actions_enabled:
            reasons.append("Les envois réels sont désactivés sur ce serveur.")
        if not settings().public_url.startswith("https://"):
            reasons.append("L’envoi nécessite HTTPS.")
        if sender:
            reasons.extend(senders.readiness(sender, reception=True, cold=False))
            reasons.extend(
                senders.limits_reasons(db, sender, state=state, exclude_id=message.id if message else None)
            )
        if message and (
            not sender
            or message.sender_id != sender.id
            or message.sender_version != sender.config_version
            or message.recipient_email != (contact.email if contact else None)
        ):
            reasons.append("Le destinataire ou la boîte a changé. Relisez le brouillon.")
    return list(dict.fromkeys(reasons))


@router.get("/inbox/{event_id}/conversation")
def conversation(
    event_id: str, page: int = Query(1, ge=1), user=Depends(current_user), db: Session = Depends(get_db)
):
    event = inbox.get_event(db, event_id)
    incoming = (
        list(
            db.scalars(
                received()
                .where(m.Event.contact_id == event.contact_id)
                .order_by(m.Event.created_at, m.Event.id)
            )
        )
        if event.contact_id
        else [event]
    )
    outgoing = (
        list(
            db.scalars(
                select(m.Message)
                .where(
                    m.Message.contact_id == event.contact_id,
                    or_(
                        m.Message.purpose == "reply",
                        m.Message.status.in_(["sent", "uncertain", "sending", "failed"]),
                    ),
                )
                .order_by(m.Message.created_at)
            )
        )
        if event.contact_id
        else []
    )
    items = []
    for row in incoming:
        email = row.data.get("email", {})
        items.append(
            {
                "id": row.id,
                "direction": "in",
                "at": row.created_at.isoformat(),
                "subject": email.get("subject", "Réponse reçue"),
                "body": email.get("body", row.summary),
                "legacy": not bool(email),
                "truncated": email.get("truncated", False),
                "attachments": email.get("attachments", []),
                "status": row.kind,
                "from_email": email.get("from_email", ""),
                "review_status": inbox.inbox_state(db, row),
            }
        )
    for row in outgoing:
        items.append(
            {
                **d.serialize(row),
                "direction": "out",
                "at": (row.sent_at or row.created_at).isoformat(),
                "body": row.sent_body or row.body,
                "legacy": row.status == "sent" and not row.sent_body,
                "from_email": row.sender_email,
            }
        )
    latest = next((row for row in reversed(incoming) if row.channel == "email"), event)
    latest_item = next((row for row in items if row["id"] == latest.id), None)
    items.sort(key=lambda row: (row["at"], row["id"]), reverse=True)
    total = len(items)
    page = min(page, max(1, (total + 9) // 10))
    items = items[(page - 1) * 10 : page * 10][::-1]
    contact, linked, sender, campaign = reply_context(db, latest)
    draft = next(
        (
            row
            for row in reversed(outgoing)
            if row.purpose == "reply"
            and row.status in {"draft", "queued", "sending", "uncertain"}
            and row.reply_event_id == latest.id
        ),
        None,
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": 10,
        "latest_message": latest_item,
        "reply_to_event_id": latest.id,
        "latest_updated_at": latest.updated_at.isoformat(),
        "recipient": contact.email if contact else "",
        "sender": {"id": sender.id, "email": sender.from_email} if sender else None,
        "draft": d.serialize(draft),
        "draft_reasons": reply_reasons(db, latest),
        "send_reasons": reply_reasons(db, latest, sending=True),
        "subject": (
            "Re: "
            + latest.data.get("email", {})
            .get("subject", linked.subject if linked else "Votre message")
            .removeprefix("Re: ")
        )[:500],
    }


class ReplyDraft(s.Input):
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    request_id: UUID
    expected_event_updated_at: datetime
    draft_id: str | None = None
    expected_updated_at: datetime | None = None


@router.post("/inbox/{event_id}/reply-draft")
def save_reply(event_id: str, data: ReplyDraft, user=Depends(current_user), db: Session = Depends(get_db)):
    return persist_reply(event_id, data, user, db, commit=True)


def persist_reply(event_id, data, user, db, *, commit=False):
    d.state(db, lock=True)
    event = response_event(db, event_id)
    key = "reply-draft:" + str(data.request_id)
    previous = db.scalar(select(m.Event).where(m.Event.external_id == key))
    if previous:
        return d.serialize(d.get(db, m.Message, previous.data["message_id"]))
    if event.updated_at != data.expected_event_updated_at:
        raise HTTPException(409, "La conversation a changé. Rechargez-la.")
    reasons = reply_reasons(db, event)
    if reasons:
        raise HTTPException(409, " ".join(reasons))
    if not data.body.strip() or not data.subject.strip() or re.search(r"[\r\n]", data.subject):
        raise HTTPException(422, "Objet ou contenu invalide.")
    # Same price policy as existing outgoing emails, including manual replies.
    from . import email_policy

    if email_policy.contains_price(data.subject, data.body):
        raise HTTPException(422, email_policy.REASON)
    contact, linked, sender, campaign = reply_context(db, event)
    newer = db.scalar(
        received()
        .where(
            m.Event.contact_id == event.contact_id,
            m.Event.channel == "email",
            m.Event.created_at > event.created_at,
        )
        .limit(1)
    )
    if newer:
        raise HTTPException(409, "Une réponse plus récente est arrivée. Ouvrez-la avant de rédiger.")
    if data.draft_id:
        message = d.get(db, m.Message, data.draft_id)
        if (
            message.purpose != "reply"
            or message.reply_event_id != event.id
            or message.status != "draft"
            or message.updated_at != data.expected_updated_at
        ):
            raise HTTPException(409, "Le brouillon a changé ou a déjà été envoyé.")
    else:
        existing = db.scalar(
            select(m.Message).where(
                m.Message.reply_event_id == event.id,
                m.Message.purpose == "reply",
                m.Message.status.in_(["draft", "queued", "sending", "uncertain"]),
            )
        )
        if existing:
            raise HTTPException(
                409, "Un brouillon ou un envoi existe déjà pour cette réponse. Rechargez la conversation."
            )
        message = m.Message(
            contact_id=contact.id,
            campaign_id=campaign.id,
            purpose="reply",
            step=-1,
            reply_event_id=event.id,
            dedupe_key=key,
        )
        db.add(message)
    incoming_id = event.data.get("email", {}).get("message_id", "")
    parent = incoming_id or (linked.provider_id if linked else "") or ""
    refs = ([linked.provider_id] if linked and linked.provider_id else []) + ([parent] if parent else [])
    message.subject, message.body = data.subject.strip(), data.body.strip()
    # A reconciled, unsent attempt may be edited before an explicit new send.
    message.sent_body = ""
    message.failure_reason = ""
    message.recipient_email = contact.email
    message.sender_id, message.sender_version = sender.id, sender.config_version
    message.sender_email, message.sender_domain, message.sender_provider = (
        sender.from_email,
        sender.domain,
        sender.provider_group,
    )
    message.in_reply_to = parent if re.fullmatch(r"<[^<>\s]+>", parent) else ""
    message.reference_ids = list(dict.fromkeys(ref for ref in refs if re.fullmatch(r"<[^<>\s]+>", ref)))
    message.approved_by = None
    db.flush()
    d.log(
        db,
        "reply_draft_saved",
        contact=contact,
        campaign_id=campaign.id,
        actor_id=user.id,
        external_id=key,
        summary="Brouillon de réponse enregistré.",
        data={"message_id": message.id},
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return d.serialize(message)


class QueueReply(s.Input):
    expected_updated_at: datetime
    request_id: UUID


@router.post("/reply-messages/{message_id}/send")
def queue_reply(message_id: str, data: QueueReply, user=Depends(current_user), db: Session = Depends(get_db)):
    return schedule_reply(message_id, data, user, db, commit=True)


def schedule_reply(message_id, data, user, db, *, commit=False):
    d.state(db, lock=True)
    message = d.get(db, m.Message, message_id)
    if message.purpose != "reply":
        raise HTTPException(404, "Réponse introuvable.")
    key = "reply-send:" + str(data.request_id)
    previous = db.scalar(select(m.Job).where(m.Job.idempotency_key == key, m.Job.message_id == message.id))
    if previous:
        return d.serialize(previous)
    if message.status != "draft" or message.updated_at != data.expected_updated_at:
        raise HTTPException(409, "Relisez la version actuelle du brouillon.")
    event = response_event(db, message.reply_event_id)
    reasons = reply_reasons(db, event, sending=True, message=message)
    newer = db.scalar(
        received()
        .where(m.Event.contact_id == event.contact_id, m.Event.created_at > message.created_at)
        .limit(1)
    )
    if newer:
        reasons.append("Une nouvelle réponse est arrivée. Relisez la conversation avant envoi.")
    if reasons:
        raise HTTPException(409, " ".join(reasons))
    message.status, message.approved_by, message.due_at = "queued", user.id, m.now()
    job = m.Job(
        kind="reply",
        provider="smtp",
        contact_id=message.contact_id,
        campaign_id=message.campaign_id,
        message_id=message.id,
        idempotency_key=key,
    )
    db.add(job)
    if commit:
        db.commit()
    else:
        db.flush()
    return d.serialize(job)


@router.post("/reply-messages/{message_id}/cancel")
def cancel_reply(message_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    d.state(db, lock=True)
    message = d.get(db, m.Message, message_id)
    if message.purpose != "reply" or message.status not in {"draft", "queued"}:
        raise HTTPException(409, "Cette réponse ne peut plus être annulée.")
    message.status = "cancelled"
    for job in db.scalars(select(m.Job).where(m.Job.message_id == message.id, m.Job.status == "queued")):
        job.status = "cancelled"
    db.commit()
    return d.serialize(message)


def reply_tick():
    with SessionLocal() as db:
        state = d.state(db, lock=True)
        for job in db.scalars(
            select(m.Job).where(
                m.Job.kind == "reply",
                m.Job.status == "running",
                m.Job.locked_at < m.now() - timedelta(minutes=3),
            )
        ):
            job.status, job.error = (
                "uncertain",
                "Worker interrompu : vérifier SMTP avant toute nouvelle tentative.",
            )
            message = db.get(m.Message, job.message_id)
            if message:
                message.status, message.failure_reason = "uncertain", job.error
        if state.paused or not settings().live_actions_enabled:
            db.commit()
            return 0
        job = db.scalar(
            select(m.Job)
            .where(m.Job.kind == "reply", m.Job.status == "queued", m.Job.run_at <= m.now())
            .order_by(m.Job.run_at)
            .limit(1)
        )
        if not job:
            db.commit()
            return 0
        message = db.get(m.Message, job.message_id)
        event = db.get(m.Event, message.reply_event_id) if message else None
        if not message or not event or message.status != "queued":
            job.status = "cancelled"
            db.commit()
            return 1
        reasons = reply_reasons(db, event, sending=True, message=message)
        if reasons:
            job.error = " ".join(reasons)
            job.run_at = m.now() + timedelta(minutes=1)
            db.commit()
            return 0
        contact, linked, sender, campaign = reply_context(db, event)
        config = mail.campaign_config(decrypt_config(sender.encrypted_config), campaign)
        message.sent_body = mail.content(message, contact, config)[0]
        message.provider_id = message.provider_id or f"<lumelia.reply.{message.id}@{sender.domain}>"
        message.attempted_at = m.now()
        message.status = "sending"
        job.status, job.locked_at = "running", m.now()
        job.attempts += 1
        job_id = job.id
        db.commit()
    with SessionLocal() as db:
        d.state(db, lock=True)
        job = d.get(db, m.Job, job_id)
        message = d.get(db, m.Message, job.message_id)
        event = db.get(m.Event, message.reply_event_id)
        reasons = (
            reply_reasons(db, event, sending=True, message=message) if event else ["Conversation effacée."]
        )
        if job.status != "running" or message.status != "sending":
            reasons.append("Envoi annulé.")
        if reasons:
            job.status, message.status = "cancelled", "cancelled"
            job.error = " ".join(reasons)
            message.attempted_at = None
        else:
            contact, linked, sender, campaign = reply_context(db, event)
            config = mail.campaign_config(decrypt_config(sender.encrypted_config), campaign)
            try:
                result = mail.send(message, contact, config)
                message.status, message.sent_at = "sent", m.now()
                job.status, job.result = "completed", result
                d.log(
                    db,
                    "email_sent",
                    contact=contact,
                    campaign_id=message.campaign_id,
                    channel="email",
                    source="smtp",
                    summary="Réponse acceptée par SMTP.",
                    data={"message_id": message.id, "purpose": "reply"},
                    external_id="sent:" + job.id,
                )
            except ProviderError as exc:
                job.status = message.status = "uncertain" if exc.uncertain else "failed"
                job.error = message.failure_reason = str(exc)
        db.commit()
        return 1
