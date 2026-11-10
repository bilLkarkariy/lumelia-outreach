"""Free controlled SMTP/IMAP preparation, never a reputation or inbox guarantee.

Only explicitly selected, authenticated workspace mailboxes can exchange plainly
labelled technical tests and one receipt acknowledgement per original message.
No prospects, paid API, tracking pixels or fabricated human engagement. SMTP intent
is durable and never retransmitted. Acknowledgements cannot trigger more replies.
Campaign pauses/activation are separate from this explicit administrative consent.
"""

import itertools
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, getaddresses
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import domain as d
from . import mail, senders, warmup_progress
from . import models as m
from . import schemas as s
from .db import SessionLocal, get_db
from .providers import ProviderError
from .security import admin, current_user, decrypt_config
from .warmup_policy import (
    DAILY_INCREMENT,
    HEALTHY_DAYS_PER_STEP,
    INITIAL_DAILY,
    KIND,
    MAX_DAILY,
    REPLY_DELAY_MINUTES,
    config,
    plan,
    plan_key,
    program_start,
    timestamp,
)

router = APIRouter(prefix="/api/mailbox-warmup", tags=["free mailbox preparation"])
PARIS = ZoneInfo("Europe/Paris")


class Start(s.Input):
    mailbox_ids: list[str] = Field(min_length=2, max_length=20)
    confirm_owned: bool


class ProgressPlan(s.Input):
    target_active_days: int = Field(default=14, ge=7, le=90)
    target_received: int = Field(default=50, ge=10, le=5000)
    expected_plan_key: str = Field(min_length=64, max_length=64)


class ValidatePreparation(s.Input):
    expected_plan_key: str = Field(min_length=64, max_length=64)
    note: str = Field(min_length=20, max_length=2000)


class CompletionSettings(s.Input):
    recipient: EmailStr
    mailbox_ids: list[str] = Field(min_length=2, max_length=20)
    sender_id: str
    enabled: bool = True


def invalidate_validation(row, reason):
    cfg = config(row)
    validation = cfg.get("validation")
    if validation and not validation.get("invalidated_at"):
        row.state = {
            **row.state,
            "native_warmup": {
                **cfg,
                "validation": {
                    **validation,
                    "invalidated_at": m.now().isoformat(),
                    "invalidated_reason": reason,
                },
            },
        }


def selected(db):
    return [
        row
        for row in db.scalars(select(m.SendingMailbox).order_by(m.SendingMailbox.id))
        if config(row).get("enabled")
    ]


def history(db, row):
    return list(
        db.scalars(
            select(m.Job)
            .where(
                m.Job.kind == KIND,
                m.Job.payload["sender_id"].as_string() == row.id,
                m.Job.created_at >= m.now() - timedelta(days=30),
            )
            .order_by(m.Job.created_at.desc())
        )
    )


def technical_blockers(row):
    if row.status != "authenticated" or senders.protected(row.from_email):
        return ["Boîte non authentifiée ou protégée"]
    cfg = decrypt_config(row.encrypted_config)
    if not cfg.get("dns_verified"):
        return ["SPF, DKIM et DMARC à vérifier"]
    if not row.last_event_at or row.last_event_at < m.now() - timedelta(minutes=10):
        return ["Collecte IMAP non à jour"]
    if config(row).get("enabled") and config(row).get("config_version") != row.config_version:
        return ["Connexion modifiée : nouvelle activation requise"]
    return []


def cadence_details(row, jobs):
    # Preserve healthy activity across a pause, but never inherit another connection.
    now = m.now()
    started = program_start(row) or now
    relevant = [
        j
        for j in jobs
        if j.attempts
        and started <= j.created_at <= now
        and j.payload.get("sender_version") == row.config_version
    ]

    def local_day(at):
        return at.astimezone(PARIS).date()

    dates = sorted({local_day(j.created_at) for j in relevant})
    reset = None
    for previous, current in itertools.pairwise(dates):
        if (current - previous).days > 7:
            reset = current
    if reset:
        relevant = [j for j in relevant if local_day(j.created_at) >= reset]
    good = {
        local_day(j.created_at)
        for j in relevant
        if j.status == "completed"
        and j.result.get("received") is True
        and timestamp(j.result.get("received_at"))
        and j.result.get("placement") != "spam"
    }
    bad = {local_day(j.created_at) for j in relevant if j.status in {"failed", "uncertain"}}
    # A reviewed incident resumes at low volume instead of immediately restoring the old peak.
    days = len({day for day in good - bad if day < local_day(now) and (not bad or day > max(bad))})
    latest = max((j.created_at for j in relevant), default=now)
    if latest < now - timedelta(days=7):
        days = 0
    limit = min(MAX_DAILY, INITIAL_DAILY + DAILY_INCREMENT * (days // HEALTHY_DAYS_PER_STEP))
    return {
        "daily_limit": limit,
        "initial_daily": INITIAL_DAILY,
        "max_daily": MAX_DAILY,
        "increment": DAILY_INCREMENT,
        "healthy_days_per_step": HEALTHY_DAYS_PER_STEP,
        "healthy_days": days,
        "healthy_days_until_increase": HEALTHY_DAYS_PER_STEP - days % HEALTHY_DAYS_PER_STEP
        if limit < MAX_DAILY
        else 0,
        "next_daily_limit": min(MAX_DAILY, limit + DAILY_INCREMENT),
        "reply_delay_minutes": REPLY_DELAY_MINUTES,
    }


def cadence(row, jobs):
    return cadence_details(row, jobs)["daily_limit"]


def next_transmission_at(db, row, jobs, reply=None):
    """One scheduling rule for the worker and the displayed next send (rolling 24 h)."""
    now, limit = m.now(), cadence(row, jobs)
    attempted = sorted((j.created_at for j in jobs if j.attempts), reverse=True)
    recent = [at for at in attempted if at > now - timedelta(hours=24)]
    due = max(now, campaign_next_at(db, row))
    if attempted:
        interval = row.min_interval_seconds if reply else max(row.min_interval_seconds, 86400 // limit)
        due = max(due, attempted[0] + timedelta(seconds=interval))
    if reply:
        due = max(due, reply.created_at + timedelta(minutes=REPLY_DELAY_MINUTES))
    if len(recent) >= limit:
        due = max(due, recent[limit - 1] + timedelta(hours=24))
    return due


def pending_reply(row, jobs, peers):
    candidates = []
    by_id = {p.id: p for p in peers}
    for job in jobs:
        if job.status != "queued" or not job.payload.get("reply_to_job_id"):
            continue
        recipient = by_id.get(job.payload.get("recipient_id"))
        if (
            recipient
            and not technical_blockers(recipient)
            and row.config_version == job.payload.get("sender_version")
            and recipient.config_version == job.payload.get("recipient_version")
        ):
            candidates.append(job)
    return min(candidates, key=lambda job: job.created_at, default=None)


def campaign_next_at(db, row):
    last = db.scalar(select(func.max(m.Message.attempted_at)).where(m.Message.sender_id == row.id))
    return last + timedelta(seconds=row.min_interval_seconds) if last else m.now()


def blockers(db, row, jobs, peers):
    reasons = technical_blockers(row)
    peers = [p for p in peers if p.id != row.id and not technical_blockers(p)]
    if not peers:
        reasons.append("Une deuxième boîte activée et connectée est nécessaire")
    restart = config(row).get("started_at", "")
    if any(j.status in {"failed", "uncertain"} and j.created_at.isoformat() >= restart for j in jobs):
        reasons.append("Un test a échoué ou reste incertain : contrôler le journal avant de reprendre")
    campaign_used = senders.count(db, m.Message.sender_id == row.id)
    if campaign_used + row.external_daily_reserve + MAX_DAILY > row.total_daily_limit:
        reasons.append("Capacité de la boîte insuffisante pour les tests")
    for scope, key in (("domain", row.domain), ("provider", row.provider_group)):
        limit = db.scalar(select(m.EmailLimit).where(m.EmailLimit.scope == scope, m.EmailLimit.key == key))
        used, reserved = senders.group_usage(db, scope, key)
        if not limit or used + reserved > limit.daily_limit or limit.daily_limit <= 0:
            reasons.append("Capacité partagée " + scope + " insuffisante pour les tests")
    return reasons


def snapshot(db):
    peers = selected(db)
    result = []
    for row in db.scalars(select(m.SendingMailbox).order_by(m.SendingMailbox.from_email)):
        if row.status in {"disabled", "excluded"} or senders.protected(row.from_email):
            continue
        jobs = history(db, row)
        recent = [j for j in jobs if j.created_at >= m.now() - timedelta(days=7)]
        active = config(row).get("enabled") is True
        reasons = blockers(db, row, jobs, peers) if active else technical_blockers(row)
        current = [j for j in jobs if j.created_at > m.now() - timedelta(hours=24) and j.attempts]
        today = [
            j
            for j in jobs
            if j.attempts and j.created_at.astimezone(PARIS).date() == m.now().astimezone(PARIS).date()
        ]
        cadence_info = cadence_details(row, jobs)
        limit = cadence_info["daily_limit"]
        last = next((j for j in jobs if j.result.get("accepted") is True), None)
        reply = pending_reply(row, jobs, peers)
        waiting = any(j.status in {"running", "waiting"} for j in jobs)
        next_at = next_transmission_at(db, row, jobs, reply)
        progress = warmup_progress.assess(db, row)
        technical = technical_blockers(row)
        progress["validation_reasons"] = list(dict.fromkeys(progress["validation_reasons"] + technical))
        if progress["complete"] and technical:
            progress["readiness"] = "checks_required"
        progress["can_validate"] = not progress["validation_reasons"] and progress["readiness"] != "validated"
        result.append(
            {
                "id": row.id,
                "progress": progress,
                "email": row.from_email,
                "enabled": active,
                "status": "paused"
                if not active
                else "blocked"
                if reasons
                else "awaiting_receipt"
                if any(j.status in {"running", "waiting"} for j in jobs)
                else "scheduled",
                "blockers": reasons,
                "daily_limit": limit,
                "cadence": cadence_info,
                "attempted_today": len(today),
                "accepted_today": sum(j.result.get("accepted") is True for j in today),
                "received_today": sum(j.result.get("received") is True for j in today),
                "originals_sent_24h": sum(
                    j.result.get("accepted") is True and not j.payload.get("reply_to_job_id") for j in current
                ),
                "replies_sent_24h": sum(
                    j.result.get("accepted") is True and bool(j.payload.get("reply_to_job_id"))
                    for j in current
                ),
                "last_sent_at": last.created_at.isoformat() if last else None,
                "last_received_at": progress["last_received_at"],
                "attempted_24h": len(current),
                "accepted_7d": sum(j.result.get("accepted") is True for j in recent),
                "received_7d": sum(j.result.get("received") is True for j in recent),
                "replies_received_7d": sum(
                    j.result.get("received") is True and bool(j.payload.get("reply_to_job_id"))
                    for j in recent
                ),
                "replies_pending": sum(
                    j.status == "queued" and bool(j.payload.get("reply_to_job_id")) for j in jobs
                ),
                "failed_7d": sum(j.status in {"failed", "uncertain"} for j in recent),
                "next_at": next_at.isoformat() if active and not reasons and not waiting else None,
                "next_kind": ("reply" if reply else "original")
                if active and not reasons and not waiting
                else None,
                "history": [
                    {
                        "id": j.id,
                        "at": j.created_at.isoformat(),
                        "recipient": j.payload.get("recipient_email"),
                        "status": j.status,
                        "is_reply": bool(j.payload.get("reply_to_job_id")),
                        "error": j.error,
                        "accepted": j.result.get("accepted", False),
                        "received_at": j.result.get("received_at"),
                        "folder": j.result.get("folder"),
                    }
                    for j in jobs[:10]
                ],
            }
        )
    return {
        "provider": "native",
        "paid_service": False,
        "mailboxes": result,
        "scope": "Tests techniques entre vos boîtes uniquement ; placement Gmail/Outlook non mesuré.",
    }


@router.get("")
def status(user=Depends(current_user), db: Session = Depends(get_db)):
    return snapshot(db)


@router.get("/completion")
def completion_status(user=Depends(admin), db: Session = Depends(get_db)):
    from . import warmup_completion

    return warmup_completion.status(db)


@router.put("/completion")
def completion_settings(data: CompletionSettings, user=Depends(admin), db: Session = Depends(get_db)):
    from . import warmup_completion

    return warmup_completion.configure(db, data, user.id)


@router.patch("/{mailbox_id}/plan")
def update_plan(mailbox_id: str, data: ProgressPlan, user=Depends(admin), db: Session = Depends(get_db)):
    d.state(db, lock=True)
    row = d.get(db, m.SendingMailbox, mailbox_id)
    if data.expected_plan_key != plan_key(row):
        raise HTTPException(409, "Le programme a changé ; actualisez avant de modifier ses objectifs.")
    proposed = data.model_dump(exclude={"expected_plan_key"})
    if proposed != plan(row):
        invalidate_validation(row, "Objectifs du programme modifiés")
        row.state = {**row.state, "native_warmup": {**config(row), "plan": proposed}}
        d.log(
            db,
            "native_warmup_plan_updated",
            actor_id=user.id,
            source="smtp",
            summary=row.from_email,
            data=proposed,
        )
        db.commit()
    return snapshot(db)


@router.post("/{mailbox_id}/validate")
def validate_preparation(
    mailbox_id: str, data: ValidatePreparation, user=Depends(admin), db: Session = Depends(get_db)
):
    d.state(db, lock=True)
    row = d.get(db, m.SendingMailbox, mailbox_id)
    if data.expected_plan_key != plan_key(row):
        raise HTTPException(409, "Le programme a changé ; actualisez avant de le valider.")
    progress = warmup_progress.assess(db, row)
    reasons = progress["validation_reasons"] + technical_blockers(row)
    if reasons:
        raise HTTPException(409, "; ".join(dict.fromkeys(reasons)))
    row.state = {
        **row.state,
        "native_warmup": {
            **config(row),
            "last_received_at": progress["last_received_at"],
            "validation": {
                "at": m.now().isoformat(),
                "actor_id": user.id,
                "note": data.note,
                "plan_key": plan_key(row),
                "active_days": progress["active_days"],
                "received": progress["received"],
                "checks": progress["checks"],
            },
        },
    }
    d.log(db, "native_warmup_validated", actor_id=user.id, source="smtp", summary=row.from_email)
    # Do not unpause the mailbox/campaigns, enable live sending, or raise any limits.
    db.commit()
    return snapshot(db)


@router.post("/start")
def start(data: Start, user=Depends(admin), db: Session = Depends(get_db)):
    if not data.confirm_owned or len(set(data.mailbox_ids)) < 2:
        raise HTTPException(422, "Confirmez que ces deux boîtes au minimum vous appartiennent.")
    d.state(db, lock=True)
    rows = [d.get(db, m.SendingMailbox, key) for key in dict.fromkeys(data.mailbox_ids)]
    for row in rows:
        if technical_blockers(row):
            raise HTTPException(409, row.from_email + " : " + "; ".join(technical_blockers(row)))
        # A second click must not reset cadence, consent or unknown transmissions.
        if not config(row).get("enabled"):
            previous = config(row)
            same_connection = previous.get("config_version") == row.config_version
            beginning = program_start(row) if same_connection else None
            row.state = {
                **row.state,
                "native_warmup": {
                    **previous,
                    "enabled": True,
                    "program_started_at": beginning.isoformat() if beginning else m.now().isoformat(),
                    "validation": previous.get("validation") if same_connection else None,
                    "started_at": m.now().isoformat(),
                    "config_version": row.config_version,
                    "actor_id": user.id,
                },
            }
    db.flush()
    for row in rows:
        reasons = blockers(db, row, history(db, row), selected(db))
        if reasons:
            raise HTTPException(409, row.from_email + " : " + "; ".join(reasons))
    d.log(
        db, "native_warmup_started", actor_id=user.id, source="smtp", data={"mailbox_ids": data.mailbox_ids}
    )
    db.commit()
    return snapshot(db)


@router.post("/pause")
def pause(user=Depends(admin), db: Session = Depends(get_db)):
    d.state(db, lock=True)
    for row in selected(db):
        row.state = {
            **row.state,
            "native_warmup": {
                **config(row),
                "enabled": False,
                "reserve_until": (m.now() + timedelta(hours=24)).isoformat(),
            },
        }
    d.log(db, "native_warmup_paused", actor_id=user.id, source="smtp")
    db.commit()
    return snapshot(db)


def transmit(job, sender):
    cfg = decrypt_config(sender.encrypted_config)
    email = EmailMessage()
    email["From"], email["To"] = sender.from_email, job.payload["recipient_email"]
    is_reply = bool(job.payload.get("reply_to_job_id"))
    email["Subject"] = ("Re: " if is_reply else "") + "Lumelia — test technique de messagerie"
    email["Date"], email["Message-ID"] = format_datetime(m.now()), job.external_id
    email["Auto-Submitted"] = "auto-replied" if is_reply else "auto-generated"
    email["X-Lumelia-Preparation"] = job.id
    if is_reply:
        email["In-Reply-To"] = job.payload["in_reply_to"]
        email["References"] = job.payload["in_reply_to"]
    email.set_content(
        (
            "Accusé de réception automatique : le test initial a été retrouvé par IMAP.\n"
            if is_reply
            else "Test technique automatique entre les boîtes autorisées de votre espace Lumelia.\n"
        )
        + "Objectif : vérifier la transmission SMTP et la réception IMAP.\n"
        "Une seule réponse de contrôle est autorisée. Aucun prospect n’est destinataire de ce test.\n"
        "Arrêt : Boîtes email > Préparation gratuite > Mettre en pause.\n"
    )
    try:
        smtp = mail.connect_smtp(cfg)
    except Exception:
        raise ProviderError("smtp", "Connexion refusée avant transmission") from None
    try:
        refused = smtp.send_message(
            email, from_addr=sender.from_email, to_addrs=[job.payload["recipient_email"]]
        )
        if refused:
            raise ProviderError("smtp", "Destinataire refusé")
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise ProviderError("smtp", "Test explicitement rejeté") from None
    except ProviderError:
        raise
    except Exception:
        raise ProviderError(
            "smtp", "Transmission incertaine : aucune répétition automatique", uncertain=True
        ) from None
    finally:
        try:
            smtp.quit()
        except Exception:
            smtp.close()


def record_receipt(db, email, mailbox_id, folder, external_id):
    message_id = str(email.get("Message-ID", ""))
    if not message_id.startswith("<lumelia-preparation-"):
        return False
    job = db.scalar(select(m.Job).where(m.Job.kind == KIND, m.Job.external_id == message_id))
    if not job or mailbox_id != job.payload.get("recipient_id"):
        return False
    address = next((value.lower() for _, value in getaddresses(email.get_all("From", []))), "")
    if address != job.payload["sender_email"] or str(email.get("X-Lumelia-Preparation", "")) != job.id:
        return False
    if job.status == "completed":
        return True
    job.result = {
        **job.result,
        "received": True,
        "received_at": m.now().isoformat(),
        "folder": folder,
        "imap_receipt_id": external_id,
    }
    # Observing a test in junk is a delivery incident, not a healthy warm-up signal.
    if any(name in folder.casefold() for name in ("spam", "junk", "indésirable", "indesirable")):
        job.result = {**job.result, "placement": "spam"}
        job.status, job.error = "failed", "Test retrouvé dans les courriers indésirables"
        sender = db.get(m.SendingMailbox, job.payload["sender_id"])
        if sender:
            invalidate_validation(sender, job.error)
        return True
    job.status, job.error = "completed", ""
    sender = db.get(m.SendingMailbox, job.payload["sender_id"])
    if sender and sender.config_version == job.payload["sender_version"]:
        sender.state = {
            **sender.state,
            "native_warmup": {**config(sender), "last_received_at": m.now().isoformat()},
        }
    # One reply at most, queued only from a receipt actually observed in IMAP.
    # Never acknowledge an acknowledgement. Quotas and consent are checked at send time.
    if not job.payload.get("reply_to_job_id"):
        key = "native-warmup-reply:" + job.id
        if not db.scalar(select(m.Job.id).where(m.Job.idempotency_key == key)):
            db.add(
                m.Job(
                    kind=KIND,
                    provider="smtp",
                    status="queued",
                    idempotency_key=key,
                    external_id="<lumelia-preparation-"
                    + m.uid()
                    + "@"
                    + job.payload["recipient_email"].split("@")[1]
                    + ">",
                    payload={
                        "sender_id": job.payload["recipient_id"],
                        "sender_email": job.payload["recipient_email"],
                        "sender_version": job.payload["recipient_version"],
                        "recipient_id": job.payload["sender_id"],
                        "recipient_email": job.payload["sender_email"],
                        "recipient_version": job.payload["sender_version"],
                        "reply_to_job_id": job.id,
                        "in_reply_to": job.external_id,
                    },
                )
            )
    return True


def tick():
    from .warmup_completion import tick as completion_tick

    completion_tick()
    with SessionLocal() as db:
        # Shared lock with campaign transport/policy changes; no burst across replicas.
        d.state(db, lock=True)
        for job in db.scalars(
            select(m.Job).where(m.Job.kind == KIND, m.Job.status.in_(["running", "waiting"]))
        ):
            if job.status == "running" and job.created_at < m.now() - timedelta(minutes=3):
                job.status, job.error = "uncertain", "Exécution interrompue ; transmission non répétée"
            elif job.status == "waiting" and job.created_at < m.now() - timedelta(hours=2):
                job.status, job.error = (
                    "failed",
                    "Accepté par SMTP mais non retrouvé en réception après deux heures",
                )
            if job.status in {"failed", "uncertain"}:
                sender = db.get(m.SendingMailbox, job.payload["sender_id"])
                if sender:
                    invalidate_validation(sender, job.error)
        peers = selected(db)
        by_id = {row.id: row for row in peers}
        replies = {}
        for reply in db.scalars(
            select(m.Job).where(m.Job.kind == KIND, m.Job.status == "queued").order_by(m.Job.created_at)
        ):
            sender, recipient = (
                by_id.get(reply.payload["sender_id"]),
                by_id.get(reply.payload["recipient_id"]),
            )
            if not sender or not recipient:
                continue
            if (
                sender.config_version != reply.payload["sender_version"]
                or recipient.config_version != reply.payload["recipient_version"]
            ):
                reply.status, reply.error = "cancelled", "Connexion modifiée avant réponse de contrôle"
                continue
            if not technical_blockers(recipient):
                replies.setdefault(sender.id, reply)
        candidates = []
        for row in peers:
            jobs = history(db, row)
            if blockers(db, row, jobs, peers) or any(j.status in {"running", "waiting"} for j in jobs):
                continue
            if next_transmission_at(db, row, jobs, replies.get(row.id)) > m.now():
                continue
            candidates.append((row, jobs))
        if not candidates:
            db.commit()
            return 0
        candidates.sort(
            key=lambda item: (
                item[0].id not in replies,
                next(
                    (j.created_at for j in item[1] if j.attempts), datetime.min.replace(tzinfo=m.now().tzinfo)
                ),
            )
        )
        row, jobs = candidates[0]
        reply = replies.get(row.id)
        # Spread original tests across peers; a reply backlog must not attract more originals.
        queued = list(db.scalars(select(m.Job).where(m.Job.kind == KIND, m.Job.status == "queued")))
        recipients = sorted(
            [p for p in peers if p.id != row.id and not technical_blockers(p)],
            key=lambda p: (
                sum(j.payload.get("sender_id") == p.id for j in queued),
                sum(
                    j.attempts > 0
                    and not j.payload.get("reply_to_job_id")
                    and j.payload.get("recipient_id") == p.id
                    for j in jobs
                ),
                p.domain == row.domain,
                p.id,
            ),
        )
        recipient = by_id[reply.payload["recipient_id"]] if reply else recipients[0]
        job = reply or m.Job(
            kind=KIND,
            provider="smtp",
            status="running",
            attempts=1,
            locked_at=m.now(),
            idempotency_key="native-warmup:" + m.uid(),
            external_id="<lumelia-preparation-" + m.uid() + "@" + row.domain + ">",
            payload={
                "sender_id": row.id,
                "sender_email": row.from_email,
                "sender_version": row.config_version,
                "recipient_id": recipient.id,
                "recipient_email": recipient.from_email,
                "recipient_version": recipient.config_version,
            },
        )
        if reply:
            job.status, job.attempts, job.locked_at = "running", 1, m.now()
            job.created_at = m.now()  # Start the rolling transport window, not the queue waiting time.
        db.add(job)
        db.commit()  # A committed intent is never retransmitted after a crash.
        d.state(db, lock=True)
        db.refresh(row)
        db.refresh(recipient)
        if (
            not config(row).get("enabled")
            or not config(recipient).get("enabled")
            or row.config_version != job.payload["sender_version"]
            or recipient.config_version != job.payload["recipient_version"]
            or blockers(db, row, history(db, row), selected(db))
            or technical_blockers(recipient)
            or campaign_next_at(db, row) > m.now()
        ):
            job.status, job.error = "cancelled", "Configuration ou autorisation modifiée avant transmission"
        else:
            try:
                transmit(job, row)
                job.status, job.result = "waiting", {"accepted": True, "accepted_at": m.now().isoformat()}
            except ProviderError as exc:
                job.status, job.error = "uncertain" if exc.uncertain else "failed", str(exc)
                invalidate_validation(row, job.error)
        db.commit()
        return 1
