"""Enrich selected decision-makers using the existing FullEnrich credit balance only."""

from datetime import timedelta

from sqlalchemy import select

from . import domain as d
from . import models as m
from . import prospect_pipeline as pipeline
from . import providers
from .db import SessionLocal
from .security import decrypt_config

KIND = "prospect_enrich"
CREDIT_CAP = 20


def used(db):
    return sum(
        j.payload.get("credit_units", 0)
        for j in db.scalars(
            select(m.Job).where(m.Job.kind == KIND, m.Job.status.notin_(["cancelled", "failed"]))
        )
    )


def tick():
    with SessionLocal() as db:
        state = d.state(db, lock=True)
        cfg = state.settings.get("prospect_pipeline", {})
        for stale in db.scalars(
            select(m.Job).where(
                m.Job.kind == KIND,
                m.Job.status == "running",
                m.Job.locked_at < m.now() - timedelta(minutes=3),
            )
        ):
            stale.status = "uncertain"
            stale.error = "Résultat fournisseur à rapprocher ; aucune nouvelle demande automatique."
        integration = db.scalar(
            select(m.Integration).where(
                m.Integration.provider == "fullenrich", m.Integration.status == "authenticated"
            )
        )
        if not integration:
            db.commit()
            return 0
        job = db.scalar(
            select(m.Job)
            .where(m.Job.kind == KIND, m.Job.status == "waiting", m.Job.run_at <= m.now())
            .order_by(m.Job.created_at)
            .limit(1)
        )
        config = decrypt_config(integration.encrypted_config)
        poll = bool(job)
        if not poll:
            if not cfg.get("enabled") or not cfg.get("auto_enrich") or used(db) >= CREDIT_CAP:
                db.commit()
                return 0
            candidates = list(
                db.scalars(
                    select(m.ProspectProfile)
                    .where(m.ProspectProfile.score >= 55, m.ProspectProfile.status == "ready")
                    .order_by(m.ProspectProfile.score.desc())
                    .limit(30)
                )
            )
            selected = None
            for profile in candidates:
                checks = {c["key"]: c["status"] for c in profile.facts.get("checks", [])}
                if any(checks.get(key) not in {None, "match"} for key in ("size", "b2b")):
                    continue
                account = db.get(m.Account, profile.account_id)
                contacts = pipeline.people(db, account.id)
                if not pipeline.allowed(db, account, contacts):
                    continue
                for contact in contacts:
                    if not contact.first_name or not contact.last_name or not contact.role:
                        continue
                    fields = ["phone"] if not contact.phone else ["email"] if not contact.email else []
                    cost = 10 if fields == ["phone"] else 1
                    key = "prospect-enrich:" + contact.id + ":" + ",".join(fields)
                    if (
                        fields
                        and used(db) + cost <= CREDIT_CAP
                        and not db.scalar(select(m.Job.id).where(m.Job.idempotency_key == key))
                    ):
                        selected = (profile, account, contact, fields, cost, key)
                        break
                if selected:
                    break
            if not selected:
                db.commit()
                return 0
            try:
                balance = providers.verify_connection("fullenrich", config)["quota"]["credits"]
            except (providers.ProviderError, KeyError, TypeError):
                db.commit()
                return 0
            profile, account, contact, fields, cost, key = selected
            pending = sum(
                j.payload.get("credit_units", 0)
                for j in db.scalars(
                    select(m.Job).where(
                        m.Job.kind == KIND, m.Job.status.in_(["running", "waiting", "uncertain"])
                    )
                )
            )
            if not isinstance(balance, (int, float)) or balance - pending < cost:
                db.commit()
                return 0
            job = m.Job(
                kind=KIND,
                provider="fullenrich",
                contact_id=contact.id,
                campaign_id=profile.campaign_id,
                idempotency_key=key,
                payload={
                    "credit_units": cost,
                    "fields": fields,
                    "identity": {
                        "first_name": contact.first_name,
                        "last_name": contact.last_name,
                        "domain": account.domain,
                        "company_name": account.name,
                        "linkedin_url": contact.linkedin_url,
                    },
                },
            )
            db.add(job)
            db.flush()
        job_id = job.id
        remote_id = job.external_id
        payload = job.payload
        job.status = "running"
        job.locked_at = m.now()
        job.attempts += 1
        db.commit()
    try:
        result = (
            providers.poll_fullenrich(config, remote_id, job_id)
            if poll
            else providers.enrich("fullenrich", config, payload["identity"], payload["fields"], job_id)
        )
        error = None
    except providers.ProviderError as exc:
        result = {}
        error = exc
    with SessionLocal() as db:
        d.state(db, lock=True)
        job = d.get(db, m.Job, job_id)
        if error:
            job.status = (
                "waiting"
                if poll and job.attempts < 40
                else "uncertain"
                if poll or error.uncertain
                else "failed"
            )
            job.error = str(error)
            job.run_at = m.now() + timedelta(minutes=3)
        elif not result["complete"]:
            job.external_id = result["external_id"]
            job.status = "waiting"
            job.run_at = m.now() + timedelta(minutes=1)
        else:
            job.status = "completed"
            job.result = result
            contact = db.get(m.Contact, job.contact_id)
            account = db.get(m.Account, contact.account_id) if contact else None
            if contact and account and pipeline.allowed(db, account, pipeline.people(db, account.id)):
                for field in ["email", "phone"]:
                    value = result.get(field)
                    if not value:
                        continue
                    if field == "phone":
                        try:
                            value = d.normalize_phone(value, account.country)
                        except Exception:
                            continue
                    pipeline.add_evidence(
                        db,
                        account.id,
                        field,
                        str(value),
                        "https://app.fullenrich.com",
                        "fullenrich",
                        contact.id,
                    )
                    if getattr(contact, field):
                        continue
                    if field == "email":
                        from email_validator import EmailNotValidError, validate_email

                        try:
                            value = validate_email(value, check_deliverability=False).normalized.lower()
                        except EmailNotValidError:
                            continue
                        if db.scalar(select(m.Contact.id).where(m.Contact.email == value)):
                            continue
                        contact.email = value
                        contact.email_status = result.get("email_status", "unknown")
                    else:
                        contact.phone = value
                        contact.phone_type = "unknown"
                        contact.phone_status = "unknown"
                if d.is_suppressed(db, contact):
                    contact.paused = True
                    contact.approved = False
                profile = db.get(m.ProspectProfile, account.id)
                if profile:
                    pipeline.evaluate(db, profile)
            d.log(
                db,
                "prospect_coordinates_received",
                contact=contact,
                source="fullenrich",
                summary="Coordonnées reçues pour un interlocuteur ciblé ; source conservée.",
                data={"job_id": job.id},
            )
        db.commit()
    return 1
