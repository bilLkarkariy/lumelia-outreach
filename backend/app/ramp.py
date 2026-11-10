"""Progressive sending based on observed activity, without a warm-up network.

SMTP acceptance is not an inbox-placement signal. No synthetic messages, opens or
replies are generated. Recompute before every transmission so a newly received
bounce takes effect immediately, including between intent and transmission.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from math import ceil

from sqlalchemy import select

from . import models as m
from .warmup_policy import reserve


def enabled(row):
    return (row.state or {}).get("ramp", {}).get("enabled") is True


def enable(row):
    if not enabled(row):
        row.state = {
            **(row.state or {}),
            "ramp": {"enabled": True, "started_at": m.now().isoformat()},
        }
    row.warmup_status = "warming"
    row.warmup_started_at = row.warmup_started_at or m.now()
    row.ready_at = None


def assess(db, row):
    target = max(0, min(row.daily_limit, row.total_daily_limit - row.external_daily_reserve - reserve(row)))
    result = {"enabled": enabled(row), "target_limit": target, "effective_limit": target, "blockers": []}
    if not result["enabled"]:
        return result
    now = m.now()
    try:
        start = datetime.fromisoformat(row.state["ramp"]["started_at"])
        if start.tzinfo is None:
            raise ValueError("Timezone required")
    except (KeyError, ValueError, TypeError):
        start = now
    start = max(start, now - timedelta(days=90))
    # Only the app's actual sending history counts, never elapsed time alone.
    messages = list(
        db.scalars(select(m.Message).where(m.Message.sender_id == row.id, m.Message.attempted_at >= start))
    )
    events = list(
        db.scalars(
            select(m.Event)
            .join(m.Contact, m.Contact.id == m.Event.contact_id)
            .where(
                m.Contact.email_sender_id == row.id,
                m.Event.channel == "email",
                m.Event.created_at >= start,
                m.Event.kind.in_(["bounce", "reply", "positive_reply"]),
            )
        )
    )
    days = defaultdict(lambda: {"accepted": 0, "errors": 0})
    errors = []
    accepted_7d = failed_7d = 0
    for message in messages:
        day = days[message.attempted_at.date()]
        if message.status == "sent":
            day["accepted"] += 1
            accepted_7d += int(message.attempted_at >= now - timedelta(days=7))
        elif message.status in {"failed", "uncertain"}:
            day["errors"] += 1
            errors.append(message.attempted_at)
            failed_7d += int(message.attempted_at >= now - timedelta(days=7))
    bounces = {}
    replies = set()
    for event in events:
        identity = event.data.get("message_id") or event.contact_id
        if event.kind == "bounce":
            bounces.setdefault(identity, event.created_at)
        elif event.created_at >= now - timedelta(days=7):
            replies.add(event.contact_id)
    for at in bounces.values():
        days[at.date()]["errors"] += 1
    limit, healthy_days, last_day, last_reset = 5, 0, None, None
    for day, stats in sorted(days.items()):
        if last_day and (day - last_day).days > 7:
            limit, healthy_days, last_reset = 5, 0, day
        last_day = day
        if stats["errors"]:
            limit, healthy_days, last_reset = max(5, limit // 2), 0, day
        elif day < now.date() and stats["accepted"] >= max(3, ceil(limit / 2)):
            healthy_days += 1
            if healthy_days == 3:
                limit, healthy_days = limit + 5, 0
    if last_day is None or (now.date() - last_day).days > 7:
        limit, healthy_days, last_reset = 5, 0, now.date()
    cooldowns = [at + timedelta(hours=24) for at in errors]
    cooldowns.extend(at + timedelta(hours=48) for at in bounces.values())
    until = max(cooldowns, default=now)
    blockers = []
    if until > now:
        blockers.append(
            "Montée en charge suspendue après une erreur ou un rejet ; reprise à volume réduit après "
            + until.isoformat()
        )
    checks = (row.state or {}).get("delivery_checks", {})
    recent = {}
    for provider, check in checks.items():
        try:
            at = datetime.fromisoformat(check["observed_at"])
            if at.tzinfo and now - timedelta(days=7) <= at <= now:
                recent[provider] = check
        except (KeyError, ValueError, TypeError):
            continue
    placed = all(recent.get(provider, {}).get("outcome") == "inbox" for provider in ["gmail", "outlook"])
    adverse = any(check.get("outcome") in {"spam", "missing"} for check in recent.values())
    approved_limit = max(5, int(row.state.get("ramp", {}).get("approved_limit", 5)))
    try:
        reviewed_at = datetime.fromisoformat(row.state.get("ramp", {}).get("reviewed_at", ""))
        if not reviewed_at.tzinfo or (last_reset and reviewed_at.date() <= last_reset):
            approved_limit = 5
    except (ValueError, TypeError):
        approved_limit = 5
    if adverse:
        blockers.append("Dernier test externe en spam ou non reçu : contrôler la réception avant reprise.")
    result.update(
        started_at=start.isoformat(),
        effective_limit=min(target, limit, approved_limit if placed else 5),
        activity_limit=min(target, limit),
        approved_limit=approved_limit,
        review_required=min(target, limit) > (approved_limit if placed else 5),
        next_limit=min(target, limit, (approved_limit if placed else 5) + 5),
        delivery_checks=checks,
        external_checks_current=placed,
        healthy_days_in_step=healthy_days,
        days_required=3,
        increment=5,
        accepted_7d=accepted_7d,
        failed_7d=failed_7d,
        bounces_7d=sum(at >= now - timedelta(days=7) for at in bounces.values()),
        replies_7d=len(replies),
        cooldown_until=until.isoformat() if until > now else None,
        blockers=blockers,
        placement="reported_inbox" if placed else "issue_reported" if adverse else "unmeasured",
    )
    return result
