"""Capacity reserved for native, controlled mailbox preparation; no paid provider."""

import hashlib
import json
from datetime import datetime, timedelta

from . import models as m

INITIAL_DAILY = 2
MAX_DAILY = 10
DAILY_INCREMENT = 2
HEALTHY_DAYS_PER_STEP = 2
REPLY_DELAY_MINUTES = 15
KIND = "mailbox_warmup"
DEFAULT_PLAN = {"target_active_days": 14, "target_received": 50}


def timestamp(value):
    try:
        at = datetime.fromisoformat(value)
        return at if at.tzinfo and at <= m.now() else None
    except (ValueError, TypeError):
        return None


def plan(row):
    return {**DEFAULT_PLAN, **config(row).get("plan", {})}


def program_start(row):
    cfg = config(row)
    return timestamp(cfg.get("program_started_at") or cfg.get("started_at"))


def plan_key(row):
    start = program_start(row)
    value = {"plan": plan(row), "start": start.isoformat() if start else None, "version": row.config_version}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def external_checks(row):
    checks = (row.state or {}).get("delivery_checks", {})
    result = {}
    for provider in ("gmail", "outlook"):
        check = checks.get(provider, {})
        at = timestamp(check.get("observed_at"))
        result[provider] = {
            "outcome": check.get("outcome"),
            "observed_at": check.get("observed_at"),
            "valid": bool(at and at >= m.now() - timedelta(days=7) and check.get("outcome") == "inbox"),
        }
    return result


def cold_validation_reasons(row):
    """An enrolled mailbox cannot become cold-ready from a percentage alone."""
    cfg = config(row)
    if not cfg.get("started_at"):
        return []  # Existing, non-enrolled mailboxes keep their readiness policy.
    validation = cfg.get("validation") or {}
    if (
        not timestamp(validation.get("at"))
        or validation.get("plan_key") != plan_key(row)
        or validation.get("invalidated_at")
    ):
        return ["Préparation gratuite à terminer et valider dans Réglages > Warm-up"]
    reasons = []
    last = timestamp(cfg.get("last_received_at"))
    if not last or last < m.now() - timedelta(days=7):
        reasons.append("Activité de préparation trop ancienne : contrôler la boîte avant cold email")
    return reasons


def config(row):
    return (row.state or {}).get("native_warmup", {})


def reserve(row):
    cfg = config(row)
    if cfg.get("enabled"):
        return MAX_DAILY
    try:
        until = datetime.fromisoformat(cfg.get("reserve_until", ""))
        if until.tzinfo and until > m.now():
            return MAX_DAILY
    except (ValueError, TypeError):
        pass
    return 0
