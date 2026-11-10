"""Server-side critical notifications and observable pilot results."""

from datetime import datetime, timedelta
from email.message import EmailMessage
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import EmailStr
from sqlalchemy import select

from . import domain as d
from . import mail
from . import models as m
from . import schemas as s
from .config import settings
from .db import SessionLocal, get_db
from .security import admin, current_user, decrypt_config

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


class AlertSettings(s.Input):
    recipient: EmailStr
    sender_id: str
    enabled: bool


class StartPilot(s.Input):
    request_id: UUID
    mission_id: str


@router.get("")
def status(user=Depends(current_user), db=Depends(get_db)):
    state = d.state(db)
    recent = list(
        db.scalars(
            select(m.Job)
            .where(m.Job.kind.in_(["critical_notification", "pilot_snapshot"]))
            .order_by(m.Job.created_at.desc())
            .limit(20)
        )
    )
    pilot = state.settings.get("pilot", {})
    elapsed = 0
    if pilot.get("started_at"):
        elapsed = max(0, int((m.now() - datetime.fromisoformat(pilot["started_at"])).total_seconds()))
    return {
        "alerts": state.settings.get("critical_alerts", {}),
        "pilot": {
            **pilot,
            "elapsed_seconds": elapsed,
            "observation_seconds": 7 * 86400,
            "observation_complete": elapsed >= 7 * 86400,
        },
        "jobs": [d.serialize(row) for row in recent],
    }


@router.put("/alerts")
def configure(data: AlertSettings, user=Depends(admin), db=Depends(get_db)):
    sender = d.get(db, m.SendingMailbox, data.sender_id)
    if data.enabled and sender.status != "authenticated":
        raise HTTPException(409, "Connectez une boîte pour envoyer les alertes")
    state = d.state(db, lock=True)
    state.settings = {**state.settings, "critical_alerts": data.model_dump(mode="json")}
    db.commit()
    return {"alerts": data.model_dump(mode="json")}


@router.post("/pilot", status_code=201)
def start_pilot(data: StartPilot, user=Depends(admin), db=Depends(get_db)):
    from .mission_models import Mission

    mission = d.get(db, Mission, data.mission_id)
    if not 25 <= mission.target_count <= 50 or mission.accepted_count < 25:
        raise HTTPException(
            409,
            "Préparer au moins 25 prospects exploitables dans une mission visant 25 à 50 prospects avant le pilote",
        )
    state = d.state(db, lock=True)
    prior = state.settings.get("pilot", {})
    if prior:
        if prior.get("request_id") == str(data.request_id):
            return prior
        raise HTTPException(409, "Un pilote existe déjà ; son historique est conservé")
    pilot = {
        "request_id": str(data.request_id),
        "mission_id": mission.id,
        "started_at": m.now().isoformat(),
        "campaign_id": mission.campaign_id,
    }
    state.settings = {**state.settings, "pilot": pilot}
    db.commit()
    return pilot


def snapshot(db, pilot):
    from .call_models import CallSession
    from .mission_models import Mission, MissionStep

    mission = db.get(Mission, pilot["mission_id"])
    started = datetime.fromisoformat(pilot["started_at"])
    accounts = select(MissionStep.account_id).where(MissionStep.mission_id == mission.id)
    calls = list(
        db.scalars(
            select(CallSession).where(
                CallSession.account_id.in_(accounts),
                CallSession.created_at >= started,
                CallSession.campaign_id == mission.campaign_id,
            )
        )
    )
    actions = list(
        db.scalars(
            select(m.AgentAction).where(
                m.AgentAction.account_id.in_(accounts),
                m.AgentAction.created_at >= started,
                m.AgentAction.campaign_id == mission.campaign_id,
            )
        )
    )
    messages = list(
        db.scalars(
            select(m.Message)
            .join(m.Contact)
            .where(
                m.Contact.account_id.in_(accounts),
                m.Message.created_at >= started,
                m.Message.campaign_id == mission.campaign_id,
            )
        )
    )
    expenses = list(
        db.scalars(
            select(m.Expense).where(
                m.Expense.campaign_id == mission.campaign_id, m.Expense.created_at >= started
            )
        )
    )
    costs = {}
    for expense in expenses:
        currency = "EUR"
        costs[currency] = costs.get(currency, 0) + expense.amount_cents / 100
    return {
        "target_prospects": mission.target_count,
        "usable_prospects": mission.accepted_count,
        "calls_attempted": len(calls),
        "calls_connected": sum(bool(c.answered_at) for c in calls),
        "calls_with_report": sum(c.report_version > 0 for c in calls),
        "calls_interested": sum(c.outcome == "interested" for c in calls),
        "emails_prepared": len(messages),
        "emails_smtp_accepted": sum(x.status == "sent" and x.sent_at is not None for x in messages),
        "actions": len(actions),
        "actions_completed": sum(a.status == "completed" for a in actions),
        "human_interventions_needed": sum(
            a.status in {"waiting_human", "failed", "uncertain"} for a in actions
        ),
        "expenses_by_currency": costs,
        "accuracy_verified": None,
        "human_minutes": None,
        "note": "Joignabilité et exactitude nécessitent de vrais appels et un audit ; les valeurs inconnues restent nulles.",
    }


def tick():
    with SessionLocal() as db:
        state = d.state(db, lock=True)
        pilot = state.settings.get("pilot", {})
        if pilot:
            key = "pilot:" + pilot["request_id"] + ":" + m.now().strftime("%Y-%m-%dT%H")
            if not db.scalar(select(m.Job.id).where(m.Job.idempotency_key == key)):
                db.add(
                    m.Job(
                        kind="pilot_snapshot",
                        status="completed",
                        idempotency_key=key,
                        result=snapshot(db, pilot),
                        campaign_id=pilot.get("campaign_id"),
                    )
                )
        cfg = state.settings.get("critical_alerts", {})
        for stale in db.scalars(
            select(m.Job).where(
                m.Job.kind == "critical_notification",
                m.Job.status == "running",
                m.Job.locked_at < m.now() - timedelta(minutes=3),
            )
        ):
            stale.status, stale.error = (
                "uncertain",
                "Transmission interrompue : rapprocher SMTP avant de renvoyer",
            )
        if not cfg.get("enabled"):
            db.commit()
            return
        # A provider error is never retried blindly. Each notification has one durable delivery intent.
        notices = list(
            db.scalars(
                select(m.ActionNotification)
                .where(m.ActionNotification.delivered_at.is_(None), m.ActionNotification.read_at.is_(None))
                .order_by(m.ActionNotification.created_at)
                .limit(20)
            )
        )
        notices = [
            n
            for n in notices
            if not db.scalar(select(m.Job.id).where(m.Job.idempotency_key == "critical:" + n.id))
        ]
        sender = db.get(m.SendingMailbox, cfg.get("sender_id"))
        if not notices or not sender or sender.status != "authenticated":
            db.commit()
            return
        notice = notices[0]
        action = db.get(m.AgentAction, notice.action_id)
        if not action or action.status not in {"waiting_human", "failed", "uncertain"}:
            db.commit()
            return
        job = m.Job(
            kind="critical_notification",
            idempotency_key="critical:" + notice.id,
            status="running",
            locked_at=m.now(),
            payload={"notification_id": notice.id},
            provider="smtp",
        )
        db.add(job)
        db.flush()
        job_id = job.id
        config = decrypt_config(sender.encrypted_config)
        recipient, sender_email = cfg["recipient"], sender.from_email
        message = EmailMessage()
        message["From"], message["To"] = sender_email, recipient
        message["Subject"] = "Lumelia · " + notice.title.replace("\n", " ").replace("\r", " ")[:180]
        message["Message-ID"] = "<lumelia.alert." + job.id + "@" + sender_email.split("@")[-1] + ">"
        target = (
            action.target_url
            if action.target_url.startswith("/") and not action.target_url.startswith("//")
            else "/agent?view=human"
        )
        message.set_content(notice.body + "\n\n" + settings().public_url.rstrip("/") + target)
        db.commit()
    smtp, submitted, failure = None, False, None
    try:
        smtp = mail.connect_smtp(config)
        submitted = True
        refused = smtp.send_message(message, from_addr=sender_email, to_addrs=[recipient])
        if refused:
            failure = "Adresse destinataire refusée"
    except Exception:
        failure = "Réponse SMTP incertaine" if submitted else "Connexion SMTP indisponible"
    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:
                pass
    with SessionLocal() as db:
        row = db.get(m.Job, job_id)
        row.status = "completed" if not failure else "uncertain" if submitted else "failed"
        row.error = failure or ""
        if not failure:
            row.result = {"smtp_accepted": True, "delivery_confirmed": False}
            notice = db.get(m.ActionNotification, row.payload["notification_id"])
            if notice:
                notice.delivered_at = m.now()
        db.commit()


def record_worker_health(operation, failure=None):
    """Persist repeated worker failures even while its process heartbeat remains fresh."""
    from .agent_actions import enqueue

    key = "worker-health:" + operation
    with SessionLocal() as db:
        d.state(db, lock=True)
        job = db.scalar(select(m.Job).where(m.Job.idempotency_key == key))
        if not job:
            job = m.Job(kind="worker_health", idempotency_key=key, payload={"operation": operation})
            db.add(job)
            db.flush()
        if failure:
            job.attempts += 1
            job.status, job.error = "failed", failure
            job.result = {**job.result, "last_failure_at": m.now().isoformat()}
            if job.attempts >= 3:
                action = enqueue(
                    db,
                    key + ":" + str(job.result.get("recovery_count", 0)),
                    "worker_issue",
                    "Traitement serveur bloqué",
                    target_url="/alerts",
                    human_reason="Un traitement interne échoue à plusieurs reprises. Examiner le service ; les résultats déjà obtenus sont conservés.",
                    payload={"operation": operation, "error_type": failure},
                )
                job.result = {**job.result, "action_id": action.id}
        else:
            job.status, job.error, job.attempts = "completed", "", 0
            action = db.get(m.AgentAction, job.result.get("action_id", ""))
            if action and action.status == "waiting_human":
                action.status, action.completed_at = "completed", m.now()
                action.result = {"health_restored": True, "operation": operation}
            job.result = {
                **job.result,
                "last_success_at": m.now().isoformat(),
                "recovery_count": job.result.get("recovery_count", 0) + 1,
            }
        db.commit()
