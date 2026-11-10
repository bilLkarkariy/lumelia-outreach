"""Scoped business effects for mission steps; no arbitrary requests or shell tools."""

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select

from . import domain as d
from . import models as m
from . import schemas as s
from .mission_models import Mission, MissionStep


class BusinessResult(s.Input):
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=20000)
    name: str = Field(default="", max_length=180)
    sequence: list[s.SequenceStep] = Field(default_factory=list, max_length=10)


def context(db, action):
    step = d.get(db, MissionStep, action.payload["mission_step_id"])
    mission = d.get(db, Mission, step.mission_id)
    return {
        "step": d.serialize(step),
        "mission": d.serialize(mission),
        "policy": "Produire uniquement le livrable demandé. Aucun engagement tarifaire ou contractuel. Réutiliser les faits fournis et signaler les informations manquantes.",
    }


def execute_step(db, mission, step):
    from .agent_actions import enqueue
    from .missions import complete

    contact = db.get(m.Contact, step.contact_id) if step.contact_id else None
    if step.kind == "assign_contact":
        if not contact or not mission.campaign_id:
            step.status, step.last_error = "waiting_data", "Contact et campagne nécessaires"
            return None
        if contact.campaign_id and contact.campaign_id != mission.campaign_id:
            step.status, step.last_error = "waiting_human", "Le contact appartient déjà à une autre campagne"
            return None
        contact.campaign_id = mission.campaign_id
        complete(step, {"contact_id": contact.id, "campaign_id": mission.campaign_id})
        return None
    if step.kind == "update_opportunity":
        opportunity = db.scalar(
            select(m.Opportunity)
            .where(
                m.Opportunity.account_id == step.account_id,
                m.Opportunity.campaign_id == mission.campaign_id,
                m.Opportunity.stage.notin_(["won", "lost"]),
            )
            .order_by(m.Opportunity.updated_at.desc())
            .limit(1)
        )
        if not opportunity:
            opportunity = m.Opportunity(
                account_id=step.account_id,
                contact_id=step.contact_id,
                campaign_id=mission.campaign_id,
                owner_id=mission.owner_id,
                title=step.title,
                stage="conversation",
            )
            db.add(opportunity)
        opportunity.next_action = step.payload.get("reason", step.title)
        opportunity.next_action_at = step.due_at
        db.flush()
        complete(step, {"opportunity_id": opportunity.id})
        return None
    if step.kind == "send_email":
        message = db.get(m.Message, step.payload.get("message_id", ""))
        if not message:
            step.status, step.last_error = "waiting_data", "Brouillon indisponible"
        elif message.status == "sent" and message.sent_at:
            complete(step, {"message_id": message.id, "sent_at": message.sent_at.isoformat()})
        elif message.status in {"failed", "uncertain", "cancelled"}:
            step.status, step.last_error = "waiting_human", message.failure_reason or message.status
        else:
            step.status = "waiting_external"
        return None
    if step.kind == "draft_email" and (not contact or not contact.email or not mission.campaign_id):
        step.status, step.last_error = (
            "waiting_data",
            "Identifier le contact, son email et la campagne avant de rédiger",
        )
        step.due_at = m.now() + timedelta(hours=1)
        return None
    return enqueue(
        db,
        f"business:{step.id}:{step.attempts}",
        "mission_step",
        "Préparer · " + step.title,
        account_id=step.account_id,
        contact_id=step.contact_id,
        campaign_id=mission.campaign_id,
        payload={
            "mission_step_id": step.id,
            "lane": "post_call" if mission.kind == "post_call" else "research",
        },
        priority=5 if mission.kind == "post_call" else 40,
        target_url="/campaigns/" + str(mission.campaign_id or ""),
    )


def apply_result(db, action, result):
    from . import campaign_groups, conversations, email_policy, senders
    from .agent_actions import enqueue
    from .mission_models import CallReport
    from .missions import add_step, complete

    step = d.get(db, MissionStep, action.payload.get("mission_step_id", ""))
    mission = d.get(db, Mission, step.mission_id)
    if mission.status != "active" or step.action_id != action.id:
        raise HTTPException(409, "La mission ou l’étape n’est plus active")
    campaign = d.get(db, m.Campaign, mission.campaign_id) if mission.campaign_id else None
    if not campaign:
        raise HTTPException(409, "Campagne nécessaire pour ce livrable")
    if step.kind == "draft_email":
        contact = d.get(db, m.Contact, step.contact_id)
        report = d.get(db, CallReport, step.payload.get("report_id", ""))
        if report.call_id != step.payload.get("call_id"):
            raise HTTPException(409, "Compte rendu incohérent")
        sender = db.get(m.SendingMailbox, contact.email_sender_id) if contact.email_sender_id else None
        if not sender:
            sender = next(
                (
                    r
                    for r in db.scalars(select(m.SendingMailbox).order_by(m.SendingMailbox.created_at))
                    if not senders.readiness(r, reception=True)
                ),
                None,
            )
        if not sender:
            raise HTTPException(409, "Aucune boîte prête pour cette conversation")
        event_key = "call-followup:" + step.id
        event = db.scalar(select(m.Event).where(m.Event.external_id == event_key))
        if not event:
            event = m.Event(
                kind="call_followup_request",
                channel="phone",
                contact_id=contact.id,
                account_id=contact.account_id,
                campaign_id=campaign.id,
                external_id=event_key,
                summary=step.payload.get("quote", ""),
                data={
                    "call_id": report.call_id,
                    "report_id": report.id,
                    "sender_id": sender.id,
                    "step_id": step.id,
                },
            )
            db.add(event)
            db.flush()
        draft = conversations.persist_reply(
            event.id,
            conversations.ReplyDraft(
                subject=result.subject,
                body=result.body,
                request_id=uuid5(NAMESPACE_URL, "draft:" + step.id),
                expected_event_updated_at=event.updated_at,
            ),
            db.get(m.User, mission.owner_id),
            db,
        )
        complete(step, {"message_id": draft["id"], "effect": "draft_saved"})
        send_step = add_step(
            db,
            mission,
            "send:" + step.id,
            "send_email",
            step.title,
            account_id=step.account_id,
            contact_id=contact.id,
            dependencies=[step.id],
            payload={**step.payload, "message_id": draft["id"]},
        )
        send_action = enqueue(
            db,
            "send-reply:" + draft["id"],
            "send_reply",
            "Envoyer · " + step.title,
            account_id=step.account_id,
            contact_id=contact.id,
            campaign_id=campaign.id,
            event_id=event.id,
            target_url="/contacts/" + contact.id,
            payload={"message_id": draft["id"], "draft_revision": draft["updated_at"]},
        )
        send_step.action_id = send_action.id
        return step.result
    if step.kind in {"draft_campaign", "draft_sequence"}:
        if not result.name.strip() or not result.sequence:
            raise HTTPException(422, "Nom et séquence nécessaires")
        if any(email_policy.contains_price(x.subject, x.body) for x in result.sequence):
            raise HTTPException(422, email_policy.REASON)
        payload = s.CampaignCreate(
            name=result.name,
            offer_id=campaign.offer_id,
            icp_id=campaign.icp_id,
            group_id=campaign.group_id,
            niche=campaign.niche,
            mode="simulation",
            transport="smtp",
            sequence=result.sequence,
            budget_cents=0,
            daily_limit=campaign.daily_limit,
            timezone=campaign.timezone,
            config={"parent_campaign_id": campaign.id, "mission_step_id": step.id},
        )
        values = campaign_groups.campaign_values(db, payload)
        draft = m.Campaign(**values, owner_id=mission.owner_id)
        db.add(draft)
        db.flush()
        complete(step, {"campaign_id": draft.id, "effect": "draft_saved"})
        return step.result
    raise HTTPException(422, "Cette étape ne nécessite pas de résultat textuel")
