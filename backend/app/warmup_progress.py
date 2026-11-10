"""Measured programme completion, explicitly separate from mailbox reputation."""

from datetime import timedelta
from math import floor
from zoneinfo import ZoneInfo

from sqlalchemy import select

from . import models as m
from .warmup_policy import KIND, config, external_checks, plan, plan_key, program_start, timestamp

PARIS = ZoneInfo("Europe/Paris")


def assess(db, row):
    target = plan(row)
    start = program_start(row)
    jobs = (
        list(
            db.scalars(
                select(m.Job).where(
                    m.Job.kind == KIND,
                    m.Job.payload["sender_id"].as_string() == row.id,
                    m.Job.payload["sender_version"].as_integer() == row.config_version,
                    m.Job.created_at >= start,
                    m.Job.created_at <= m.now(),
                    m.Job.attempts > 0,
                )
            )
        )
        if start
        else []
    )
    delivered = [
        j
        for j in jobs
        if j.status == "completed"
        and j.result.get("received") is True
        and timestamp(j.result.get("received_at"))
    ]
    # Each distinct Paris calendar day counts once, and only after a real receipt.
    days = {j.created_at.astimezone(PARIS).date() for j in delivered}
    active_days, received = len(days), len(delivered)
    ratio = min(1, active_days / target["target_active_days"], received / target["target_received"])
    complete = active_days >= target["target_active_days"] and received >= target["target_received"]
    last = max((timestamp(j.result["received_at"]) for j in delivered), default=None)
    incidents = [
        j for j in jobs if j.status in {"failed", "uncertain"} and j.created_at >= m.now() - timedelta(days=7)
    ]
    checks = external_checks(row)
    reasons = []
    if not complete:
        reasons.append("Objectifs de journées actives et de réceptions non atteints")
    if incidents:
        reasons.append("Incident de préparation dans les sept derniers jours")
    if not last or last < m.now() - timedelta(days=7):
        reasons.append("Une réception récente doit être confirmée")
    validation = config(row).get("validation") or {}
    approved = (
        timestamp(validation.get("at"))
        and validation.get("plan_key") == plan_key(row)
        and not validation.get("invalidated_at")
        and not reasons
    )
    return {
        **target,
        "plan_key": plan_key(row),
        "started_at": start.isoformat() if start else None,
        "percent": floor(1000 * ratio) / 10,
        "complete": complete,
        "active_days": active_days,
        "received": received,
        "accepted": sum(j.result.get("accepted") is True for j in jobs),
        "replies_received": sum(bool(j.payload.get("reply_to_job_id")) for j in delivered),
        "remaining_active_days": max(0, target["target_active_days"] - active_days),
        "remaining_received": max(0, target["target_received"] - received),
        "last_received_at": last.isoformat() if last else None,
        "elapsed_days": (m.now().astimezone(PARIS).date() - start.astimezone(PARIS).date()).days + 1
        if start
        else 0,
        "validation": validation or None,
        "checks": checks,
        "validation_reasons": reasons,
        "readiness": "validated"
        if approved
        else "not_started"
        if not start
        else "in_progress"
        if not complete
        else "checks_required"
        if reasons
        else "awaiting_validation",
        "timezone": "Europe/Paris",
    }
