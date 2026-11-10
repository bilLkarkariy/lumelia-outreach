"""Human mobile bridges and inbound routing; every paid initiation is recorded first."""

from datetime import datetime, timedelta
from html import escape
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from . import domain as d
from . import models as m
from .call_models import CallSession, CallTranscript
from .calls import (
    CALLER_ID,
    CallIntent,
    _linked_entities,
    _mission_binding,
    _outbound_allowed,
    _provider_call,
    _urls,
    call_settings,
    call_view,
    dial_twiml,
    remember_provider_event,
    signed_twilio_form,
)
from .db import SessionLocal, get_db
from .security import admin, current_user

router = APIRouter(prefix="/api/calls", tags=["phone routing"])
TERMINAL = {"completed", "missed", "failed", "canceled"}
ACTIVE = {
    "dispatching",
    "operator_ringing",
    "awaiting_operator",
    "connecting",
    "ringing",
    "in_progress",
    "uncertain",
}


def xml(body="<Hangup/>"):
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?><Response>' + body + "</Response>",
        media_type="application/xml",
    )


def say(message):
    return '<Say language="fr-FR">' + escape(message) + "</Say>"


def endpoint(row, suffix):
    return call_settings().callback_base + f"/api/calls/provider/{row.id}/" + suffix


def enabled(db):
    cfg = call_settings()
    return (
        cfg.mobile_ready
        and cfg.inbound_enabled
        and d.state(db).settings.get("voice_routing", {}).get("enabled", True)
    )


def check_account(params):
    if params.get("AccountSid") != call_settings().twilio_account_sid:
        raise HTTPException(403, "Compte fournisseur incorrect")


def attention(db, row, kind, title, reason):
    from .agent_actions import enqueue

    return enqueue(
        db,
        f"phone:{kind}:{row.id}",
        kind,
        title,
        human_reason=reason,
        account_id=row.account_id,
        contact_id=row.contact_id,
        campaign_id=row.campaign_id,
        target_url="/settings?incoming=" + row.id,
        payload={"call_id": row.id},
        priority=10,
    )


def missed_followup(db, row):
    if row.source != "inbound" or row.status != "missed":
        return
    contact = db.get(m.Contact, row.contact_id) if row.contact_id else None
    if contact:
        try:
            _outbound_allowed(db, db.get(m.Account, contact.account_id), contact)
        except HTTPException:
            attention(
                db,
                row,
                "missed_call",
                "Appel entrant manqué · vérifier la suite",
                "Cette fiche est en opposition ou en pause ; décider de la réponse appropriée.",
            )
            return
        key = "missed-call:" + row.id
        if not db.scalar(select(m.Task.id).where(m.Task.dedupe_key == key)):
            db.add(
                m.Task(
                    contact_id=contact.id,
                    campaign_id=contact.campaign_id,
                    owner_id=row.created_by,
                    channel="phone",
                    title="Rappeler après l’appel entrant manqué",
                    due_at=m.now(),
                    dedupe_key=key,
                    notes="Appel entrant manqué. Fiche : /settings?incoming=" + row.id,
                )
            )
    else:
        attention(
            db,
            row,
            "missed_call",
            "Appel entrant manqué · interlocuteur à identifier",
            "Rattacher cet appel à la bonne fiche avant de rappeler. Numéro : "
            + (row.destination or "masqué"),
        )


def finish(db, row, status):
    if row.status in TERMINAL:
        return
    connected = bool(row.data.get("operator_accepted")) if row.source == "inbound" else bool(row.answered_at)
    row.status = (
        "completed"
        if connected
        else (
            "missed"
            if row.source == "inbound"
            else ("canceled" if status in {"canceled", "declined"} else "failed")
        )
    )
    row.ended_at = m.now()
    if not connected:
        row.duration_seconds = 0
    recorded = connected and row.data.get("recording_information", {}).get("confirmed")
    if not recorded and row.processing_status == "awaiting_recording":
        row.processing_status = "not_recorded"
    key = "call-ended:" + row.id
    if not db.scalar(select(m.Event.id).where(m.Event.external_id == key)):
        d.log(
            db,
            "call_ended",
            actor_id=row.created_by,
            contact=db.get(m.Contact, row.contact_id) if row.contact_id else None,
            campaign_id=row.campaign_id,
            channel="phone",
            external_id=key,
            summary="Appel entrant manqué" if row.status == "missed" else "Appel téléphonique terminé",
            data={
                "account_id": row.account_id,
                "call_id": row.id,
                "status": row.status,
                "source": row.source,
            },
        )
    missed_followup(db, row)
    for action in db.scalars(
        select(m.AgentAction).where(m.AgentAction.dedupe_key == f"phone:phone_issue:{row.id}")
    ):
        action.status, action.completed_at = "completed", m.now()


class RoutingInput(BaseModel):
    enabled: bool


@router.post("/routing")
def configure_routing(data: RoutingInput, user=Depends(admin), db=Depends(get_db)):
    cfg = call_settings()
    if data.enabled and not (cfg.mobile_ready and cfg.inbound_enabled):
        raise HTTPException(409, "Le routage Twilio et le portable doivent être configurés sur le serveur")
    state = d.state(db, lock=True)
    state.settings = {**state.settings, "voice_routing": {"enabled": data.enabled}}
    db.commit()
    return {"enabled": data.enabled}


@router.get("/link-candidates")
def link_candidates(q: str = Query("", max_length=100), user=Depends(current_user), db=Depends(get_db)):
    if len(q.strip()) < 2:
        return {"items": []}
    rows = db.execute(
        select(m.Contact, m.Account)
        .join(m.Account)
        .where(
            (
                m.Contact.first_name
                + " "
                + m.Contact.last_name
                + " "
                + m.Account.name
                + " "
                + m.Contact.phone
            ).ilike("%" + q.strip() + "%")
        )
        .limit(20)
    ).all()
    return {
        "items": [
            {
                "id": c.id,
                "name": (c.first_name + " " + c.last_name).strip(),
                "account_name": a.name,
                "phone": c.phone,
            }
            for c, a in rows
        ]
    }


class LinkInput(BaseModel):
    contact_id: UUID


@router.post("/{call_id}/link")
def link_call(call_id: str, data: LinkInput, user=Depends(current_user), db=Depends(get_db)):
    d.state(db, lock=True)
    row = d.get(db, CallSession, call_id)
    if row.source != "inbound" or row.report_version:
        raise HTTPException(409, "Seul un appel entrant sans compte rendu peut être rattaché")
    contact = d.get(db, m.Contact, str(data.contact_id))
    if row.contact_id and row.contact_id != contact.id:
        raise HTTPException(409, "Cet appel est déjà rattaché à une autre fiche")
    row.account_id, row.contact_id, row.campaign_id = contact.account_id, contact.id, contact.campaign_id
    for action in db.scalars(
        select(m.AgentAction).where(
            m.AgentAction.dedupe_key.in_([f"phone:link_call:{row.id}", f"phone:missed_call:{row.id}"])
        )
    ):
        action.status, action.completed_at = "completed", m.now()
    missed_followup(db, row)
    transcript = db.scalar(
        select(CallTranscript)
        .where(CallTranscript.call_id == row.id, CallTranscript.status == "ready")
        .order_by(CallTranscript.version.desc())
        .limit(1)
    )
    if transcript:
        from .call_processing import enqueue_report

        enqueue_report(db, row, transcript)
    d.log(
        db,
        "incoming_call_linked",
        actor_id=user.id,
        contact=contact,
        channel="phone",
        summary="Appel entrant rattaché à la fiche",
        data={"call_id": row.id, "account_id": row.account_id},
    )
    db.commit()
    return call_view(db, row, detail=True)


@router.post("/mobile", status_code=201)
def mobile_call(data: CallIntent, user=Depends(current_user), db=Depends(get_db)):
    cfg = call_settings()
    if not cfg.mobile_ready:
        raise HTTPException(503, "Le portable de réception n’est pas configuré")
    d.state(db, lock=True)
    key = "mobile-intent:" + str(data.request_id)
    prior = db.scalar(select(CallSession).where(CallSession.data["request_key"].as_string() == key))
    destination = d.normalize_phone(data.destination)
    if prior:
        if (
            prior.created_by != user.id
            or prior.destination != destination
            or prior.account_id != data.account_id
            or prior.contact_id != data.contact_id
        ):
            raise HTTPException(409, "Cette demande appartient à un autre appel")
        return call_view(db, prior, detail=True)
    if destination in {cfg.operator_number, CALLER_ID}:
        raise HTTPException(
            422, "Choisissez le numéro du prospect, différent du portable de réception et de la ligne Lumelia"
        )
    if db.scalar(
        select(CallSession.id)
        .where(CallSession.source.in_(["mobile", "inbound"]), CallSession.status.in_(ACTIVE))
        .limit(1)
    ):
        raise HTTPException(
            409, "Un appel mobile est déjà actif ou à rapprocher ; terminez-le avant de recommencer"
        )
    account, contact, _ = _linked_entities(db, data.account_id, data.contact_id, data.campaign_id)
    _outbound_allowed(db, account, contact)
    if data.mission_step_id:
        cid, campaign_id = _mission_binding(
            db, data.mission_step_id, account.id, data.contact_id, data.campaign_id
        )
        contact = db.get(m.Contact, cid) if cid else contact
        data.campaign_id = campaign_id
    row = CallSession(
        account_id=account.id,
        contact_id=contact.id if contact else None,
        campaign_id=data.campaign_id,
        created_by=user.id,
        source="mobile",
        status="dispatching",
        destination=destination,
        caller_id=CALLER_ID,
        data={
            "request_key": key,
            "operator_number": cfg.operator_number,
            "mission_step_id": data.mission_step_id,
        },
    )
    db.add(row)
    db.flush()
    remember_provider_event(db, row.id, key, "mobile_intent", {"request_id": str(data.request_id)})
    db.commit()  # Never repeat an uncertain Calls POST.
    payload = {
        "To": cfg.operator_number,
        "From": CALLER_ID,
        "Url": endpoint(row, "mobile/answer"),
        "Method": "POST",
        "StatusCallback": endpoint(row, "mobile/status"),
        "StatusCallbackMethod": "POST",
        "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
        "Timeout": "30",
        "TimeLimit": "3600",
    }
    try:
        response = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{cfg.twilio_account_sid}/Calls.json",
            data=payload,
            auth=(cfg.twilio_account_sid, cfg.twilio_auth_token),
            timeout=20,
        )
        if response.status_code >= 500:
            raise ValueError("Provider result uncertain")
        if response.status_code not in {200, 201}:
            row = db.get(CallSession, row.id, with_for_update=True, populate_existing=True)
            if row.status == "dispatching":
                row.status = "failed"
                row.ended_at = m.now()
                row.processing_status = "not_recorded"
            row.data = {
                **row.data,
                "initiation_error": f"Twilio a refusé la demande (HTTP {response.status_code})",
            }
        else:
            sid = response.json().get("sid", "")
            if not sid.startswith("CA") or len(sid) != 34:
                raise ValueError("Missing provider ID")
            row = db.get(CallSession, row.id, with_for_update=True, populate_existing=True)
            if row.external_id and row.external_id != sid:
                raise ValueError("Different provider ID")
            row.external_id = sid
            if row.status == "dispatching":
                row.status = "operator_ringing"
    except (httpx.RequestError, ValueError):
        row = db.get(CallSession, row.id, with_for_update=True, populate_existing=True)
        if row.status == "dispatching":
            row.status = "uncertain"
        row.data = {
            **row.data,
            "initiation_error": "Réponse Twilio incertaine. Aucun second appel n’est lancé automatiquement.",
        }
        attention(
            db,
            row,
            "phone_issue",
            "Appel mobile à vérifier",
            "La demande a pu être reçue par Twilio. Attendre le statut fournisseur avant une nouvelle tentative.",
        )
    db.commit()
    return call_view(db, row, detail=True)


def mobile_row(db, call_id, params):
    check_account(params)
    row = db.get(CallSession, call_id, with_for_update=True)
    if (
        not row
        or row.source != "mobile"
        or params.get("To") != row.data.get("operator_number")
        or params.get("From") != CALLER_ID
    ):
        raise HTTPException(403, "Appel opérateur incorrect")
    sid = params.get("CallSid", "")
    if not sid.startswith("CA") or len(sid) != 34 or (row.external_id and row.external_id != sid):
        raise HTTPException(409, "Appel fournisseur incohérent")
    row.external_id = sid
    return row


@router.post("/provider/{call_id}/mobile/answer")
async def mobile_answer(call_id: str, request: Request, db=Depends(get_db)):
    params = await signed_twilio_form(request)
    row = mobile_row(db, call_id, params)
    if row.status in TERMINAL or row.data.get("bridge_started"):
        return xml()
    row.status = "awaiting_operator"
    row.started_at = row.started_at or m.now()
    db.commit()
    return xml(
        f'<Gather input="dtmf" numDigits="1" timeout="10" action="{escape(endpoint(row, "mobile/connect"), quote=True)}" method="POST">'
        + say(
            "Lumelia. Tapez 1 si votre prospect est déjà informé de l’enregistrement, ou 2 pour appeler sans enregistrement. Sans confirmation, aucun prospect ne sera appelé."
        )
        + "</Gather><Hangup/>"
    )


@router.post("/provider/{call_id}/mobile/connect")
async def mobile_connect(call_id: str, request: Request, db=Depends(get_db)):
    params = await signed_twilio_form(request)
    row = mobile_row(db, call_id, params)
    if row.status in TERMINAL or row.data.get("bridge_started"):
        return xml()
    if params.get("Digits") not in {"1", "2"}:
        finish(db, row, "declined")
        db.commit()
        return xml()
    account, contact, _ = _linked_entities(db, row.account_id, row.contact_id, row.campaign_id)
    try:
        _outbound_allowed(db, account, contact)
    except HTTPException:
        finish(db, row, "canceled")
        db.commit()
        return xml(
            say("Cet appel ne peut plus être lancé. La fiche est en pause ou en opposition.") + "<Hangup/>"
        )
    recorded = params["Digits"] == "1"
    row.data = {
        **row.data,
        "bridge_started": True,
        "recording_information": {
            "confirmed": recorded,
            "proof": "Information préalable de l’interlocuteur attestée par l’opérateur en tapant 1"
            if recorded
            else "Appel sans enregistrement choisi par l’opérateur",
        },
    }
    row.status = "connecting"
    db.commit()
    urls = _urls(call_settings(), row.id)
    urls["status"] = endpoint(row, "bridge-status")
    body = dial_twiml(row, urls).replace('timeout="30"', 'timeout="30" timeLimit="3600" hangupOnStar="true"')
    if not recorded:
        body = body.replace('record="record-from-answer-dual"', 'record="do-not-record"')
    return Response(body, media_type="application/xml")


@router.post("/provider/{call_id}/mobile/status")
async def mobile_status(call_id: str, request: Request, db=Depends(get_db)):
    params = await signed_twilio_form(request)
    row = mobile_row(db, call_id, params)
    status = params.get("CallStatus", "")
    if row.status not in TERMINAL:
        if status in {"completed", "busy", "failed", "no-answer", "canceled"}:
            row.data = {**row.data, "root_terminal_status": status}
            # The root includes the operator greeting. Only the prospect leg proves connection.
            if not row.data.get("bridge_started"):
                finish(db, row, status)
        elif not row.data.get("bridge_started"):
            row.status = "awaiting_operator" if status == "in-progress" else "operator_ringing"
    db.commit()
    return {"ok": True}


@router.post("/{call_id}/cancel")
def cancel_call(call_id: str, user=Depends(current_user), db=Depends(get_db)):
    row = db.get(CallSession, call_id, with_for_update=True)
    cfg = call_settings()
    if not row or row.source not in {"mobile", "inbound"}:
        raise HTTPException(409, "Cet appel ne peut pas être arrêté ici")
    if row.status in TERMINAL:
        return {"status": row.status}
    if not row.external_id:
        if row.status == "uncertain" and not row.data.get("bridge_started"):
            # Revoke the intent: a delayed answer webhook hangs up before dialing the prospect.
            finish(db, row, "canceled")
            row.data = {**row.data, "cancel_state": "revoked_before_bridge"}
            db.commit()
            return {"status": "canceled"}
        raise HTTPException(409, "Attendre l’identifiant fournisseur avant de pouvoir arrêter cet appel")
    if row.data.get("cancel_state") in {"requested", "uncertain"}:
        return {"status": row.data["cancel_state"]}
    row.data = {**row.data, "cancel_state": "requested"}
    db.commit()
    try:
        response = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{cfg.twilio_account_sid}/Calls/{row.external_id}.json",
            data={"Status": "completed"},
            auth=(cfg.twilio_account_sid, cfg.twilio_auth_token),
            timeout=15,
        )
        response.raise_for_status()
        row = db.get(CallSession, row.id, with_for_update=True, populate_existing=True)
        finish(db, row, "canceled")
        row.data = {**row.data, "cancel_state": "succeeded"}
    except (httpx.RequestError, httpx.HTTPStatusError):
        row = db.get(CallSession, row.id, with_for_update=True, populate_existing=True)
        row.data = {**row.data, "cancel_state": "uncertain"}
        attention(
            db,
            row,
            "phone_issue",
            "Arrêt d’appel à vérifier",
            "La confirmation d’arrêt n’a pas été reçue. Vérifier la ligne Twilio.",
        )
    db.commit()
    return {"status": row.status if row.data["cancel_state"] == "succeeded" else "uncertain"}


def match_caller(db, number):
    if not number:
        return None, None
    contacts = list(db.scalars(select(m.Contact).where(m.Contact.phone == number).limit(2)))
    if len(contacts) == 1:
        return contacts[0].account_id, contacts[0]
    if contacts:
        return None, None
    accounts = list(
        db.scalars(
            select(m.Evidence.account_id)
            .where(m.Evidence.field == "company_phone", m.Evidence.value == number)
            .distinct()
            .limit(2)
        )
    )
    return (accounts[0], None) if len(accounts) == 1 else (None, None)


@router.post("/provider/incoming")
async def incoming(request: Request, db=Depends(get_db)):
    params = await signed_twilio_form(request)
    check_account(params)
    cfg = call_settings()
    if (
        params.get("To") != CALLER_ID
        or params.get("Direction") != "inbound"
        or not params.get("CallSid", "").startswith("CA")
        or len(params.get("CallSid", "")) != 34
    ):
        raise HTTPException(403, "Ligne entrante incorrecte")
    d.state(db, lock=True)
    row = db.scalar(select(CallSession).where(CallSession.external_id == params["CallSid"]))
    if not row:
        try:
            number = d.normalize_phone(params.get("From", "")) or None
        except (HTTPException, ValueError):
            number = None
        aid, contact = match_caller(db, number)
        owner = db.scalar(
            select(m.User)
            .where(m.User.active.is_(True), m.User.role == "admin")
            .order_by(m.User.created_at)
            .limit(1)
        )
        if not owner:
            raise HTTPException(503, "Aucun opérateur disponible")
        row = CallSession(
            account_id=aid,
            contact_id=contact.id if contact else None,
            campaign_id=contact.campaign_id if contact else None,
            created_by=owner.id,
            source="inbound",
            external_id=params["CallSid"],
            destination=number,
            status="connecting",
            started_at=m.now(),
            data={"operator_number": cfg.operator_number, "recording_information": {"confirmed": False}},
        )
        db.add(row)
        db.flush()
        if not aid:
            attention(
                db,
                row,
                "link_call",
                "Identifier l’appelant entrant",
                "Le numéro est inconnu, masqué ou correspond à plusieurs fiches ; choisir la bonne fiche.",
            )
    if row.status in TERMINAL or row.data.get("bridge_started"):
        return xml()
    busy = db.scalar(
        select(CallSession.id)
        .where(
            CallSession.id != row.id,
            CallSession.source.in_(["inbound", "mobile"]),
            CallSession.status.in_(ACTIVE),
        )
        .limit(1)
    )
    if not enabled(db) or busy or row.destination == cfg.operator_number:
        row.data = {
            **row.data,
            "routing_reason": "disabled"
            if not enabled(db)
            else "self_call"
            if row.destination == cfg.operator_number
            else "busy",
        }
        finish(db, row, "no-answer")
        db.commit()
        return xml(
            say("Lumelia n’est pas disponible pour le moment. Votre appel a été enregistré dans notre suivi.")
            + "<Hangup/>"
        )
    db.commit()
    target = escape(endpoint(row, "incoming/connect"), quote=True)
    return xml(
        f'<Gather input="dtmf" numDigits="1" timeout="3" action="{target}" method="POST" actionOnEmptyResult="true">'
        + say(
            "Bonjour, vous êtes chez Lumelia. Cet échange sera enregistré pour assurer votre suivi. Pour continuer sans enregistrement, tapez 2."
        )
        + "</Gather>"
    )


def incoming_row(db, call_id, params):
    check_account(params)
    row = _provider_call(db, call_id, params.get("CallSid", ""))
    if row.source != "inbound" or params.get("To") != CALLER_ID:
        raise HTTPException(403, "Appel entrant incorrect")
    return row


@router.post("/provider/{call_id}/incoming/connect")
async def incoming_connect(call_id: str, request: Request, db=Depends(get_db)):
    params = await signed_twilio_form(request)
    row = incoming_row(db, call_id, params)
    if row.status in TERMINAL or row.data.get("bridge_started"):
        return xml()
    if not enabled(db):
        finish(db, row, "no-answer")
        db.commit()
        return xml(say("La ligne est actuellement indisponible.") + "<Hangup/>")
    recorded = params.get("Digits") != "2"
    row.data = {
        **row.data,
        "bridge_started": True,
        "recording_information": {
            "confirmed": recorded,
            "proof": "Annonce préalable ; possibilité de continuer sans enregistrement en tapant 2",
        },
    }
    row.status = "ringing"
    db.commit()
    urls = _urls(call_settings(), row.id)
    attrs = (
        f'answerOnBridge="true" timeout="25" timeLimit="3600" callerId="{CALLER_ID}" '
        f'action="{escape(endpoint(row, "bridge-status"), quote=True)}" method="POST" '
        f'record="{"record-from-answer-dual" if recorded else "do-not-record"}" '
        f'recordingStatusCallback="{escape(urls["recording"], quote=True)}" recordingStatusCallbackMethod="POST" recordingStatusCallbackEvent="in-progress completed absent"'
    )
    return xml(
        f'<Dial {attrs}><Number url="{escape(endpoint(row, "incoming/accept"), quote=True)}" method="POST">{escape(row.data["operator_number"])}</Number></Dial>'
    )


@router.post("/provider/{call_id}/incoming/accept")
async def incoming_accept(call_id: str, request: Request, db=Depends(get_db)):
    params = await signed_twilio_form(request)
    check_account(params)
    row = db.get(CallSession, call_id, with_for_update=True)
    if (
        not row
        or row.source != "inbound"
        or params.get("To") != row.data.get("operator_number")
        or params.get("From") != CALLER_ID
        or row.status in TERMINAL
    ):
        return xml()
    # Number-whisper callbacks carry the child CallSid; ParentCallSid is optional.
    if params.get("ParentCallSid") and params["ParentCallSid"] != row.external_id:
        raise HTTPException(403, "Branche fournisseur incorrecte")
    if not params.get("CallSid", "").startswith("CA") or len(params.get("CallSid", "")) != 34:
        raise HTTPException(403, "Identifiant fournisseur incorrect")
    prior = row.data.get("operator_call_sid")
    if prior and prior != params.get("CallSid"):
        raise HTTPException(403, "Opérateur fournisseur incohérent")
    row.data = {**row.data, "operator_call_sid": params.get("CallSid")}
    if params.get("Digits") == "1":
        row.data = {**row.data, "operator_accepted": True}
        row.answered_at = row.answered_at or m.now()
        row.status = "in_progress"
        db.commit()
        return xml("")
    if "Digits" in params:
        db.commit()
        return xml()
    db.commit()
    return xml(
        f'<Gather input="dtmf" numDigits="1" timeout="7" action="{escape(endpoint(row, "incoming/accept"), quote=True)}" method="POST">'
        + say("Appel entrant Lumelia. Tapez 1 pour accepter.")
        + "</Gather><Hangup/>"
    )


@router.post("/provider/{call_id}/bridge-status")
async def bridge_status(call_id: str, request: Request, db=Depends(get_db)):
    params = await signed_twilio_form(request)
    check_account(params)
    row = _provider_call(db, call_id, params.get("ParentCallSid") or params.get("CallSid", ""))
    status = params.get("DialCallStatus") or params.get("CallStatus", "")
    if row.source not in {"mobile", "inbound"}:
        raise HTTPException(409, "Type d’appel incorrect")
    duration = params.get("DialCallDuration") or params.get("CallDuration")
    if duration and duration.isdigit() and (row.answered_at or row.status not in TERMINAL):
        row.duration_seconds = int(duration)
    if row.status not in TERMINAL:
        if status in {"in-progress", "answered"} and row.source == "mobile":
            row.status = "in_progress"
            row.answered_at = row.answered_at or m.now()
        if status in {"completed", "busy", "failed", "no-answer", "canceled"}:
            if (
                row.source == "mobile"
                and status == "completed"
                and seconds(params.get("DialCallDuration") or params.get("CallDuration")) > 0
            ):
                row.answered_at = row.answered_at or m.now()
            finish(db, row, status)
    db.commit()
    return xml() if "DialCallStatus" in params else {"ok": True}


@router.post("/provider/incoming/status")
async def incoming_status(request: Request, db=Depends(get_db)):
    params = await signed_twilio_form(request)
    check_account(params)
    row = db.scalar(
        select(CallSession)
        .where(CallSession.external_id == params.get("CallSid"), CallSession.source == "inbound")
        .with_for_update()
    )
    if row and params.get("CallStatus") in {"completed", "busy", "failed", "canceled", "no-answer"}:
        if row.answered_at and row.duration_seconds is None:
            row.duration_seconds = seconds(params.get("CallDuration"))
        finish(db, row, params["CallStatus"])
    db.commit()
    return {"ok": True}


def seconds(value):
    return int(value) if str(value or "").isdigit() else 0


def reconcile_tick():
    """Recover missing callbacks with reads only; never repeat an outbound Calls POST."""
    cfg = call_settings()
    if not cfg.mobile_ready:
        return {"checked": 0}
    with SessionLocal() as db:
        candidates = list(
            db.scalars(
                select(CallSession)
                .where(
                    CallSession.source.in_(["mobile", "inbound"]),
                    CallSession.status.in_(ACTIVE),
                    CallSession.created_at < m.now() - timedelta(seconds=90),
                )
                .order_by(CallSession.created_at)
                .limit(20)
            )
        )
        row = next(
            (
                item
                for item in candidates
                if not item.data.get("phone_checked_at")
                or datetime.fromisoformat(item.data["phone_checked_at"]) < m.now() - timedelta(minutes=2)
            ),
            None,
        )
        if not row:
            return {"checked": 0}
        row = db.get(CallSession, row.id, with_for_update=True, populate_existing=True)
        if row.status in TERMINAL:
            return {"checked": 0}
        row.data = {**row.data, "phone_checked_at": m.now().isoformat()}
        sid = row.external_id
        if not sid:
            row.status = "uncertain"
            attention(
                db,
                row,
                "phone_issue",
                "Appel mobile à rapprocher",
                "L’identifiant Twilio manque. Vérifier le journal fournisseur avant de clore l’incident ; ne pas relancer l’appel.",
            )
            db.commit()
            return {"checked": 1, "uncertain": True}
        call_id = row.id
        db.commit()
        base = f"https://api.twilio.com/2010-04-01/Accounts/{cfg.twilio_account_sid}/Calls"
        try:
            response = httpx.get(
                base + f"/{sid}.json", auth=(cfg.twilio_account_sid, cfg.twilio_auth_token), timeout=15
            )
            response.raise_for_status()
            root = response.json()
            status = root.get("status")
            if status not in {"completed", "busy", "failed", "no-answer", "canceled"}:
                return {"checked": 1}
            child = None
            if row.source == "mobile" and row.data.get("bridge_started"):
                response = httpx.get(
                    base + ".json",
                    params={"ParentCallSid": sid, "PageSize": 10},
                    auth=(cfg.twilio_account_sid, cfg.twilio_auth_token),
                    timeout=15,
                )
                response.raise_for_status()
                children = [
                    item for item in response.json().get("calls", []) if item.get("to") == row.destination
                ]
                if len(children) > 1:
                    raise ValueError("Ambiguous bridge")
                child = children[0] if children else None
            row = db.get(CallSession, call_id, with_for_update=True, populate_existing=True)
            if row.status not in TERMINAL:
                if child and child.get("status") == "completed" and seconds(child.get("duration")) > 0:
                    row.answered_at = row.answered_at or m.now()
                    row.duration_seconds = seconds(child["duration"])
                finish(db, row, status)
                row.data = {**row.data, "reconciled_at": m.now().isoformat()}
                for action in db.scalars(
                    select(m.AgentAction).where(m.AgentAction.dedupe_key == f"phone:phone_issue:{row.id}")
                ):
                    action.status, action.completed_at = "completed", m.now()
            db.commit()
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
            db.rollback()  # The next bounded read can retry without another paid call.
        return {"checked": 1}
