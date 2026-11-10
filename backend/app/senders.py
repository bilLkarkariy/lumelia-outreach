"""Mailbox affinity and shared rolling limits. Authentication does not prove deliverability."""

import hashlib
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import domain as d
from . import mail, ramp
from . import models as m
from . import schemas as s
from .config import settings
from .db import get_db
from .providers import ProviderError
from .security import admin, current_user, decrypt_config, encrypt_config
from .warmup_policy import KIND as WARMUP_KIND
from .warmup_policy import cold_validation_reasons
from .warmup_policy import reserve as preparation_reserve

router = APIRouter(prefix="/api")


def protected(address):
    """The primary domain keeps its reputation: it never sends or warms up for outreach."""
    address = canonical(address)
    domain = address.rpartition("@")[2]
    return address in settings().protected_addresses or any(
        domain == item or domain.endswith("." + item) for item in settings().protected_domains
    )


class SetupConfig(s.SMTPConfig):
    legal_footer: str = Field(default="", max_length=3000)
    privacy_url: str = Field(default="", max_length=1000)


class SenderCreate(s.Input):
    config: SetupConfig
    provider_group: str = Field(default="", max_length=253)


class ExistingMailbox(s.Input):
    address: EmailStr
    from_name: str = Field(min_length=1, max_length=120)
    confirm_owned_shared_access: Literal[True]
    dns_verified: bool = False


class SenderPolicy(s.Input):
    paused: bool
    warmup_status: Literal["not_started", "warming", "ready"]
    readiness_note: str = Field(default="", max_length=2000)
    daily_limit: int = Field(ge=1, le=500)
    total_daily_limit: int = Field(ge=1, le=1000)
    external_daily_reserve: int = Field(ge=0, le=999)
    min_interval_seconds: int = Field(ge=60, le=86400)


class LimitInput(s.Input):
    scope: Literal["domain", "provider"]
    key: str = Field(min_length=3, max_length=253)
    daily_limit: int = Field(ge=0, le=50000)
    note: str = Field(default="", max_length=1000)


class Assignment(s.Input):
    sender_id: str | None = None


class DeliveryCheck(s.Input):
    provider: Literal["gmail", "outlook"]
    outcome: Literal["inbox", "spam", "missing"]
    observed_at: datetime
    note: str = Field(min_length=10, max_length=1500)


def global_limit(db, state):
    if state.settings.get("daily_limit_mode") != "mailboxes":
        return state.daily_limit
    return min(
        10000,
        sum(
            ramp.assess(db, row)["effective_limit"]
            for row in db.scalars(select(m.SendingMailbox))
            if not readiness(row, reception=True, cold=False)
        ),
    )


def canonical(value):
    return value.strip().lower().rstrip(".")


def configure(db, data, row=None):
    config = data.config.model_dump(mode="json")
    config["from_email"] = canonical(config["from_email"])
    if any(
        protected(address)
        for address in (config["from_email"], config.get("reply_to") or config["from_email"])
    ):
        raise HTTPException(
            422, "Adresse réservée aux échanges habituels. Utilisez une boîte dédiée à la prospection."
        )
    addresses = {canonical(x["address"]) for x in config["mailboxes"]}
    if not {config["from_email"], canonical(config.get("reply_to") or config["from_email"])} <= addresses:
        raise HTTPException(422, "La réception doit couvrir l’expéditeur et le Reply-To.")
    if config["privacy_url"]:
        d.validate_url(config["privacy_url"])
        if not config["privacy_url"].startswith("https://"):
            raise HTTPException(422, "La page d’information doit utiliser HTTPS.")
    identity = hashlib.sha256(
        (canonical(config["host"]) + "\n" + canonical(config["username"])).encode()
    ).hexdigest()
    duplicate = db.scalar(
        select(m.SendingMailbox).where(
            (m.SendingMailbox.identity_hash == identity)
            | (m.SendingMailbox.from_email == config["from_email"])
        )
    )
    if duplicate and (not row or duplicate.id != row.id):
        raise HTTPException(
            409,
            "Cette boîte existe déjà. Un alias du même compte SMTP ne crée pas de capacité supplémentaire.",
        )
    provider = canonical(data.provider_group or config["host"])
    if (
        row
        and db.scalar(
            select(m.Message.id)
            .where(m.Message.sender_id == row.id, m.Message.attempted_at.is_not(None))
            .limit(1)
        )
        and (
            row.from_email != config["from_email"]
            or row.identity_hash != identity
            or row.provider_group != provider
        )
    ):
        raise HTTPException(
            409, "L’identité d’une boîte déjà utilisée reste fixe. Ajoutez une nouvelle boîte."
        )
    if row is None:
        row = m.SendingMailbox()
        db.add(row)
    else:
        row.config_version += 1
        invalidate_approvals(db, row.id)
    row.from_email, row.from_name = config["from_email"], config["from_name"]
    row.domain, row.provider_group = config["from_email"].split("@")[1], provider
    row.identity_hash, row.encrypted_config = identity, encrypt_config(config)
    row.status, row.paused = "configured", True
    row.last_checked_at = row.last_event_at = None
    automatic = ramp.enabled(row)
    previous_preparation = (row.state or {}).get("native_warmup", {})
    row.state = {key: value for key, value in (row.state or {}).items() if key == "warmup_reserve"}
    if previous_preparation:
        row.state = {
            **row.state,
            "native_warmup": {
                **previous_preparation,
                "enabled": False,
                "reserve_until": (m.now() + timedelta(hours=24)).isoformat(),
            },
        }
    if automatic:
        ramp.enable(row)
    db.flush()
    for scope, key in (("domain", row.domain), ("provider", row.provider_group)):
        if not db.scalar(select(m.EmailLimit).where(m.EmailLimit.scope == scope, m.EmailLimit.key == key)):
            db.add(m.EmailLimit(scope=scope, key=key, daily_limit=0))
    db.flush()
    return row


def invalidate_approvals(db, sender_id):
    for message in db.scalars(
        select(m.Message).where(
            m.Message.sender_id == sender_id, m.Message.status.in_(["approved", "queued"])
        )
    ):
        message.status, message.approved_by = "draft", None
        for job in db.scalars(select(m.Job).where(m.Job.message_id == message.id, m.Job.status == "queued")):
            job.status, job.error = "cancelled", "Configuration de l’expéditeur modifiée : relire et valider."


def migrate_legacy(db):
    legacy = db.scalar(select(m.Integration).where(m.Integration.provider == "smtp"))
    if not legacy or db.scalar(
        select(m.SendingMailbox.id).where(m.SendingMailbox.legacy_integration_id == legacy.id)
    ):
        return
    config = decrypt_config(legacy.encrypted_config)
    if any(
        protected(address)
        for address in (config["from_email"], config.get("reply_to") or config["from_email"])
    ):
        legacy.state = {
            **legacy.state,
            "limitation": "Configuration historique exclue : adresse protégée du cold emailing.",
        }
        return
    row = configure(db, SenderCreate(config=SetupConfig.model_validate(config)))
    row.legacy_integration_id = legacy.id
    row.readiness_note = "Configuration historique importée en pause. Préparation et plafonds à vérifier."
    # Old SMTP messages all used this one account. Keep their history and conversation affinity.
    for message in db.scalars(select(m.Message).where(m.Message.sender_id.is_(None))):
        campaign = db.get(m.Campaign, message.campaign_id)
        if campaign and campaign.mode == "live" and campaign.transport == "smtp":
            message.sender_id = row.id
            message.sender_email, message.sender_domain, message.sender_provider = (
                row.from_email,
                row.domain,
                row.provider_group,
            )
            db.get(m.Contact, message.contact_id).email_sender_id = row.id
            if message.status in {"sent", "sending", "uncertain", "failed"}:
                message.attempted_at = message.sent_at or message.updated_at
    invalidate_approvals(db, row.id)


def readiness(row, *, reception=False, cold=True):
    reasons = cold_validation_reasons(row) if cold else []
    if row.paused:
        reasons.append("Boîte en pause")
    if not ramp.enabled(row) and (
        row.warmup_status != "ready" or not row.ready_at or len(row.readiness_note.strip()) < 10
    ):
        reasons.append("Préparation de la boîte non validée")
    if row.status != "authenticated":
        reasons.append("Connexion SMTP/IMAP à vérifier")
    config = decrypt_config(row.encrypted_config)
    if any(
        protected(address)
        for address in (config["from_email"], config.get("reply_to") or config["from_email"])
    ):
        reasons.append("Adresse protégée du cold emailing")
    if not config.get("dns_verified"):
        reasons.append("SPF, DKIM et DMARC à vérifier")
    if len(config.get("legal_footer", "").strip()) < 20 or not config.get("privacy_url", "").startswith(
        "https://"
    ):
        reasons.append("Pied d’email et page d’information à compléter")
    if reception and (not row.last_event_at or row.last_event_at < m.now() - timedelta(minutes=10)):
        reasons.append("Collecte des réponses non à jour")
    return reasons


def recent_condition(exclude_id=None):
    # A stable timestamp prevents edits or reconciliation from resetting the rolling window.
    condition = func.coalesce(m.Message.attempted_at, m.Message.sent_at) >= m.now() - timedelta(hours=24)
    if exclude_id:
        condition &= m.Message.id != exclude_id
    return condition


def count(db, *filters, exclude_id=None):
    return (
        db.scalar(select(func.count()).select_from(m.Message).where(recent_condition(exclude_id), *filters))
        or 0
    )


def group_usage(db, scope, key, exclude_id=None):
    column = m.Message.sender_domain if scope == "domain" else m.Message.sender_provider
    group_column = m.SendingMailbox.domain if scope == "domain" else m.SendingMailbox.provider_group
    used = count(db, column == key, exclude_id=exclude_id)
    reserved = sum(
        row.external_daily_reserve + preparation_reserve(row)
        for row in db.scalars(select(m.SendingMailbox).where(group_column == key))
    )
    return used, reserved


def limits_reasons(db, row, campaign=None, state=None, exclude_id=None):
    progress = ramp.assess(db, row)
    reasons = list(progress["blockers"])
    used = count(db, m.Message.sender_id == row.id, exclude_id=exclude_id)
    if used >= progress["effective_limit"]:
        reasons.append("Plafond de la boîte atteint sur 24 h")
    last = db.scalar(
        select(func.max(m.Message.attempted_at)).where(
            m.Message.sender_id == row.id, recent_condition(exclude_id)
        )
    )
    last_test = db.scalar(
        select(func.max(m.Job.created_at)).where(
            m.Job.kind == WARMUP_KIND,
            m.Job.payload["sender_id"].as_string() == row.id,
            m.Job.attempts > 0,
            m.Job.created_at >= m.now() - timedelta(hours=24),
        )
    )
    if last_test and (not last or last_test > last):
        last = last_test
    if last and last + timedelta(seconds=row.min_interval_seconds) > m.now():
        reasons.append("Intervalle entre deux emails à respecter")
    for scope, key in (("domain", row.domain), ("provider", row.provider_group)):
        policy = db.scalar(select(m.EmailLimit).where(m.EmailLimit.scope == scope, m.EmailLimit.key == key))
        group_used, reserve = group_usage(db, scope, key, exclude_id)
        if not policy or group_used + reserve >= policy.daily_limit:
            reasons.append(
                "Plafond "
                + ("du domaine" if scope == "domain" else "du fournisseur")
                + " non défini ou atteint"
            )
    if state and count(db, exclude_id=exclude_id) >= global_limit(db, state):
        reasons.append("Plafond général atteint sur 24 h")
    if (
        campaign
        and count(db, m.Message.campaign_id == campaign.id, exclude_id=exclude_id) >= campaign.daily_limit
    ):
        reasons.append("Plafond de la campagne atteint sur 24 h")
    return reasons


def assign(db, contact, sender_id=None):
    if contact.email_sender_id and sender_id is None:
        return db.get(m.SendingMailbox, contact.email_sender_id)
    if contact.email_sender_id != sender_id and db.scalar(
        select(m.Message.id)
        .where(m.Message.contact_id == contact.id, m.Message.attempted_at.is_not(None))
        .limit(1)
    ):
        raise HTTPException(409, "Ce contact a déjà un historique d’envoi : son expéditeur reste fixe.")
    if sender_id:
        row = d.get(db, m.SendingMailbox, sender_id)
    else:
        rows = list(
            db.scalars(
                select(m.SendingMailbox).order_by(
                    m.SendingMailbox.last_assigned_at.asc().nullsfirst(), m.SendingMailbox.id
                )
            )
        )
        row = next((x for x in rows if not readiness(x)), None)
        if not row:
            raise HTTPException(409, "Aucune boîte prête. Configurez et validez une boîte dans les réglages.")
    if contact.email_sender_id != row.id:
        contact.email_sender_id, row.last_assigned_at = row.id, m.now()
        for message in db.scalars(
            select(m.Message).where(m.Message.contact_id == contact.id, m.Message.attempted_at.is_(None))
        ):
            message.sender_id, message.sender_version = row.id, None
            if message.status in {"approved", "queued"}:
                message.status, message.approved_by = "draft", None
            for job in db.scalars(
                select(m.Job).where(m.Job.message_id == message.id, m.Job.status == "queued")
            ):
                job.status, job.error = "cancelled", "Expéditeur modifié : relire les messages."
    return row


def view(db, row, include_config=False):
    used = count(db, m.Message.sender_id == row.id)
    progress = ramp.assess(db, row)
    state = d.state(db)
    reasons = readiness(row, reception=True) + limits_reasons(db, row, state=state)
    pool_limit = global_limit(db, state)
    remaining = min(max(0, progress["effective_limit"] - used), max(0, pool_limit - count(db)))
    for scope, key in [("domain", row.domain), ("provider", row.provider_group)]:
        policy = db.scalar(select(m.EmailLimit).where(m.EmailLimit.scope == scope, m.EmailLimit.key == key))
        group_used, reserve = group_usage(db, scope, key)
        remaining = min(remaining, max(0, policy.daily_limit - group_used - reserve) if policy else 0)
    sending_paused = state.paused or not settings().live_actions_enabled
    status = (
        "connection_required"
        if row.status != "authenticated"
        else "paused"
        if row.paused or sending_paused
        else "blocked"
        if reasons
        else "waiting_first_send"
        if not progress.get("accepted_7d")
        else "review_required"
        if progress.get("review_required")
        else "sending"
    )
    result = {
        **d.serialize(row),
        "used_24h": used,
        "preparation_reserved_24h": preparation_reserve(row),
        "ramp": progress,
        "available_24h": remaining if not sending_paused and not reasons else 0,
        "potential_24h": remaining,
        "global_sending_paused": sending_paused,
        "operational_status": status,
        "global_limit": pool_limit,
        "blocking_reasons": reasons,
    }
    if include_config:
        config = decrypt_config(row.encrypted_config)
        config.pop("password", None)
        for mailbox in config["mailboxes"]:
            mailbox.pop("password", None)
        result["config"] = config
    return result


@router.get("/senders")
def list_senders(user=Depends(current_user), db: Session = Depends(get_db)):
    return [
        view(db, row) for row in db.scalars(select(m.SendingMailbox).order_by(m.SendingMailbox.from_email))
    ]


@router.get("/senders/{sender_id}")
def get_sender(sender_id: str, user=Depends(admin), db: Session = Depends(get_db)):
    return view(db, d.get(db, m.SendingMailbox, sender_id), include_config=True)


@router.post("/senders", status_code=201)
def create_sender(data: SenderCreate, user=Depends(admin), db: Session = Depends(get_db)):
    row = configure(db, data)
    ramp.enable(row)
    d.log(db, "sender_created", actor_id=user.id, summary=row.from_email)
    db.commit()
    return view(db, row)


@router.post("/senders/{sender_id}/existing-mailbox", status_code=201)
def connect_existing_mailbox(
    sender_id: str, data: ExistingMailbox, user=Depends(admin), db: Session = Depends(get_db)
):
    """Connect an owned account on the same servers; never reveal or transmit its secrets elsewhere."""
    source = db.scalar(select(m.SendingMailbox).where(m.SendingMailbox.id == sender_id).with_for_update())
    if not source:
        raise HTTPException(404, "Boîte source introuvable.")
    if source.status != "authenticated":
        raise HTTPException(409, "Vérifiez d’abord les accès de la boîte source.")
    address = canonical(str(data.address))
    if protected(address):
        raise HTTPException(422, "Domaine protégé : cette boîte reste hors prospection et préparation.")
    if db.scalar(select(m.SendingMailbox.id).where(m.SendingMailbox.from_email == address)):
        raise HTTPException(409, "Cette boîte est déjà connectée.")
    config = decrypt_config(source.encrypted_config)
    incoming = next(
        (item for item in config["mailboxes"] if canonical(item["address"]) == source.from_email), None
    )
    if not incoming:
        raise HTTPException(409, "La boîte source doit disposer de sa propre réception IMAP.")
    # Hosts and passwords are never supplied by the caller. A separate login proves
    # this is an existing mailbox, not merely a new From alias of the source account.
    config.update(
        username=address,
        from_email=address,
        from_name=data.from_name,
        reply_to=None,
        dns_verified=data.dns_verified,
        mailboxes=[{**incoming, "username": address, "address": address}],
    )
    payload = SenderCreate(config=SetupConfig.model_validate(config), provider_group=source.provider_group)
    try:
        result = mail.verify(payload.config.model_dump(mode="json"))
        if not result.get("authenticated"):
            raise ProviderError("smtp", "Accès refusés")
    except ProviderError:
        # Provider error text is deliberately not returned: it may contain login details.
        raise HTTPException(
            422,
            "Accès SMTP/IMAP refusés. Aucune boîte ajoutée. Utilisez la connexion manuelle si les mots de passe diffèrent.",
        ) from None
    row = configure(db, payload)
    for key in ("daily_limit", "total_daily_limit", "external_daily_reserve", "min_interval_seconds"):
        setattr(row, key, getattr(source, key))
    row.status, row.last_checked_at = "authenticated", m.now()
    ramp.enable(row)
    # No readiness, external observations, warmup progress or collection cursor is inherited.
    d.log(
        db,
        "existing_mailbox_connected",
        actor_id=user.id,
        summary=row.from_email,
        data={"source_sender_id": source.id, "campaigns_paused": True},
    )
    db.commit()
    return view(db, row)


@router.post("/senders/{sender_id}/ramp")
def enable_ramp(sender_id: str, user=Depends(admin), db: Session = Depends(get_db)):
    row = d.get(db, m.SendingMailbox, sender_id)
    ramp.enable(row)
    d.log(db, "sender_ramp_enabled", actor_id=user.id, summary=row.from_email)
    db.commit()
    return view(db, row)


@router.post("/senders/{sender_id}/delivery-check")
def record_delivery_check(
    sender_id: str, data: DeliveryCheck, user=Depends(admin), db: Session = Depends(get_db)
):
    if (
        not data.observed_at.tzinfo
        or data.observed_at > m.now()
        or data.observed_at < m.now() - timedelta(days=30)
    ):
        raise HTTPException(
            422, "Le contrôle doit être daté des trente derniers jours, avec un fuseau horaire."
        )
    row = db.scalar(select(m.SendingMailbox).where(m.SendingMailbox.id == sender_id).with_for_update())
    if not row:
        raise HTTPException(404, "Boîte introuvable.")
    checks = {**(row.state or {}).get("delivery_checks", {})}
    old = checks.get(data.provider)
    if old and datetime.fromisoformat(old["observed_at"]) > data.observed_at:
        raise HTTPException(409, "Un contrôle plus récent existe déjà pour ce fournisseur.")
    checks[data.provider] = {**data.model_dump(mode="json"), "recorded_by": user.id}
    row.state = {**(row.state or {}), "delivery_checks": checks}
    d.log(
        db,
        "delivery_check_recorded",
        actor_id=user.id,
        summary=row.from_email,
        data=data.model_dump(mode="json"),
    )
    db.commit()
    return view(db, row)


@router.post("/senders/{sender_id}/approve-ramp-step")
def approve_ramp_step(sender_id: str, user=Depends(admin), db: Session = Depends(get_db)):
    row = db.scalar(select(m.SendingMailbox).where(m.SendingMailbox.id == sender_id).with_for_update())
    if not row:
        raise HTTPException(404, "Boîte introuvable.")
    report = ramp.assess(db, row)
    if not report.get("external_checks_current") or report.get("blockers"):
        raise HTTPException(
            422,
            "Renseignez des tests Gmail et Outlook reçus en boîte de réception depuis moins de sept jours, sans incident en cours.",
        )
    if not report.get("review_required"):
        raise HTTPException(422, "Aucun nouveau palier éligible. Attendez l’activité suffisante.")
    limit = report["next_limit"]
    row.state = {
        **row.state,
        "ramp": {**row.state.get("ramp", {}), "approved_limit": limit, "reviewed_at": m.now().isoformat()},
    }
    d.log(
        db, "sender_ramp_step_approved", actor_id=user.id, summary=row.from_email, data={"daily_limit": limit}
    )
    db.commit()
    return view(db, row)


@router.patch("/senders/{sender_id}/connection")
def update_sender(sender_id: str, data: SenderCreate, user=Depends(admin), db: Session = Depends(get_db)):
    row = configure(db, data, d.get(db, m.SendingMailbox, sender_id))
    d.log(db, "sender_connection_updated", actor_id=user.id, summary=row.from_email)
    db.commit()
    return view(db, row)


@router.patch("/senders/{sender_id}/policy")
def update_policy(sender_id: str, data: SenderPolicy, user=Depends(admin), db: Session = Depends(get_db)):
    if data.external_daily_reserve + data.daily_limit > data.total_daily_limit:
        raise HTTPException(
            422, "Le plafond total doit couvrir la prospection et la réserve hors application."
        )
    if data.warmup_status == "ready" and len(data.readiness_note.strip()) < 10:
        raise HTTPException(422, "Documentez la préparation et les contrôles réels de cette boîte.")
    row = d.get(db, m.SendingMailbox, sender_id)
    if data.warmup_status == "warming" and not row.warmup_started_at:
        row.warmup_started_at = m.now()
    row.ready_at = (row.ready_at or m.now()) if data.warmup_status == "ready" else None
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    d.log(db, "sender_policy_updated", actor_id=user.id, summary=row.from_email, data=data.model_dump())
    db.commit()
    return view(db, row)


@router.post("/senders/{sender_id}/check")
def check_sender(sender_id: str, user=Depends(admin), db: Session = Depends(get_db)):
    row = d.get(db, m.SendingMailbox, sender_id)
    try:
        result = mail.verify(decrypt_config(row.encrypted_config))
        row.status = "authenticated" if result.get("authenticated") else "error"
        row.state = {**row.state, "connection_error": ""}
    except ProviderError as exc:
        row.status = "error"
        row.last_event_at = None
        row.state = {**row.state, "connection_error": str(exc)}
    row.last_checked_at = m.now()
    db.commit()
    return view(db, row)


@router.get("/email-limits")
def list_limits(user=Depends(current_user), db: Session = Depends(get_db)):
    rows = []
    for row in db.scalars(select(m.EmailLimit).order_by(m.EmailLimit.scope, m.EmailLimit.key)):
        used, reserve = group_usage(db, row.scope, row.key)
        rows.append({**d.serialize(row), "used_24h": used, "external_daily_reserve": reserve})
    return rows


@router.post("/email-limits")
def save_limit(data: LimitInput, user=Depends(admin), db: Session = Depends(get_db)):
    key = canonical(data.key)
    row = db.scalar(select(m.EmailLimit).where(m.EmailLimit.scope == data.scope, m.EmailLimit.key == key))
    if not row:
        row = m.EmailLimit(scope=data.scope, key=key)
        db.add(row)
    row.daily_limit, row.note = data.daily_limit, data.note
    d.log(
        db, "email_limit_updated", actor_id=user.id, summary=data.scope + ": " + key, data=data.model_dump()
    )
    db.commit()
    return d.serialize(row)


@router.post("/contacts/{contact_id}/sender")
def assign_sender(contact_id: str, data: Assignment, user=Depends(admin), db: Session = Depends(get_db)):
    contact = d.get(db, m.Contact, contact_id)
    row = assign(db, contact, data.sender_id)
    d.log(db, "contact_sender_assigned", contact=contact, actor_id=user.id, summary=row.from_email)
    db.commit()
    return {"sender_id": row.id, "from_email": row.from_email}
