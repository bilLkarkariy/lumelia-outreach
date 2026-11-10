"""An account's public facts are shared; its suitability belongs to one campaign."""

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from . import models as m
from . import prospect_sources as sources
from .mission_models import CampaignQualification


def assess(db, account, profile, campaign_id=None):
    from .prospect_pipeline import allowed, people, phone_for

    campaign = db.get(m.Campaign, campaign_id) if campaign_id else None
    icp = db.get(m.ICP, campaign.icp_id) if campaign else None
    offer = db.get(m.Offer, campaign.offer_id) if campaign else None
    criteria = icp.criteria if icp else {}
    contacts = people(db, account.id)
    facts = dict(profile.facts) if profile else {}
    # Legacy agent conclusions are only valid for the campaign actually examined.
    if profile and profile.campaign_id != campaign_id:
        for key in ("agent_decision", "agent_reason", "agent_reviewed_at"):
            facts.pop(key, None)
    prior = (
        db.scalar(
            select(CampaignQualification).where(
                CampaignQualification.account_id == account.id,
                CampaignQualification.campaign_id == campaign_id,
            )
        )
        if campaign_id
        else None
    )
    if prior:
        facts.update({k: v for k, v in prior.facts.items() if k.startswith("agent_")})
    registry = facts.get("registry", {})
    bounds = sources.SIZE_RANGES.get(registry.get("size_code"))
    if account.employee_count is not None:
        bounds = (account.employee_count, account.employee_count)
    checks = []

    def check(key, label, state, weight):
        state = state if state in {"match", "mismatch", "unknown"} else "unknown"
        checks.append({"key": key, "label": label, "status": state, "weight": weight})

    if criteria.get("country"):
        check(
            "country",
            "Pays cible : " + criteria["country"],
            "match" if account.country == criteria["country"] else "mismatch",
            10,
        )
    if criteria.get("min_employees") is not None or criteria.get("max_employees") is not None:
        lower, upper = criteria.get("min_employees", 0), criteria.get("max_employees", 10**9)
        recent = account.employee_count is not None or bool(
            registry.get("size_year") and int(registry["size_year"]) >= m.now().year - 3
        )
        state = (
            "unknown"
            if not bounds or not recent
            else (
                "mismatch"
                if bounds[1] < lower or bounds[0] > upper
                else "match"
                if bounds[0] >= lower and bounds[1] <= upper
                else "unknown"
            )
        )
        label = f"Effectif cible : {lower}–{upper}" if upper < 10**9 else f"Effectif minimum : {lower}"
        check("size", label, state, 15)
    business = str(criteria.get("business_type", "b2b")).lower()
    if business in {"b2b", "b2c"}:
        state = facts.get("b2b", "unknown")
        if business == "b2c":
            state = {"match": "mismatch", "mismatch": "match"}.get(state, "unknown")
        check("b2b", f"Activité {business.upper()} documentée", state, 15)
    industries = criteria.get("industries") or criteria.get("industry")
    if industries:
        terms = industries if isinstance(industries, list) else [industries]
        check(
            "industry",
            "Activité ciblée",
            "match"
            if account.industry and any(str(x).casefold() in account.industry.casefold() for x in terms)
            else "unknown",
            15,
        )
    crm = str(criteria.get("crm", "")).strip()
    if crm:
        check(
            "crm",
            "Usage commercial de " + crm,
            {"confirmed": "match", "absent": "mismatch"}.get(account.hubspot_status, "unknown")
            if crm.casefold() == "hubspot"
            else "unknown",
            20,
        )
    if registry.get("active") is False:
        check("active", "Entreprise en activité", "mismatch", 0)
    total_weight = sum(c["weight"] for c in checks) or 1
    for c in checks:
        c["points"] = round(60 * c["weight"] / total_weight) if c["status"] == "match" else 0
    phone, label = phone_for(db, account, contacts)
    email = next((c.email for c in contacts if c.email and c.email_status != "invalid"), None)
    coords = (15 if label == "Ligne directe" else 8 if phone else 0) + (10 if email else 0)
    checked = profile.checked_at if profile else None
    freshness = 15 if checked and checked >= m.now() - timedelta(days=90) and facts.get("sources") else 0
    score = min(100, sum(c["points"] for c in checks) + coords + freshness)
    unknown = [c["label"] for c in checks if c["status"] == "unknown"]
    matches = bool(checks) and all(c["status"] == "match" for c in checks)
    if not allowed(db, account, contacts):
        status, score = "excluded", 0
    elif any(c["status"] == "mismatch" for c in checks) or facts.get("agent_decision") == "not_fit":
        status, score = "not_fit", 0
    elif phone and matches:
        status = "ready"
    elif contacts:
        status = "contact_found"
    elif checked:
        status = "qualified" if matches else "review"
    else:
        status = "pending"
    # HubSpot can remain unknown while a documented B2B/size match merits an exploratory call.
    core = [c for c in checks if c["key"] != "crm"]
    if status not in {"excluded", "not_fit"} and phone and core and all(c["status"] == "match" for c in core):
        status = "ready"
    if status in {"excluded", "not_fit"}:
        next_action = "Ne pas contacter · " + (
            "Exclusion ou pause conservée" if status == "excluded" else "Hors cible pour cette campagne"
        )
    elif not contacts:
        next_action = "Identifier le dirigeant ou le responsable concerné" + (
            " via le standard" if phone else ""
        )
    elif phone:
        next_action = (
            "Appeler "
            + (contacts[0].first_name + " " + contacts[0].last_name).strip()
            + (" · confirmer le ciblage" if unknown else "")
        )
    else:
        next_action = "Compléter les coordonnées de l’interlocuteur"
    purpose = criteria.get("process") or (
        offer.summary if offer else "le fonctionnement et les besoins de l’entreprise"
    )
    brief = f"Objectif : comprendre {purpose}.\nQuestion : « Qui est responsable de ce sujet chez vous et comment le gérez-vous aujourd’hui ? »\n"
    if contacts:
        brief += f"Interlocuteur documenté : {contacts[0].first_name} {contacts[0].last_name} · {contacts[0].role}.\n"
    if unknown:
        brief += "À confirmer : " + ", ".join(unknown) + ".\n"
    brief += "Confirmer le besoin pendant l’échange ; consigner les engagements et la suite convenue."
    return SimpleNamespace(
        account_id=account.id,
        campaign_id=campaign_id,
        status=status,
        score=score,
        facts={**facts, "checks": checks, "size_label": f"{bounds[0]}–{bounds[1]}" if bounds else "Inconnu"},
        checked_at=checked,
        next_action=next_action,
        call_brief=brief,
    )


def save(db, account, profile, campaign_id):
    value = assess(db, account, profile, campaign_id)
    row = db.scalar(
        select(CampaignQualification).where(
            CampaignQualification.account_id == account.id, CampaignQualification.campaign_id == campaign_id
        )
    )
    if not row:
        row = CampaignQualification(account_id=account.id, campaign_id=campaign_id)
        db.add(row)
    for key in ("status", "score", "facts", "checked_at", "next_action", "call_brief"):
        setattr(row, key, getattr(value, key))
    row.updated_at = m.now()  # Projection refreshed even when the business verdict is unchanged.
    db.flush()
    return row


def tick():
    """Refresh a bounded projection per campaign, without provider calls."""
    from . import domain as d
    from .db import SessionLocal

    with SessionLocal() as db:
        d.state(db, lock=True)
        for campaign in db.scalars(select(m.Campaign).where(m.Campaign.status != "archived").limit(12)):
            icp = db.get(m.ICP, campaign.icp_id)
            query = (
                select(m.Account)
                .join(m.ProspectProfile)
                .outerjoin(
                    CampaignQualification,
                    (CampaignQualification.account_id == m.Account.id)
                    & (CampaignQualification.campaign_id == campaign.id),
                )
                .where(
                    (CampaignQualification.id.is_(None))
                    | (CampaignQualification.updated_at < m.ProspectProfile.checked_at)
                    | (CampaignQualification.updated_at < m.Account.updated_at)
                    | (CampaignQualification.updated_at < m.now() - timedelta(days=1))
                    | (CampaignQualification.updated_at < (icp.updated_at if icp else campaign.updated_at))
                )
                .order_by(CampaignQualification.updated_at.asc().nullsfirst())
                .limit(25)
            )
            for account in db.scalars(query):
                save(db, account, db.get(m.ProspectProfile, account.id), campaign.id)
        db.commit()
