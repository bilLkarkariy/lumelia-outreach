"""Commercial campaign groups; each niche remains an independent sending campaign."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import domain as d
from . import models as m
from . import offers
from . import schemas as s
from .db import get_db
from .security import admin, current_user

router = APIRouter(prefix="/api/campaign-groups")


def get_group(db, group_id):
    group = db.get(m.CampaignGroup, group_id)
    if not group:
        raise HTTPException(404, "Campagne principale introuvable.")
    return group


def default_group(db, offer_id):
    # Serialize fallback creation for older clients and the existing automatic workflow.
    offer = db.scalar(select(m.Offer).where(m.Offer.id == offer_id).with_for_update())
    if not offer:
        raise HTTPException(422, "Choisissez une offre du catalogue.")
    group = db.scalar(
        select(m.CampaignGroup)
        .where(m.CampaignGroup.offer_id == offer_id)
        .order_by(m.CampaignGroup.created_at)
        .limit(1)
    )
    if not group:
        group = m.CampaignGroup(name=offer.name, offer_id=offer_id)
        db.add(group)
        db.flush()
    return group


def campaign_values(db, data, previous=None):
    values = data.model_dump(exclude={"target_profile", "language", "localized_footer"})
    language = data.language or (previous.config.get("language", "") if previous else "")
    if language:
        values["config"] = {
            **values["config"],
            "language": language,
            "localized_footer": data.localized_footer,
        }
    else:
        values["config"].pop("language", None)
        values["config"].pop("localized_footer", None)
    group_id = data.group_id
    if not group_id and previous and previous.offer_id == data.offer_id:
        group_id = previous.group_id
    group = get_group(db, group_id) if group_id else default_group(db, data.offer_id)
    if group.offer_id != data.offer_id:
        raise HTTPException(422, "La sous-campagne doit utiliser l’offre de sa campagne principale.")
    if data.target_profile:
        if data.target_profile.offer_id != data.offer_id:
            raise HTTPException(422, "Le profil dédié doit utiliser la même offre.")
        profile = m.ICP(**offers.icp_values(db, data.target_profile))
        db.add(profile)
        db.flush()
        values["icp_id"] = profile.id
    reasons = offers.campaign_reasons(db, values["offer_id"], values["icp_id"], values["purpose"])
    if reasons:
        raise HTTPException(422, reasons[0])
    values["group_id"] = group.id
    values["niche"] = data.niche or (previous.niche if previous else "") or "Ciblage initial"
    return values


def metrics(db, campaign_ids):
    def count(model, *conditions):
        return db.scalar(
            select(func.count()).select_from(model).where(model.campaign_id.in_(campaign_ids), *conditions)
        )

    return {
        "contacts": count(m.Contact),
        "companies": db.scalar(
            select(func.count(func.distinct(m.Contact.account_id))).where(
                m.Contact.campaign_id.in_(campaign_ids)
            )
        ),
        "sent": count(m.Message, m.Message.status == "sent"),
        "replies": db.scalar(
            select(func.count(func.distinct(m.Event.contact_id))).where(
                m.Event.campaign_id.in_(campaign_ids), m.Event.kind.in_(["reply", "positive_reply"])
            )
        ),
        "meetings_held": count(m.Opportunity, m.Opportunity.meeting_held.is_(True)),
        "customers_won": db.scalar(
            select(func.count(func.distinct(m.Opportunity.account_id))).where(
                m.Opportunity.campaign_id.in_(campaign_ids), m.Opportunity.stage == "won"
            )
        ),
    }


def campaign_list(db):
    rows = list(db.scalars(select(m.Campaign).order_by(m.Campaign.created_at.desc())))
    by_id = {row.id: row for row in rows}
    roots = {}
    for row in rows:
        cursor, seen = row, set()
        while cursor.config.get("parent_campaign_id") and cursor.id not in seen:
            seen.add(cursor.id)
            parent = by_id.get(cursor.config["parent_campaign_id"])
            if not parent or parent.group_id != row.group_id:
                break
            cursor = parent
        roots[row.id] = cursor.id
    families = {
        root: metrics(db, [key for key, value in roots.items() if value == root])
        for root in set(roots.values())
    }
    return [
        {
            **d.serialize(row),
            "metrics": metrics(db, [row.id]),
            "family_id": roots[row.id],
            "family_metrics": families[roots[row.id]],
        }
        for row in rows
    ]


@router.get("")
def list_groups(user=Depends(current_user), db: Session = Depends(get_db)):
    result = []
    for group in db.scalars(select(m.CampaignGroup).order_by(m.CampaignGroup.created_at)):
        ids = list(db.scalars(select(m.Campaign.id).where(m.Campaign.group_id == group.id)))
        result.append({**d.serialize(group), "metrics": metrics(db, ids)})
    return result


@router.post("", status_code=201)
def create_group(data: s.CampaignGroupCreate, user=Depends(admin), db: Session = Depends(get_db)):
    offer = db.get(m.Offer, data.offer_id)
    if not offer or offer.status == "historical":
        raise HTTPException(422, "Choisissez une offre disponible pour cette campagne.")
    group = m.CampaignGroup(**data.model_dump())
    db.add(group)
    db.commit()
    return d.serialize(group)


@router.patch("/{group_id}")
def edit_group(
    group_id: str, data: s.CampaignGroupCreate, user=Depends(admin), db: Session = Depends(get_db)
):
    group = get_group(db, group_id)
    if data.offer_id != group.offer_id:
        raise HTTPException(422, "L’offre d’une campagne principale ne peut pas être remplacée.")
    group.name, group.notes = data.name, data.notes
    db.commit()
    return d.serialize(group)


@router.post("/{group_id}/pause")
def pause_group(group_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    get_group(db, group_id)
    changed = 0
    for campaign in db.scalars(select(m.Campaign).where(m.Campaign.group_id == group_id).with_for_update()):
        if campaign.status == "active":
            campaign.status = "paused"
            d.log(db, "campaign_paused", campaign_id=campaign.id, actor_id=user.id)
            changed += 1
    db.commit()
    return {"paused_subcampaigns": changed}
