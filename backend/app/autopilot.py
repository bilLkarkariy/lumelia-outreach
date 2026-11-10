"""Automatic Hunter sourcing and email preparation, with a bounded free-credit budget.

Each provider operation has a durable intent. Unknown purchases are never repeated.
Preparing a pipeline is independent of the campaign send switch; the SMTP worker
keeps enforcing readiness, affinity, pauses, opt-outs and rolling limits.
"""

import hashlib
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from . import domain as d
from . import models as m
from . import providers, senders, sourcing
from . import schemas as s
from .config import settings
from .db import SessionLocal, get_db
from .security import admin, current_user, decrypt_config

router = APIRouter(prefix="/api/autopilot")
BASE = "https://api.hunter.io/v2"
FILTERS = {
    "headquarters_location": {"include": [{"country": "FR"}]},
    "headcount": ["11-50", "51-200"],
    "industry": {"include": ["IT Services and IT Consulting", "Business Consulting and Services"]},
    "company_type": {"include": ["privately held"]},
}
SEQUENCE = [
    {
        "channel": "email",
        "day": 0,
        "subject": "Le suivi des opportunités chez {{societe}}",
        "body": "Bonjour {{prenom}},\n\nJe vous contacte au sujet du suivi commercial chez {{societe}}. "
        "Nous aidons les équipes B2B à garder leurs opportunités ouvertes à jour dans leur CRM : "
        "un responsable, une prochaine action et des relances suivies.\n\n"
        "Est-ce un sujet chez vous en ce moment ?\n\nL’équipe Lumelia",
    },
    {
        "channel": "email",
        "day": 5,
        "subject": "Re: Le suivi des opportunités chez {{societe}}",
        "body": "Bonjour {{prenom}},\n\nJe reviens sur mon message. Le point de départ est simple : savoir "
        "quelles opportunités attendent une action, qui les suit et quand relancer. "
        "Est-ce déjà bien couvert chez {{societe}} ?\n\nL’équipe Lumelia",
    },
    {
        "channel": "email",
        "day": 12,
        "subject": "Re: Le suivi des opportunités chez {{societe}}",
        "body": "Bonjour {{prenom}},\n\nJe clôture mon suivi. Si le sujet devient d’actualité, vous pourrez "
        "répondre à cet email.\n\nBonne continuation,\nL’équipe Lumelia",
    },
]


class Configuration(s.Input):
    enabled: bool = False
    free_credit_limit: int = Field(default=20, ge=0, le=50)
    contacts_per_day: int = Field(default=200, ge=1, le=1000)


def config(state):
    return dict(state.settings.get("autopilot", {}))


def store(state, data):
    state.settings = {**state.settings, "autopilot": data}


def credits(db, campaign_id=None):
    # Units are half-credits: integer reservations avoid floating-point drift.
    return sum(
        job.payload.get("credit_units", 0)
        for job in db.scalars(
            select(m.Job).where(
                m.Job.kind == "autopilot",
                (m.Job.campaign_id == campaign_id) if campaign_id else True,
                m.Job.status.in_(["running", "waiting", "completed", "uncertain"]),
            )
        )
    )


def view(db, state):
    data = config(state)
    campaign = db.get(m.Campaign, data.get("campaign_id")) if data.get("campaign_id") else None
    result = {"configured": campaign is not None, **data}
    if not campaign:
        return result
    credit_units_used = credits(db, campaign.id)
    result.update(
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        credits_reserved_or_used=credit_units_used / 2,
        contacts=db.scalar(
            select(func.count()).select_from(m.Contact).where(m.Contact.campaign_id == campaign.id)
        ),
        verified=db.scalar(
            select(func.count())
            .select_from(m.Contact)
            .where(m.Contact.campaign_id == campaign.id, m.Contact.email_status == "valid")
        ),
        prepared=db.scalar(
            select(func.count())
            .select_from(m.Message)
            .where(
                m.Message.campaign_id == campaign.id, m.Message.status.in_(["draft", "approved", "queued"])
            )
        ),
        message_counts=sourcing.message_counts(db, campaign.id),
        queued=db.scalar(
            select(func.count())
            .select_from(m.Job)
            .where(m.Job.campaign_id == campaign.id, m.Job.kind == "send", m.Job.status == "queued")
        ),
        pending_operations=db.scalar(
            select(func.count())
            .select_from(m.Job)
            .where(
                m.Job.campaign_id == campaign.id,
                m.Job.kind == "autopilot",
                m.Job.status.in_(["queued", "running", "waiting"]),
            )
        ),
        sending_enabled=settings().live_actions_enabled and not state.paused and campaign.status == "active",
        sequence=campaign.sequence,
    )
    result["send_blockers"] = []
    if not settings().live_actions_enabled:
        result["send_blockers"].append("Envois réels désactivés sur le serveur")
    if state.paused:
        result["send_blockers"].append("Envois en pause")
    if campaign.status != "active":
        result["send_blockers"].append("Campagne non activée")
    if not any(not senders.readiness(row, reception=True) for row in db.scalars(select(m.SendingMailbox))):
        result["send_blockers"].append("Aucune boîte prête pour la prospection")
    result["next_actions"] = []
    for job in db.scalars(
        select(m.Job)
        .where(m.Job.campaign_id == campaign.id, m.Job.status.in_(["queued", "waiting", "running"]))
        .order_by(m.Job.run_at)
        .limit(12)
    ):
        contact = db.get(m.Contact, job.contact_id) if job.contact_id else None
        account = db.get(m.Account, contact.account_id) if contact else None
        stage = job.payload.get("stage", job.kind)
        required_units = 4 if stage == "domain" else 1 if stage == "verify" else 0
        wait_reason = ""
        if (
            job.kind == "autopilot"
            and job.status == "queued"
            and required_units
            and credit_units_used + required_units > data.get("free_credit_limit", 0) * 2
        ):
            wait_reason = "Plafond de crédits atteint"
        result["next_actions"].append(
            {
                "id": job.id,
                "kind": "email" if job.kind == "send" else job.payload.get("stage", job.kind),
                "status": job.status,
                "wait_reason": wait_reason,
                "due_at": job.run_at.isoformat(),
                "subject": (
                    (contact.first_name + " " + contact.last_name + " · " + account.name)
                    if contact and account
                    else job.payload.get("company", "Recherche de nouvelles entreprises")
                ),
            }
        )
    return result


@router.get("")
def status(user=Depends(current_user), db: Session = Depends(get_db)):
    return view(db, d.state(db))


@router.post("/configure")
def configure(data: Configuration, user=Depends(admin), db: Session = Depends(get_db)):
    from .campaign_groups import default_group
    from .offers import PRIMARY_ID

    state = d.state(db, lock=True)
    previous = config(state)
    campaign = db.get(m.Campaign, previous.get("campaign_id")) if previous.get("campaign_id") else None
    if not campaign:
        icp = db.scalar(select(m.ICP).where(m.ICP.offer_id == PRIMARY_ID).order_by(m.ICP.created_at).limit(1))
        campaign = m.Campaign(
            name="Revue du pipeline · prospection automatique",
            offer_id=PRIMARY_ID,
            group_id=default_group(db, PRIMARY_ID).id,
            niche="Ciblage initial",
            icp_id=icp.id,
            owner_id=user.id,
            mode="live",
            transport="smtp",
            sequence=SEQUENCE,
            daily_limit=600,
            config={"automatic": True},
            budget_approved=True,
        )
        db.add(campaign)
        db.flush()
    values = {
        **previous,
        **data.model_dump(),
        "campaign_id": campaign.id,
        "version": previous.get("version", 0) + 1,
    }
    store(state, values)
    for contact in db.scalars(
        select(m.Contact).where(m.Contact.campaign_id == campaign.id, m.Contact.retention_until.is_(None))
    ):
        contact.retention_until = m.now() + timedelta(days=365)
    d.log(db, "autopilot_configured", actor_id=user.id, campaign_id=campaign.id, data=data.model_dump())
    db.commit()
    return view(db, state)


def make_job(db, campaign, stage, key, payload=None, contact=None):
    identity = "auto:" + campaign.id + ":" + stage + ":" + hashlib.sha256(key.encode()).hexdigest()
    existing = db.scalar(select(m.Job).where(m.Job.idempotency_key == identity))
    if existing:
        if existing.status == "cancelled" and existing.attempts == 0:
            existing.status, existing.error, existing.run_at = "queued", "", m.now()
            existing.payload = {"stage": stage, **(payload or {})}
        return existing
    job = m.Job(
        kind="autopilot",
        provider="hunter",
        campaign_id=campaign.id,
        contact_id=contact.id if contact else None,
        idempotency_key=identity,
        payload={"stage": stage, **(payload or {})},
    )
    db.add(job)
    db.flush()
    return job


def relevant_role(value):
    role = value.casefold()
    return any(
        word in role
        for word in (
            "ceo",
            "chief executive",
            "founder",
            "fondateur",
            "fondatrice",
            "gérant",
            "gerant",
            "dirigeant",
            "président",
            "president",
            "directeur commercial",
            "directrice commerciale",
            "sales director",
            "head of sales",
            "commercial director",
            "managing director",
            "directeur général",
            "directrice générale",
            "geschäftsführer",
            "geschäftsführerin",
            "inhaber",
            "vertriebsleiter",
            "geschäftsleitung",
            "zaakvoerder",
            "bestuurder",
            "algemeen directeur",
            "oprichter",
        )
    )


def domain_excluded(db, domain):
    account = db.scalar(select(m.Account).where(m.Account.domain == domain))
    return (
        senders.protected("team@" + domain)
        or (account and (account.lifecycle != "prospect" or account.outreach_paused))
        or db.scalar(
            select(m.Suppression.id)
            .where(
                m.Suppression.kind == "domain",
                m.Suppression.value == domain,
                m.Suppression.scope.in_(["all", "email"]),
            )
            .limit(1)
        )
        is not None
    )


def consume(db, job, result):
    campaign = d.get(db, m.Campaign, job.campaign_id)
    cfg = sourcing.runtime(d.state(db), campaign) or {}
    source_filters = job.payload.get("source_filters", cfg.get("filters", FILTERS))
    country = (
        source_filters.get("headquarters_location", {})
        .get("include", [{"country": "FR"}])[0]
        .get("country", "FR")
    )
    profile = db.get(m.ICP, campaign.icp_id)
    stage = job.payload["stage"]
    if stage == "discover":
        candidates = []
        for item in result.get("data", [])[:100]:
            try:
                domain = d.normalize_domain(item.get("domain", ""))
            except HTTPException:
                continue
            company = str(item.get("organization") or "").strip()
            if not company or domain_excluded(db, domain):
                continue
            # Domain-level opt-outs and existing clients never trigger another purchase.
            account = db.scalar(select(m.Account).where(m.Account.domain == domain))
            if account and (account.lifecycle != "prospect" or account.outreach_paused):
                continue
            account, created = d.create_account(
                db,
                s.AccountCreate(
                    name=company,
                    domain=domain,
                    country=country,
                    notes="Entreprise découverte par Hunter. Activité, effectif exact et correspondance au profil restent à vérifier.",
                ),
            )
            if created:
                db.add(
                    m.Evidence(
                        account_id=account.id,
                        field="découverte de l’entreprise",
                        value="Sous-campagne : "
                        + campaign.name
                        + ". Filtres de recherche : "
                        + str(source_filters)
                        + ". Correspondance exacte au profil à vérifier.",
                        source_url="https://hunter.io",
                        source_type="provider",
                        confidence="unverified",
                    )
                )
            candidates.append({"domain": domain, "company": company})
            # One durable domain purchase across niches; never move an existing contact.
            claimed = any(
                other.payload.get("domain") == domain
                for other in db.scalars(
                    select(m.Job).where(
                        m.Job.kind == "autopilot",
                        m.Job.campaign_id != campaign.id,
                        m.Job.status.in_(["queued", "running", "waiting", "completed", "uncertain"]),
                    )
                )
            )
            if claimed or db.scalar(select(m.Contact.id).where(m.Contact.account_id == account.id).limit(1)):
                continue
            make_job(db, campaign, "domain", domain, {"domain": domain, "company": company})
        return {
            "companies": candidates,
            "source_filters": source_filters,
            "applied_filters": result.get("meta", {}).get("filters", {}),
        }
    if stage == "domain":
        data = result.get("data") or {}
        if data.get("webmail") or data.get("disposable") or data.get("accept_all"):
            return {"excluded": "Domaine non approprié ou accept-all", "contacts": []}
        created = []
        for item in data.get("emails", [])[: job.payload.get("contact_limit", 2)]:
            first, last = str(item.get("first_name") or "").strip(), str(item.get("last_name") or "").strip()
            role = str(item.get("position") or item.get("position_raw") or "").strip()
            address = str(item.get("value") or "").strip().lower()
            if not first or not last or "*" in first + last or not relevant_role(role):
                continue
            if address.rpartition("@")[2] != job.payload["domain"] or senders.protected(address):
                continue
            source = next(
                (
                    x.get("uri")
                    for x in item.get("sources", [])
                    if str(x.get("uri", "")).startswith(("https://", "http://"))
                ),
                "https://hunter.io",
            )
            try:
                with db.begin_nested():
                    account, _ = d.create_account(
                        db,
                        s.AccountCreate(
                            name=job.payload["company"],
                            domain=job.payload["domain"],
                            country=country,
                            notes="Candidat issu de la sous-campagne "
                            + campaign.name
                            + ". Critères à vérifier : "
                            + str(profile.criteria),
                        ),
                    )
                    if account.lifecycle != "prospect" or account.outreach_paused:
                        continue
                    existing = db.scalar(select(m.Contact).where(m.Contact.email == address))
                    if existing:
                        continue  # Do not move or resume an existing conversation.
                    contact, was_created = d.create_contact(
                        db,
                        s.ContactCreate(
                            account_id=account.id,
                            campaign_id=campaign.id,
                            first_name=first,
                            last_name=last,
                            role=role,
                            email=address,
                            information_status="planned",
                            source_type="provider",
                            source_url=source,
                            linkedin_url=item.get("linkedin") or "",
                            sourcing_note="Coordonnées professionnelles retournées par Hunter le "
                            + m.now().date().isoformat(),
                            professional_relevance="Fonction retournée par Hunter : "
                            + role
                            + ". Pertinence pour "
                            + profile.name
                            + " et besoin commercial à confirmer.",
                        ),
                        campaign.owner_id,
                    )
                    if was_created:
                        created.append(contact.id)
                        make_job(db, campaign, "verify", contact.id, {"email": contact.email}, contact)
            except (HTTPException, ValueError):
                continue
        return {"contacts": created}
    contact = db.get(m.Contact, job.contact_id)
    data = result.get("data") or {}
    if not contact:
        return {"erased": True}
    account = db.get(m.Account, contact.account_id)
    if (
        contact.paused
        or not account
        or account.outreach_paused
        or account.lifecycle != "prospect"
        or d.is_suppressed(db, contact)
    ):
        return {"excluded": "Contact arrêté pendant la vérification"}
    valid = data.get("status") == "valid" and not any(
        data.get(x) for x in ("webmail", "disposable", "accept_all", "gibberish")
    )
    contact.email_status = "valid" if valid else "invalid" if data.get("status") == "invalid" else "risky"
    contact.email_verified_at = m.now() if valid else None
    db.add(
        m.Evidence(
            contact_id=contact.id,
            account_id=contact.account_id,
            field="email_verification",
            value=str(data.get("status", "unknown")),
            source_type="hunter",
            source_url="https://hunter.io",
            confidence="confirmed" if valid else "unverified",
        )
    )
    if valid and cfg.get("legacy") and campaign.sequence:
        d.prepare_contact(db, contact, campaign.owner_id)
        other = db.scalar(
            select(m.Contact.id)
            .where(
                m.Contact.account_id == contact.account_id,
                m.Contact.id != contact.id,
                m.Contact.approved.is_(True),
                m.Contact.paused.is_(False),
            )
            .limit(1)
        )
        contact.approved = not other and not d.eligibility(db, contact, "email", require_approved=False)
        if contact.approved:
            contact.qualification = {
                **d.score_contact(db, contact),
                "validation": "automatic_email_eligibility",
            }
        d.log(
            db,
            "autopilot_contact_prepared",
            contact=contact,
            source="hunter",
            summary="Email vérifié et séquence préparée automatiquement.",
        )
    return {
        "email_status": contact.email_status,
        "prepared": bool(valid and cfg.get("legacy") and campaign.sequence),
    }


def prepare_sends(db, state, campaign):
    if (
        not settings().live_actions_enabled
        or state.paused
        or not campaign.budget_approved
        or campaign.status == "paused"
    ):
        return
    if not settings().public_url.startswith("https://"):
        return
    for contact in db.scalars(
        select(m.Contact).where(m.Contact.campaign_id == campaign.id, m.Contact.approved.is_(True))
    ):
        if d.eligibility(db, contact, "email"):
            continue
        try:
            sender = senders.assign(db, contact)
        except HTTPException:
            continue
        if senders.readiness(sender, reception=True):
            continue
        for message in db.scalars(
            select(m.Message).where(m.Message.contact_id == contact.id, m.Message.status == "draft")
        ):
            key = "send:" + message.id
            if db.scalar(select(m.Job.id).where(m.Job.idempotency_key == key)):
                continue  # A cancelled/uncertain operation is never silently reactivated.
            message.sender_id, message.sender_version = sender.id, sender.config_version
            message.status, message.approved_by = "queued", campaign.owner_id
            db.add(
                m.Job(
                    kind="send",
                    provider="smtp",
                    contact_id=contact.id,
                    campaign_id=campaign.id,
                    message_id=message.id,
                    idempotency_key=key,
                    run_at=message.due_at,
                )
            )
            campaign.status, campaign.approved_by, campaign.approved_at = "active", campaign.owner_id, m.now()


def tick(campaign_id=None):
    with SessionLocal() as db:
        state = d.state(db, lock=True)
        global_cfg = config(state)
        if not global_cfg.get("enabled"):
            return 0
        choices = [(row, sourcing.runtime(state, row)) for row in db.scalars(select(m.Campaign))]
        choices = [
            (row, cfg)
            for row, cfg in choices
            if cfg and cfg.get("enabled") and (not campaign_id or row.id == campaign_id)
        ]
        if not choices:
            return 0
        campaign, cfg = min(choices, key=lambda pair: pair[1].get("last_tick_at") or "")
        cfg["last_tick_at"] = m.now().isoformat()
        sourcing.save_runtime(state, campaign, cfg)
        if cfg.get("legacy"):
            prepare_sends(db, state, campaign)
        for stale in db.scalars(
            select(m.Job).where(
                m.Job.kind == "autopilot",
                m.Job.status == "running",
                m.Job.locked_at < m.now() - timedelta(minutes=3),
            )
        ):
            stale.status, stale.error = "uncertain", "Opération interrompue ; aucun nouvel achat automatique."
        integration = db.scalar(select(m.Integration).where(m.Integration.provider == "hunter"))
        if not integration:
            sourcing.save_runtime(state, campaign, {**cfg, "last_error": "Connexion Hunter manquante"})
            db.commit()
            return 0
        # A free discovery round per week; domains retain stable deduplication keys.
        week = str(m.now().date().isocalendar()[:2])
        filter_key = hashlib.sha256(str(cfg.get("filters", FILTERS)).encode()).hexdigest()[:12]
        make_job(
            db, campaign, "discover", week + ":" + filter_key, {"source_filters": cfg.get("filters", FILTERS)}
        )
        job = db.scalar(
            select(m.Job)
            .where(
                m.Job.kind == "autopilot",
                m.Job.campaign_id == campaign.id,
                m.Job.status.in_(["queued", "waiting"]),
                m.Job.run_at <= m.now(),
            )
            .order_by(case((m.Job.contact_id.is_not(None), 0), else_=1), m.Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            db.commit()
            return 0
        stage, polling = job.payload["stage"], job.status == "waiting"
        units = 0 if stage == "discover" else 1 if stage == "verify" else 2
        if stage == "domain":
            if domain_excluded(db, job.payload["domain"]):
                job.status, job.error = "cancelled", "Domaine exclu avant recherche"
                db.commit()
                return 0
            today_count = (
                db.scalar(
                    select(func.count())
                    .select_from(m.Contact)
                    .where(
                        m.Contact.campaign_id == campaign.id,
                        m.Contact.created_at >= m.now().replace(hour=0, minute=0, second=0, microsecond=0),
                    )
                )
                or 0
            )
            remaining_contacts = cfg.get("contacts_per_day", 200) - today_count
            if remaining_contacts <= 0:
                job.run_at = m.now() + timedelta(hours=1)
                db.commit()
                return 0
            job.payload = {**job.payload, "contact_limit": min(2, remaining_contacts)}
        # Keep headroom for every contact requested from this domain.
        required_units = units + (job.payload.get("contact_limit", 2) if stage == "domain" else 0)
        if not polling and (
            credits(db, campaign.id) + required_units > cfg.get("free_credit_limit", 0) * 2
            or credits(db) + required_units > global_cfg.get("free_credit_limit", 0) * 2
        ):
            job.run_at = m.now() + timedelta(hours=1)
            sourcing.save_runtime(
                state,
                campaign,
                {**cfg, "last_error": "Limite de crédits gratuits atteinte ; préparation conservée."},
            )
            db.commit()
            return 0
        if polling and job.attempts >= 20:
            job.status, job.error = "uncertain", "Vérification sans résultat définitif"
            db.commit()
            return 0
        job.status, job.locked_at = "running", m.now()
        job.attempts += 1
        job.payload = {
            **job.payload,
            "credit_units": units,
            "config_version": cfg.get("version"),
            "global_version": global_cfg.get("version", 0),
            "source_filters": cfg.get("filters", FILTERS),
            "polling": polling,
        }
        job_id, encrypted_config = job.id, integration.encrypted_config
        db.commit()
    with SessionLocal() as db:
        state = d.state(db, lock=True)
        global_cfg = config(state)
        job = db.get(m.Job, job_id)
        campaign = db.get(m.Campaign, job.campaign_id)
        cfg = sourcing.runtime(state, campaign) or {}
        integration = db.scalar(select(m.Integration).where(m.Integration.provider == "hunter"))
        if (
            not global_cfg.get("enabled")
            or global_cfg.get("version", 0) != job.payload["global_version"]
            or not cfg.get("enabled")
            or cfg.get("version") != job.payload["config_version"]
            or not integration
            or integration.encrypted_config != encrypted_config
        ):
            job.status, job.error = "cancelled", "Automatisation ou accès modifié avant exécution"
            db.commit()
            return 0
        key = decrypt_config(integration.encrypted_config)["api_key"]
        try:
            if stage == "domain":
                today_count = (
                    db.scalar(
                        select(func.count())
                        .select_from(m.Contact)
                        .where(
                            m.Contact.campaign_id == campaign.id,
                            m.Contact.created_at
                            >= m.now().replace(hour=0, minute=0, second=0, microsecond=0),
                        )
                    )
                    or 0
                )
                remaining_contacts = cfg.get("contacts_per_day", 200) - today_count
                if remaining_contacts <= 0:
                    job.status, job.run_at = "queued", m.now() + timedelta(hours=1)
                    db.commit()
                    return 0
                job.payload = {
                    **job.payload,
                    "contact_limit": min(job.payload.get("contact_limit", 2), remaining_contacts),
                }
            if units and not polling:
                balance = providers.verify_connection("hunter", {"api_key": key})
                quota = balance.get("quota", {})
                remaining = quota.get("requests", {}).get("credits", {}).get("remaining")
                integration.state = {**integration.state, "quota": quota}
                if (
                    str(quota.get("plan_name", "")).lower() != "free"
                    or not isinstance(remaining, (int, float))
                    or remaining < required_units / 2
                ):
                    job.status, job.run_at = "queued", m.now() + timedelta(hours=1)
                    sourcing.save_runtime(
                        state,
                        campaign,
                        {
                            **cfg,
                            "last_error": "Solde gratuit insuffisant ou forfait à vérifier. Aucun crédit payant engagé.",
                        },
                    )
                    db.commit()
                    return 0
            if stage == "discover":
                result = providers.request(
                    "hunter", "POST", BASE + "/discover", api_key=key, payload=job.payload["source_filters"]
                )
            elif stage == "domain":
                if domain_excluded(db, job.payload["domain"]):
                    job.status, job.error = "cancelled", "Domaine exclu avant recherche"
                    db.commit()
                    return 0
                result = providers.request(
                    "hunter",
                    "GET",
                    BASE + "/domain-search",
                    api_key=key,
                    params={
                        "domain": job.payload["domain"],
                        "limit": job.payload.get("contact_limit", 2),
                        "type": "personal",
                        "department": "executive,sales",
                        "seniority": "executive",
                        "required_field": "full_name,position",
                    },
                )
            else:
                contact = db.get(m.Contact, job.contact_id)
                account = db.get(m.Account, contact.account_id) if contact else None
                if (
                    not contact
                    or not account
                    or contact.paused
                    or account.outreach_paused
                    or account.lifecycle != "prospect"
                    or d.is_suppressed(db, contact)
                ):
                    job.status = "cancelled"
                    db.commit()
                    return 0
                result = providers.request(
                    "hunter", "GET", BASE + "/email-verifier", api_key=key, params={"email": contact.email}
                )
            if result.get("_pending"):
                job.status, job.run_at = "waiting", m.now() + timedelta(minutes=1)
            else:
                job.result = consume(db, job, result)
                job.status = "completed"
            sourcing.save_runtime(
                state,
                campaign,
                {**cfg, "last_error": "", "last_operation": stage, "last_operation_at": m.now().isoformat()},
            )
        except providers.ProviderError as exc:
            job.status, job.error = ("uncertain" if exc.uncertain else "failed"), str(exc)
            sourcing.save_runtime(state, campaign, {**cfg, "last_error": str(exc)})
        db.commit()
    return 1
