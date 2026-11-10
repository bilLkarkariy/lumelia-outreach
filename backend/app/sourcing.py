"""Independent sourcing configurations sharing one bounded provider allowance."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import domain as d
from . import models as m
from . import schemas as s
from .db import get_db
from .security import admin, current_user

router = APIRouter(prefix="/api/sourcing")
INDUSTRIES = json.loads((Path(__file__).with_name("hunter_industries.json")).read_text())
SIZES = [
    ("1-10", 1, 10),
    ("11-50", 11, 50),
    ("51-200", 51, 200),
    ("201-500", 201, 500),
    ("501-1000", 501, 1000),
    ("1001-5000", 1001, 5000),
    ("5001-10000", 5001, 10000),
    ("10001+", 10001, float("inf")),
]


class ControlInput(s.Input):
    enabled: bool


class SourcingInput(s.Input):
    enabled: bool = False
    industries: list[str] = Field(default_factory=list, max_length=10)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=10)
    free_credit_limit: int = Field(default=5, ge=0, le=50)
    contacts_per_day: int = Field(default=10, ge=1, le=100)

    @field_validator("industries", "keywords", "excluded_keywords")
    @classmethod
    def terms(cls, value):
        if any(not word.strip() or len(word.strip()) > 160 for word in value):
            raise ValueError("Chaque filtre doit contenir entre 1 et 160 caractères.")
        return list(dict.fromkeys(word.strip() for word in value))

    @field_validator("industries")
    @classmethod
    def industries_valid(cls, value):
        if any(word not in INDUSTRIES for word in value):
            raise ValueError("Choisissez un secteur dans la liste Hunter.")
        return value

    @model_validator(mode="after")
    def scoped(self):
        if self.enabled and not (self.industries or self.keywords):
            raise ValueError("Précisez au moins un secteur ou un mot-clé pour cette sous-campagne.")
        return self


def runtime(state, campaign):
    from .autopilot import FILTERS, config

    global_config = config(state)
    dedicated = campaign.config.get("sourcing")
    if dedicated is not None:
        return {
            **dedicated,
            "campaign_id": campaign.id,
            "legacy": False,
            "global_version": global_config.get("version", 0),
        }
    if global_config.get("campaign_id") == campaign.id:
        return {
            **global_config,
            "filters": FILTERS,
            "legacy": True,
            "global_version": global_config.get("version", 0),
        }
    return None


def save_runtime(state, campaign, values):
    from .autopilot import store

    clean = {k: v for k, v in values.items() if k not in {"global_version", "legacy"}}
    if values.get("legacy"):
        store(state, clean)
    else:
        campaign.config = {**campaign.config, "sourcing": clean}


def filters_for(profile, data):
    criteria = profile.criteria
    country = str(criteria.get("country", "FR")).upper()
    if len(country) != 2 or not country.isalpha():
        raise HTTPException(422, "Le pays du profil doit être un code à deux lettres.")
    result = {"headquarters_location": {"include": [{"country": country}]}}
    if data.industries:
        result["industry"] = {"include": data.industries}
    if data.keywords or data.excluded_keywords:
        result["keywords"] = {"include": data.keywords, "exclude": data.excluded_keywords, "match": "any"}
    if "min_employees" in criteria or "max_employees" in criteria:
        lower, upper = criteria.get("min_employees", 0), criteria.get("max_employees", float("inf"))
        result["headcount"] = [name for name, lo, hi in SIZES if hi >= lower and lo <= upper]
    return result


def message_counts(db, campaign_id=None):
    query = select(m.Message.status, func.count()).group_by(m.Message.status)
    if campaign_id:
        query = query.where(m.Message.campaign_id == campaign_id)
    counts = dict(db.execute(query).all())
    return {
        "drafts": counts.get("draft", 0),
        "validated": counts.get("approved", 0) + counts.get("queued", 0),
        "cancelled": counts.get("cancelled", 0),
        "sent": counts.get("sent", 0),
        "historical": sum(counts.values()),
    }


def campaign_view(db, state, campaign):
    from .autopilot import credits

    cfg = runtime(state, campaign) or {}
    profile = db.get(m.ICP, campaign.icp_id)
    filters = cfg.get("filters", {})
    return {
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "niche": campaign.niche,
        "group_id": campaign.group_id,
        "configured": bool(cfg),
        "enabled": cfg.get("enabled", False),
        "legacy": cfg.get("legacy", False),
        "filters": filters,
        "industries": filters.get("industry", {}).get("include", []),
        "keywords": filters.get("keywords", {}).get("include", []),
        "excluded_keywords": filters.get("keywords", {}).get("exclude", []),
        "profile": {"name": profile.name, "criteria": profile.criteria},
        "free_credit_limit": cfg.get("free_credit_limit", 5),
        "credits_reserved_or_used": credits(db, campaign.id) / 2,
        "contacts_per_day": cfg.get("contacts_per_day", 10),
        "last_tick_at": cfg.get("last_tick_at"),
        "last_error": cfg.get("last_error", ""),
        "messages": message_counts(db, campaign.id),
    }


def pending_actions(db, state):
    from .autopilot import config, credits

    result = []
    global_used = credits(db)
    global_cap = config(state).get("free_credit_limit", 20) * 2
    for job in db.scalars(
        select(m.Job)
        .where(m.Job.kind == "autopilot", m.Job.status.in_(["queued", "running", "waiting"]))
        .order_by(m.Job.run_at)
        .limit(100)
    ):
        campaign = db.get(m.Campaign, job.campaign_id)
        if not campaign:
            continue
        cfg = runtime(state, campaign) or {}
        stage = job.payload.get("stage", "")
        required = (
            2 + job.payload.get("contact_limit", 2) if stage == "domain" else 1 if stage == "verify" else 0
        )
        reason = ""
        if not cfg.get("enabled") or not config(state).get("enabled"):
            reason = "Recherche suspendue"
        elif job.status == "queued" and (
            global_used + required > global_cap
            or credits(db, campaign.id) + required > cfg.get("free_credit_limit", 0) * 2
        ):
            reason = "Plafond de crédits atteint"
        elif job.run_at > m.now():
            reason = "Prochain passage : " + job.run_at.isoformat()
        result.append(
            {
                "id": job.id,
                "campaign_id": campaign.id,
                "kind": stage,
                "status": job.status,
                "wait_reason": reason,
                "due_at": job.run_at.isoformat(),
                "subject": campaign.name
                + " · "
                + job.payload.get("company", job.payload.get("email", "Recherche d’entreprises")),
            }
        )
    return result


@router.get("")
def status(user=Depends(current_user), db: Session = Depends(get_db)):
    from .autopilot import config, credits

    state = d.state(db)
    cfg = config(state)
    campaigns = list(db.scalars(select(m.Campaign).order_by(m.Campaign.created_at.desc())))
    return {
        "enabled": cfg.get("enabled", False),
        "free_credit_limit": cfg.get("free_credit_limit", 20),
        "credits_reserved_or_used": credits(db) / 2,
        "messages": message_counts(db),
        "campaigns": [campaign_view(db, state, campaign) for campaign in campaigns],
        "industries": INDUSTRIES,
        "next_actions": pending_actions(db, state),
    }


@router.post("/control")
def control(data: ControlInput, user=Depends(admin), db: Session = Depends(get_db)):
    from .autopilot import config, store

    state = d.state(db, lock=True)
    cfg = config(state)
    store(
        state,
        {
            "free_credit_limit": 20,
            "contacts_per_day": 200,
            **cfg,
            "enabled": data.enabled,
            "version": cfg.get("version", 0) + 1,
        },
    )
    d.log(db, "sourcing_control_updated", actor_id=user.id, data=data.model_dump())
    db.commit()
    return {"enabled": data.enabled}


@router.put("/{campaign_id}")
def configure(campaign_id: str, data: SourcingInput, user=Depends(admin), db: Session = Depends(get_db)):
    from .autopilot import config

    state = d.state(db, lock=True)
    campaign = d.get(db, m.Campaign, campaign_id)
    offer = db.get(m.Offer, campaign.offer_id)
    if offer.status in {"network", "historical"} or campaign.purpose != "cold_outreach":
        raise HTTPException(422, "Cette offre est réservée au réseau ou archivée.")
    if data.free_credit_limit > config(state).get("free_credit_limit", 20):
        raise HTTPException(422, "Le plafond de cette sous-campagne ne peut pas dépasser la réserve commune.")
    previous = runtime(state, campaign) or {}
    filters = filters_for(db.get(m.ICP, campaign.icp_id), data)
    changed = filters != previous.get("filters")
    cfg = {
        **previous,
        **data.model_dump(),
        "legacy": False,
        "filters": filters,
        "version": previous.get("version", 0) + 1,
        "last_error": "",
    }
    if changed:
        for job in db.scalars(
            select(m.Job).where(
                m.Job.kind == "autopilot", m.Job.campaign_id == campaign.id, m.Job.status == "queued"
            )
        ):
            job.status, job.error = (
                "cancelled",
                "Ciblage modifié : recherche à refaire avec les nouveaux filtres.",
            )
    save_runtime(state, campaign, cfg)
    d.log(db, "sourcing_configured", actor_id=user.id, campaign_id=campaign.id, data=data.model_dump())
    db.commit()
    return campaign_view(db, state, campaign)
