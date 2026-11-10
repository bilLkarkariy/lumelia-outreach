"""Server-owned warm-up completion and one durable, explicitly requested email.

No cold-email authorization is inferred from programme completion. Notifications
use the selected mailboxes' external reserve, not warm-up progress or campaigns.
An SMTP submission of uncertain outcome is never repeated automatically.
"""

import hashlib
import json
from datetime import timedelta
from email.message import EmailMessage
from email.utils import format_datetime

from fastapi import HTTPException
from sqlalchemy import select

from . import domain as d
from . import mail, senders, warmup_progress
from . import models as m
from .config import settings
from .db import SessionLocal
from .security import decrypt_config
from .warmup_policy import KIND, config, plan_key, reserve, timestamp

SETTING = "warmup_completion"
NOTICE_KIND = "warmup_notification"


def status(db):
    cfg = d.state(db).settings.get(SETTING, {})
    jobs = list(
        db.scalars(
            select(m.Job)
            .where(
                m.Job.kind == NOTICE_KIND,
                m.Job.payload["run_key"].as_string() == cfg.get("run_key", ""),
            )
            .order_by(m.Job.created_at.desc())
        )
    )
    return {
        **cfg,
        "notifications": [
            {
                "id": job.id,
                "event": job.payload.get("event"),
                "status": job.status,
                "error": job.error,
                "result": job.result,
            }
            for job in jobs
        ],
    }


def configure(db, data, actor_id):
    state = d.state(db, lock=True)
    if not data.enabled:
        state.settings = {**state.settings, SETTING: {**state.settings.get(SETTING, {}), "enabled": False}}
        db.commit()
        return status(db)
    ids = sorted(set(data.mailbox_ids))
    rows = [d.get(db, m.SendingMailbox, key) for key in ids]
    if len(rows) < 2 or data.sender_id not in ids:
        raise HTTPException(422, "Choisissez au moins deux boîtes et un expéditeur parmi elles")
    if any(not config(row).get("enabled") or senders.protected(row.from_email) for row in rows):
        raise HTTPException(409, "Activez d’abord le programme des boîtes sélectionnées")
    sender = next(row for row in rows if row.id == data.sender_id)
    if sender.status != "authenticated" or sender.external_daily_reserve < 1:
        raise HTTPException(409, "Une boîte connectée avec une réserve externe est nécessaire")
    tracked = {row.id: plan_key(row) for row in rows}
    definition = {"recipient": str(data.recipient), "sender_id": data.sender_id, "plans": tracked}
    key = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
    old = state.settings.get(SETTING, {})
    cfg = (
        old
        if old.get("run_key") == key
        else {
            **definition,
            "run_key": key,
            "configured_at": m.now().isoformat(),
            "actor_id": actor_id,
        }
    )
    state.settings = {**state.settings, SETTING: {**cfg, "enabled": True}}
    d.log(
        db,
        "warmup_completion_configured",
        actor_id=actor_id,
        source="smtp",
        data={"recipient": str(data.recipient), "mailbox_ids": ids},
    )
    db.commit()
    return status(db)


def enqueue(db, cfg, event, summary):
    key = "warmup-notice:" + cfg["run_key"] + ":" + event
    if db.scalar(select(m.Job.id).where(m.Job.idempotency_key == key)):
        return
    db.add(
        m.Job(
            kind=NOTICE_KIND,
            provider="smtp",
            status="queued",
            idempotency_key=key,
            payload={
                "run_key": cfg["run_key"],
                "recipient": cfg["recipient"],
                "event": event,
                "summary": summary,
            },
        )
    )


def observe(db, state, cfg):
    from . import native_warmup as nw

    checked = timestamp(cfg.get("checked_at"))
    if cfg.get("completed_at") or (checked and checked > m.now() - timedelta(minutes=1)):
        return cfg
    cfg = {**cfg, "checked_at": m.now().isoformat()}
    peers = nw.selected(db)
    rows, summary, issues, in_flight = [], [], [], False
    for key, expected in cfg["plans"].items():
        row = db.get(m.SendingMailbox, key)
        if not row or plan_key(row) != expected:
            issues.append("Une boîte ou son programme a changé ; la fin ne peut pas être attestée.")
            continue
        rows.append(row)
        jobs = nw.history(db, row)
        reasons = nw.blockers(db, row, jobs, peers)
        if not config(row).get("enabled"):
            reasons.append("Programme mis en pause")
        if reasons:
            issues.append(row.from_email + " : " + "; ".join(reasons))
        in_flight |= any(job.status in {"running", "waiting"} for job in jobs)
        progress = warmup_progress.assess(db, row)
        summary.append(
            {
                "email": row.from_email,
                **{
                    field: progress[field]
                    for field in (
                        "complete",
                        "percent",
                        "active_days",
                        "received",
                        "target_active_days",
                        "target_received",
                        "validation_reasons",
                    )
                },
            }
        )
    cfg["issues"] = list(dict.fromkeys(issues))
    if issues:
        cfg["blocked_since"] = cfg.get("blocked_since") or m.now().isoformat()
        if timestamp(cfg["blocked_since"]) <= m.now() - timedelta(minutes=30):
            enqueue(db, cfg, "blocked", cfg["issues"])
    else:
        cfg.pop("blocked_since", None)
        if summary and all(item["complete"] for item in summary) and not in_flight:
            cfg["completed_at"], cfg["summary"] = m.now().isoformat(), summary
            for row in rows:
                row.state = {
                    **row.state,
                    "native_warmup": {
                        **config(row),
                        "enabled": False,
                        "completed_at": cfg["completed_at"],
                        "reserve_until": (m.now() + timedelta(hours=24)).isoformat(),
                    },
                }
            for job in db.scalars(select(m.Job).where(m.Job.kind == KIND, m.Job.status == "queued")):
                if job.payload.get("sender_id") in cfg["plans"]:
                    job.status, job.error = "cancelled", "Programme terminé ; réponse non encore transmise"
            enqueue(db, cfg, "completed", summary)
            d.log(db, "native_warmup_completed", source="smtp", data={"mailbox_ids": list(cfg["plans"])})
    state.settings = {**state.settings, SETTING: cfg}
    return cfg


def available(db, sender, exclude_id=None):
    from . import native_warmup as nw

    if not sender or nw.technical_blockers(sender) or sender.external_daily_reserve < 1:
        return False
    if (
        senders.count(db, m.Message.sender_id == sender.id) + sender.external_daily_reserve + reserve(sender)
        > sender.total_daily_limit
    ):
        return False
    for scope, key in (("domain", sender.domain), ("provider", sender.provider_group)):
        limit = db.scalar(select(m.EmailLimit).where(m.EmailLimit.scope == scope, m.EmailLimit.key == key))
        used, reserved = senders.group_usage(db, scope, key)
        if not limit or used + reserved > limit.daily_limit or limit.daily_limit <= 0:
            return False
    notices = list(
        db.scalars(
            select(m.Job).where(
                m.Job.kind == NOTICE_KIND,
                m.Job.attempts > 0,
                m.Job.payload["sender_id"].as_string() == sender.id,
                m.Job.locked_at > m.now() - timedelta(hours=24),
            )
        )
    )
    notices = [job for job in notices if job.id != exclude_id]
    if len(notices) >= sender.external_daily_reserve:
        return False
    last = [job.created_at for job in nw.history(db, sender) if job.attempts]
    last += [job.locked_at for job in notices]
    return nw.campaign_next_at(db, sender) <= m.now() and (
        not last or max(last) + timedelta(seconds=sender.min_interval_seconds) <= m.now()
    )


def message_for(job, sender):
    message = EmailMessage()
    message["From"], message["To"] = sender.from_email, job.payload["recipient"]
    message["Date"], message["Message-ID"] = format_datetime(m.now()), job.external_id
    message["Auto-Submitted"] = "auto-generated"
    if job.payload["event"] == "completed":
        message["Subject"] = "Lumelia — programme de warm-up terminé"
        lines = [
            "Bonjour,",
            "",
            "Le programme de warm-up des boîtes suivies est terminé.",
            "Les échanges de préparation ont été arrêtés automatiquement.",
            "",
        ]
        for item in job.payload["summary"]:
            lines.append(
                f"- {item['email']} : {item['active_days']}/{item['target_active_days']} journées actives ; "
                f"{item['received']}/{item['target_received']} réceptions confirmées."
            )
        lines += [
            "",
            "Cette fin de programme ne certifie pas la réputation ni le placement chez Gmail/Outlook.",
            "Les contrôles externes et la validation cold email restent distincts. Aucune campagne n’a été activée.",
        ]
    else:
        message["Subject"] = "Lumelia — warm-up bloqué, programme non terminé"
        lines = [
            "Bonjour,",
            "",
            "Un blocage persiste depuis au moins 30 minutes.",
            "La boîte concernée n’envoie pas tant que ses contrôles ne sont pas satisfaits.",
            "",
            *job.payload["summary"],
            "",
            "Le programme n’est pas déclaré terminé.",
        ]
    message.set_content(
        "\n".join(lines) + "\n\n" + settings().public_url.rstrip("/") + "/settings?tab=warmup"
    )
    return message


def tick():
    smtp = None
    try:
        with SessionLocal() as db:
            state = d.state(db, lock=True)
            cfg = state.settings.get(SETTING, {})
            if not cfg.get("enabled"):
                return
            for stale in db.scalars(
                select(m.Job).where(
                    m.Job.kind == NOTICE_KIND,
                    m.Job.status == "running",
                    m.Job.locked_at < m.now() - timedelta(minutes=3),
                )
            ):
                stale.status, stale.error = "uncertain", "Transmission interrompue ; aucun renvoi automatique"
            cfg = observe(db, state, cfg)
            db.flush()
            job = db.scalar(
                select(m.Job)
                .where(m.Job.kind == NOTICE_KIND, m.Job.status == "queued", m.Job.run_at <= m.now())
                .order_by(m.Job.created_at)
                .limit(1)
            )
            if not job:
                db.commit()
                return
            if job.payload["run_key"] != cfg["run_key"] or job.payload["recipient"] != cfg["recipient"]:
                job.status, job.error = "cancelled", "Notification remplacée par une nouvelle configuration"
                db.commit()
                return
            if job.payload["event"] == "blocked" and not cfg.get("issues"):
                job.status, job.error = "cancelled", "Blocage résolu avant notification"
                db.commit()
                return
            # Prefer the configured sender; use another explicitly enrolled box if it is unavailable.
            ids = [cfg["sender_id"], *[key for key in cfg["plans"] if key != cfg["sender_id"]]]
            sender = next((row for key in ids if available(db, row := db.get(m.SendingMailbox, key))), None)
            if not sender:
                db.commit()
                return
            try:
                smtp = mail.connect_smtp(decrypt_config(sender.encrypted_config))
            except Exception:
                failures = job.result.get("connection_failures", 0) + 1
                job.result = {**job.result, "connection_failures": failures}
                job.error = "Connexion SMTP indisponible avant soumission"
                job.status = "failed" if failures >= 6 else "queued"
                job.run_at = m.now() + timedelta(minutes=min(60, 5 * 2 ** (failures - 1)))
                db.commit()
                return
            job.payload = {**job.payload, "sender_id": sender.id, "sender_version": sender.config_version}
            job.external_id = (
                job.external_id or "<lumelia.warmup.notice." + job.id + "@" + sender.domain + ">"
            )
            job.status, job.locked_at, job.attempts = "running", m.now(), job.attempts + 1
            db.commit()  # Persist the unique SMTP intent before any submission.
            state = d.state(db, lock=True)
            db.refresh(sender)
            current = state.settings.get(SETTING, {})
            if (
                not current.get("enabled")
                or current.get("run_key") != cfg["run_key"]
                or sender.config_version != job.payload["sender_version"]
            ):
                job.status, job.error = "cancelled", "Autorisation ou connexion modifiée avant transmission"
            elif not available(db, sender, job.id):
                # Nothing has been submitted: a concurrent send may safely defer this same intent.
                job.status, job.attempts, job.locked_at = "queued", 0, None
                job.run_at = m.now() + timedelta(minutes=1)
                job.error = "Créneau de notification reporté avant soumission"
            else:
                try:
                    refused = smtp.send_message(
                        message_for(job, sender),
                        from_addr=sender.from_email,
                        to_addrs=[job.payload["recipient"]],
                    )
                    job.status = "failed" if refused else "completed"
                    job.error = "Destinataire refusé" if refused else ""
                    job.result = {
                        **job.result,
                        "smtp_accepted": not bool(refused),
                        "accepted_at": m.now().isoformat() if not refused else None,
                        "delivery_confirmed": False,
                    }
                except Exception:
                    job.status, job.error = "uncertain", "Réponse SMTP incertaine ; aucun renvoi automatique"
            db.commit()
    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:
                pass
