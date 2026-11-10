from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import (
    acquisition,
    activity,
    agent_actions,
    agent_budget,
    autopilot,
    call_reports,
    calls,
    campaign_groups,
    conversations,
    email_policy,
    inbox,
    languages,
    lists,
    missions,
    native_warmup,
    offers,
    operations,
    phone_routing,
    privacy,
    prospect_pipeline,
    prospects,
    runtime_ops,
    senders,
    sequence_editor,
    sourcing,
    warmup,
    workspaces,
)
from . import domain as d
from . import models as m
from . import schemas as s
from .config import settings
from .db import SessionLocal, get_db
from .security import admin, current_user, decrypt_config, digest, new_session, password_valid


@asynccontextmanager
async def lifespan(app):
    cfg = settings()
    if cfg.environment == "production" and (not cfg.cookie_secure or not cfg.encryption_key):
        raise RuntimeError("Production requires secure cookies and an encryption key.")
    with SessionLocal() as db:
        from .workspace import initialize

        initialize(db)
        d.state(db, lock=True)
        senders.migrate_legacy(db)
        db.commit()
    yield


app = FastAPI(title="Lumelia Outreach", version="0.1.0", lifespan=lifespan)

app.include_router(acquisition.router)
app.include_router(senders.router)
app.include_router(sourcing.router)
app.include_router(languages.router)
app.include_router(warmup.router)
app.include_router(native_warmup.router)
app.include_router(autopilot.router)
app.include_router(privacy.router)
app.include_router(campaign_groups.router)
app.include_router(prospect_pipeline.router)
app.include_router(lists.router)
app.include_router(workspaces.router)
app.include_router(sequence_editor.router)
app.include_router(inbox.router)

app.include_router(conversations.router)
app.include_router(operations.router)
app.include_router(agent_actions.router)
app.include_router(agent_budget.router)
app.include_router(prospects.router)
app.include_router(phone_routing.router)
app.include_router(calls.router)
app.include_router(call_reports.router)
app.include_router(missions.router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.url.path.startswith(
        ("/api/webhooks/", "/api/unsubscribe/")
    ):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in settings().origins:
            return JSONResponse({"detail": "Origine non autorisée."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(IntegrityError)
async def integrity_error(request, exc):
    return JSONResponse(
        {"detail": "Une fiche existe déjà ou une référence est invalide. Rechargez les données."},
        status_code=409,
    )


def issue_session(db, response, user):
    token, session = new_session(db, user)
    db.commit()
    response.set_cookie(
        "lumelia_session",
        token,
        httponly=True,
        secure=settings().cookie_secure,
        samesite="strict",
        max_age=settings().session_hours * 3600,
        path="/",
    )
    return {"user": {"id": "workspace", "name": "Lumelia", "role": "admin"}, "csrf_token": session.csrf_token}


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "version": app.version}


@app.get("/api/auth/status")
def auth_status():
    return {"setup_required": False, "access": "password"}


@app.post("/api/auth/login")
def login(data: s.Login, request: Request, response: Response, db: Session = Depends(get_db)):
    bucket = digest(request.client.host + "|workspace")
    failures = db.scalar(
        select(func.count())
        .select_from(m.Event)
        .where(
            m.Event.kind == "auth_failed",
            m.Event.summary == bucket,
            m.Event.created_at > m.now() - timedelta(minutes=15),
        )
    )
    if (failures or 0) >= 8:
        raise HTTPException(429, "Trop de tentatives. Réessayez dans quinze minutes.")
    user = db.get(m.User, "workspace")
    if not user or not password_valid(data.password, user.password_hash):
        d.log(db, "auth_failed", summary=bucket)
        db.commit()
        raise HTTPException(401, "Mot de passe incorrect.")
    return issue_session(db, response, user)


@app.get("/api/auth/me")
def me(request: Request, user=Depends(current_user)):
    return {
        "user": {"id": "workspace", "name": "Lumelia", "role": "admin"},
        "csrf_token": request.state.login_session.csrf_token,
    }


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user=Depends(current_user), db: Session = Depends(get_db)):
    db.delete(request.state.login_session)
    db.commit()
    response.delete_cookie("lumelia_session", path="/")
    return {"ok": True}


@app.get("/api/offers")
def offer_catalogue(user=Depends(current_user), db: Session = Depends(get_db)):
    return [d.serialize(x) for x in db.scalars(select(m.Offer).order_by(m.Offer.sort_order, m.Offer.name))]


@app.get("/api/icps")
def icps(user=Depends(current_user), db: Session = Depends(get_db)):
    return [d.serialize(x) for x in db.scalars(select(m.ICP).order_by(m.ICP.created_at.desc()))]


@app.post("/api/icps", status_code=201)
def create_icp(data: s.ICPCreate, user=Depends(admin), db: Session = Depends(get_db)):
    row = m.ICP(**offers.icp_values(db, data))
    db.add(row)
    db.commit()
    return d.serialize(row)


@app.post("/api/icps/{icp_id}/versions", status_code=201)
def version_icp(icp_id: str, data: s.ICPCreate, user=Depends(admin), db: Session = Depends(get_db)):
    old = d.get(db, m.ICP, icp_id)
    row = m.ICP(**offers.icp_values(db, data), version=old.version + 1, parent_id=old.id)
    db.add(row)
    db.commit()
    return d.serialize(row)


@app.get("/api/campaigns")
def campaigns(user=Depends(current_user), db: Session = Depends(get_db)):
    return campaign_groups.campaign_list(db)


@app.post("/api/campaigns", status_code=201)
def create_campaign(data: s.CampaignCreate, user=Depends(admin), db: Session = Depends(get_db)):
    values = campaign_groups.campaign_values(db, data)
    if data.owner_id:
        d.get(db, m.User, data.owner_id)
    values["owner_id"] = data.owner_id or user.id
    row = m.Campaign(**values)
    db.add(row)
    db.commit()
    return d.serialize(row)


@app.get("/api/accounts")
def accounts(user=Depends(current_user), db: Session = Depends(get_db)):
    contact_counts = dict(
        db.execute(select(m.Contact.account_id, func.count()).group_by(m.Contact.account_id)).tuples().all()
    )
    evidence_counts = dict(
        db.execute(select(m.Evidence.account_id, func.count()).group_by(m.Evidence.account_id)).tuples().all()
    )
    return [
        {
            **d.serialize(row),
            "contact_count": contact_counts.get(row.id, 0),
            "evidence_count": evidence_counts.get(row.id, 0),
        }
        for row in db.scalars(select(m.Account).order_by(m.Account.name).limit(1000))
    ]


@app.post("/api/accounts", status_code=201)
def create_account(data: s.AccountCreate, user=Depends(current_user), db: Session = Depends(get_db)):
    row, created = d.create_account(db, data)
    db.commit()
    return {**d.serialize(row), "new": created}


@app.get("/api/accounts/{account_id}")
def account_detail(account_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    account = d.get(db, m.Account, account_id)
    return {
        **d.serialize(account),
        "evidence": [
            d.serialize(row)
            for row in db.scalars(
                select(m.Evidence)
                .where(m.Evidence.account_id == account.id)
                .order_by(m.Evidence.observed_at.desc())
            )
        ],
        "contacts": [
            {"id": row.id, "first_name": row.first_name, "last_name": row.last_name, "role": row.role}
            for row in db.scalars(select(m.Contact).where(m.Contact.account_id == account.id))
        ],
    }


@app.post("/api/accounts/{account_id}/evidence", status_code=201)
def add_account_evidence(
    account_id: str, data: s.EvidenceCreate, user=Depends(current_user), db: Session = Depends(get_db)
):
    account = d.get(db, m.Account, account_id)
    d.validate_url(data.source_url)
    # Replaying an applied review must not duplicate the company's evidence.
    existing = db.scalar(
        select(m.Evidence)
        .where(
            m.Evidence.account_id == account.id,
            m.Evidence.contact_id.is_(None),
            m.Evidence.field == data.field,
            m.Evidence.value == data.value,
            m.Evidence.source_url == data.source_url,
            m.Evidence.source_type == data.source_type,
            m.Evidence.confidence == data.confidence,
        )
        .limit(1)
    )
    if existing:
        return d.serialize(existing)
    row = m.Evidence(account_id=account.id, **data.model_dump())
    db.add(row)
    d.log(db, "account_evidence_added", actor_id=user.id, summary=data.field, data={"account_id": account.id})
    db.commit()
    return d.serialize(row)


def contact_view(db, row):
    return {
        **d.serialize(row),
        "account": d.serialize(db.get(m.Account, row.account_id)),
        "communication_language": languages.reviewed(db, row),
        "eligibility": {
            channel: d.eligibility(db, row, channel) for channel in ["email", "phone", "linkedin"]
        },
        "fit": d.score_contact(db, row),
    }


@app.get("/api/contacts")
def contacts(
    q: str = "",
    campaign_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(m.Contact).join(m.Account, m.Contact.account_id == m.Account.id)
    if q:
        query = query.where(
            (m.Contact.first_name + " " + m.Contact.last_name + " " + m.Account.name).ilike(
                "%" + q[:100] + "%"
            )
        )
    if campaign_id:
        query = query.where(m.Contact.campaign_id == campaign_id)
    if status:
        query = query.where(m.Contact.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(
        query.order_by(m.Contact.created_at.desc()).limit(min(max(limit, 1), 200)).offset(max(offset, 0))
    ).all()
    return {"items": [contact_view(db, x) for x in rows], "total": total}


@app.post("/api/contacts", status_code=201)
def create_contact(data: s.ContactCreate, user=Depends(current_user), db: Session = Depends(get_db)):
    row, created = d.create_contact(db, data, user.id)
    db.commit()
    return {**contact_view(db, row), "new": created}


@app.get("/api/contacts/{contact_id}")
def contact_detail(contact_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    row = d.get(db, m.Contact, contact_id)
    return {
        **contact_view(db, row),
        "evidence": [
            d.serialize(x)
            for x in db.scalars(
                select(m.Evidence)
                .where(m.Evidence.contact_id == row.id)
                .order_by(m.Evidence.observed_at.desc())
            )
        ],
        "events": [
            d.serialize(x)
            for x in db.scalars(
                select(m.Event)
                .where(m.Event.contact_id == row.id)
                .order_by(m.Event.created_at.desc())
                .limit(100)
            )
        ],
    }


@app.patch("/api/contacts/{contact_id}")
def edit_contact(
    contact_id: str, data: s.ContactEdit, user=Depends(current_user), db: Session = Depends(get_db)
):
    row = d.get(db, m.Contact, contact_id)
    fields = data.model_dump(exclude_unset=True)
    for key, value in fields.items():
        if value is None and key not in {"email", "campaign_id", "owner_id"}:
            raise HTTPException(422, "Ce champ ne peut pas être nul.")
    if "email" in fields:
        fields["email"] = str(fields["email"]).lower() if fields["email"] else None
        fields["email_status"] = "unknown"
        row.email_verified_at = None
    if "phone" in fields:
        fields["phone"] = d.normalize_phone(
            fields["phone"] or "", d.get(db, m.Account, row.account_id).country
        )
        fields["phone_status"] = "unknown"
        fields["phone_type"] = "unknown"
    for field in ("campaign_id", "owner_id"):
        if fields.get(field):
            d.get(db, m.Campaign if field == "campaign_id" else m.User, fields[field])
    if "linkedin_url" in fields:
        d.validate_url(fields["linkedin_url"] or "")
    if fields.get("email_status") == "valid":
        if not (fields.get("sourcing_note") or row.sourcing_note):
            raise HTTPException(422, "Documentez la vérification email.")
        row.email_verified_at = m.now()
    row.approved = False
    if row.status == "qualified":
        row.status = "candidate"
    d.cancel_pending(db, row, "Contact modifié : nouvelle validation requise")
    for key, value in fields.items():
        setattr(row, key, value)
    row.identity_key = (
        row.email
        or row.linkedin_url.strip().lower()
        or f"{row.first_name.strip().lower()}|{row.last_name.strip().lower()}"
    )
    if row.identity_key == "|":
        raise HTTPException(422, "Un nom ou une adresse est nécessaire.")
    d.log(
        db,
        "contact_updated",
        contact=row,
        actor_id=user.id,
        summary="Fiche modifiée ; validation réinitialisée.",
    )
    db.commit()
    return contact_view(db, row)


@app.post("/api/contacts/{contact_id}/evidence", status_code=201)
def add_evidence(
    contact_id: str, data: s.EvidenceCreate, user=Depends(current_user), db: Session = Depends(get_db)
):
    contact = d.get(db, m.Contact, contact_id)
    d.validate_url(data.source_url)
    row = m.Evidence(contact_id=contact.id, account_id=contact.account_id, **data.model_dump())
    db.add(row)
    d.log(db, "evidence_added", contact=contact, actor_id=user.id, summary=data.field)
    db.commit()
    return d.serialize(row)


@app.post("/api/contacts/{contact_id}/events")
def add_event(
    contact_id: str, data: s.ManualEvent, user=Depends(current_user), db: Session = Depends(get_db)
):
    contact = d.get(db, m.Contact, contact_id)
    result = d.receive_event(
        db,
        contact,
        data.kind,
        data.channel,
        data.summary,
        external_id=("manual:" + data.external_id) if data.external_id else None,
        actor_id=user.id,
    )
    db.commit()
    return result


@app.post("/api/batches/prepare")
def prepare_batch(data: s.Batch, user=Depends(current_user), db: Session = Depends(get_db)):
    count = sum(
        d.prepare_contact(db, d.get(db, m.Contact, key), user.id) for key in dict.fromkeys(data.contact_ids)
    )
    db.commit()
    return {"created": count}


@app.post("/api/batches/approve")
def approve_batch(data: s.Batch, user=Depends(admin), db: Session = Depends(get_db)):
    results = []
    for key in dict.fromkeys(data.contact_ids):
        contact = d.get(db, m.Contact, key)
        d.approve_contact(db, contact, user.id)
        results.append(key)
    db.commit()
    return {"approved": results}


@app.get("/api/messages")
def messages(
    contact_id: str | None = None,
    campaign_id: str | None = None,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(m.Message)
    if contact_id:
        query = query.where(m.Message.contact_id == contact_id)
    if campaign_id:
        query = query.where(m.Message.campaign_id == campaign_id)
    return [d.serialize(x) for x in db.scalars(query.order_by(m.Message.due_at).limit(1000))]


@app.post("/api/messages/{message_id}/approve")
def approve_message(message_id: str, user=Depends(admin), db: Session = Depends(get_db)):
    row = d.get(db, m.Message, message_id)
    if row.purpose == "reply":
        raise HTTPException(409, "Modifiez et envoyez cette réponse depuis la conversation.")
    if row.status != "draft":
        raise HTTPException(409, "Seul un brouillon peut être validé.")
    if email_policy.contains_price(row.subject, row.body):
        raise HTTPException(422, email_policy.REASON)
    contact = d.get(db, m.Contact, row.contact_id)
    reasons = d.eligibility(db, contact, "email")
    if reasons:
        raise HTTPException(409, reasons)
    campaign = d.get(db, m.Campaign, row.campaign_id)
    if campaign.mode == "live":
        sender = senders.assign(db, contact)
        blocking = senders.readiness(sender)
        if blocking:
            raise HTTPException(409, blocking)
        row.sender_id, row.sender_version = sender.id, sender.config_version
    row.status, row.approved_by = "approved", user.id
    d.log(db, "message_approved", contact=contact, actor_id=user.id, data={"message_id": row.id})
    db.commit()
    return d.serialize(row)


@app.get("/api/tasks")
def tasks(
    status: str | None = None,
    channel: str | None = None,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(m.Task)
    if status:
        query = query.where(m.Task.status == status)
    if channel:
        query = query.where(m.Task.channel == channel)
    return [
        {**d.serialize(x), "contact": contact_view(db, d.get(db, m.Contact, x.contact_id))}
        for x in db.scalars(query.order_by(m.Task.due_at).limit(1000))
    ]


@app.post("/api/tasks", status_code=201)
def create_task(data: s.TaskCreate, user=Depends(current_user), db: Session = Depends(get_db)):
    contact = d.get(db, m.Contact, data.contact_id)
    if data.due_at.tzinfo is None:
        raise HTTPException(422, "Une date avec fuseau horaire est requise.")
    if data.channel != "review":
        reasons = d.eligibility(db, contact, data.channel)
        if reasons:
            raise HTTPException(409, reasons)
    if data.owner_id:
        d.get(db, m.User, data.owner_id)
    row = m.Task(
        **{**data.model_dump(), "owner_id": data.owner_id or user.id}, campaign_id=contact.campaign_id
    )
    db.add(row)
    db.commit()
    return d.serialize(row)


@app.post("/api/tasks/{task_id}/complete")
def complete_task(
    task_id: str, data: s.TaskComplete, user=Depends(current_user), db: Session = Depends(get_db)
):
    task = activity.complete(db, task_id, data, user.id)
    db.commit()
    return d.serialize(task)


@app.get("/api/suppressions")
def suppressions(user=Depends(current_user), db: Session = Depends(get_db)):
    return [
        d.serialize(x)
        for x in db.scalars(select(m.Suppression).order_by(m.Suppression.created_at.desc()).limit(1000))
    ]


@app.post("/api/suppressions", status_code=201)
def suppress(data: s.SuppressionCreate, user=Depends(current_user), db: Session = Depends(get_db)):
    row = d.add_suppression(db, **data.model_dump(), actor_id=user.id)
    db.commit()
    return d.serialize(row)


@app.get("/api/opportunities")
def opportunities(user=Depends(current_user), db: Session = Depends(get_db)):
    return [
        d.serialize(x) for x in db.scalars(select(m.Opportunity).order_by(m.Opportunity.updated_at.desc()))
    ]


@app.post("/api/opportunities", status_code=201)
def create_opportunity(data: s.OpportunityCreate, user=Depends(current_user), db: Session = Depends(get_db)):
    contact = d.get(db, m.Contact, data.contact_id)
    existing = db.scalar(
        select(m.Opportunity).where(
            m.Opportunity.account_id == contact.account_id, m.Opportunity.stage.not_in(["won", "lost"])
        )
    )
    if existing:
        raise HTTPException(409, "Une opportunité est déjà ouverte pour cette entreprise.")
    values = data.model_dump()
    if values["amount_cents"] is None:
        campaign = db.get(m.Campaign, contact.campaign_id) if contact.campaign_id else None
        offer = db.get(m.Offer, campaign.offer_id) if campaign else None
        values["amount_cents"] = offer.pricing[0]["min_cents"] if offer and offer.pricing else 0
    row = m.Opportunity(
        **values, account_id=contact.account_id, campaign_id=contact.campaign_id, owner_id=user.id
    )
    db.add(row)
    d.pause_conversation(db, contact, "Opportunité active")
    d.log(db, "opportunity_created", contact=contact, actor_id=user.id, summary=data.title)
    db.commit()
    return d.serialize(row)


@app.patch("/api/opportunities/{opportunity_id}")
def edit_opportunity(
    opportunity_id: str, data: s.OpportunityEdit, user=Depends(current_user), db: Session = Depends(get_db)
):
    row = d.get(db, m.Opportunity, opportunity_id)
    fields = data.model_dump(exclude_unset=True)
    for key, value in fields.items():
        if value is None and key not in {"meeting_at", "next_action_at"}:
            raise HTTPException(422, "Ce champ ne peut pas être vide.")
        setattr(row, key, value)
    if row.stage == "lost" and not row.lost_reason:
        raise HTTPException(422, "Documentez la raison de perte.")
    if row.deposit_cents > row.amount_cents:
        raise HTTPException(422, "L’acompte ne peut dépasser le montant de l’opportunité.")
    if row.stage == "won":
        d.get(db, m.Account, row.account_id).lifecycle = "customer"
    d.log(
        db,
        "opportunity_updated",
        campaign_id=row.campaign_id,
        actor_id=user.id,
        data={"opportunity_id": row.id, "stage": row.stage},
    )
    db.commit()
    return d.serialize(row)


@app.get("/api/state")
def app_state(user=Depends(current_user), db: Session = Depends(get_db)):
    row = d.state(db)
    return {
        **d.serialize(row),
        "live_actions_enabled": settings().live_actions_enabled,
        "daily_limit_mode": row.settings.get("daily_limit_mode", "manual"),
        "effective_daily_limit": senders.global_limit(db, row),
    }


@app.patch("/api/state")
def update_state(data: s.StateEdit, user=Depends(admin), db: Session = Depends(get_db)):
    row = d.state(db, lock=True)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise HTTPException(422, "Valeur requise.")
        if key == "daily_limit_mode":
            row.settings = {**row.settings, "daily_limit_mode": value}
        else:
            setattr(row, key, value)
    d.log(db, "settings_updated", actor_id=user.id, data=data.model_dump(exclude_unset=True))
    db.commit()
    return d.serialize(row)


@app.get("/api/expenses")
def expenses(user=Depends(current_user), db: Session = Depends(get_db)):
    return [
        d.serialize(x)
        for x in db.scalars(select(m.Expense).order_by(m.Expense.created_at.desc()).limit(1000))
    ]


@app.post("/api/expenses", status_code=201)
def create_expense(data: s.ExpenseCreate, user=Depends(admin), db: Session = Depends(get_db)):
    if data.campaign_id:
        d.get(db, m.Campaign, data.campaign_id)
    row = m.Expense(**data.model_dump(), status="spent")
    db.add(row)
    db.commit()
    return d.serialize(row)


@app.get("/api/jobs")
def jobs(user=Depends(current_user), db: Session = Depends(get_db)):
    return [d.serialize(x) for x in db.scalars(select(m.Job).order_by(m.Job.created_at.desc()).limit(500))]


@app.get("/api/dashboard")
def dashboard(
    campaign_id: str | None = None,
    group_id: str | None = None,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if campaign_id and group_id:
        raise HTTPException(422, "Choisissez une campagne principale ou une sous-campagne.")
    scope_ids = [campaign_id] if campaign_id else None
    if group_id:
        campaign_groups.get_group(db, group_id)
        scope_ids = list(db.scalars(select(m.Campaign.id).where(m.Campaign.group_id == group_id)))

    def count(model, *conditions):
        query = select(func.count()).select_from(model).where(*conditions)
        if scope_ids is not None:
            query = query.where(model.campaign_id.in_(scope_ids))
        return db.scalar(query)

    def amount(column, *conditions):
        query = select(func.coalesce(func.sum(column), 0)).where(*conditions)
        if scope_ids is not None:
            query = query.where(column.class_.campaign_id.in_(scope_ids))
        return db.scalar(query)

    def responders(*kinds):
        query = select(func.count(func.distinct(m.Event.contact_id))).where(m.Event.kind.in_(kinds))
        if scope_ids is not None:
            query = query.where(m.Event.campaign_id.in_(scope_ids))
        return db.scalar(query)

    cost = amount(m.Expense.amount_cents, m.Expense.status.in_(["spent", "estimated"]))
    won_accounts = select(func.count(func.distinct(m.Opportunity.account_id))).where(
        m.Opportunity.stage == "won"
    )
    if scope_ids is not None:
        won_accounts = won_accounts.where(m.Opportunity.campaign_id.in_(scope_ids))
    customers = db.scalar(won_accounts)
    shared_cost = db.scalar(
        select(func.coalesce(func.sum(m.Expense.amount_cents), 0)).where(
            m.Expense.campaign_id.is_(None), m.Expense.status.in_(["spent", "estimated"])
        )
    )
    held = count(m.Opportunity, m.Opportunity.meeting_held.is_(True))
    qualified = count(m.Opportunity, m.Opportunity.meeting_held.is_(True), m.Opportunity.qualified.is_(True))
    return {
        "contacts": count(m.Contact),
        "valid_emails": count(
            m.Contact,
            m.Contact.email_status == "valid",
            m.Contact.email_verified_at >= m.now() - timedelta(days=90),
        ),
        "calls_completed": count(m.Task, m.Task.channel == "phone", m.Task.status == "completed"),
        "overdue_tasks": count(m.Task, m.Task.status == "pending", m.Task.due_at < m.now()),
        "approved_contacts": count(m.Contact, m.Contact.approved.is_(True)),
        "sent": count(m.Message, m.Message.status == "sent"),
        "simulated": count(m.Message, m.Message.status == "simulated"),
        "replies": responders("reply", "positive_reply"),
        "positive_replies": responders("positive_reply"),
        "bounces": count(m.Event, m.Event.kind == "bounce"),
        "meetings_held": held,
        "qualified_meetings": qualified,
        "won": count(m.Opportunity, m.Opportunity.stage == "won"),
        "deposits_cents": amount(m.Opportunity.deposit_cents),
        "cost_cents": cost,
        "customers_won": customers,
        "cost_per_customer_cents": round(cost / customers) if customers else None,
        "shared_cost_cents": shared_cost,
        "reserved_cents": amount(m.Expense.amount_cents, m.Expense.status == "reserved"),
        "cost_per_qualified_meeting_cents": round(cost / qualified) if qualified else None,
        "pending_tasks": count(m.Task, m.Task.status == "pending"),
        "failed_jobs": count(m.Job, m.Job.status.in_(["failed", "uncertain"])),
    }


@app.post("/api/import/csv")
async def import_contacts(
    request: Request,
    campaign_id: str | None = None,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if campaign_id:
        d.get(db, m.Campaign, campaign_id)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 2_000_000:
            raise HTTPException(413, "Le fichier dépasse 2 Mo.")
    try:
        content = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "Exportez le CSV en UTF-8.") from None
    result = d.import_csv(db, content, campaign_id, user.id)
    db.commit()
    return result


@app.post("/api/campaigns/{campaign_id}/approve-budget")
def approve_budget(campaign_id: str, user=Depends(admin), db: Session = Depends(get_db)):
    row = d.get(db, m.Campaign, campaign_id)
    row.budget_approved = True
    d.log(
        db, "budget_approved", campaign_id=row.id, actor_id=user.id, data={"budget_cents": row.budget_cents}
    )
    db.commit()
    return d.serialize(row)


@app.post("/api/campaigns/{campaign_id}/activate")
def activate_campaign(campaign_id: str, user=Depends(admin), db: Session = Depends(get_db)):
    row = db.scalar(select(m.Campaign).where(m.Campaign.id == campaign_id).with_for_update())
    if not row:
        raise HTTPException(404, "Campagne introuvable.")
    offers.validate_campaign(db, row)
    if any(
        email_policy.contains_price(step.get("subject"), step.get("body"))
        for step in row.sequence
        if step["channel"] == "email"
    ):
        raise HTTPException(422, email_policy.REASON)
    if not row.sequence:
        raise HTTPException(422, "Ajoutez une séquence.")
    if row.mode == "live":
        if not settings().live_actions_enabled or row.transport != "smtp":
            raise HTTPException(409, "Le transport réel SMTP n’est pas activé sur ce serveur.")
        if not row.budget_approved:
            raise HTTPException(409, "Validez le budget avant activation.")
        if not settings().public_url.startswith("https://"):
            raise HTTPException(409, "L’URL publique doit utiliser HTTPS.")
        for message in db.scalars(
            select(m.Message).where(m.Message.campaign_id == row.id, m.Message.status == "approved")
        ):
            sender = db.get(m.SendingMailbox, message.sender_id) if message.sender_id else None
            if not sender or message.sender_version != sender.config_version:
                raise HTTPException(409, "Attribuez un expéditeur puis validez de nouveau le message.")
            reasons = senders.readiness(sender, reception=True)
            if reasons:
                raise HTTPException(409, reasons)
    if row.status == "draft":
        for contact in db.scalars(select(m.Contact).where(m.Contact.campaign_id == row.id)):
            first = db.scalar(
                select(func.min(m.Message.due_at)).where(
                    m.Message.contact_id == contact.id,
                    m.Message.campaign_id == row.id,
                    m.Message.status.in_(["draft", "approved"]),
                )
            )
            if first:
                d.shift_sequence(db, contact.id, row.id, m.now() - first)
    row.status, row.approved_by, row.approved_at = "active", user.id, m.now()
    for message in db.scalars(
        select(m.Message).where(m.Message.campaign_id == row.id, m.Message.status == "approved")
    ):
        contact = d.get(db, m.Contact, message.contact_id)
        if d.eligibility(db, contact, "email"):
            continue
        key = f"send:{message.id}"
        existing = db.scalar(select(m.Job).where(m.Job.idempotency_key == key))
        if not existing:
            db.add(
                m.Job(
                    kind="send",
                    provider=row.transport,
                    contact_id=contact.id,
                    campaign_id=row.id,
                    message_id=message.id,
                    idempotency_key=key,
                    run_at=message.due_at,
                )
            )
        elif existing.status == "cancelled":
            existing.status, existing.run_at, existing.error = "queued", message.due_at, ""
        else:
            raise HTTPException(409, "Une opération existe déjà pour ce message : rapprochez son résultat.")
        message.status = "queued"
    for task in db.scalars(select(m.Task).where(m.Task.campaign_id == row.id, m.Task.status == "draft")):
        if not d.eligibility(db, d.get(db, m.Contact, task.contact_id), task.channel):
            task.status = "pending"
    d.log(
        db,
        "campaign_activated",
        campaign_id=row.id,
        actor_id=user.id,
        summary="Campagne activée : " + row.mode,
    )
    db.commit()
    return d.serialize(row)


@app.post("/api/campaigns/{campaign_id}/pause")
def pause_campaign(campaign_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    row = d.get(db, m.Campaign, campaign_id)
    row.status = "paused"
    d.log(db, "campaign_paused", campaign_id=row.id, actor_id=user.id)
    db.commit()
    return d.serialize(row)


@app.get("/api/integrations")
def integrations(user=Depends(admin), db: Session = Depends(get_db)):
    return [d.serialize(x) for x in db.scalars(select(m.Integration).order_by(m.Integration.provider))]


@app.post("/api/backups")
def download_backup(user=Depends(admin), db: Session = Depends(get_db)):
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    from scripts.backup import backup

    recent = db.scalar(
        select(m.Event.id)
        .where(m.Event.kind == "backup_started", m.Event.created_at > m.now() - timedelta(minutes=5))
        .limit(1)
    )
    if recent:
        raise HTTPException(429, "Une sauvegarde a été demandée récemment. Attendez cinq minutes.")
    d.log(db, "backup_started", actor_id=user.id, summary="Export chiffré de l’espace")
    db.commit()
    directory = tempfile.mkdtemp(prefix="lumelia-backup-")
    filename = "lumelia-" + m.now().strftime("%Y%m%d-%H%M%S") + ".dump.fernet"
    path = Path(directory) / filename
    try:
        backup(path)
    except (subprocess.SubprocessError, OSError):
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(
            503, "La sauvegarde a échoué. Vérifiez la connexion et la version du client PostgreSQL."
        ) from None
    return FileResponse(
        path,
        filename=filename,
        media_type="application/octet-stream",
        background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True),
    )


@app.post("/api/integrations/{provider}")
def save_integration(
    provider: str, data: s.IntegrationSave, user=Depends(admin), db: Session = Depends(get_db)
):
    from .security import encrypt_config

    if provider not in {"apollo", "fullenrich", "hunter", "smtp", "lemlist", "apify"}:
        raise HTTPException(422, "Fournisseur non pris en charge dans cette version.")
    if provider != "smtp" and (
        set(data.config) != {"api_key"}
        or not isinstance(data.config.get("api_key"), str)
        or not data.config["api_key"].strip()
    ):
        raise HTTPException(422, "Une clé API est requise.")
    if provider == "smtp":
        raise HTTPException(
            409, "Gérez les connexions SMTP dans Boîtes d’envoi, avec leurs plafonds et leur préparation."
        )
    row = db.scalar(select(m.Integration).where(m.Integration.provider == provider))
    if not row:
        row = m.Integration(provider=provider)
        db.add(row)
    if provider == "apify" and row.encrypted_config:
        acquisition.bind_previous_account(db, row)
    row.encrypted_config = encrypt_config(data.config)
    row.unit_cost_cents, row.monthly_limit_cents = data.unit_cost_cents, data.monthly_limit_cents
    row.status, row.last_checked_at = "configured", None
    row.state = {}
    d.log(db, "integration_saved", actor_id=user.id, summary=provider)
    db.commit()
    return d.serialize(row)


@app.post("/api/integrations/{provider}/check")
def check_integration(provider: str, user=Depends(admin), db: Session = Depends(get_db)):
    from .providers import ProviderError, verify_connection
    from .security import decrypt_config

    row = db.scalar(select(m.Integration).where(m.Integration.provider == provider))
    if not row:
        raise HTTPException(404, "Configurez d’abord ce fournisseur.")
    # Explicit authenticated connection checks never send mail or purchase data.
    # They stay available while all campaign and billable actions are disabled.
    try:
        if provider == "smtp":
            from .mail import verify

            result = verify(decrypt_config(row.encrypted_config))
        else:
            result = verify_connection(provider, decrypt_config(row.encrypted_config))
        row.status = "authenticated" if result.get("authenticated") else "error"
    except ProviderError as exc:
        row.status = "error"
        result = {"error": str(exc)}
    row.last_checked_at = m.now()
    row.state = {**(row.state or {}), "checked_at": row.last_checked_at.isoformat()}
    row.state.pop("quota", None)
    row.state.pop("connection_error", None)
    row.state.pop("limitation", None)
    if result.get("account_bucket"):
        row.state = {**row.state, "account_bucket": result["account_bucket"]}
    if "capabilities" in result:
        row.state = {**row.state, "capabilities": result["capabilities"]}
    if result.get("limitation"):
        row.state = {**row.state, "limitation": result["limitation"]}
    if result.get("quota"):
        row.state = {**row.state, "quota": result["quota"]}
    if result.get("error"):
        row.state = {**row.state, "connection_error": result["error"]}
    db.commit()
    return {**d.serialize(row), "check": result}


@app.post("/api/enrichments", status_code=201)
def queue_enrichment(data: s.EnrichBatch, user=Depends(admin), db: Session = Depends(get_db)):
    import hashlib
    import json

    if not settings().live_actions_enabled:
        raise HTTPException(
            409,
            "L’enrichissement externe est désactivé. Configurez et autorisez les accès avant de consommer des crédits.",
        )
    integration = db.scalar(select(m.Integration).where(m.Integration.provider == data.provider))
    if not integration:
        raise HTTPException(422, "Configurez le fournisseur.")
    if not data.fields:
        raise HTTPException(422, "Choisissez email ou téléphone.")
    fields = sorted(set(data.fields))
    if data.provider in {"hunter", "apollo"} and "phone" in fields:
        raise HTTPException(422, "Utilisez FullEnrich pour l’enrichissement téléphone de cette version.")
    jobs = []
    for key in dict.fromkeys(data.contact_ids):
        contact = d.get(db, m.Contact, key)
        campaign = d.get(db, m.Campaign, contact.campaign_id)
        account = d.get(db, m.Account, contact.account_id)
        if campaign.mode != "live":
            raise HTTPException(
                409, "L’achat de coordonnées nécessite une campagne en mode réel et son budget validé."
            )
        if (
            contact.paused
            or account.outreach_paused
            or account.lifecycle != "prospect"
            or d.is_suppressed(db, contact)
        ):
            raise HTTPException(409, "Contact exclu ou en pause.")
        if not contact.role or not contact.professional_relevance:
            raise HTTPException(409, "Vérifiez le rôle et le ciblage avant d’acheter des coordonnées.")
        if data.provider == "hunter" and not contact.email:
            raise HTTPException(422, "Hunter vérifie une adresse existante.")
        identity = {
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "domain": account.domain,
            "company_name": account.name,
            "linkedin_url": contact.linkedin_url,
            "email": contact.email,
        }
        fingerprint = hashlib.sha256(
            json.dumps({"identity": identity, "fields": fields}, sort_keys=True).encode()
        ).hexdigest()
        idempotency_key = f"enrich:{data.provider}:{contact.id}:{fingerprint}"
        existing = db.scalar(select(m.Job).where(m.Job.idempotency_key == idempotency_key))
        if existing:
            jobs.append(d.serialize(existing))
            continue
        job = m.Job(
            kind="enrich",
            provider=data.provider,
            contact_id=key,
            campaign_id=campaign.id,
            idempotency_key=idempotency_key,
            payload={"identity": identity, "fields": fields},
        )
        db.add(job)
        db.flush()
        d.reserve_budget(db, campaign, integration, job, integration.unit_cost_cents)
        jobs.append(d.serialize(job))
    db.commit()
    return jobs


@app.get("/api/unsubscribe/{token}")
def unsubscribe_page(token: str):
    import html

    from fastapi.responses import HTMLResponse

    from .mail import unsubscribe_contact

    if not unsubscribe_contact(token):
        raise HTTPException(404, "Lien invalide.")
    # GET never mutates: scanners cannot unsubscribe a contact by prefetching.
    return HTMLResponse(
        '<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Ne plus être contacté — Lumelia</title><body style="font:18px system-ui;max-width:560px;margin:60px auto;padding:24px"><h1>Ne plus être contacté par Lumelia</h1><p>Votre demande arrêtera nos prises de contact commerciales.</p><form method="post" action="/api/unsubscribe/'
        + html.escape(token, quote=True)
        + '"><button style="font:inherit;padding:14px" type="submit">Confirmer ma demande</button></form></body></html>'
    )


@app.post("/api/unsubscribe/{token}")
def unsubscribe(token: str, db: Session = Depends(get_db)):
    from fastapi.responses import HTMLResponse

    from .mail import unsubscribe_contact

    contact_id = unsubscribe_contact(token)
    if not contact_id:
        raise HTTPException(404, "Lien invalide.")
    d.state(db, lock=True)
    contact = db.get(m.Contact, contact_id)
    if contact:
        d.receive_event(
            db,
            contact,
            "stop",
            "email",
            "Désinscription via le lien Lumelia",
            external_id="unsubscribe:" + contact.id,
            source="unsubscribe",
        )
    db.commit()
    return HTMLResponse(
        '<!doctype html><html lang="fr"><meta charset="utf-8"><title>Demande enregistrée</title><body style="font:18px system-ui;max-width:560px;margin:60px auto;padding:24px"><h1>Votre demande est enregistrée.</h1><p>Vous ne recevrez plus de prospection de Lumelia via cette fiche.</p></body></html>'
    )


@app.patch("/api/messages/{message_id}")
def edit_message(
    message_id: str, data: s.MessageEdit, user=Depends(current_user), db: Session = Depends(get_db)
):
    row = d.get(db, m.Message, message_id)
    if row.purpose == "reply":
        raise HTTPException(409, "Modifiez et envoyez cette réponse depuis la conversation.")
    if row.status not in {"draft", "approved", "queued"}:
        raise HTTPException(409, "Ce message ne peut plus être modifié.")
    if "\n" in data.subject or "\r" in data.subject or "{{" in data.body or "{{" in data.subject:
        raise HTTPException(422, "Renseignez les variables et un objet sans retour à la ligne.")
    for job in db.scalars(select(m.Job).where(m.Job.message_id == row.id, m.Job.status == "queued")):
        job.status = "cancelled"
        job.error = "Message modifié ; nouvelle approbation requise."
    row.subject, row.body, row.status, row.approved_by = data.subject, data.body, "draft", None
    d.log(
        db,
        "message_edited",
        contact=d.get(db, m.Contact, row.contact_id),
        actor_id=user.id,
        data={"message_id": row.id},
    )
    db.commit()
    return d.serialize(row)


@app.post("/api/contacts/{contact_id}/resume")
def resume_contact(contact_id: str, data: s.Resume, user=Depends(admin), db: Session = Depends(get_db)):
    contact = d.get(db, m.Contact, contact_id)
    account = d.get(db, m.Account, contact.account_id)
    if account.lifecycle != "prospect" or any(
        d.is_suppressed(db, contact, ch) for ch in ["email", "phone", "linkedin"]
    ):
        raise HTTPException(
            409, "Un client, un compte exclu ou une opposition ne peut pas être réactivé par ce parcours."
        )
    if db.scalar(
        select(m.Opportunity.id).where(
            m.Opportunity.account_id == account.id, m.Opportunity.stage.not_in(["won", "lost"])
        )
    ):
        raise HTTPException(409, "Une opportunité est encore active sur cette entreprise.")
    if db.scalar(
        select(m.Message.id).where(
            m.Message.contact_id == contact.id, m.Message.status.in_(["sending", "uncertain"])
        )
    ):
        raise HTTPException(409, "Réconciliez l’envoi incertain avant toute reprise.")
    contact.paused, contact.approved, contact.status = False, False, "candidate"
    account.outreach_paused, account.pause_reason = False, ""
    messages = list(
        db.scalars(
            select(m.Message).where(m.Message.contact_id == contact.id, m.Message.status == "cancelled")
        )
    )
    tasks = list(
        db.scalars(select(m.Task).where(m.Task.contact_id == contact.id, m.Task.status == "cancelled"))
    )
    pending = [*messages, *tasks]
    # Shift the remaining sequence together; never collapse all overdue steps onto today.
    shift = max(timedelta(0), m.now() - min(x.due_at for x in pending)) if pending else timedelta(0)
    for message in messages:
        message.status, message.approved_by, message.failure_reason = "draft", None, ""
        message.due_at += shift
    for task in tasks:
        task.status = "draft"
        task.due_at += shift
    d.log(db, "resumed_for_review", contact=contact, actor_id=user.id, summary=data.reason)
    db.commit()
    return contact_view(db, contact)


@app.post("/api/jobs/{job_id}/reconcile")
def reconcile_job(job_id: str, data: s.Reconcile, user=Depends(admin), db: Session = Depends(get_db)):
    row = d.get(db, m.Job, job_id)
    if row.status not in {"uncertain", "failed"}:
        raise HTTPException(409, "Cette opération ne nécessite pas de rapprochement.")
    if row.kind in {"send", "reply"}:
        message = d.get(db, m.Message, row.message_id)
        if data.outcome == "accepted":
            row.status, message.status = "completed", "sent"
            message.sent_at = message.sent_at or m.now()
        elif data.outcome == "not_sent":
            row.status, message.status = "cancelled", "draft"
            message.approved_by = None
        else:
            raise HTTPException(422, "Choisissez accepté ou non envoyé.")
    elif (
        row.kind == "enrich"
        and row.provider == "fullenrich"
        and data.outcome == "received"
        and data.external_id
    ):
        row.external_id, row.status, row.run_at = data.external_id, "waiting", m.now()
    else:
        raise HTTPException(422, "Rapprochement incompatible : vérifiez le résultat dans le fournisseur.")
    d.log(
        db,
        "job_reconciled",
        campaign_id=row.campaign_id,
        actor_id=user.id,
        summary=data.evidence,
        data={"job_id": row.id, "outcome": data.outcome},
    )
    db.commit()
    return d.serialize(row)


@app.post("/api/discoveries", status_code=201)
def queue_discovery(data: s.Discovery, user=Depends(admin), db: Session = Depends(get_db)):
    import hashlib
    import json

    if not settings().live_actions_enabled:
        raise HTTPException(409, "Les recherches externes sont désactivées sur ce serveur.")
    campaign = d.get(db, m.Campaign, data.campaign_id)
    if campaign.mode != "live":
        raise HTTPException(409, "Sélectionnez une campagne réelle avec budget validé.")
    integration = db.scalar(select(m.Integration).where(m.Integration.provider == "apollo"))
    if not integration:
        raise HTTPException(422, "Configurez Apollo et un plafond par recherche.")
    allowed = {
        "person_titles",
        "person_locations",
        "person_seniorities",
        "organization_locations",
        "q_organization_domains_list",
        "organization_ids",
        "organization_num_employees_ranges",
        "currently_using_all_of_technology_uids",
        "q_keywords",
        "q_organization_name",
    }
    if not data.filters or set(data.filters) - allowed:
        raise HTTPException(422, "Définissez des filtres de ciblage pris en charge.")
    payload = data.model_dump()
    key = "discover:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    existing = db.scalar(select(m.Job).where(m.Job.idempotency_key == key))
    if existing:
        return d.serialize(existing)
    job = m.Job(
        kind="discover", provider="apollo", campaign_id=campaign.id, idempotency_key=key, payload=payload
    )
    db.add(job)
    db.flush()
    d.reserve_budget(db, campaign, integration, job, integration.unit_cost_cents)
    db.commit()
    return d.serialize(job)


@app.get("/api/contacts/{contact_id}/export")
def export_contact(contact_id: str, user=Depends(admin), db: Session = Depends(get_db)):
    contact = d.get(db, m.Contact, contact_id)
    result = {
        "contact": d.serialize(contact),
        "account": d.serialize(d.get(db, m.Account, contact.account_id)),
    }
    for name, model in [
        ("evidence", m.Evidence),
        ("messages", m.Message),
        ("tasks", m.Task),
        ("events", m.Event),
        ("opportunities", m.Opportunity),
    ]:
        result[name] = [
            d.serialize(row) for row in db.scalars(select(model).where(model.contact_id == contact.id))
        ]
    return JSONResponse(
        result, headers={"Content-Disposition": f'attachment; filename="contact-{contact.id}.json"'}
    )


@app.post("/api/contacts/{contact_id}/retention")
def set_retention(contact_id: str, data: s.Retention, user=Depends(admin), db: Session = Depends(get_db)):
    if data.retention_until.tzinfo is None:
        raise HTTPException(422, "Une date avec fuseau horaire est requise.")
    contact = d.get(db, m.Contact, contact_id)
    contact.retention_until = data.retention_until
    contact.approved = False
    if contact.status == "qualified":
        contact.status = "candidate"
    d.cancel_pending(db, contact, "Échéance de conservation modifiée : nouvelle revue requise")
    d.log(db, "retention_set", contact=contact, actor_id=user.id, summary=data.reason)
    db.commit()
    return d.serialize(contact)


@app.post("/api/contacts/{contact_id}/erase")
def erase_contact(contact_id: str, data: s.EraseContact, user=Depends(admin), db: Session = Depends(get_db)):
    contact = d.get(db, m.Contact, contact_id)
    if data.confirmation != "EFFACER":
        raise HTTPException(
            422, "Saisissez EFFACER pour confirmer la suppression de cette fiche et de son historique."
        )
    privacy.erase(db, contact, user.id)
    db.commit()
    return {
        "erased": True,
        "suppression_preserved": True,
        "note": "Fiche et historique liés effacés. Traiter séparément les copies externes, notes libres et sauvegardes.",
    }


@app.put("/api/accounts/{account_id}")
def edit_account(
    account_id: str, data: s.AccountCreate, user=Depends(current_user), db: Session = Depends(get_db)
):
    account = d.get(db, m.Account, account_id)
    fields = data.model_dump()
    fields["domain"] = d.normalize_domain(fields["domain"])
    for key, value in fields.items():
        setattr(account, key, value)
    for contact in db.scalars(select(m.Contact).where(m.Contact.account_id == account.id)):
        contact.approved = False
        if contact.status == "qualified":
            contact.status = "candidate"
        d.cancel_pending(db, contact, "Entreprise modifiée : nouvelle qualification requise")
    if account.lifecycle != "prospect":
        account.outreach_paused = True
        account.pause_reason = "Client ou entreprise exclue"
    d.log(
        db,
        "account_updated",
        actor_id=user.id,
        summary="Entreprise modifiée ; contacts à revalider.",
        data={"account_id": account.id},
    )
    db.commit()
    return d.serialize(account)


@app.post("/api/campaigns/{campaign_id}/versions", status_code=201)
def version_campaign(
    campaign_id: str, data: s.CampaignCreate, user=Depends(admin), db: Session = Depends(get_db)
):
    old = d.get(db, m.Campaign, campaign_id)
    values = campaign_groups.campaign_values(db, data, previous=old)
    if data.owner_id:
        d.get(db, m.User, data.owner_id)
    values["owner_id"] = data.owner_id or user.id
    values["config"] = {
        **values["config"],
        "parent_campaign_id": old.id,
        "sequence_version": old.config.get("sequence_version", 1) + 1,
    }
    row = m.Campaign(**values)
    db.add(row)
    db.flush()
    d.log(
        db,
        "sequence_version_created",
        campaign_id=row.id,
        actor_id=user.id,
        summary="Nouvelle version en brouillon ; campagnes et contacts antérieurs inchangés.",
        data={"parent_campaign_id": old.id},
    )
    db.commit()
    return d.serialize(row)


@app.get("/api/messages/{message_id}/preview")
def preview_message(message_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    from .mail import campaign_config, content

    message = d.get(db, m.Message, message_id)
    contact = d.get(db, m.Contact, message.contact_id)
    sender = (
        db.get(m.SendingMailbox, message.sender_id or contact.email_sender_id)
        if (message.sender_id or contact.email_sender_id)
        else None
    )
    if not sender:
        return {
            "subject": message.subject,
            "body": message.body,
            "complete": False,
            "note": "Attribuez une boîte à ce contact pour relire l’expéditeur et le pied d’email.",
        }
    config = decrypt_config(sender.encrypted_config)
    config = campaign_config(config, d.get(db, m.Campaign, message.campaign_id))
    body, _ = content(message, contact, config)
    complete = len(config.get("legal_footer", "").strip()) >= 20 and config.get("privacy_url", "").startswith(
        "https://"
    )
    return {
        "subject": message.subject,
        "body": body,
        "complete": complete,
        "note": "Complétez le pied d’email et la page d’information." if not complete else "",
        "from_email": config["from_email"],
        "reply_to": config.get("reply_to") or config["from_email"],
    }


@app.post("/api/discoveries/{job_id}/accept", status_code=201)
def accept_candidate(
    job_id: str, data: s.DiscoveryAccept, user=Depends(admin), db: Session = Depends(get_db)
):
    job = d.get(db, m.Job, job_id)
    rows = job.result.get("candidates", [])
    if (
        job.kind != "discover"
        or job.status != "completed"
        or data.index >= len(rows)
        or rows[data.index].get("erased")
    ):
        raise HTTPException(409, "Ce candidat n’est pas disponible pour la revue.")
    accepted = dict(job.result.get("accepted", {}))
    if str(data.index) in accepted:
        return accepted[str(data.index)]
    account, _ = d.create_account(db, s.AccountCreate(name=data.company, domain=data.domain))
    contact = None
    if job.payload["kind"] == "people":
        if not data.first_name.strip() or not data.last_name.strip() or "*" in data.last_name:
            raise HTTPException(
                422,
                "Vérifiez l’identité complète : un nom masqué ne doit pas devenir une identité confirmée.",
            )
        contact, _ = d.create_contact(
            db,
            s.ContactCreate(
                account_id=account.id,
                campaign_id=job.campaign_id,
                first_name=data.first_name,
                last_name=data.last_name,
                role=data.role,
                linkedin_url=data.linkedin_url,
                sourcing_note=data.review_note,
                source_type="provider",
                source_url="https://app.apollo.io",
            ),
            user.id,
        )
    db.add(
        m.Evidence(
            account_id=account.id,
            contact_id=contact.id if contact else None,
            field="discovery",
            value="Candidat Apollo revu : " + data.review_note,
            source_type="apollo",
            source_url="https://app.apollo.io",
            observed_at=job.created_at,
            confidence="signal",
        )
    )
    result = {"account_id": account.id, "contact_id": contact.id if contact else None}
    accepted[str(data.index)] = result
    job.result = {**job.result, "accepted": accepted}
    db.commit()
    return result


app.include_router(runtime_ops.router)
