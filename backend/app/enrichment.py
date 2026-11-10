"""Persistent enrichment jobs; uncertain purchases are never automatically repeated."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from . import domain as d
from . import models as m
from . import providers
from .config import settings
from .db import SessionLocal
from .security import decrypt_config


def apply_result(db, job, result):
    contact = db.get(m.Contact, job.contact_id)
    if not contact:
        return
    account = d.get(db, m.Account, contact.account_id)
    for field in ["email", "phone"]:
        value = result.get(field)
        if not value:
            continue
        db.add(
            m.Evidence(
                contact_id=contact.id,
                account_id=contact.account_id,
                field=field,
                value=str(value),
                source_type=job.provider,
                source_url="https://"
                + (
                    "app.fullenrich.com"
                    if job.provider == "fullenrich"
                    else "app.apollo.io"
                    if job.provider == "apollo"
                    else "hunter.io"
                ),
                confidence="unverified" if result.get("review_required") or field == "phone" else "signal",
            )
        )
        if (
            contact.paused
            or account.outreach_paused
            or d.is_suppressed(db, contact, field)
            or result.get("review_required")
        ):
            continue
        if field == "email":
            from email_validator import EmailNotValidError, validate_email

            try:
                value = validate_email(value, check_deliverability=False).normalized.lower()
            except EmailNotValidError:
                continue
            existing = db.scalar(
                select(m.Contact.id).where(m.Contact.email == value, m.Contact.id != contact.id)
            )
            if existing or (contact.email and contact.email != value):
                continue
            contact.email = value
            contact.email_status = result.get("email_status", "unknown")
            contact.email_verified_at = m.now() if contact.email_status == "valid" else None
        else:
            try:
                normalized = d.normalize_phone(value, account.country)
            except HTTPException:
                continue
            if not contact.phone or contact.phone == normalized:
                contact.phone = normalized
                contact.phone_status = "unknown"
                contact.phone_type = "unknown"
    contact.approved = False
    if contact.status == "qualified":
        contact.status = "candidate"
    d.cancel_pending(db, contact, "Coordonnées enrichies : nouvelle revue requise")
    d.log(
        db,
        "enrichment_received",
        contact=contact,
        source=job.provider,
        external_id=f"enrichment:{job.id}",
        data={"job_id": job.id, "review_required": True},
        summary="Résultat fournisseur reçu ; vérifiez les preuves avant activation.",
    )
    db.add(
        m.Task(
            contact_id=contact.id,
            campaign_id=contact.campaign_id,
            owner_id=contact.owner_id,
            channel="review",
            title="Vérifier les coordonnées enrichies",
            dedupe_key=f"enrich-review:{job.id}",
        )
    )


def tick():
    if not settings().live_actions_enabled:
        return 0
    with SessionLocal() as db:
        state = d.state(db, lock=True)
        # A worker killed after the persisted intent may already have consumed credits.
        for stale in db.scalars(
            select(m.Job).where(
                m.Job.kind == "enrich",
                m.Job.status == "running",
                m.Job.locked_at < m.now() - timedelta(minutes=3),
            )
        ):
            stale.status, stale.error = (
                "uncertain",
                "Worker interrompu après demande : rapprocher le fournisseur avant toute nouvelle opération.",
            )
        query = select(m.Job).where(
            m.Job.kind == "enrich", m.Job.status.in_(["queued", "waiting"]), m.Job.run_at <= m.now()
        )
        if state.paused:
            query = query.where(m.Job.status == "waiting")
        job = db.scalar(query.order_by(m.Job.run_at).with_for_update(skip_locked=True).limit(1))
        if not job:
            db.commit()
            return 0
        is_poll = job.status == "waiting"
        integration = db.scalar(select(m.Integration).where(m.Integration.provider == job.provider))
        if not integration:
            job.status, job.error = "failed", "Connexion fournisseur manquante"
            db.commit()
            return 1
        if not is_poll:
            contact = db.get(m.Contact, job.contact_id)
            account = db.get(m.Account, contact.account_id) if contact else None
            if (
                not contact
                or contact.paused
                or not account
                or account.outreach_paused
                or account.lifecycle != "prospect"
                or d.is_suppressed(db, contact)
            ):
                job.status, job.error = "cancelled", "Contact devenu inéligible"
                expense = db.scalar(select(m.Expense).where(m.Expense.job_id == job.id))
                if expense:
                    expense.status = "released"
                db.commit()
                return 1
        if is_poll and job.attempts >= 40:
            job.status, job.error = (
                "uncertain",
                "Résultat tardif : vérifier le fournisseur sans racheter les coordonnées.",
            )
            db.commit()
            return 1
        job_id, provider, payload, external_id = job.id, job.provider, job.payload, job.external_id
        config = decrypt_config(integration.encrypted_config)
        job.status, job.locked_at = "running", m.now()
        job.attempts += 1
        db.commit()  # Persist intent BEFORE network call.
    try:
        with SessionLocal() as db:
            state = d.state(db, lock=True)
            job = d.get(db, m.Job, job_id)
            integration = db.scalar(select(m.Integration).where(m.Integration.provider == provider))
            if not integration:
                raise providers.ProviderError(provider, "Configuration retirée avant exécution")
            config = decrypt_config(integration.encrypted_config)
            if not is_poll:
                contact = d.get(db, m.Contact, job.contact_id)
                account = d.get(db, m.Account, contact.account_id)
                campaign = d.get(db, m.Campaign, job.campaign_id)
                if (
                    state.paused
                    or contact.paused
                    or account.outreach_paused
                    or account.lifecycle != "prospect"
                    or d.is_suppressed(db, contact)
                    or not campaign.budget_approved
                    or not settings().live_actions_enabled
                ):
                    raise providers.ProviderError(provider, "Autorisation retirée avant achat de coordonnées")
            result = (
                providers.poll_fullenrich(config, external_id, job_id)
                if is_poll and provider == "fullenrich"
                else providers.enrich(provider, config, payload["identity"], payload["fields"], job_id)
            )
            failure = None
    except providers.ProviderError as exc:
        result, failure = None, exc
    with SessionLocal() as db:
        d.state(db, lock=True)
        job = d.get(db, m.Job, job_id)
        expense = db.scalar(select(m.Expense).where(m.Expense.job_id == job_id))
        if failure:
            # Safe read polling can resume, unlike billable initial requests.
            if is_poll and failure.uncertain and job.attempts < 40:
                job.status, job.run_at = "waiting", m.now() + timedelta(minutes=3)
            else:
                job.status = "uncertain" if failure.uncertain else "failed"
            job.error = str(failure)
            if expense and not failure.uncertain and not is_poll:
                expense.status = "released"
        else:
            job.result = result
            if result.get("external_id"):
                job.external_id = result["external_id"]
            if result["complete"]:
                apply_result(db, job, result)
                job.status = "completed"
            else:
                job.status, job.run_at = "waiting", m.now() + timedelta(minutes=1)
            if expense:
                expense.status = "estimated"
                expense.note = "Plafond estimé ; rapprocher la facture fournisseur."
        db.commit()
    return 1
