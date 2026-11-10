"""Shared call/task completion rules; callers own the transaction."""

from fastapi import HTTPException
from sqlalchemy import select

from . import domain as d
from . import models as m


def complete(db, task_id, data, actor_id):
    contact_id = db.scalar(select(m.Task.contact_id).where(m.Task.id == task_id))
    if not contact_id:
        raise HTTPException(404, "Tâche introuvable.")
    db.scalar(select(m.Contact).where(m.Contact.id == contact_id).with_for_update())
    task = db.scalar(select(m.Task).where(m.Task.id == task_id).with_for_update())
    if not task:
        raise HTTPException(404, "Tâche introuvable.")
    if task.status != "pending":
        raise HTTPException(409, "Cette tâche n’est plus à réaliser.")
    contact = d.get(db, m.Contact, task.contact_id)
    if data.outcome == "callback" and (
        not data.next_at or data.next_at.tzinfo is None or data.next_at <= m.now()
    ):
        raise HTTPException(422, "Indiquez la date future du rappel avec son fuseau horaire.")
    task.status, task.outcome = "completed", data.outcome
    task.notes = (task.notes + "\n" + data.notes).strip()
    d.log(
        db,
        "task_completed",
        contact=contact,
        actor_id=actor_id,
        channel=task.channel,
        summary=data.notes,
        data={"outcome": data.outcome, "task_id": task.id},
    )
    if data.outcome in {"stop", "meeting"}:
        d.receive_event(db, contact, data.outcome, task.channel, data.notes, actor_id=actor_id)
    elif data.outcome in {"callback", "no_need", "wrong_person"}:
        d.pause_conversation(db, contact, "Rappel demandé" if data.outcome == "callback" else data.outcome)
        if data.outcome == "callback":
            db.add(
                m.Task(
                    contact_id=contact.id,
                    campaign_id=contact.campaign_id,
                    owner_id=task.owner_id,
                    channel="phone",
                    title="Rappel convenu",
                    due_at=data.next_at,
                    notes=data.notes,
                    dedupe_key=f"callback:{task.id}",
                )
            )
    elif data.outcome == "wrong_number":
        contact.phone_status = "invalid"
        d.cancel_pending(db, contact, "Numéro incorrect", ["phone"])
    return task
