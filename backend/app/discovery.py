"""Bounded Apollo discovery, durable intent and explicit review of candidates."""

from datetime import timedelta

from sqlalchemy import select

from . import domain as d
from . import models as m
from . import providers
from .config import settings
from .db import SessionLocal
from .security import decrypt_config


def candidates(result, kind):
    rows = result.get("people" if kind == "people" else "organizations", [])
    clean = []
    for row in rows[:100]:
        org = row.get("organization") or {} if kind == "people" else row
        domain = org.get("primary_domain") or org.get("website_url") or ""
        try:
            domain = d.normalize_domain(domain)
        except Exception:
            domain = ""
        clean.append(
            {
                "provider_id": str(row.get("id", "")),
                "company": str(org.get("name") or ""),
                "domain": domain,
                "first_name": str(row.get("first_name") or "") if kind == "people" else "",
                "last_name": str(row.get("last_name") or row.get("last_name_obfuscated") or "")
                if kind == "people"
                else "",
                "role": str(row.get("title") or "") if kind == "people" else "",
                "linkedin_url": str(row.get("linkedin_url") or "") if kind == "people" else "",
                "review_required": True,
            }
        )
    return clean


def tick():
    if not settings().live_actions_enabled:
        return 0
    with SessionLocal() as db:
        state = d.state(db, lock=True)
        for stale in db.scalars(
            select(m.Job).where(
                m.Job.kind == "discover",
                m.Job.status == "running",
                m.Job.locked_at < m.now() - timedelta(minutes=3),
            )
        ):
            stale.status, stale.error = (
                "uncertain",
                "Recherche interrompue : vérifier le fournisseur avant de renouveler.",
            )
        if state.paused:
            db.commit()
            return 0
        job = db.scalar(
            select(m.Job)
            .where(m.Job.kind == "discover", m.Job.status == "queued")
            .order_by(m.Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            db.commit()
            return 0
        integration = db.scalar(select(m.Integration).where(m.Integration.provider == "apollo"))
        campaign = db.get(m.Campaign, job.campaign_id)
        if not integration or not campaign or not campaign.budget_approved or campaign.mode != "live":
            job.status, job.error = "cancelled", "Accès ou budget indisponible"
            expense = db.scalar(select(m.Expense).where(m.Expense.job_id == job.id))
            if expense:
                expense.status = "released"
            db.commit()
            return 1
        job.status, job.locked_at = "running", m.now()
        job.attempts += 1
        job_id = job.id
        db.commit()
    # Serialize against pause/config changes, after the persisted purchase intent.
    with SessionLocal() as db:
        state = d.state(db, lock=True)
        job = d.get(db, m.Job, job_id)
        expense = db.scalar(select(m.Expense).where(m.Expense.job_id == job_id))
        if state.paused or job.status != "running":
            job.status = "cancelled"
            if expense:
                expense.status = "released"
        else:
            integration = db.scalar(select(m.Integration).where(m.Integration.provider == "apollo"))
            try:
                payload = job.payload
                result = providers.apollo_search(
                    decrypt_config(integration.encrypted_config),
                    payload["kind"],
                    payload["filters"],
                    payload["page"],
                    payload["limit"],
                )
                job.result = {"candidates": candidates(result, payload["kind"]), "review_required": True}
                job.status = "completed"
                integration.status, integration.last_checked_at = "authenticated", m.now()
                if expense:
                    expense.status = "estimated"
            except providers.ProviderError as exc:
                job.status = "uncertain" if exc.uncertain else "failed"
                job.error = str(exc)
                if expense and not exc.uncertain:
                    expense.status = "released"
        db.commit()
    return 1
