"""Public recipient information and expiry of automatically sourced prospect records."""

import hashlib
from html import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select

from . import domain as d
from . import models as m
from .config import settings
from .db import SessionLocal

router = APIRouter()


NOTICE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vos données professionnelles — {company}</title>
<style>
body{{margin:0;background:#f7f7f2;color:#172322;font:17px/1.65 system-ui,sans-serif}}
main{{max-width:740px;margin:48px auto;padding:0 24px 56px}}
header{{border-bottom:1px solid #d7dfd9;padding-bottom:24px;margin-bottom:32px}}
.brand{{font-size:16px;letter-spacing:.15em;text-transform:uppercase;color:#296751}}
h1{{font-size:clamp(30px,6vw,44px);line-height:1.15;letter-spacing:-.04em}}
h2{{font-size:21px;margin-top:30px}}
a{{color:#176146;text-underline-offset:3px}}
.notice{{padding:18px 22px;background:#e5eee6;border-radius:12px}}
</style></head>
<body><main>
<header><p class="brand">{company}</p><h1>Vos données professionnelles</h1>
<p>Les informations utiles pour comprendre notre prise de contact et exercer vos droits.</p></header>
<div class="notice">Vous pouvez refuser toute nouvelle sollicitation en répondant à notre email ou en utilisant le lien de désinscription qu’il contient. Cette démarche est gratuite.</div>
<h2>Qui vous contacte et pourquoi ?</h2>
<p>{company} utilise des coordonnées professionnelles pour présenter ses services et répondre aux échanges qui en résultent. Vous pouvez nous écrire à <a href="mailto:{contact}">{contact}</a>.</p>
<p>La prospection vise des fonctions professionnelles auxquelles le sujet peut être utile. Elle repose sur l’intérêt légitime de développer l’activité de {company}, sous réserve de votre droit d’opposition. Une prise de contact ne signifie pas que nous connaissons vos besoins, vos outils ou votre intention d’achat.</p>
<h2>Quelles données et quelles sources ?</h2>
<p>Nous utilisons votre nom, votre fonction, votre société, son site, votre adresse email professionnelle, les références des sources, la vérification technique de cette adresse et l’historique de nos échanges. Ces informations peuvent provenir de sources professionnelles publiques, notamment les sites d’entreprises, et de fournisseurs de données professionnelles. La source rattachée à votre fiche peut vous être communiquée sur demande.</p>
<p>Nous ne demandons pas de données sensibles. La vérification d’une adresse et la préparation des messages peuvent être automatisées. Elles ne constituent pas une décision produisant des effets juridiques à votre égard. Nos emails ne comportent pas de pixel de suivi d’ouverture.</p>
<h2>Appels et enregistrements</h2>
<p>Lorsqu’un appel est enregistré, une annonce vous en informe et vous pouvez demander son arrêt. Vous pouvez également nous demander de poursuivre sans enregistrement. Les audios privés servent à transcrire l’échange, préparer un compte rendu et suivre les engagements convenus. Ils sont conservés trente jours par défaut, puis supprimés ; les transcriptions et comptes rendus suivent la durée de conservation de la fiche.</p>
<h2>Qui peut y accéder ?</h2>
<p>Les données sont accessibles à l’équipe de {company} et aux prestataires techniques nécessaires à la recherche et à la vérification des coordonnées, à l’hébergement de l’application, à l’envoi des emails, à la téléphonie, à la transcription et à la préparation automatisée des comptes rendus. Leur liste à jour est communiquée sur demande. Les données ne sont pas revendues.</p>
<h2>Combien de temps ?</h2>
<p>Les fiches expirent douze mois après leur collecte, ou après votre dernier échange entrant. Une réponse automatique d’absence et nos relances ne prolongent pas cette durée. À l’échéance, la prospection s’arrête et la fiche est effacée automatiquement lorsque aucune opération externe n’est en attente et qu’aucun dossier commercial ne nécessite un traitement distinct.</p>
<p>Un dossier de rendez-vous, de proposition ou de relation client fait l’objet d’une conservation adaptée à son suivi et aux obligations applicables. Des identifiants minimaux sont conservés dans une liste d’exclusion pour éviter de vous réimporter et de vous recontacter. Les sauvegardes sont séparées de la base active et ne servent pas à prospecter.</p>
<h2>Vos droits</h2>
<p>Vous pouvez demander l’accès à vos données, leur rectification, leur effacement ou la limitation de leur traitement, et vous opposer à tout moment à la prospection. Répondez à notre email ou écrivez à <a href="mailto:{contact}">{contact}</a>. Nous répondrons dans le délai applicable, en principe un mois.</p>
<p>Si vous estimez que vos droits ne sont pas respectés, vous pouvez adresser une réclamation à la <a href="https://www.cnil.fr/fr/plaintes">CNIL</a>.</p>
</main></body></html>"""


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def information():
    config = settings()
    return HTMLResponse(
        NOTICE.format(company=escape(config.company_name), contact=escape(config.privacy_contact))
    )


def erase(db, contact, actor_id=None):
    if db.scalar(
        select(m.Job.id).where(
            m.Job.contact_id == contact.id, m.Job.status.in_(["running", "waiting", "uncertain"])
        )
    ):
        raise HTTPException(409, "Réconciliez les opérations externes avant l’effacement.")
    if db.scalar(select(m.Opportunity.id).where(m.Opportunity.contact_id == contact.id)):
        raise HTTPException(
            409,
            "Cette fiche est liée à un suivi commercial : traitez sa conservation séparément avant l’effacement.",
        )
    from .call_models import CallRecording, CallSession
    from .mission_models import Mission, MissionStep

    for call in db.scalars(select(CallSession).where(CallSession.contact_id == contact.id)):
        if call.status in {"connecting", "in_progress"} or call.processing_status in {
            "processing",
            "transcribing",
        }:
            raise HTTPException(409, "Terminez l’appel ou son traitement avant l’effacement")
        for recording in db.scalars(select(CallRecording).where(CallRecording.call_id == call.id)):
            if recording.blob_ref or recording.provider_ref:
                key = "erase-audio:" + recording.id
                if not db.scalar(select(m.Job.id).where(m.Job.idempotency_key == key)):
                    db.add(
                        m.Job(
                            kind="erase_audio",
                            idempotency_key=key,
                            payload={"blob_ref": recording.blob_ref, "provider_ref": recording.provider_ref},
                        )
                    )
        mission = db.scalar(select(Mission).where(Mission.request_key == "post-call:" + call.id))
        if mission:
            db.delete(mission)
        db.delete(call)
    for step in db.scalars(select(MissionStep).where(MissionStep.contact_id == contact.id)):
        step.status, step.payload, step.result, step.title = "cancelled", {}, {}, "Fiche effacée"
    for action in db.scalars(select(m.AgentAction).where(m.AgentAction.contact_id == contact.id)):
        action.status, action.payload, action.result = "cancelled", {}, {}
        action.title, action.human_reason, action.last_error, action.lease_hash = "Fiche effacée", "", "", ""
        action.lease_until = None
        for notice in db.scalars(
            select(m.ActionNotification).where(m.ActionNotification.action_id == action.id)
        ):
            db.delete(notice)
    account = d.get(db, m.Account, contact.account_id)
    identity = f"{account.domain}|{contact.first_name.strip().lower()}|{contact.last_name.strip().lower()}"
    identifiers = [("identity_digest", hashlib.sha256(identity.encode()).hexdigest())]
    if contact.linkedin_url:
        identifiers.append(
            ("linkedin_digest", hashlib.sha256(contact.linkedin_url.strip().lower().encode()).hexdigest())
        )
    identifiers.extend(
        (kind, value)
        for kind, value in [("email", contact.email), ("phone", contact.phone), ("contact", contact.id)]
        if value
    )
    for kind, value in identifiers:
        d.add_suppression(
            db, kind, value, "all", "Fiche effacée — ne pas réimporter en prospection", actor_id
        )
    for job in db.scalars(select(m.Job).where(m.Job.contact_id == contact.id)):
        job.payload, job.result, job.error, job.external_id = {}, {}, "", None
        job.status = "cancelled" if job.status == "queued" else job.status
    for source in db.scalars(select(m.Job).where(m.Job.kind == "discover")):
        accepted = source.result.get("accepted", {})
        indices = [int(index) for index, link in accepted.items() if link.get("contact_id") == contact.id]
        if indices:
            candidates = list(source.result.get("candidates", []))
            for index in indices:
                if index < len(candidates):
                    candidates[index] = {"erased": True}
            source.result = {**source.result, "candidates": candidates}
    for action in db.scalars(select(m.AgentAction).where(m.AgentAction.kind == "discover_leads")):
        leads = action.result.get("leads", [])
        if any(lead.get("id") == contact.id for lead in leads):
            action.result = {
                **action.result,
                "summary": "Résultat de recherche actualisé après effacement d’une fiche.",
                "leads": [lead for lead in leads if lead.get("id") != contact.id],
            }
    db.flush()
    db.execute(delete(m.Event).where(m.Event.contact_id == contact.id))
    db.delete(contact)


def tick():
    with SessionLocal() as db:
        d.state(db, lock=True)
        rows = list(
            db.scalars(
                select(m.Contact)
                .where(m.Contact.retention_until <= m.now())
                .order_by(m.Contact.retention_until)
                .limit(100)
            )
        )
        for contact in rows:
            campaign = db.get(m.Campaign, contact.campaign_id)
            if not campaign or not campaign.config.get("automatic"):
                continue
            account = db.get(m.Account, contact.account_id)
            if account and account.lifecycle != "prospect":
                d.cancel_pending(
                    db, contact, "Durée de conservation atteinte : dossier commercial à examiner"
                )
                contact.approved = False
                continue
            try:
                erase(db, contact)
            except HTTPException:
                d.cancel_pending(db, contact, "Durée de conservation atteinte")
                contact.approved = False
        db.commit()
