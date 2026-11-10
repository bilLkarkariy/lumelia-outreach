"""Read-only, filtered lists for the workspace tables. Existing API shapes stay intact."""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from . import domain as d
from . import models as m
from . import prospects
from .db import get_db
from .security import current_user

router = APIRouter(prefix="/api/lists", tags=["workspace lists"])
Resource = Literal["contacts", "accounts", "tasks", "opportunities", "messages"]


def count(db, query):
    return db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0


def require_option(value, options, label):
    if value and value not in options:
        raise HTTPException(422, f"Filtre {label} invalide.")


def person(db, contact):
    if contact is None:
        return None
    return {**d.serialize(contact), "account": d.serialize(db.get(m.Account, contact.account_id))}


@router.get("/{resource}")
def workspace_list(
    resource: Resource,
    q: str = Query("", max_length=120),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sort: str = "",
    direction: Literal["asc", "desc"] = "asc",
    status: str = "",
    campaign_id: str = Query("", max_length=36),
    group_id: str = Query("", max_length=36),
    account_id: str = Query("", max_length=36),
    country: str = Query("", max_length=2),
    email_status: str = "",
    phone: str = "",
    qualification: str = "",
    lifecycle: str = "",
    employees: str = "",
    crm: str = "",
    has_contacts: str = "",
    channel: str = "",
    period: str = "",
    focus: str = "",
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    require_option(page_size, {10, 25, 50, 100}, "de pagination")
    model = {
        "contacts": m.Contact,
        "accounts": m.Account,
        "tasks": m.Task,
        "opportunities": m.Opportunity,
        "messages": m.Message,
    }[resource]
    query = select(model)
    if resource == "contacts":
        query = query.join(m.Account, m.Account.id == m.Contact.account_id)
    elif resource in {"tasks", "messages"}:
        query = query.join(m.Contact, m.Contact.id == model.contact_id).join(
            m.Account, m.Account.id == m.Contact.account_id
        )
    elif resource == "opportunities":
        query = query.join(m.Account, m.Account.id == m.Opportunity.account_id).outerjoin(
            m.Contact, m.Contact.id == model.contact_id
        )

    contact_count = (
        select(func.count())
        .where(m.Contact.account_id == m.Account.id)
        .correlate(m.Account)
        .scalar_subquery()
    )
    evidence_count = (
        select(func.count())
        .where(m.Evidence.account_id == m.Account.id)
        .correlate(m.Account)
        .scalar_subquery()
    )
    next_task = (
        select(func.min(m.Task.due_at))
        .where(m.Task.contact_id == m.Contact.id, m.Task.status == "pending")
        .correlate(m.Contact)
        .scalar_subquery()
    )
    campaign_name = (
        select(m.Campaign.name).where(m.Campaign.id == model.campaign_id).correlate(model).scalar_subquery()
        if resource != "accounts"
        else None
    )
    sorts = {
        "contacts": {
            "name": func.lower(m.Contact.last_name + " " + m.Contact.first_name),
            "company": func.lower(m.Account.name),
            "status": case(
                (m.Account.lifecycle == "excluded", "excluded"),
                (m.Contact.status == "suppressed", "suppressed"),
                (
                    or_(m.Contact.paused, m.Account.outreach_paused, m.Account.lifecycle == "customer"),
                    "paused",
                ),
                else_=m.Contact.status,
            ),
            "email": m.Contact.email,
            "created": m.Contact.created_at,
            "updated": m.Contact.updated_at,
            "next_action": next_task,
            "campaign": campaign_name,
            "score": prospects.score_expression(),
            "last_call": prospects.last_call_expression(),
            "last_email": prospects.last_email_expression(),
        },
        "accounts": {
            "name": func.lower(m.Account.name),
            "country": m.Account.country,
            "employees": m.Account.employee_count,
            "industry": m.Account.industry,
            "status": m.Account.lifecycle,
            "contacts": contact_count,
            "evidence": evidence_count,
            "updated": m.Account.updated_at,
        },
        "tasks": {
            "title": func.lower(m.Task.title),
            "company": func.lower(m.Account.name),
            "due": m.Task.due_at,
            "status": m.Task.status,
            "channel": m.Task.channel,
            "created": m.Task.created_at,
        },
        "opportunities": {
            "title": func.lower(m.Opportunity.title),
            "company": func.lower(m.Account.name),
            "status": m.Opportunity.stage,
            "amount": m.Opportunity.amount_cents,
            "deposit": m.Opportunity.deposit_cents,
            "next_action": m.Opportunity.next_action_at,
            "updated": m.Opportunity.updated_at,
        },
        "messages": {
            "subject": func.lower(m.Message.subject),
            "company": func.lower(m.Account.name),
            "status": m.Message.status,
            "due": m.Message.due_at,
            "sent": m.Message.sent_at,
            "created": m.Message.created_at,
            "campaign": campaign_name,
        },
    }[resource]
    sort = (
        sort
        or {
            "contacts": "name",
            "accounts": "name",
            "tasks": "due",
            "opportunities": "updated",
            "messages": "due",
        }[resource]
    )
    require_option(sort, sorts, "de tri")
    statuses = {
        "contacts": {"candidate", "qualified", "conversation", "suppressed", "paused"},
        "accounts": {"prospect", "customer", "excluded", "paused"},
        "tasks": {"pending", "completed", "cancelled"},
        "opportunities": {"conversation", "meeting", "proposal", "won", "lost"},
        "messages": {
            "draft",
            "approved",
            "queued",
            "sent",
            "cancelled",
            "failed",
            "uncertain",
            "simulated",
        },
    }
    require_option(status, statuses[resource], "de statut")
    require_option(email_status, {"valid", "unknown", "invalid", "risky", "missing"}, "email")
    require_option(phone, {"present", "direct", "missing"}, "téléphone")
    require_option(qualification, {"approved", "unapproved"}, "qualification")
    require_option(focus, {"active", "priority", "call", "incomplete", "never_contacted"}, "priorité")
    require_option(lifecycle, {"prospect", "customer", "excluded"}, "entreprise")
    require_option(employees, {"unknown", "1-19", "20-49", "50-99", "100-249", "250+"}, "effectif")
    require_option(crm, {"unknown", "signal", "confirmed", "absent"}, "CRM")
    require_option(has_contacts, {"yes", "no"}, "interlocuteurs")
    require_option(channel, {"review", "phone", "linkedin", "email"}, "canal")
    require_option(period, {"overdue", "today", "week", "upcoming", "undated"}, "échéance")

    if account_id:
        d.get(db, m.Account, account_id)
        query = query.where(m.Account.id == account_id)
    if group_id:
        d.get(db, m.CampaignGroup, group_id)
        scope_ids = select(m.Campaign.id).where(m.Campaign.group_id == group_id)
        if resource == "accounts":
            query = query.where(
                m.Account.id.in_(select(m.Contact.account_id).where(m.Contact.campaign_id.in_(scope_ids)))
            )
        else:
            query = query.where(model.campaign_id.in_(scope_ids))
    unfiltered = count(db, query)
    summary = {"total": unfiltered}
    if resource == "contacts":
        held = or_(
            m.Contact.paused,
            m.Account.outreach_paused,
            m.Account.lifecycle != "prospect",
            m.Contact.status == "suppressed",
        )
        summary.update(
            approved=count(db, query.where(m.Contact.approved)),
            email_verified=count(
                db, query.where(m.Contact.email_status == "valid", m.Contact.email.is_not(None))
            ),
            direct_phones=count(db, query.where(m.Contact.phone != "", m.Contact.phone_type == "direct")),
            paused=count(db, query.where(held)),
        )
    elif resource == "accounts":
        summary.update(
            prospects=count(db, query.where(m.Account.lifecycle == "prospect")),
            with_contacts=count(db, query.where(contact_count > 0)),
            excluded=count(db, query.where(m.Account.lifecycle == "excluded")),
        )
    elif resource == "tasks":
        summary.update(
            pending=count(db, query.where(m.Task.status == "pending")),
            overdue=count(db, query.where(m.Task.status == "pending", m.Task.due_at < m.now())),
            completed=count(db, query.where(m.Task.status == "completed")),
        )
    elif resource == "messages":
        summary.update(
            drafts=count(db, query.where(m.Message.status == "draft")),
            sent=count(db, query.where(m.Message.status == "sent")),
            cancelled=count(db, query.where(m.Message.status == "cancelled")),
        )
    else:
        summary.update(
            open=count(db, query.where(m.Opportunity.stage.not_in(["won", "lost"]))),
            won=count(db, query.where(m.Opportunity.stage == "won")),
            amount_cents=db.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            query.where(m.Opportunity.stage.not_in(["won", "lost"])).subquery().c.amount_cents
                        ),
                        0,
                    )
                )
            ),
        )

    search_fields: list[InstrumentedAttribute[Any]] = [m.Account.name, m.Account.domain, m.Account.industry]
    if resource != "accounts":
        search_fields += [
            m.Contact.first_name,
            m.Contact.last_name,
            m.Contact.role,
            m.Contact.email,
            m.Contact.phone,
        ]
    if resource in {"tasks", "opportunities"}:
        search_fields.append(model.title)
    if resource == "messages":
        search_fields.append(m.Message.subject)
    if q.strip():
        query = query.where(func.concat_ws(" ", *search_fields).icontains(q.strip(), autoescape=True))
    if country:
        query = query.where(m.Account.country == country.upper())
    if status:
        if status == "paused":
            query = query.where(held if resource == "contacts" else m.Account.outreach_paused)
        else:
            column = (
                m.Account.lifecycle
                if resource == "accounts"
                else m.Opportunity.stage
                if resource == "opportunities"
                else model.status
            )
            query = query.where(column == status)
            if resource == "contacts" and status in {"candidate", "qualified", "conversation"}:
                query = query.where(~held)
    if campaign_id and resource != "accounts":
        query = query.where(
            model.campaign_id.is_(None) if campaign_id == "none" else model.campaign_id == campaign_id
        )
    if resource == "contacts":
        if focus in {"active", "priority", "call", "never_contacted"}:
            query = query.where(~held)
        if focus == "priority":
            query = query.where(prospects.score_expression() >= 70)
        elif focus == "call":
            query = query.where(m.Contact.phone != "", m.Contact.phone_status != "invalid")
        elif focus == "incomplete":
            query = query.where(or_(m.Contact.phone == "", m.Contact.email.is_(None), m.Contact.role == ""))
        elif focus == "never_contacted":
            query = query.where(
                prospects.last_call_expression().is_(None), prospects.last_email_expression().is_(None)
            )
        if email_status:
            query = query.where(
                or_(m.Contact.email.is_(None), m.Contact.email == "")
                if email_status == "missing"
                else and_(
                    m.Contact.email.is_not(None),
                    m.Contact.email != "",
                    m.Contact.email_status == email_status,
                )
            )
        if phone:
            query = query.where(m.Contact.phone == "" if phone == "missing" else m.Contact.phone != "")
            if phone == "direct":
                query = query.where(m.Contact.phone_type == "direct")
        if qualification:
            query = query.where(m.Contact.approved.is_(qualification == "approved"))
    if lifecycle:
        query = query.where(m.Account.lifecycle == lifecycle)
    if resource == "accounts":
        if employees == "unknown":
            query = query.where(m.Account.employee_count.is_(None))
        elif employees == "250+":
            query = query.where(m.Account.employee_count >= 250)
        elif employees:
            low, high = [int(value) for value in employees.split("-")]
            query = query.where(m.Account.employee_count.between(low, high))
        if crm:
            query = query.where(m.Account.hubspot_status == crm)
        if has_contacts:
            query = query.where(contact_count > 0 if has_contacts == "yes" else contact_count == 0)
    if channel and resource == "tasks":
        query = query.where(m.Task.channel == channel)
    if period and resource in {"tasks", "opportunities", "messages"}:
        due = m.Opportunity.next_action_at if resource == "opportunities" else model.due_at
        now = m.now()
        today = datetime.now(ZoneInfo("Europe/Paris")).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today.astimezone(UTC)
        tomorrow = (today + timedelta(days=1)).astimezone(UTC)
        if period == "overdue":
            query = query.where(due < now)
            if resource == "tasks":
                query = query.where(m.Task.status == "pending")
            elif resource == "opportunities":
                query = query.where(m.Opportunity.stage.not_in(["won", "lost"]))
            else:
                query = query.where(m.Message.status.in_(["draft", "approved", "queued"]))
        elif period == "today":
            query = query.where(due >= start, due < tomorrow)
        elif period == "week":
            query = query.where(due >= start, due < (today + timedelta(days=7)).astimezone(UTC))
        elif period == "upcoming":
            query = query.where(due >= now)
        else:
            query = query.where(due.is_(None))
    total = count(db, query)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    order = sorts[sort].desc() if direction == "desc" else sorts[sort].asc()
    rows = list(
        db.scalars(
            query.order_by(order.nulls_last(), model.id.asc()).offset((page - 1) * page_size).limit(page_size)
        )
    )
    items = []
    prospect_data = {}
    if resource == "contacts" and rows:
        prospect_data = {
            identifier: {"score": int(score), "last_call_at": call_at, "last_email_at": email_at}
            for identifier, score, call_at, email_at in db.execute(
                select(
                    m.Contact.id,
                    prospects.score_expression(),
                    prospects.last_call_expression(),
                    prospects.last_email_expression(),
                )
                .join(m.Account)
                .where(m.Contact.id.in_([row.id for row in rows]))
            )
        }
    for row in rows:
        item = d.serialize(row)
        if resource == "contacts":
            item = person(db, row)
            item.update(prospect_data[row.id])
            item["fit"] = d.score_contact(db, row)
            task = db.scalar(
                select(m.Task)
                .where(m.Task.contact_id == row.id, m.Task.status == "pending")
                .order_by(m.Task.due_at, m.Task.id)
                .limit(1)
            )
            item["next_task"] = d.serialize(task) if task else None
            sender = db.get(m.SendingMailbox, row.email_sender_id) if row.email_sender_id else None
            item["sender"] = {"id": sender.id, "from_email": sender.from_email} if sender else None
        elif resource == "accounts":
            item["contact_count"] = db.scalar(select(contact_count).where(m.Account.id == row.id))
            item["company_phone"] = db.scalar(
                select(m.Evidence.value)
                .where(m.Evidence.account_id == row.id, m.Evidence.field == "company_phone")
                .order_by(m.Evidence.observed_at.desc())
                .limit(1)
            )
            item["evidence_count"] = db.scalar(select(evidence_count).where(m.Account.id == row.id))
        else:
            item["contact"] = person(db, db.get(m.Contact, row.contact_id)) if row.contact_id else None
            item["account"] = (
                d.serialize(db.get(m.Account, row.account_id))
                if resource == "opportunities"
                else item["contact"]["account"]
            )
        if resource == "tasks":
            item["contact"]["fit"] = d.score_contact(db, db.get(m.Contact, row.contact_id))
            item["sent_count"] = db.scalar(
                select(func.count())
                .select_from(m.Message)
                .where(m.Message.contact_id == row.contact_id, m.Message.status == "sent")
            )
        if resource != "accounts":
            campaign = db.get(m.Campaign, row.campaign_id) if row.campaign_id else None
            item["campaign"] = (
                {
                    "id": campaign.id,
                    "name": campaign.name,
                    "niche": campaign.niche,
                    "offer_id": campaign.offer_id,
                }
                if campaign
                else None
            )
        items.append(item)
    return {
        "items": items,
        "total": total,
        "total_unfiltered": unfiltered,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "summary": summary,
        "facets": {
            "countries": list(db.scalars(select(m.Account.country).distinct().order_by(m.Account.country)))
        },
    }
