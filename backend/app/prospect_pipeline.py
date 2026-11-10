"""One prospect per company, with sourced people added only after identification."""

import hashlib
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import SQLColumnExpression, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from . import domain as d
from . import models as m
from . import prospect_sources as sources
from . import schemas as s
from .db import SessionLocal, get_db
from .security import admin, current_user

router = APIRouter(prefix="/api", tags=["prospect pipeline"])
STAGES = {
    "pending": "À qualifier",
    "checking": "Qualification en cours",
    "review": "À approfondir",
    "qualified": "Cible retenue",
    "contact_found": "Interlocuteur identifié",
    "ready": "À appeler",
    "not_fit": "Hors cible",
    "excluded": "Exclu",
}


def sync(db, campaign_id=None):
    missing = select(m.Account.id).where(
        ~select(m.ProspectProfile.account_id).where(m.ProspectProfile.account_id == m.Account.id).exists()
    )
    for aid in db.scalars(missing):
        db.execute(
            insert(m.ProspectProfile)
            .values(
                account_id=aid,
                campaign_id=campaign_id,
                status="pending",
                score=0,
                facts={},
                attempts=0,
                next_action="Qualifier l’entreprise et identifier un interlocuteur",
                call_brief="",
                error="",
            )
            .on_conflict_do_nothing()
        )
    if campaign_id:
        from sqlalchemy import update

        db.execute(
            update(m.ProspectProfile)
            .where(m.ProspectProfile.campaign_id.is_(None))
            .values(campaign_id=campaign_id)
        )
    db.flush()


def people(db, account_id):
    return list(
        db.scalars(
            select(m.Contact)
            .where(m.Contact.account_id == account_id)
            .order_by(m.Contact.paused, m.Contact.created_at)
        )
    )


def allowed(db, account, contacts):
    return (
        account.lifecycle == "prospect"
        and not account.outreach_paused
        and not any(d.is_suppressed(db, c) or c.paused for c in contacts)
    )


def phone_for(db, account, contacts):
    contact = next(
        (c for c in contacts if c.phone and c.phone_status != "invalid" and c.status != "suppressed"), None
    )
    if contact:
        return (
            contact.phone,
            "Ligne directe" if contact.phone_type == "direct" else "Numéro du contact · à vérifier",
        )
    invalid = list(
        db.scalars(
            select(m.Evidence.value).where(
                m.Evidence.account_id == account.id, m.Evidence.field == "company_phone_invalid"
            )
        )
    )
    proof = db.scalar(
        select(m.Evidence)
        .where(
            m.Evidence.account_id == account.id,
            m.Evidence.field == "company_phone",
            m.Evidence.value.notin_(invalid),
        )
        .order_by(m.Evidence.observed_at.desc())
        .limit(1)
    )
    return (proof.value, "Téléphone d’établissement") if proof else ("", "Téléphone à récupérer")


def evaluate(db, profile):
    from .campaign_qualification import assess, save
    from .mission_models import CampaignQualification

    account = db.get(m.Account, profile.account_id)
    value = assess(db, account, profile, profile.campaign_id)
    for key in ("status", "score", "facts", "next_action", "call_brief"):
        setattr(profile, key, getattr(value, key))
    campaign_ids = set(
        db.scalars(
            select(CampaignQualification.campaign_id).where(CampaignQualification.account_id == account.id)
        )
    )
    if profile.campaign_id:
        campaign_ids.add(profile.campaign_id)
    for campaign_id in campaign_ids:
        save(db, account, profile, campaign_id)


def add_evidence(db, account_id, field, value, url, source_type="official_website", contact_id=None):
    if not db.scalar(
        select(m.Evidence.id).where(
            m.Evidence.account_id == account_id,
            m.Evidence.contact_id == contact_id,
            m.Evidence.field == field,
            m.Evidence.value == value,
            m.Evidence.source_url == url,
        )
    ):
        db.add(
            m.Evidence(
                account_id=account_id,
                contact_id=contact_id,
                field=field,
                value=value,
                source_url=url,
                source_type=source_type,
                confidence="signal",
            )
        )


def import_registry_people(db, profile, registry):
    account = db.get(m.Account, profile.account_id)
    if not allowed(db, account, people(db, account.id)) or registry.get("active") is not True:
        return
    for person in registry.get("directors", [])[:3]:
        first, last, role = (
            value.strip() if isinstance(value, str) else ""
            for value in (person.get("first_name"), person.get("last_name"), person.get("role"))
        )
        import re

        if (
            not first
            or not last
            or not re.search(r"président|president|gérant|gerant|directeur|CEO|fondateur", role, re.I)
        ):
            continue
        existing = db.scalar(
            select(m.Contact.id).where(
                m.Contact.account_id == account.id,
                func.lower(m.Contact.first_name) == first.lower(),
                func.lower(m.Contact.last_name) == last.lower(),
            )
        )
        if existing:
            continue
        contact, _ = d.create_contact(
            db,
            s.ContactCreate(
                account_id=account.id,
                campaign_id=profile.campaign_id,
                first_name=first,
                last_name=last,
                role=role,
                professional_relevance="Représentant légal public de l’entreprise. Responsabilité opérationnelle et pertinence pour la cible à confirmer.",
                sourcing_note="Identité professionnelle du registre public, reliée au SIREN publié sur le site officiel. Aucun téléphone personnel attribué.",
                source_url=registry["source_url"],
                source_type="official_website",
                information_status="planned",
            ),
            "workspace",
        )
        add_evidence(
            db, account.id, "identity", first + " " + last, registry["source_url"], "sirene", contact.id
        )
        add_evidence(db, account.id, "role", role, registry["source_url"], "sirene", contact.id)


def apply_public(db, aid, collected, registry):
    profile = db.get(m.ProspectProfile, aid)
    account = db.get(m.Account, aid)
    if not allowed(db, account, people(db, aid)):
        profile.status = "excluded"
        return
    facts = profile.facts.copy()
    facts["sources"] = [{"url": p["url"], "excerpt": p["text"][:800]} for p in collected["pages"]]
    facts["research_text"] = "\n".join(p["text"][:9000] for p in collected["pages"])[:18000]
    import re

    for page in collected["pages"]:
        match = re.search(
            r"\b(?:B2B|interentreprises|pour les entreprises|aux entreprises|clients professionnels|pour les professionnels)\b",
            page["text"],
            re.I,
        )
        if match:
            facts["b2b"] = "match"
            add_evidence(
                db, aid, "b2b", page["text"][max(0, match.start() - 100) : match.end() + 180], page["url"]
            )
            break
    for email, url in collected["emails"].items():
        add_evidence(db, aid, "company_email", email, url)
    if registry:
        other = db.scalar(
            select(m.Account.id).where(m.Account.siren == registry["siren"], m.Account.id != aid)
        )
        if not other:
            account.siren = registry["siren"]
            facts["registry"] = registry
            add_evidence(
                db,
                aid,
                "siren",
                registry["siren"],
                collected["sirens"].get(registry["siren"], registry["source_url"]),
            )
            add_evidence(
                db,
                aid,
                "employee_range",
                str(registry.get("size_code")) + " · " + str(registry.get("size_year")),
                registry["source_url"],
                "sirene",
            )
            import_registry_people(db, profile, registry)
        else:
            facts["registry_note"] = "SIREN déjà rattaché à une autre fiche : rapprochement nécessaire"
    profile.facts = facts
    profile.checked_at = m.now()
    profile.locked_at = None
    profile.status = "review"
    profile.error = "; ".join(collected["errors"])
    evaluate(db, profile)


class Enable(s.Input):
    campaign_id: str
    enabled: bool = True
    auto_enrich: bool = True


@router.post("/prospect-pipeline/config")
def configure(data: Enable, user=Depends(admin), db=Depends(get_db)):
    d.get(db, m.Campaign, data.campaign_id)
    state = d.state(db, lock=True)
    state.settings = {**state.settings, "prospect_pipeline": data.model_dump()}
    sync(db, data.campaign_id)
    d.log(
        db,
        "prospect_pipeline_configured",
        actor_id=user.id,
        summary="Qualification et identification automatiques "
        + ("activées" if data.enabled else "en pause"),
    )
    db.commit()
    return {
        "enabled": data.enabled,
        "profiles": db.scalar(select(func.count()).select_from(m.ProspectProfile)),
    }


def tick():
    with SessionLocal() as db:
        state = d.state(db, lock=True)
        cfg = state.settings.get("prospect_pipeline", {})
        if not cfg.get("enabled"):
            return 0
        sync(db, cfg.get("campaign_id"))
        for stale in db.scalars(
            select(m.ProspectProfile).where(
                m.ProspectProfile.status == "checking",
                m.ProspectProfile.locked_at < m.now() - timedelta(minutes=5),
            )
        ):
            stale.status = "pending"
        row = db.scalar(
            select(m.ProspectProfile)
            .join(m.Account)
            .where(
                m.ProspectProfile.status == "pending",
                m.Account.lifecycle == "prospect",
                m.Account.outreach_paused.is_(False),
                ~m.Account.domain.endswith(".invalid"),
            )
            .order_by(m.ProspectProfile.score.desc(), m.Account.created_at.desc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not row:
            db.commit()
            return 0
        row.status = "checking"
        row.locked_at = m.now()
        row.attempts += 1
        aid = row.account_id
        account = db.get(m.Account, aid)
        domain = account.domain
        siren = account.siren
        db.commit()
    collected = sources.collect_site(domain)
    reg = None
    candidates = list(collected["sirens"])
    if siren or len(candidates) == 1:
        try:
            reg = sources.registry(siren or candidates[0])
        except (httpx.HTTPError, ValueError):
            collected["errors"].append("Registre indisponible, à reprendre par l’agent")
    with SessionLocal() as db:
        d.state(db, lock=True)
        apply_public(db, aid, collected, reg)
        db.commit()
    return 1


def row_view(db, account, campaign_id=None):
    profile = db.get(m.ProspectProfile, account.id)
    if campaign_id:
        from .campaign_qualification import assess

        profile = assess(db, account, profile, campaign_id)
    contacts = people(db, account.id)
    contact = next((c for c in contacts if c.status != "suppressed"), None)
    phone, kind = phone_for(db, account, contacts)
    contact_ids = [c.id for c in contacts]
    last_call = db.scalar(
        select(func.max(m.Event.created_at)).where(
            m.Event.channel == "phone",
            or_(m.Event.contact_id.in_(contact_ids), m.Event.data["account_id"].as_string() == account.id),
        )
    )
    last_email = db.scalar(
        select(func.max(m.Message.sent_at)).where(
            m.Message.contact_id.in_(contact_ids), m.Message.status == "sent"
        )
    )
    event_scope = or_(
        m.Event.account_id == account.id,
        m.Event.contact_id.in_(contact_ids),
        m.Event.data["account_id"].as_string() == account.id,
    )
    latest_response = db.scalar(
        select(m.Event)
        .where(
            event_scope,
            m.Event.kind.in_(["reply", "positive_reply", "out_of_office", "bounce", "stop", "meeting"]),
        )
        .order_by(m.Event.created_at.desc(), m.Event.id.desc())
        .limit(1)
    )
    latest_sent = db.scalar(
        select(m.Message)
        .where(m.Message.contact_id.in_(contact_ids), m.Message.status == "sent")
        .order_by(m.Message.sent_at.desc(), m.Message.created_at.desc(), m.Message.id.desc())
        .limit(1)
    )
    latest_pending = db.scalar(
        select(m.Message)
        .where(
            m.Message.contact_id.in_(contact_ids),
            m.Message.status.in_(["draft", "approved", "queued", "sending", "uncertain", "failed"]),
        )
        .order_by(
            func.coalesce(m.Message.attempted_at, m.Message.created_at).desc(),
            m.Message.id.desc(),
        )
        .limit(1)
    )
    activity = latest_response or latest_sent or latest_pending
    if activity and activity.contact_id:
        contact = next((c for c in contacts if c.id == activity.contact_id), contact)
    commercial_status, commercial_status_label, commercial_status_at = (
        "never_contacted",
        "Jamais contactée",
        None,
    )
    if latest_response:
        commercial_status_at = latest_response.created_at
        commercial_status, commercial_status_label = {
            "positive_reply": ("interested", "Intérêt manifesté"),
            "meeting": ("interested", "Rendez-vous convenu"),
            "stop": ("stopped", "Ne plus contacter"),
            "bounce": ("bounced", "Adresse en rebond"),
            "out_of_office": ("reply_received", "Réponse automatique reçue"),
        }.get(latest_response.kind, ("reply_received", "Réponse reçue"))
    elif latest_sent:
        commercial_status, commercial_status_label, commercial_status_at = (
            "email_sent",
            "Email envoyé",
            latest_sent.sent_at or latest_sent.created_at,
        )
    elif latest_pending:
        commercial_status, commercial_status_label = {
            "uncertain": ("email_uncertain", "Envoi à rapprocher"),
            "failed": ("email_failed", "Échec d’envoi"),
        }.get(latest_pending.status, ("email_pending", "Email préparé / en attente"))
        commercial_status_at = latest_pending.attempted_at or latest_pending.created_at
    elif last_call:
        commercial_status, commercial_status_label, commercial_status_at = (
            "called",
            "Appel enregistré",
            last_call,
        )
    company_email = db.scalar(
        select(m.Evidence.value)
        .where(m.Evidence.account_id == account.id, m.Evidence.field == "company_email")
        .order_by(m.Evidence.observed_at.desc())
        .limit(1)
    )
    stage = profile.status if profile else "pending"
    eligible = allowed(db, account, contacts)
    if not eligible:
        stage = "excluded"
    return {
        **d.serialize(account),
        "profile": {
            key: getattr(profile, key)
            for key in ("status", "score", "facts", "checked_at", "next_action", "call_brief")
        }
        if profile
        else {},
        "contact": d.serialize(contact),
        "contact_count": len(contacts),
        "phone": phone,
        "phone_label": kind,
        "email": contact.email if contact else None,
        "company_email": company_email,
        "score": profile.score if profile and eligible else 0,
        "stage": stage,
        "stage_label": STAGES.get(stage, stage) + " · " + commercial_status_label,
        "qualification_label": STAGES.get(stage, stage),
        "can_call": bool(phone and eligible and stage != "not_fit"),
        "last_call_at": last_call,
        "last_email_at": last_email,
        "commercial_status": commercial_status,
        "commercial_status_label": commercial_status_label,
        "commercial_status_at": commercial_status_at,
        "next_action": (
            "Ne pas contacter · exclusion ou pause conservée"
            if not eligible
            else profile.next_action
            if profile
            else "Qualifier et identifier un interlocuteur"
        ),
    }


@router.get("/lists/prospects")
def listing(
    q: str = Query("", max_length=120),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sort: str = "score",
    direction: Literal["asc", "desc"] = "desc",
    stage: str = "",
    campaign_id: str = "",
    user=Depends(current_user),
    db=Depends(get_db),
):
    query = select(m.Account).outerjoin(m.ProspectProfile)
    status_column: SQLColumnExpression[Any] = m.ProspectProfile.status
    score_column: SQLColumnExpression[Any] = m.ProspectProfile.score
    if campaign_id:
        from .mission_models import CampaignQualification

        d.get(db, m.Campaign, campaign_id)
        query = query.outerjoin(
            CampaignQualification,
            (CampaignQualification.account_id == m.Account.id)
            & (CampaignQualification.campaign_id == campaign_id),
        )
        status_column = func.coalesce(CampaignQualification.status, "pending")
        score_column = func.coalesce(CampaignQualification.score, 0)
    if q:
        pattern = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(
            or_(
                m.Account.name.ilike(pattern),
                m.Account.domain.ilike(pattern),
                m.Account.id.in_(
                    select(m.Contact.account_id).where(
                        or_(
                            m.Contact.first_name.ilike(pattern),
                            m.Contact.last_name.ilike(pattern),
                            m.Contact.email.ilike(pattern),
                            m.Contact.phone.ilike(pattern),
                        )
                    )
                ),
            )
        )
    if stage:
        if stage not in STAGES:
            raise HTTPException(422, "Filtre inconnu")
        query = query.where(status_column == stage)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    overall = db.scalar(select(func.count()).select_from(m.Account))
    page = min(page, max(1, (total + page_size - 1) // page_size))
    order = {"score": score_column, "name": m.Account.name, "created": m.Account.created_at}.get(
        sort, score_column
    )
    accounts = list(
        db.scalars(
            query.order_by(
                (order.desc() if direction == "desc" else order.asc()).nulls_last(),
                m.Account.name,
                m.Account.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    summary = {
        "total": overall,
        "with_contact": db.scalar(select(func.count(func.distinct(m.Contact.account_id)))),
        "with_phone": db.scalar(
            select(func.count(func.distinct(m.Evidence.account_id))).where(
                m.Evidence.field == "company_phone"
            )
        ),
        "checked": db.scalar(
            select(func.count())
            .select_from(m.ProspectProfile)
            .where(m.ProspectProfile.checked_at.is_not(None))
        ),
    }
    return {
        "items": [row_view(db, a, campaign_id) for a in accounts],
        "total": total,
        "total_unfiltered": overall,
        "page": page,
        "page_size": page_size,
        "summary": summary,
        "facets": {},
    }


@router.get("/prospect-pipeline/accounts/{account_id}")
def detail(account_id: str, campaign_id: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    account = d.get(db, m.Account, account_id)
    row = row_view(db, account, campaign_id)
    row["evidence"] = [
        d.serialize(x)
        for x in db.scalars(
            select(m.Evidence)
            .where(m.Evidence.account_id == account_id)
            .order_by(m.Evidence.observed_at.desc())
            .limit(20)
        )
    ]
    return row


class Call(s.Input):
    request_id: UUID
    outcome: Literal["not_reached", "switchboard", "identified", "no_need", "wrong_number"]
    notes: str = Field(default="", max_length=5000)


@router.post("/prospect-pipeline/accounts/{account_id}/calls")
def call(account_id: str, data: Call, user=Depends(current_user), db=Depends(get_db)):
    d.state(db, lock=True)
    account = d.get(db, m.Account, account_id)
    key = "company-call:" + account_id + ":" + str(data.request_id)
    prior = db.scalar(select(m.Event).where(m.Event.external_id == key))
    if prior:
        if prior.data.get("outcome") != data.outcome or prior.summary != data.notes:
            raise HTTPException(409, "Cet appel est déjà enregistré avec un autre contenu")
        return {"recorded": True}
    if not allowed(db, account, people(db, account_id)):
        raise HTTPException(409, "Prospection en pause ou exclue")
    d.log(
        db,
        "task_completed",
        actor_id=user.id,
        channel="phone",
        external_id=key,
        summary=data.notes,
        data={"account_id": account_id, "outcome": data.outcome},
    )
    db.commit()
    return {"recorded": True}


def enqueue_batch(db):
    from .agent_actions import ACTIVE, enqueue

    cfg = d.state(db).settings.get("prospect_pipeline", {})
    if not cfg.get("enabled") or db.scalar(
        select(m.AgentAction.id).where(
            m.AgentAction.kind == "qualify_batch", m.AgentAction.status.in_(ACTIVE)
        )
    ):
        return
    rows = list(
        db.scalars(
            select(m.ProspectProfile)
            .join(m.Account)
            .where(
                or_(
                    m.ProspectProfile.checked_at.is_not(None),
                    # Imported accounts without a known website carry a placeholder domain.
                    m.Account.domain.endswith(".invalid"),
                ),
                m.ProspectProfile.status.notin_(["excluded", "not_fit", "checking"]),
                m.ProspectProfile.facts["agent_reviewed_at"].as_string().is_(None),
                m.Account.lifecycle == "prospect",
                m.Account.outreach_paused.is_(False),
            )
            .order_by(m.ProspectProfile.score.desc(), m.ProspectProfile.checked_at)
            .limit(10)
        )
    )
    if not rows:
        return
    ids = [r.account_id for r in rows]
    enqueue(
        db,
        "qualify-batch:"
        + hashlib.sha256((str(cfg.get("campaign_id", "")) + ":" + ":".join(ids)).encode()).hexdigest(),
        "qualify_batch",
        f"Qualifier {len(ids)} entreprises et identifier leurs interlocuteurs",
        priority=35,
        campaign_id=cfg.get("campaign_id"),
        target_url="/contacts",
        payload={
            "account_ids": ids,
            "account_revisions": {aid: db.get(m.Account, aid).updated_at.isoformat() for aid in ids},
            "limit": len(ids),
            "brief": "Traiter uniquement les entreprises fournies selon les critères ICP de la campagne. Réutiliser les extraits et le registre public ; au plus trois recherches complémentaires par entreprise. Si le domaine finit par .invalid, retrouver le site officiel et fournir account_updates.domain avec une preuve domain issue de ce site. Identifier ensuite l’interlocuteur adapté à cette offre avec preuve explicite. Enregistrer les résultats déjà obtenus même si le lot est incomplet. Conserver les incertitudes ; aucun achat ni message à un prospect.",
        },
    )


def agent_context(db, action):
    result = []
    for aid in action.payload.get("account_ids", []):
        a = db.get(m.Account, aid)
        p = db.get(m.ProspectProfile, aid)
        if not a or not p:
            continue
        result.append(
            {
                "account": d.serialize(a),
                "profile": d.serialize(p),
                "existing_people": [d.serialize(c) for c in people(db, aid)],
                "evidence": [
                    d.serialize(e)
                    for e in db.scalars(select(m.Evidence).where(m.Evidence.account_id == aid).limit(12))
                ],
            }
        )
    return result


def apply_agent_batch(db, action, reviews):
    from types import SimpleNamespace

    from .agent_actions import add_proofs, apply_account_domain, import_leads

    scope = set(action.payload.get("account_ids", []))
    if not reviews:
        raise HTTPException(422, "Enregistrez au moins une entreprise du lot")
    seen = set()
    results = []
    for review in reviews:
        if review.account_id not in scope or review.account_id in seen:
            raise HTTPException(422, "Entreprise hors du lot ou répétée")
        seen.add(review.account_id)
        account = d.get(db, m.Account, review.account_id)
        profile = d.get(db, m.ProspectProfile, review.account_id)
        if not allowed(db, account, people(db, account.id)):
            results.append({"account_id": account.id, "skipped": "excluded"})
            continue
        expected = action.payload.get("account_revisions", {}).get(account.id)
        if expected and account.updated_at.isoformat() != expected:
            results.append({"account_id": account.id, "skipped": "modified"})
            continue
        if review.decision == "not_fit" and not review.proofs:
            raise HTTPException(422, "Justifiez une exclusion par une preuve")
        add_proofs(db, review.proofs, account.id)
        domain_changed = apply_account_domain(db, account, review.account_updates, review.proofs)
        from .campaign_qualification import save as save_assessment

        campaign_id = getattr(action, "campaign_id", None) or profile.campaign_id
        assessment = save_assessment(db, account, profile, campaign_id) if campaign_id else None
        facts = {
            **profile.facts,
            "agent_reviewed_at": m.now().isoformat(),
            "agent_decision": review.decision,
            "agent_reason": review.summary,
        }
        if domain_changed:
            facts["domain_resolution"] = {"verified": True, "resolved_domain": account.domain}
        if review.business_type != "unknown":
            if not any(p.field == "b2b" for p in review.proofs):
                raise HTTPException(422, "Preuve B2B/B2C requise")
            facts["b2b"] = "match" if review.business_type == "b2b" else "mismatch"
        if review.account_updates and review.account_updates.employee_count is not None:
            value = review.account_updates.employee_count
            if not any(p.field == "employee_count" and str(value) in p.value for p in review.proofs):
                raise HTTPException(422, "Effectif exact sans preuve")
            account.employee_count = value
        # Public technical traces cannot establish commercial CRM use.
        if review.account_updates and review.account_updates.hubspot_status:
            if not any(p.field == "hubspot_status" for p in review.proofs):
                raise HTTPException(422, "Preuve CRM requise")
            if account.hubspot_status == "unknown":
                account.hubspot_status = "signal"
        fresh = []
        for lead in review.leads:
            if d.normalize_domain(lead.domain) != account.domain:
                raise HTTPException(422, "Interlocuteur hors de l’entreprise examinée")
            if not db.scalar(
                select(m.Contact.id).where(
                    m.Contact.account_id == account.id,
                    func.lower(m.Contact.first_name) == lead.first_name.lower(),
                    func.lower(m.Contact.last_name) == lead.last_name.lower(),
                )
            ):
                fresh.append(lead)
        created = (
            import_leads(db, SimpleNamespace(campaign_id=campaign_id, payload={"limit": 3}), fresh)
            if fresh
            else []
        )
        if assessment:
            assessment.facts = {
                **assessment.facts,
                **{k: v for k, v in facts.items() if k.startswith("agent_")},
            }
        if campaign_id == profile.campaign_id:
            profile.facts = facts
        else:
            profile.facts = {
                **profile.facts,
                **{k: v for k, v in facts.items() if not k.startswith("agent_") and k != "checks"},
            }
        profile.error = ""
        evaluate(db, profile)
        if assessment:
            assessment = save_assessment(db, account, profile, campaign_id)
        results.append(
            {
                "account_id": account.id,
                "decision": review.decision,
                "contacts_created": len(created),
                "score": assessment.score if assessment else profile.score,
            }
        )
    return results
