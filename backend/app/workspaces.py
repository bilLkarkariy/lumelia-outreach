"""Company/campaign workspaces and an auditable qualification queue."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from . import campaign_groups as groups
from . import domain as d
from . import models as m
from . import schemas as s
from .db import get_db
from .security import admin, current_user

router = APIRouter(prefix="/api", tags=["workspaces"])


def page_result(db, query, page, size):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    page = min(page, max(1, (total + size - 1) // size))
    return list(db.scalars(query.offset((page - 1) * size).limit(size))), total, page


def account_events(account_id):
    opportunity_ids = select(m.Opportunity.id).where(m.Opportunity.account_id == account_id)
    return (
        select(m.Event)
        .where(
            or_(
                m.Event.account_id == account_id,
                m.Event.data["account_id"].as_string() == account_id,
                m.Event.data["opportunity_id"].as_string().in_(opportunity_ids),
            )
        )
        .order_by(m.Event.created_at.desc(), m.Event.id)
    )


@router.get("/workspaces/accounts/{account_id}")
def account_workspace(account_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    account = d.get(db, m.Account, account_id)
    contacts = list(
        db.scalars(
            select(m.Contact)
            .where(m.Contact.account_id == account_id)
            .order_by(m.Contact.last_name, m.Contact.first_name, m.Contact.id)
        )
    )
    evidence = list(
        db.scalars(
            select(m.Evidence)
            .where(m.Evidence.account_id == account_id)
            .order_by(m.Evidence.observed_at.desc(), m.Evidence.id)
        )
    )
    opportunities = list(
        db.scalars(
            select(m.Opportunity)
            .where(m.Opportunity.account_id == account_id)
            .order_by(m.Opportunity.updated_at.desc())
        )
    )
    events, total, _ = page_result(db, account_events(account_id), 1, 20)
    review = db.scalar(
        select(m.Event)
        .where(m.Event.account_id == account_id, m.Event.kind == "account_reviewed")
        .order_by(m.Event.created_at.desc())
        .limit(1)
    )
    missing = [
        label
        for missing, label in [
            (not account.industry, "Activité à préciser"),
            (account.employee_count is None, "Effectif à vérifier"),
            (account.hubspot_status == "unknown", "CRM à documenter"),
            (not contacts, "Décideur à identifier"),
            (not evidence, "Source à ajouter"),
        ]
        if missing
    ]
    return {
        **d.serialize(account),
        "contacts": [{**d.serialize(contact), "fit": d.score_contact(db, contact)} for contact in contacts],
        "evidence": [d.serialize(row) for row in evidence],
        "opportunities": [d.serialize(row) for row in opportunities],
        "events": [d.serialize(row) for row in events],
        "events_total": total,
        "review": d.serialize(review),
        "missing": missing,
    }


@router.get("/workspaces/accounts/{account_id}/events")
def account_history(
    account_id: str, page: int = Query(1, ge=1), user=Depends(current_user), db: Session = Depends(get_db)
):
    d.get(db, m.Account, account_id)
    rows, total, page = page_result(db, account_events(account_id), page, 20)
    return {"items": [d.serialize(row) for row in rows], "total": total, "page": page, "page_size": 20}


class AccountNote(s.Input):
    text: str = Field(min_length=1, max_length=5000)
    request_id: UUID


@router.post("/workspaces/accounts/{account_id}/notes", status_code=201)
def account_note(
    account_id: str, data: AccountNote, user=Depends(current_user), db: Session = Depends(get_db)
):
    d.get(db, m.Account, account_id)
    if not data.text.strip():
        raise HTTPException(422, "Rédigez une note.")
    key = f"account-note:{account_id}:{data.request_id}"
    event = db.scalar(select(m.Event).where(m.Event.external_id == key))
    if not event:
        event = m.Event(
            account_id=account_id,
            actor_id=user.id,
            kind="note",
            channel="system",
            summary=data.text.strip(),
            external_id=key,
        )
        db.add(event)
    db.commit()
    return d.serialize(event)


def sum_expenses(db, campaign_ids, statuses):
    return (
        db.scalar(
            select(func.coalesce(func.sum(m.Expense.amount_cents), 0)).where(
                m.Expense.campaign_id.in_(campaign_ids), m.Expense.status.in_(statuses)
            )
        )
        or 0
    )


@router.get("/workspaces/campaigns/{group_id}")
def campaign_workspace(group_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    group = groups.get_group(db, group_id)
    versions = [row for row in groups.campaign_list(db) if row["group_id"] == group_id]
    ids = [row["id"] for row in versions]
    profile_ids = {row["icp_id"] for row in versions}
    profiles = [d.serialize(row) for row in db.scalars(select(m.ICP).where(m.ICP.id.in_(profile_ids)))]
    approved = (
        db.scalar(
            select(func.count())
            .select_from(m.Contact)
            .where(m.Contact.campaign_id.in_(ids), m.Contact.approved.is_(True))
        )
        or 0
    )
    for row in versions:
        row["ready_contacts"] = (
            db.scalar(
                select(func.count())
                .select_from(m.Contact)
                .where(m.Contact.campaign_id == row["id"], m.Contact.approved.is_(True))
            )
            or 0
        )
        issues = []
        if not row["sequence"]:
            issues.append({"key": "sequence", "label": "Séquence à rédiger", "action": "sequence"})
        if any(step["channel"] == "email" for step in row["sequence"]) and not row["config"].get("language"):
            issues.append(
                {"key": "language", "label": "Langue de la séquence à définir", "action": "sequence"}
            )
        if (
            row["config"].get("language") not in {None, "", "fr"}
            and any(step["channel"] == "email" for step in row["sequence"])
            and len(row["config"].get("localized_footer", "").strip()) < 20
        ):
            issues.append(
                {"key": "footer", "label": "Pied d’email traduit à compléter", "action": "sequence"}
            )
        if row["metrics"]["contacts"] == 0:
            issues.append({"key": "contacts", "label": "Aucun contact affecté", "action": "contacts"})
        elif row["ready_contacts"] == 0:
            issues.append(
                {"key": "qualification", "label": "Contacts à qualifier", "action": "qualification"}
            )
        if row["mode"] == "live" and not row["budget_approved"]:
            issues.append({"key": "budget", "label": "Budget à valider", "action": "budget"})
        row["readiness"] = issues
        row["cost_cents"] = sum_expenses(db, [row["id"]], ["spent", "estimated"])
        row["reserved_cents"] = sum_expenses(db, [row["id"]], ["reserved"])
    metrics = groups.metrics(db, ids)
    metrics.update(
        approved_contacts=approved,
        positive_replies=db.scalar(
            select(func.count(func.distinct(m.Event.contact_id))).where(
                m.Event.campaign_id.in_(ids), m.Event.kind == "positive_reply"
            )
        )
        or 0,
        cost_cents=sum_expenses(db, ids, ["spent", "estimated"]),
        reserved_cents=sum_expenses(db, ids, ["reserved"]),
        deposits_cents=db.scalar(
            select(func.coalesce(func.sum(m.Opportunity.deposit_cents), 0)).where(
                m.Opportunity.campaign_id.in_(ids)
            )
        )
        or 0,
        budget_cents=sum(row["budget_cents"] for row in versions),
    )
    events = db.scalars(
        select(m.Event).where(m.Event.campaign_id.in_(ids)).order_by(m.Event.created_at.desc()).limit(12)
    )
    return {
        **d.serialize(group),
        "offer": d.serialize(db.get(m.Offer, group.offer_id)),
        "versions": versions,
        "profiles": profiles,
        "metrics": metrics,
        "events": [d.serialize(row) for row in events],
    }


@router.get("/qualification/queue")
def qualification_queue(
    resource: Literal["accounts", "contacts"] = "accounts",
    campaign_id: str = Query("", max_length=36),
    q: str = Query("", max_length=120),
    country: str = Query("", max_length=2),
    page: int = Query(1, ge=1),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if campaign_id:
        d.get(db, m.Campaign, campaign_id)
    model: type[m.Account] | type[m.Contact]
    query: Select[Any]
    if resource == "accounts":
        model = m.Account
        reviewed = (
            select(m.Event.id)
            .where(
                m.Event.account_id == m.Account.id,
                m.Event.kind == "account_reviewed",
                m.Event.created_at >= m.Account.updated_at,
                m.Event.campaign_id == campaign_id if campaign_id else m.Event.campaign_id.is_(None),
            )
            .exists()
        )
        query = select(m.Account).where(
            m.Account.lifecycle == "prospect", ~m.Account.outreach_paused, ~reviewed
        )
        search = func.concat_ws(" ", m.Account.name, m.Account.domain, m.Account.industry)
    else:
        model = m.Contact
        query = (
            select(m.Contact)
            .join(m.Account, m.Contact.account_id == m.Account.id)
            .where(
                m.Contact.status == "candidate",
                ~m.Contact.approved,
                ~m.Contact.paused,
                m.Account.lifecycle == "prospect",
                ~m.Account.outreach_paused,
            )
        )
        if campaign_id:
            query = query.where(m.Contact.campaign_id == campaign_id)
        search = func.concat_ws(
            " ", m.Contact.first_name, m.Contact.last_name, m.Contact.role, m.Account.name, m.Contact.email
        )
    if q.strip():
        query = query.where(search.icontains(q.strip(), autoescape=True))
    if country:
        query = query.where(m.Account.country == country.upper())
    query = query.order_by(model.created_at, model.id)
    rows, total, page = page_result(db, query, page, 10)
    items = []
    for row in rows:
        item = d.serialize(row)
        if resource == "contacts":
            item["account"] = d.serialize(db.get(m.Account, row.account_id))
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": 10}


class QualificationDecision(s.Input):
    decision: Literal["qualified", "rejected"]
    reason: str = Field(min_length=10, max_length=3000)
    expected_updated_at: datetime
    campaign_id: str | None = None
    request_id: UUID


@router.post("/qualification/{resource}/{record_id}")
def review_record(
    resource: Literal["accounts", "contacts"],
    record_id: str,
    data: QualificationDecision,
    user=Depends(admin),
    db: Session = Depends(get_db),
):
    model = m.Account if resource == "accounts" else m.Contact
    row = d.get(db, model, record_id)
    key = f"qualification:{record_id}:{data.request_id}"
    previous = db.scalar(select(m.Event).where(m.Event.external_id == key))
    if previous:
        return {"decision": previous.data["decision"], "record_id": record_id, "duplicate": True}
    if data.expected_updated_at.tzinfo is None or row.updated_at != data.expected_updated_at:
        raise HTTPException(409, "Cette fiche a changé depuis son ouverture. Rechargez-la avant de décider.")
    reason = data.reason.strip()
    if len(reason) < 10:
        raise HTTPException(422, "Documentez la raison de votre décision (10 caractères minimum).")
    if data.campaign_id:
        d.get(db, m.Campaign, data.campaign_id)
    account = row if resource == "accounts" else d.get(db, m.Account, row.account_id)
    if account.lifecycle != "prospect" or account.outreach_paused:
        raise HTTPException(409, "Cette entreprise est exclue, cliente ou en pause. Consultez sa fiche.")
    if resource == "contacts":
        if row.status != "candidate" or row.paused or row.approved:
            raise HTTPException(409, "Ce contact n’est plus dans la file de qualification.")
        if data.campaign_id and row.campaign_id != data.campaign_id:
            raise HTTPException(409, "Ce contact a changé de sous-campagne. Rechargez la sélection.")
        if data.decision == "qualified":
            d.approve_contact(db, row, user.id)
        else:
            row.paused, row.approved = True, False
            d.cancel_pending(db, row, "Écarté après revue : " + reason)
    # A company review is scoped to a target. It never excludes the company globally
    # or changes a contact's consent, verification or campaign assignment.
    event = m.Event(
        kind="account_reviewed" if resource == "accounts" else "contact_reviewed",
        account_id=account.id,
        contact_id=row.id if resource == "contacts" else None,
        campaign_id=data.campaign_id or (row.campaign_id if resource == "contacts" else None),
        actor_id=user.id,
        summary=reason,
        external_id=key,
        data={
            "decision": data.decision,
            "resource": resource,
            "reviewed_revision": data.expected_updated_at.isoformat(),
        },
    )
    db.add(event)
    db.commit()
    return {"decision": data.decision, "record_id": record_id, "duplicate": False}


@router.post("/campaigns/{campaign_id}/contacts")
def assign_contacts(campaign_id: str, data: s.Batch, user=Depends(admin), db: Session = Depends(get_db)):
    campaign = d.get(db, m.Campaign, campaign_id)
    changed = []
    for contact_id in dict.fromkeys(data.contact_ids):
        contact = d.get(db, m.Contact, contact_id)
        account = d.get(db, m.Account, contact.account_id)
        if (
            contact.paused
            or contact.status == "suppressed"
            or account.outreach_paused
            or account.lifecycle != "prospect"
            or d.is_suppressed(db, contact)
        ):
            raise HTTPException(
                409,
                "Un contact de cette sélection est en pause ou exclu. Revoyez sa fiche avant affectation.",
            )
        if contact.campaign_id == campaign.id:
            continue
        d.cancel_pending(db, contact, "Sous-campagne modifiée : nouvelle validation requise")
        previous = contact.campaign_id
        contact.campaign_id = campaign.id
        contact.approved = False
        contact.status = "candidate"
        d.log(
            db,
            "contact_updated",
            contact=contact,
            actor_id=user.id,
            summary="Sous-campagne affectée ; qualification à valider.",
            data={"previous_campaign_id": previous},
        )
        changed.append(contact.id)
    db.commit()
    return {"assigned": changed}
