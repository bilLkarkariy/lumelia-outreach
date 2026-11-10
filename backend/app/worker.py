"""Durable worker. A committed send intent never retries automatically after interruption."""

import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select

from . import autopilot, email_policy, mail, native_warmup, privacy, senders
from . import domain as d
from . import models as m
from .config import settings
from .db import SessionLocal
from .providers import ProviderError
from .security import decrypt_config

# Background groups that reach a mail server or a provider even when live actions are disabled.
NETWORKED = frozenset({"calls", "sourcing", "mailboxes", "warmup"})


def background_groups(groups, offline):
    """Groups the worker schedules; offline mode keeps only those that stay inside the database."""
    return {key: run for key, run in groups.items() if not (offline and key in NETWORKED)}


def connection_tick():
    # Connection probes send no email and run independently of campaign activation.
    with SessionLocal() as db:
        d.state(db, lock=True)
        row = db.scalar(
            select(m.SendingMailbox)
            .where(
                m.SendingMailbox.status.in_(["configured", "error"]),
                (m.SendingMailbox.last_checked_at.is_(None))
                | (m.SendingMailbox.last_checked_at < m.now() - timedelta(minutes=15)),
            )
            .order_by(m.SendingMailbox.last_checked_at.asc().nullsfirst())
            .limit(1)
        )
        if not row:
            return
        config = decrypt_config(row.encrypted_config)
        if any(
            senders.protected(address)
            for address in (row.from_email, config.get("reply_to") or row.from_email)
        ):
            row.status, row.paused = "excluded", True
        else:
            try:
                result = mail.verify(config)
                row.status = "authenticated" if result.get("authenticated") else "error"
                row.state = {**row.state, "connection_error": ""}
            except ProviderError as exc:
                row.status, row.last_event_at = "error", None
                row.state = {**row.state, "connection_error": str(exc)}
        row.last_checked_at = m.now()
        db.commit()


def collection_tick():
    with SessionLocal() as db:
        d.state(db, lock=True)
        # One account per cycle, ordered by attempt time. A failed account cannot starve the pool.
        sender = db.scalar(
            select(m.SendingMailbox)
            .where(m.SendingMailbox.status == "authenticated")
            .order_by(m.SendingMailbox.last_collection_attempt_at.asc().nullsfirst(), m.SendingMailbox.id)
            .limit(1)
        )
        if not sender:
            return
        if senders.protected(sender.from_email):
            sender.status, sender.paused = "excluded", True
            db.commit()
            return
        sender.last_collection_attempt_at = m.now()
        try:
            mail.collect(db, sender, decrypt_config(sender.encrypted_config))
        except Exception:
            sender.state = {
                **sender.state,
                "collector_error": "Réception indisponible : nouveaux envois suspendus pour cette boîte.",
            }
            sender.last_event_at = None
        db.commit()


def send_reasons(db, job, campaign, contact, message, state):
    reasons = d.eligibility(db, contact, "email")
    if message.purpose != "sequence":
        reasons.append("Réponse manuelle : utiliser la conversation.")
    if email_policy.contains_price(message.subject, message.body):
        reasons.append(email_policy.REASON)
    if state.paused:
        reasons.append("Pause générale")
    if campaign.status != "active":
        reasons.append("Campagne en pause")
    if message.status not in {"queued", "sending"}:
        reasons.append("Message indisponible")
    return reasons


def tick():
    with SessionLocal() as db:
        state = d.state(db, lock=True)
        state.worker_seen_at = m.now()
        for stale in db.scalars(
            select(m.Job).where(
                m.Job.kind == "send",
                m.Job.status == "running",
                m.Job.locked_at < m.now() - timedelta(minutes=3),
            )
        ):
            stale.status, stale.error = (
                "uncertain",
                "Worker interrompu : vérifier le journal SMTP avant de réconcilier.",
            )
            message = db.get(m.Message, stale.message_id)
            if message:
                message.status, message.failure_reason = "uncertain", stale.error
        if state.paused:
            db.commit()
            return 0
        job = db.scalar(
            select(m.Job)
            .where(m.Job.status == "queued", m.Job.run_at <= m.now(), m.Job.kind == "send")
            .order_by(m.Job.run_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            db.commit()
            return 0
        campaign = d.get(db, m.Campaign, job.campaign_id)
        if campaign.status != "active":
            job.run_at = m.now() + timedelta(minutes=1)
            db.commit()
            return 0
        contact = d.get(db, m.Contact, job.contact_id)
        message = d.get(db, m.Message, job.message_id)
        reasons = send_reasons(db, job, campaign, contact, message, state)
        if reasons:
            job.status, job.error = "cancelled", "; ".join(reasons)
            if message.status == "queued":
                message.status, message.failure_reason = "cancelled", job.error
            db.commit()
            return 1
        if campaign.mode == "simulation":
            job.attempts += 1
            job.status = "completed"
            job.result = {"simulation": True, "subject": message.subject, "recipient": contact.email}
            message.status = "simulated"
            d.log(
                db,
                "email_simulated",
                contact=contact,
                channel="email",
                source="worker",
                external_id=f"simulation:{job.id}",
                data={"message_id": message.id},
                summary="Simulation locale : aucun email transmis.",
            )
            db.commit()
            return 1
        sender = db.get(m.SendingMailbox, message.sender_id) if message.sender_id else None
        ready = settings().live_actions_enabled and campaign.transport == "smtp" and campaign.budget_approved
        reasons = []
        if not ready or not settings().public_url.startswith("https://"):
            reasons.append("Transport, HTTPS ou autorisation indisponible")
        if (
            not sender
            or contact.email_sender_id != message.sender_id
            or message.sender_version != sender.config_version
        ):
            reasons.append("Expéditeur à attribuer puis message à valider")
        else:
            reasons.extend(senders.readiness(sender, reception=True))
            reasons.extend(senders.limits_reasons(db, sender, campaign, state))
        if not d.in_sending_window(campaign):
            reasons.append("Hors plage d’envoi")
        if reasons:
            job.error = "; ".join(reasons)
            job.run_at = m.now() + timedelta(minutes=1)
            db.commit()
            return 0
        config = mail.campaign_config(decrypt_config(sender.encrypted_config), campaign)
        message.sender_email, message.sender_domain, message.sender_provider = (
            sender.from_email,
            sender.domain,
            sender.provider_group,
        )
        message.recipient_email = contact.email
        message.sent_body = mail.content(message, contact, config)[0]
        message.attempted_at = m.now()
        message.provider_id = (
            message.provider_id or f"<lumelia.{message.id}@{config['from_email'].split('@')[1]}>"
        )
        message.status = "sending"
        job.status, job.locked_at = "running", m.now()
        job.attempts += 1
        job_id = job.id
        db.commit()  # Persist unique message id and intent before opening the network connection.
    with SessionLocal() as db:
        state = d.state(db, lock=True)
        job = d.get(db, m.Job, job_id)
        message = d.get(db, m.Message, job.message_id)
        contact = d.get(db, m.Contact, job.contact_id)
        campaign = d.get(db, m.Campaign, job.campaign_id)
        reasons = send_reasons(db, job, campaign, contact, message, state)
        sender = db.get(m.SendingMailbox, message.sender_id) if message.sender_id else None
        if (
            not settings().live_actions_enabled
            or not campaign.budget_approved
            or campaign.transport != "smtp"
            or not settings().public_url.startswith("https://")
        ):
            reasons.append("Autorisation réelle retirée")
        if (
            not sender
            or message.sender_version != sender.config_version
            or contact.email_sender_id != message.sender_id
        ):
            reasons.append("Configuration de l’expéditeur modifiée")
        else:
            reasons.extend(senders.readiness(sender, reception=True))
            reasons.extend(senders.limits_reasons(db, sender, campaign, state, exclude_id=message.id))
            config = mail.campaign_config(decrypt_config(sender.encrypted_config), campaign)
        if not d.in_sending_window(campaign):
            reasons.append("Hors plage d’envoi")

        if reasons:
            job.status, message.status = "cancelled", "cancelled"
            message.attempted_at = None  # Cancelled before SMTP; no transmission occurred.
            job.error = "; ".join(reasons)
        else:
            # The shared row lock orders send vs stop. Events accepted afterwards cannot recall an accepted email.
            try:
                result = mail.send(message, contact, config)
                message.status, message.sent_at = "sent", m.now()
                d.shift_sequence(
                    db, contact.id, campaign.id, message.sent_at - message.due_at, after_step=message.step
                )
                contact.information_status = "provided"
                job.status, job.result = "completed", result
                d.log(
                    db,
                    "email_sent",
                    contact=contact,
                    channel="email",
                    source="smtp",
                    external_id=f"sent:{job.id}",
                    summary="Email accepté par SMTP ; livraison non garantie.",
                    data={"message_id": message.id},
                )
            except ProviderError as exc:
                job.status = message.status = "uncertain" if exc.uncertain else "failed"
                job.error = message.failure_reason = str(exc)
                d.log(
                    db,
                    "send_uncertain" if exc.uncertain else "send_failed",
                    contact=contact,
                    source="smtp",
                    summary=str(exc),
                )
        db.commit()
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    from .acquisition import tick as acquisition_tick
    from .agent_actions import tick as agent_tick
    from .call_processing import purge_expired_recordings
    from .call_processing import tick as call_tick
    from .campaign_qualification import tick as qualification_tick
    from .conversations import reply_tick
    from .discovery import tick as discovery_tick
    from .enrichment import tick as enrich_tick
    from .missions import tick as mission_tick
    from .phone_routing import reconcile_tick as phone_tick
    from .prospect_enrichment import tick as prospect_enrich_tick
    from .prospect_pipeline import tick as prospect_tick
    from .runtime_ops import record_worker_health
    from .runtime_ops import tick as operations_tick

    with SessionLocal() as db:
        failed_operations = set(
            db.scalars(
                select(m.Job.payload["operation"].as_string()).where(
                    m.Job.kind == "worker_health", m.Job.status == "failed"
                )
            )
        )

    def sourcing():
        for operation in (discovery_tick, enrich_tick, acquisition_tick, prospect_tick, prospect_enrich_tick):
            safe(operation)

    def safe(operation):
        key = operation.__module__ + "." + operation.__name__
        try:
            result = operation()
            if key in failed_operations:
                record_worker_health(key)
                failed_operations.discard(key)
            return result
        except Exception as exc:
            failed_operations.add(key)
            try:
                record_worker_health(key, type(exc).__name__)
            except Exception:
                pass  # Database outages are also visible through the stale heartbeat.
            print(
                "Worker operation failed:",
                operation.__module__ + "." + operation.__name__,
                type(exc).__name__,
                flush=True,
            )

    background = background_groups(
        {
            "maintenance": lambda: (safe(purge_expired_recordings), safe(privacy.tick)),
            "calls": lambda: (safe(phone_tick), safe(call_tick)),
            "sourcing": sourcing,
            "mailboxes": lambda: (safe(connection_tick), safe(collection_tick)),
            "warmup": native_warmup.tick,
        },
        settings().offline,
    )
    with ThreadPoolExecutor(max_workers=len(background)) as executor:
        futures, scheduled = {}, {}
        while True:
            for key, operation in background.items():
                previous = futures.get(key)
                if (previous is None or previous.done()) and time.monotonic() >= scheduled.get(key, 0):
                    if previous is not None:
                        previous.result()  # safe() logs failures without exposing provider credentials.
                    futures[key] = executor.submit(safe, operation)
                    scheduled[key] = time.monotonic() + (600 if key == "maintenance" else 0)
            for operation in (
                tick,
                reply_tick,
                autopilot.tick,
                agent_tick,
                qualification_tick,
                mission_tick,
                operations_tick,
            ):
                safe(operation)
            if args.once:
                return
            time.sleep(settings().worker_interval_seconds)


if __name__ == "__main__":
    main()
