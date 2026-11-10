import csv
import io
import re
from datetime import timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import phonenumbers
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import models as m
from .schemas import AccountCreate, ContactCreate


def get(db: Session, model, key):
    obj = db.get(model, key)
    if not obj:
        raise HTTPException(404, "Élément introuvable.")
    return obj


def serialize(obj):
    if obj is None:
        return None
    out = {}
    hidden = {"password_hash", "encrypted_config", "token_hash", "csrf_token", "identity_hash"}
    for col in obj.__table__.columns:
        if col.name not in hidden:
            value = getattr(obj, col.name)
            out[col.name] = value.isoformat() if hasattr(value, "isoformat") else value
    return out


def state(db, lock=False):
    query = select(m.AppState).where(m.AppState.id == "global")
    if lock:
        query = query.with_for_update()
    item = db.scalar(query)
    if not item:
        item = m.AppState(id="global")
        db.add(item)
        db.flush()
    return item


def normalize_domain(value):
    domain = (
        (urlparse(value if "://" in value else "https://" + value).hostname or "")
        .lower()
        .removeprefix("www.")
    )
    if not re.fullmatch(r"(?=.{3,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", domain):
        raise HTTPException(422, "Domaine d’entreprise invalide.")
    return domain


def normalize_phone(value, country="FR"):
    if not value:
        return ""
    try:
        number = phonenumbers.parse(value, country)
        if not phonenumbers.is_possible_number(number):
            raise ValueError()
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except (phonenumbers.NumberParseException, ValueError) as exc:
        raise HTTPException(422, "Numéro impossible à normaliser ; vérifiez le pays et le numéro.") from exc


def validate_url(value):
    if value and (urlparse(value).scheme not in {"https", "http"} or not urlparse(value).hostname):
        raise HTTPException(422, "URL de source invalide.")
    return value


def log(
    db,
    kind,
    *,
    contact=None,
    campaign_id=None,
    actor_id=None,
    summary="",
    channel="system",
    data=None,
    external_id=None,
    source="manual",
):
    if external_id:
        previous = db.scalar(select(m.Event).where(m.Event.external_id == external_id))
        if previous:
            return previous
    event = m.Event(
        kind=kind,
        contact_id=contact.id if contact else None,
        account_id=contact.account_id if contact else None,
        campaign_id=campaign_id or (contact.campaign_id if contact else None),
        actor_id=actor_id,
        summary=summary,
        channel=channel,
        data=data or {},
        external_id=external_id,
        source=source,
    )
    db.add(event)
    db.flush()
    return event


def create_account(db, data: AccountCreate):
    domain = normalize_domain(data.domain)
    account = db.scalar(select(m.Account).where(m.Account.domain == domain))
    if account:
        return account, False
    if data.siren:
        existing = db.scalar(select(m.Account).where(m.Account.siren == data.siren))
        if existing:
            raise HTTPException(
                409, "Ce SIREN existe avec un autre domaine : vérifiez l’identité avant fusion."
            )
    account = m.Account(**{**data.model_dump(), "domain": domain})
    db.add(account)
    db.flush()
    return account, True


def create_contact(db, data: ContactCreate, actor_id):
    account = get(db, m.Account, data.account_id)
    email = str(data.email).strip().lower() if data.email else None
    identity = (
        email
        or data.linkedin_url.strip().lower()
        or f"{data.first_name.strip().lower()}|{data.last_name.strip().lower()}"
    )
    if identity == "|":
        raise HTTPException(422, "Un nom ou une adresse est nécessaire.")
    previous = db.scalar(
        select(m.Contact).where(
            or_(
                m.Contact.email == email if email else False,
                (m.Contact.account_id == account.id) & (m.Contact.identity_key == identity),
            )
        )
    )
    if previous:
        if previous.account_id != account.id:
            raise HTTPException(409, "Cette adresse appartient à une autre fiche : vérifiez le rattachement.")
        return previous, False
    if data.campaign_id:
        get(db, m.Campaign, data.campaign_id)
    if data.owner_id:
        get(db, m.User, data.owner_id)
    validate_url(data.source_url)
    validate_url(data.linkedin_url)
    values = data.model_dump(exclude={"source_url", "source_type"})
    values.update(
        email=email,
        identity_key=identity,
        owner_id=data.owner_id or actor_id,
        phone=normalize_phone(data.phone, account.country),
        retention_until=m.now() + timedelta(days=365),
    )
    if data.email_status == "valid":
        if not data.source_url and not data.sourcing_note:
            raise HTTPException(422, "Documentez la vérification de cette adresse.")
        values["email_verified_at"] = m.now()
    contact = m.Contact(**values)
    db.add(contact)
    db.flush()
    for field, value in {
        "identity": f"{data.first_name} {data.last_name}".strip(),
        "email": email,
        "phone": contact.phone,
    }.items():
        if value:
            db.add(
                m.Evidence(
                    contact_id=contact.id,
                    account_id=account.id,
                    field=field,
                    value=value,
                    source_url=data.source_url,
                    source_type=data.source_type,
                )
            )
    log(
        db,
        "contact_created",
        contact=contact,
        actor_id=actor_id,
        summary="Contact ajouté ; qualification à vérifier.",
    )
    return contact, True


def import_csv(db, text, campaign_id, actor_id):
    if len(text.encode()) > 2_000_000:
        raise HTTPException(413, "Le fichier dépasse 2 Mo.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), dialect=dialect)
    aliases = {
        "societe": "company",
        "entreprise": "company",
        "company_name": "company",
        "website": "domain",
        "prenom": "first_name",
        "prénom": "first_name",
        "nom": "last_name",
        "poste": "role",
        "telephone": "phone",
        "téléphone": "phone",
        "source": "source_url",
    }
    report = {"created": 0, "duplicates": 0, "errors": [], "contact_ids": []}
    for index, raw in enumerate(reader, 2):
        if index > 1001:
            report["errors"].append({"line": index, "error": "Limite de 1 000 lignes atteinte."})
            break
        row = {
            aliases.get((k or "").strip().lower(), (k or "").strip().lower()): (v or "").strip()
            for k, v in raw.items()
            if k and not isinstance(v, list)
        }
        try:
            with db.begin_nested():
                account, _ = create_account(
                    db,
                    AccountCreate(
                        name=row.get("company", ""),
                        domain=row.get("domain", ""),
                        country=row.get("country") or "FR",
                        industry=row.get("industry", ""),
                    ),
                )
                contact, created = create_contact(
                    db,
                    ContactCreate(
                        account_id=account.id,
                        campaign_id=campaign_id,
                        first_name=row.get("first_name", ""),
                        last_name=row.get("last_name", ""),
                        role=row.get("role", ""),
                        email=row.get("email") or None,
                        phone=row.get("phone", ""),
                        linkedin_url=row.get("linkedin_url", ""),
                        source_url=row.get("source_url", ""),
                        source_type="csv",
                        sourcing_note=row.get("sourcing_note", ""),
                        professional_relevance=row.get("professional_relevance", ""),
                    ),
                    actor_id,
                )
                report["created" if created else "duplicates"] += 1
                report["contact_ids"].append(contact.id)
        except Exception as exc:
            reason = exc.detail if isinstance(exc, HTTPException) else "Champs invalides ou identité ambiguë."
            report["errors"].append({"line": index, "error": reason})
    return report


def is_suppressed(db, contact, channel="email"):
    account = get(db, m.Account, contact.account_id)
    import hashlib

    pairs = [("contact", contact.id), ("domain", account.domain)]
    if contact.linkedin_url:
        pairs.append(
            ("linkedin_digest", hashlib.sha256(contact.linkedin_url.strip().lower().encode()).hexdigest())
        )
    identity = f"{account.domain}|{contact.first_name.strip().lower()}|{contact.last_name.strip().lower()}"
    pairs.append(("identity_digest", hashlib.sha256(identity.encode()).hexdigest()))
    if contact.email:
        pairs.append(("email", contact.email.lower()))
    if contact.phone:
        pairs.append(("phone", contact.phone))
    match = or_(*[(m.Suppression.kind == kind) & (m.Suppression.value == value) for kind, value in pairs])
    return (
        db.scalar(select(m.Suppression.id).where(match, m.Suppression.scope.in_(["all", channel])).limit(1))
        is not None
    )


def eligibility(db, contact, channel="email", require_approved=True):
    from . import offers

    account = get(db, m.Account, contact.account_id)
    reasons = []
    if contact.retention_until and contact.retention_until <= m.now():
        reasons.append("Échéance de conservation atteinte : revue requise")
    if account.lifecycle != "prospect":
        reasons.append("Entreprise cliente ou exclue")
    if account.outreach_paused:
        reasons.append("Entreprise en pause : " + account.pause_reason)
    if contact.paused:
        reasons.append("Contact en pause")
    if is_suppressed(db, contact, channel):
        reasons.append("Opposition enregistrée")
    if require_approved and not contact.approved:
        reasons.append("Contact non validé")
    if contact.approved:
        first = db.scalar(
            select(m.Contact.id)
            .where(
                m.Contact.account_id == contact.account_id,
                m.Contact.approved.is_(True),
                m.Contact.paused.is_(False),
            )
            .order_by(m.Contact.created_at, m.Contact.id)
            .limit(1)
        )
        if first and first != contact.id:
            reasons.append("Un autre interlocuteur de cette entreprise est déjà actif")
    campaign = db.get(m.Campaign, contact.campaign_id) if contact.campaign_id else None
    if campaign and channel == "email":
        from .languages import reasons as language_reasons

        reasons.extend(language_reasons(db, contact, campaign))
    if campaign:
        reasons.extend(offers.campaign_reasons(db, campaign.offer_id, campaign.icp_id, campaign.purpose))
    if (
        campaign
        and campaign.config.get("automatic")
        and any(check["status"] == "mismatch" for check in score_contact(db, contact)["checks"])
    ):
        reasons.append("Les informations confirmées ne correspondent pas à la cible de cette campagne")
    if not contact.role or not contact.professional_relevance.strip():
        reasons.append("Rôle et pertinence professionnelle à documenter")
    if not contact.sourcing_note.strip() and not db.scalar(
        select(m.Evidence.id).where(m.Evidence.contact_id == contact.id, m.Evidence.source_url != "").limit(1)
    ):
        reasons.append("Provenance à documenter")
    if contact.information_status == "pending":
        reasons.append("Information sur l’utilisation des données à préparer")
    if channel == "email":
        from .public_email import publication_review

        public_review = publication_review(db, contact, campaign)
        if not public_review:
            if not contact.email or contact.email_status != "valid":
                reasons.append("Email vérifié ou publication officielle contrôlée nécessaire")
            elif not contact.email_verified_at or contact.email_verified_at < m.now() - timedelta(days=90):
                reasons.append("Vérification email à actualiser")
    elif channel == "phone" and (not contact.phone or contact.phone_status != "appropriate"):
        reasons.append("Numéro approprié à confirmer")
    elif channel == "linkedin" and not contact.linkedin_url:
        reasons.append("Profil LinkedIn manquant")
    return reasons


def score_contact(db, contact):
    account = get(db, m.Account, contact.account_id)
    campaign = db.get(m.Campaign, contact.campaign_id) if contact.campaign_id else None
    icp = db.get(m.ICP, campaign.icp_id) if campaign else None
    criteria = icp.criteria if icp else {}
    checks = []
    country = criteria.get("country")
    if country:
        checks.append(
            {
                "criterion": "Pays",
                "status": "match" if account.country == country else "mismatch",
                "evidence": account.country,
            }
        )
    lower, upper = criteria.get("min_employees", 0), criteria.get("max_employees", float("inf"))
    if "min_employees" in criteria or "max_employees" in criteria:
        checks.append(
            {
                "criterion": "Effectif indicatif",
                "status": "unknown"
                if account.employee_count is None
                else "match"
                if lower <= account.employee_count <= upper
                else "mismatch",
                "evidence": str(account.employee_count or "Inconnu"),
            }
        )
    if str(criteria.get("crm", "")).casefold() == "hubspot":
        checks.append(
            {
                "criterion": "HubSpot commercial",
                "status": "match"
                if account.hubspot_status == "confirmed"
                else "mismatch"
                if account.hubspot_status == "absent"
                else "unknown",
                "evidence": account.hubspot_status,
            }
        )
    checks.append(
        {
            "criterion": "Rôle et pertinence",
            "status": "match" if contact.role and contact.professional_relevance else "unknown",
            "evidence": contact.role or "Inconnu",
        }
    )
    return {
        "checks": checks,
        "matched": sum(x["status"] == "match" for x in checks),
        "total": len(checks),
        "unknown": sum(x["status"] == "unknown" for x in checks),
        "pain": "À confirmer en conversation",
    }


def approve_contact(db, contact, actor_id):
    """One qualification rule for batch approval and the individual review desk."""
    campaign = get(db, m.Campaign, contact.campaign_id)
    if db.scalar(
        select(m.Contact.id).where(
            m.Contact.account_id == contact.account_id,
            m.Contact.id != contact.id,
            m.Contact.approved.is_(True),
            m.Contact.paused.is_(False),
        )
    ):
        raise HTTPException(
            409,
            "Un autre interlocuteur de cette entreprise est déjà validé. Mettez sa prospection en pause avant de choisir une alternative.",
        )
    channels = {step["channel"] for step in campaign.sequence}
    if not channels:
        raise HTTPException(422, "Préparez la séquence avant validation.")
    reasons = {channel: eligibility(db, contact, channel, require_approved=False) for channel in channels}
    if all(reasons.values()):
        raise HTTPException(409, {"contact_id": contact.id, "reasons": reasons})
    contact.approved = True
    contact.status = "qualified"
    contact.qualification = score_contact(db, contact)
    log(
        db,
        "contact_approved",
        contact=contact,
        actor_id=actor_id,
        summary="Qualification validée manuellement.",
    )


def cancel_pending(db, contact, reason, channels=None):
    if channels is None or "email" in channels:
        messages = db.scalars(
            select(m.Message).where(
                m.Message.contact_id == contact.id,
                m.Message.status.in_(["draft", "approved", "queued"]),
            )
        ).all()
        for message in messages:
            message.status = "cancelled"
            message.failure_reason = reason
        for job in db.scalars(
            select(m.Job).where(
                m.Job.contact_id == contact.id,
                m.Job.kind == "send",
                m.Job.status == "queued",
            )
        ):
            job.status = "cancelled"
            job.error = reason
    query = select(m.Task).where(
        m.Task.contact_id == contact.id, m.Task.status.in_(["pending", "draft"]), m.Task.channel != "review"
    )
    if channels:
        query = query.where(m.Task.channel.in_(channels))
    for task in db.scalars(query):
        task.status = "cancelled"
        task.notes = (task.notes + "\n" + reason).strip()


def pause_conversation(db, contact, reason):
    contact.paused = True
    contact.approved = False
    contact.status = "conversation"
    account = get(db, m.Account, contact.account_id)
    account.outreach_paused = True
    account.pause_reason = reason
    for sibling in db.scalars(select(m.Contact).where(m.Contact.account_id == account.id)):
        cancel_pending(db, sibling, reason)


def add_suppression(db, kind, value, scope, reason, actor_id=None):
    if kind in {"email", "domain"}:
        value = value.strip().lower()
    if kind == "domain":
        value = normalize_domain(value)
    if kind == "phone":
        value = normalize_phone(value)
    row = db.scalar(
        select(m.Suppression).where(
            m.Suppression.kind == kind, m.Suppression.value == value, m.Suppression.scope == scope
        )
    )
    if not row:
        row = m.Suppression(kind=kind, value=value, scope=scope, reason=reason, actor_id=actor_id)
        db.add(row)
        db.flush()
    query = select(m.Contact)
    if kind == "email":
        query = query.where(m.Contact.email == value)
    elif kind == "phone":
        query = query.where(m.Contact.phone == value)
    elif kind == "domain":
        query = query.join(m.Account, m.Contact.account_id == m.Account.id).where(m.Account.domain == value)
    else:
        query = query.where(m.Contact.id == value)
    for contact in db.scalars(query):
        if scope == "all":
            contact.paused = True
            contact.approved = False
            contact.status = "suppressed"
        cancel_pending(db, contact, "Opposition : " + reason, None if scope == "all" else [scope])
        log(db, "suppressed", contact=contact, actor_id=actor_id, summary=reason, data={"scope": scope})
    return row


def receive_event(
    db,
    contact,
    kind,
    channel,
    summary,
    *,
    external_id=None,
    actor_id=None,
    source="manual",
    data=None,
    campaign_id=None,
):
    if external_id and db.scalar(select(m.Event.id).where(m.Event.external_id == external_id)):
        return {"duplicate": True}
    event = log(
        db,
        kind,
        contact=contact,
        campaign_id=campaign_id,
        channel=channel,
        summary=summary,
        external_id=external_id,
        actor_id=actor_id,
        source=source,
        data=data,
    )
    if kind in {"reply", "positive_reply", "meeting"}:
        campaign = db.get(m.Campaign, contact.campaign_id)
        if campaign and campaign.config.get("automatic") and contact.retention_until:
            contact.retention_until = m.now() + timedelta(days=365)
    if kind in {"reply", "positive_reply", "out_of_office", "meeting"}:
        pause_conversation(db, contact, "Rendez-vous confirmé" if kind == "meeting" else "Réponse à traiter")
        db.add(
            m.Task(
                contact_id=contact.id,
                campaign_id=campaign_id or contact.campaign_id,
                owner_id=contact.owner_id,
                channel="review",
                title="Préparer le rendez-vous" if kind == "meeting" else "Traiter la réponse",
                notes=summary,
                dedupe_key=f"event:{event.id}",
            )
        )
    if kind == "bounce":
        contact.email_status = "invalid"
        cancel_pending(db, contact, "Email invalide", ["email"])
    if kind == "stop":
        add_suppression(db, "contact", contact.id, "all", summary or "Opposition", actor_id)
        if contact.email:
            add_suppression(db, "email", contact.email, "all", summary or "Opposition", actor_id)
    return {"duplicate": False, "event": serialize(event)}


def render(template, fields):
    missing = []

    def replace(match):
        key = match.group(1).strip()
        value = str(fields.get(key, "")).strip()
        if not value:
            missing.append(key)
        return value

    result = re.sub(r"\{\{\s*([\w_]+)\s*\}\}", replace, template)
    if missing or "{{" in result or "}}" in result:
        raise HTTPException(422, "Variables manquantes ou invalides : " + ", ".join(sorted(set(missing))))
    return result


def prepare_contact(db, contact, actor_id):
    from . import email_policy

    campaign = get(db, m.Campaign, contact.campaign_id)
    account = get(db, m.Account, contact.account_id)
    if not campaign.sequence:
        raise HTTPException(422, "Ajoutez une séquence à la campagne.")
    from .languages import reasons as language_reasons

    if any(step["channel"] == "email" for step in campaign.sequence):
        issues = language_reasons(db, contact, campaign)
        if issues:
            raise HTTPException(422, "; ".join(issues))
    fields = {
        "prenom": contact.first_name,
        "nom": contact.last_name,
        "societe": account.name,
        "role": contact.role,
        "pertinence": contact.professional_relevance,
    }
    created = 0
    for index, step in enumerate(campaign.sequence):
        key = f"{campaign.id}:{contact.id}:{index}"
        body = render(step["body"], fields)
        due = m.now() + timedelta(days=step["day"])
        if step["channel"] == "email":
            if not db.scalar(select(m.Message.id).where(m.Message.dedupe_key == key)):
                subject = render(step["subject"], fields)
                if not subject.strip() or "\n" in subject or "\r" in subject:
                    raise HTTPException(422, "Objet manquant ou invalide.")
                if email_policy.contains_price(subject, body):
                    raise HTTPException(422, email_policy.REASON)
                db.add(
                    m.Message(
                        contact_id=contact.id,
                        campaign_id=campaign.id,
                        step=index,
                        subject=subject,
                        body=body,
                        due_at=due,
                        dedupe_key=key,
                    )
                )
                created += 1
        elif not db.scalar(select(m.Task.id).where(m.Task.dedupe_key == key)):
            db.add(
                m.Task(
                    contact_id=contact.id,
                    campaign_id=campaign.id,
                    owner_id=contact.owner_id,
                    channel=step["channel"],
                    title="Appeler " + account.name
                    if step["channel"] == "phone"
                    else "Prise de contact LinkedIn",
                    due_at=due,
                    notes=body,
                    dedupe_key=key,
                    status="draft",
                )
            )
            created += 1
    log(db, "prepared", contact=contact, actor_id=actor_id, summary=f"{created} éléments préparés.")
    return created


def in_sending_window(campaign, at=None):
    local = (at or m.now()).astimezone(ZoneInfo(campaign.timezone))
    return local.weekday() in campaign.weekdays and campaign.start_hour <= local.hour < campaign.end_hour


def reserve_budget(db, campaign, integration, job, amount):
    global_state = state(db, lock=True)
    # Lock order is global then campaign; all reservations serialize through this row.
    db.refresh(campaign, with_for_update=True)
    if not campaign.budget_approved:
        raise HTTPException(409, "Le budget de cette campagne doit être validé.")
    if amount <= 0:
        raise HTTPException(422, "Renseignez un coût maximum par opération pour réserver le budget.")
    month_start = m.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    active = m.Expense.status.in_(["reserved", "spent", "estimated"])
    campaign_spent = db.scalar(
        select(func.coalesce(func.sum(m.Expense.amount_cents), 0)).where(
            m.Expense.campaign_id == campaign.id, active
        )
    )
    global_spent = db.scalar(
        select(func.coalesce(func.sum(m.Expense.amount_cents), 0)).where(
            active, m.Expense.created_at >= month_start
        )
    )
    provider_spent = db.scalar(
        select(func.coalesce(func.sum(m.Expense.amount_cents), 0)).where(
            active, m.Expense.created_at >= month_start, m.Expense.provider == integration.provider
        )
    )
    if (
        campaign_spent + amount > campaign.budget_cents
        or global_spent + amount > global_state.monthly_limit_cents
        or provider_spent + amount > integration.monthly_limit_cents
    ):
        raise HTTPException(409, "Plafond de dépense atteint : campagne, fournisseur ou mois.")
    db.add(
        m.Expense(
            campaign_id=campaign.id,
            job_id=job.id,
            provider=integration.provider,
            amount_cents=amount,
            status="reserved",
            note="Plafond réservé ; coût fournisseur à rapprocher.",
        )
    )


def shift_sequence(db, contact_id, campaign_id, shift, after_step=-1):
    """Keep pending intervals when review or delivery postpones the sequence start."""
    if shift <= timedelta(0):
        return
    for message in db.scalars(
        select(m.Message).where(
            m.Message.contact_id == contact_id,
            m.Message.campaign_id == campaign_id,
            m.Message.step > after_step,
            m.Message.status.in_(["draft", "approved", "queued"]),
        )
    ):
        message.due_at += shift
        for job in db.scalars(select(m.Job).where(m.Job.message_id == message.id, m.Job.status == "queued")):
            job.run_at = message.due_at
    for task in db.scalars(
        select(m.Task).where(
            m.Task.contact_id == contact_id,
            m.Task.campaign_id == campaign_id,
            m.Task.status.in_(["draft", "pending"]),
        )
    ):
        prefix = f"{campaign_id}:{contact_id}:"
        if task.dedupe_key.startswith(prefix):
            step = int(task.dedupe_key[len(prefix) :])
            if step > after_step:
                task.due_at += shift
