"""Search, calendar, data quality, sequence attribution and operational alerts."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from . import domain as d
from . import models as m
from . import native_warmup, senders
from . import schemas as s
from .config import settings
from .db import get_db
from .security import current_user

router = APIRouter(prefix="/api", tags=["workspace operations"])


def person(db, row):
    return (
        {
            **d.serialize(row),
            "account": d.serialize(db.get(m.Account, row.account_id)),
            "fit": d.score_contact(db, row),
        }
        if row
        else None
    )


@router.get("/search")
def search(q: str = Query("", max_length=120), user=Depends(current_user), db: Session = Depends(get_db)):
    if len(q.strip()) < 2:
        return {"groups": [], "total": 0}
    specs = [
        (
            "companies",
            "Entreprises",
            m.Account,
            func.concat_ws(" ", m.Account.name, m.Account.domain, m.Account.industry),
        ),
        (
            "contacts",
            "Contacts",
            m.Contact,
            func.concat_ws(
                " ",
                m.Contact.first_name,
                m.Contact.last_name,
                m.Contact.email,
                m.Contact.phone,
                m.Contact.role,
                m.Account.name,
            ),
        ),
        (
            "campaigns",
            "Campagnes",
            m.CampaignGroup,
            func.concat_ws(" ", m.CampaignGroup.name, m.CampaignGroup.notes),
        ),
        (
            "opportunities",
            "Opportunités",
            m.Opportunity,
            func.concat_ws(" ", m.Opportunity.title, m.Account.name),
        ),
    ]
    groups = []
    for kind, label, model, expression in specs:
        query = select(model)
        if kind in {"contacts", "opportunities"}:
            query = query.join(m.Account, model.account_id == m.Account.id)
        query = query.where(expression.icontains(q.strip(), autoescape=True))
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = []
        for row in db.scalars(query.order_by(model.updated_at.desc(), model.id).limit(8)):
            title = (
                ((row.first_name + " " + row.last_name).strip() or row.email or "Contact")
                if kind == "contacts"
                else row.title
                if kind == "opportunities"
                else row.name
            )
            subtitle = (
                db.get(m.Account, row.account_id).name
                if kind in {"contacts", "opportunities"}
                else row.domain
                if kind == "companies"
                else "Campagne principale"
            )
            items.append(
                {
                    "id": row.id,
                    "title": title,
                    "subtitle": subtitle,
                    "url": "/?view=pipeline&record=" + row.id
                    if kind == "opportunities"
                    else "/" + kind + "/" + row.id,
                }
            )
        groups.append({"type": kind, "label": label, "total": total, "items": items})
    return {"groups": groups, "total": sum(g["total"] for g in groups)}


@router.get("/operations/opportunities/{record_id}")
def opportunity_record(record_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    return d.serialize(d.get(db, m.Opportunity, record_id))


@router.get("/calendar")
def calendar(
    start: datetime,
    end: datetime,
    kind: Literal["all", "phone", "linkedin", "review", "meeting", "next_action"] = "all",
    status: Literal["pending", "all"] = "pending",
    campaign_id: str = "",
    overdue: bool = False,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if not start.tzinfo or not end.tzinfo or end <= start or end - start > timedelta(days=43):
        raise HTTPException(422, "Choisissez une période de 43 jours maximum avec son fuseau.")
    items = []
    if kind in {"all", "phone", "linkedin", "review"}:
        query = (
            select(m.Task).where(m.Task.due_at < m.now())
            if overdue
            else select(m.Task).where(m.Task.due_at >= start, m.Task.due_at < end)
        )
        if status == "pending" or overdue:
            query = query.where(m.Task.status == "pending")
        if campaign_id:
            query = query.where(m.Task.campaign_id == campaign_id)
        if kind != "all":
            query = query.where(m.Task.channel == kind)
        for task in db.scalars(query.order_by(m.Task.due_at, m.Task.id)):
            contact = person(db, db.get(m.Contact, task.contact_id))
            items.append(
                {
                    "id": "task:" + task.id,
                    "record_id": task.id,
                    "type": "task",
                    "kind": task.channel,
                    "title": task.title,
                    "at": task.due_at.isoformat(),
                    "status": task.status,
                    "updated_at": task.updated_at.isoformat(),
                    "contact": contact,
                    "record": {**d.serialize(task), "contact": contact},
                }
            )
    if kind in {"all", "meeting", "next_action"} and not overdue:
        deals = select(m.Opportunity).where(
            or_(
                and_(m.Opportunity.meeting_at >= start, m.Opportunity.meeting_at < end),
                and_(m.Opportunity.next_action_at >= start, m.Opportunity.next_action_at < end),
            )
        )
        if status == "pending":
            deals = deals.where(m.Opportunity.stage.not_in(["won", "lost"]))
        if campaign_id:
            deals = deals.where(m.Opportunity.campaign_id == campaign_id)
        for deal in db.scalars(deals):
            for field, category in [("meeting_at", "meeting"), ("next_action_at", "next_action")]:
                at = getattr(deal, field)
                if (
                    not at
                    or not start <= at < end
                    or kind not in {"all", category}
                    or (category == "meeting" and status == "pending" and deal.meeting_held)
                ):
                    continue
                items.append(
                    {
                        "id": category + ":" + deal.id,
                        "record_id": deal.id,
                        "type": "opportunity",
                        "kind": category,
                        "title": deal.title if category == "meeting" else deal.next_action or deal.title,
                        "at": at.isoformat(),
                        "status": "completed" if category == "meeting" and deal.meeting_held else "pending",
                        "updated_at": deal.updated_at.isoformat(),
                        "contact": person(db, db.get(m.Contact, deal.contact_id))
                        if deal.contact_id
                        else None,
                        "company": db.get(m.Account, deal.account_id).name,
                        "record": d.serialize(deal),
                    }
                )
    items.sort(key=lambda row: (row["at"], row["id"]))
    return {"items": items, "total": len(items)}


class Schedule(s.Input):
    kind: Literal["task", "meeting", "next_action"]
    at: datetime
    expected_updated_at: datetime
    request_id: UUID


@router.patch("/calendar/{record_id}")
def reschedule(record_id: str, data: Schedule, user=Depends(current_user), db: Session = Depends(get_db)):
    d.state(db, lock=True)
    key = "calendar:" + str(data.request_id)
    previous = db.scalar(select(m.Event).where(m.Event.external_id == key))
    if previous:
        return previous.data
    if not data.at.tzinfo:
        raise HTTPException(422, "Une date avec fuseau horaire est requise.")
    row = d.get(db, m.Task if data.kind == "task" else m.Opportunity, record_id)
    if row.updated_at != data.expected_updated_at:
        raise HTTPException(409, "Cet élément a changé. Rechargez le planning.")
    if (data.kind == "task" and row.status != "pending") or (
        data.kind != "task" and row.stage in {"won", "lost"}
    ):
        raise HTTPException(409, "Cet élément est clôturé.")
    setattr(
        row,
        "due_at" if data.kind == "task" else "meeting_at" if data.kind == "meeting" else "next_action_at",
        data.at,
    )
    d.log(
        db,
        "schedule_updated",
        contact=db.get(m.Contact, row.contact_id) if row.contact_id else None,
        campaign_id=row.campaign_id,
        actor_id=user.id,
        summary="Échéance modifiée dans le planning.",
        data={"record_id": row.id, "kind": data.kind, "at": data.at.isoformat()},
        external_id=key,
    )
    db.commit()
    return d.serialize(row)


def quality_rules(resource):
    now = m.now()
    if resource == "contacts":
        source = select(m.Evidence.id).where(m.Evidence.contact_id == m.Contact.id).correlate(m.Contact)
        phone = source.where(
            m.Evidence.field.in_(["phone", "phone_verification", "Téléphone", "Vérification du téléphone"]),
            m.Evidence.value.contains(m.Contact.phone),
            m.Evidence.observed_at >= now - timedelta(days=90),
        )
        return {
            "identity": (
                "Identité ou rôle incomplet",
                or_(m.Contact.last_name == "", m.Contact.role == "", m.Contact.professional_relevance == ""),
            ),
            "email_missing": ("Email manquant", or_(m.Contact.email.is_(None), m.Contact.email == "")),
            "email_review": (
                "Email à vérifier",
                and_(
                    m.Contact.email.is_not(None),
                    or_(
                        m.Contact.email_status != "valid",
                        m.Contact.email_verified_at.is_(None),
                        m.Contact.email_verified_at < now - timedelta(days=90),
                    ),
                ),
            ),
            "phone_review": ("Téléphone à actualiser", and_(m.Contact.phone != "", ~phone.exists())),
            "no_source": ("Aucune preuve", ~source.exists()),
            "stale_source": (
                "Preuves de plus de 180 jours",
                and_(
                    source.exists(),
                    ~source.where(m.Evidence.observed_at >= now - timedelta(days=180)).exists(),
                ),
            ),
        }
    source = (
        select(m.Evidence.id)
        .where(m.Evidence.account_id == m.Account.id, m.Evidence.contact_id.is_(None))
        .correlate(m.Account)
    )
    return {
        "identity": (
            "Activité ou effectif incomplet",
            or_(m.Account.industry == "", m.Account.employee_count.is_(None)),
        ),
        "crm": ("CRM inconnu", m.Account.hubspot_status == "unknown"),
        "no_contact": (
            "Aucun interlocuteur",
            ~select(m.Contact.id).where(m.Contact.account_id == m.Account.id).exists(),
        ),
        "no_source": ("Aucune preuve entreprise", ~source.exists()),
        "stale_source": (
            "Preuves de plus de 180 jours",
            and_(
                source.exists(), ~source.where(m.Evidence.observed_at >= now - timedelta(days=180)).exists()
            ),
        ),
    }


@router.get("/data-quality")
def quality(
    resource: Literal["contacts", "accounts"] = "contacts",
    issue: str = "",
    q: str = Query("", max_length=120),
    scope: Literal["prospects", "all"] = "prospects",
    page: int = Query(1, ge=1),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    rules = quality_rules(resource)
    if issue and issue not in rules:
        raise HTTPException(422, "Contrôle inconnu.")
    model = m.Contact if resource == "contacts" else m.Account
    query = select(model)
    if resource == "contacts":
        query = query.join(m.Account, m.Account.id == m.Contact.account_id)
    if scope == "prospects":
        query = query.where(m.Account.lifecycle == "prospect")
    if q.strip():
        expression = (
            func.concat_ws(
                " ",
                m.Account.name,
                m.Account.domain,
                m.Contact.first_name,
                m.Contact.last_name,
                m.Contact.email,
            )
            if resource == "contacts"
            else func.concat_ws(" ", m.Account.name, m.Account.domain)
        )
        query = query.where(expression.icontains(q.strip(), autoescape=True))
    counts = {
        key: db.scalar(select(func.count()).select_from(query.where(condition).subquery())) or 0
        for key, (label, condition) in rules.items()
    }
    total_records = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    flagged = query.add_columns(*(condition.label(key) for key, (_, condition) in rules.items())).where(
        rules[issue][1] if issue else or_(*(condition for _, condition in rules.values()))
    )
    total = db.scalar(select(func.count()).select_from(flagged.subquery())) or 0
    page = min(page, max(1, (total + 9) // 10))
    items = []
    for result in db.execute(flagged.order_by(model.updated_at, model.id).offset((page - 1) * 10).limit(10)):
        row = result[0]
        items.append(
            {
                "id": row.id,
                "name": ((row.first_name + " " + row.last_name).strip() or row.email or "Contact sans nom")
                if resource == "contacts"
                else row.name,
                "company": db.get(m.Account, row.account_id).name if resource == "contacts" else row.domain,
                "issues": [
                    {"key": key, "label": rules[key][0]} for i, key in enumerate(rules) if result[i + 1]
                ],
                "url": "/contacts/" + row.id + "?tab=qualification"
                if resource == "contacts"
                else "/companies/" + row.id,
                "updated_at": row.updated_at.isoformat(),
            }
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": 10,
        "total_records": total_records,
        "checks": [{"key": key, "label": label, "count": counts[key]} for key, (label, _) in rules.items()],
        "thresholds": {"email_days": 90, "phone_days": 90, "evidence_days": 180},
    }


@router.get("/sequence-stats")
def sequence_stats(
    group_id: str = "", campaign_id: str = "", user=Depends(current_user), db: Session = Depends(get_db)
):
    query = select(m.Campaign).order_by(m.Campaign.created_at, m.Campaign.id)
    if group_id:
        query = query.where(m.Campaign.group_id == group_id)
    if campaign_id:
        query = query.where(m.Campaign.id == campaign_id)
    versions = list(db.scalars(query))
    ids = [row.id for row in versions]
    messages = list(db.scalars(select(m.Message).where(m.Message.campaign_id.in_(ids))))
    by_id = {row.id: row for row in messages}
    events = list(
        db.scalars(
            select(m.Event).where(
                m.Event.campaign_id.in_(ids), m.Event.kind.in_(["reply", "positive_reply", "bounce"])
            )
        )
    )
    attributed: dict[tuple, set] = {}
    unattributed = 0
    manual_reply_events = 0
    for event in events:
        message = by_id.get(event.data.get("message_id", ""))
        if not message or event.data.get("attribution") == "sender_fallback":
            unattributed += 1
            continue
        if message.purpose != "sequence":
            manual_reply_events += 1
            continue
        if message.status != "sent":
            unattributed += 1
            continue
        attributed.setdefault((message.campaign_id, message.step, event.kind), set()).add(message.contact_id)
    rows = []
    for version in versions:
        steps = []
        for index, step in enumerate(version.sequence):
            related = [
                message
                for message in messages
                if message.campaign_id == version.id
                and message.step == index
                and message.purpose == "sequence"
            ]
            sent = [message for message in related if message.status == "sent"]
            recipients = {message.contact_id for message in sent}
            positive = attributed.get((version.id, index, "positive_reply"), set())
            replies = attributed.get((version.id, index, "reply"), set()) | positive
            steps.append(
                {
                    "index": index,
                    "channel": step["channel"],
                    "day": step["day"],
                    "subject": step.get("subject", ""),
                    "sent": len(sent),
                    "recipients": len(recipients),
                    "replies": len(replies),
                    "positive": len(positive),
                    "bounces": len(attributed.get((version.id, index, "bounce"), set())),
                    "reply_rate": round(100 * len(replies) / len(recipients), 1) if recipients else None,
                    "failed": sum(message.status in {"failed", "uncertain"} for message in related),
                    "drafts": sum(message.status in {"draft", "approved", "queued"} for message in related),
                }
            )
        rows.append(
            {
                "id": version.id,
                "group_id": version.group_id,
                "name": version.name,
                "niche": version.niche,
                "version": version.config.get("sequence_version", 1),
                "status": version.status,
                "steps": steps,
            }
        )
    return {
        "versions": rows,
        "unattributed_events": unattributed,
        "manual_reply_events": manual_reply_events,
        "method": "Réponses rattachées par référence au message envoyé ; un contact compte une fois par étape. Les simulations et réponses manuelles sont exclues des taux de séquence.",
    }


@router.get("/alerts")
def alerts(
    severity: Literal["all", "critical", "warning", "info"] = "all",
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    now = m.now()
    items = []

    def add(key, level, title, detail, url, count=1):
        items.append(
            {"id": key, "severity": level, "title": title, "detail": detail, "url": url, "count": count}
        )

    state = d.state(db)
    if not state.worker_seen_at or state.worker_seen_at < now - timedelta(minutes=3):
        add(
            "worker",
            "critical",
            "Traitements automatiques interrompus",
            "Le dernier signal du worker a plus de trois minutes.",
            "/?view=settings",
        )
    for row in db.scalars(select(m.SendingMailbox).order_by(m.SendingMailbox.from_email)):
        if row.status in {"excluded", "disabled"}:
            continue
        if row.status != "authenticated":
            add(
                "mailbox:" + row.id,
                "critical",
                "Boîte à reconnecter",
                row.from_email
                + " · "
                + (row.state.get("connection_error") or "Connexion SMTP/IMAP à vérifier."),
                "/?view=senders",
            )
        elif not row.last_event_at or row.last_event_at < now - timedelta(minutes=10):
            add(
                "collection:" + row.id,
                "critical",
                "Réception à contrôler",
                row.from_email + " · la collecte des réponses n’est pas à jour.",
                "/?view=senders",
            )
        for index, reason in enumerate(senders.limits_reasons(db, row, state=state)):
            if "Intervalle" not in reason:
                add("limit:" + row.id + ":" + str(index), "warning", row.from_email, reason, "/?view=senders")
        if (
            row.warmup_status == "warming"
            and not state.paused
            and not row.paused
            and settings().live_actions_enabled
        ):
            progress = senders.ramp.assess(db, row)
            if not progress.get("accepted_7d"):
                add(
                    "warming:" + row.id,
                    "info",
                    "Cadence en attente des premiers envois",
                    row.from_email
                    + " · aucun envoi accepté sur les sept derniers jours ; plafond effectif "
                    + str(progress["effective_limit"])
                    + "/24 h.",
                    "/?view=senders",
                )
    for account in native_warmup.snapshot(db)["mailboxes"]:
        if account["enabled"] and account["blockers"]:
            add(
                "native-warmup:" + account["id"],
                "warning",
                "Préparation gratuite à contrôler",
                account["email"] + " · " + "; ".join(account["blockers"]),
                "/?view=senders",
            )
    usage_jobs = []
    collection_failures = []
    for job in db.scalars(
        select(m.Job).where(m.Job.status.in_(["failed", "uncertain"])).order_by(m.Job.updated_at.desc())
    ):
        if job.kind == "agent_usage":
            usage_jobs.append(job)
            continue
        if job.kind == native_warmup.KIND:
            continue  # Grouped by mailbox above; history remains in the preparation view.
        if job.provider == "apify" and job.status == "failed" and job.error == "apify: HTTP 400":
            collection_failures.append(job)
            continue
        add(
            "job:" + job.id,
            "critical" if job.status == "uncertain" else "warning",
            (
                "Envoi à rapprocher"
                if job.kind in {"send", "reply"}
                else "Collecte à rapprocher"
                if job.kind == "apify_discovery"
                else "Traitement à rapprocher"
            )
            if job.status == "uncertain"
            else "Traitement en échec",
            job.error or "Consultez le journal du traitement.",
            "/?view=settings&tab=operations&job=" + job.id,
        )
    if collection_failures:
        add(
            "apify-rejected",
            "warning",
            "Collecte Apify : requêtes refusées",
            str(len(collection_failures))
            + " requête(s) HTTP 400 conservée(s) dans le journal. Ces échecs de collecte ne concernent pas les boîtes email ni la préparation gratuite.",
            "/?view=settings&tab=operations&job=" + collection_failures[0].id,
            len(collection_failures),
        )
    if usage_jobs:
        add(
            "agent-usage",
            "warning",
            "Consommation de l’agent : relevés incomplets",
            str(len(usage_jobs))
            + " relevé(s) à vérifier. Ce ne sont pas des échecs d’email ; les mesures et réserves restent conservées.",
            "/?view=settings&tab=operations&job=" + usage_jobs[0].id,
            len(usage_jobs),
        )
    overdue = (
        db.scalar(
            select(func.count()).select_from(m.Task).where(m.Task.status == "pending", m.Task.due_at < now)
        )
        or 0
    )
    if overdue:
        add(
            "overdue",
            "warning",
            str(overdue) + " action(s) en retard",
            "Consultez le planning et choisissez la prochaine échéance.",
            "/calendar?overdue=1",
            overdue,
        )
    late = (
        db.scalar(
            select(func.count())
            .select_from(m.Opportunity)
            .where(m.Opportunity.stage.not_in(["won", "lost"]), m.Opportunity.next_action_at < now)
        )
        or 0
    )
    if late:
        add(
            "deals-overdue",
            "warning",
            str(late) + " opportunité(s) à reprendre",
            "La date de prochaine action est dépassée.",
            "/?view=pipeline",
            late,
        )
    counts = {
        level: sum(row["severity"] == level for row in items) for level in ["critical", "warning", "info"]
    }
    return {
        "items": [row for row in items if severity == "all" or row["severity"] == severity],
        "counts": counts,
        "total": len(items),
        "checked_at": now.isoformat(),
    }
