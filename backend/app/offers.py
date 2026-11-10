"""Offer catalogue (fictional demo offers). Prices are never email template variables."""

from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from . import models as m

PRIMARY_ID = "pipeline-review-v1"
CATALOG = [
    {
        "id": PRIMARY_ID,
        "name": "Revue du pipeline commercial",
        "status": "primary",
        "sort_order": 1,
        "summary": "Remettre à plat les opportunités ouvertes et installer un suivi hebdomadaire dans le CRM existant.",
        "brief": {
            "audience": "PME de services B2B en France, environ 20 à 100 salariés, utilisant HubSpot pour leurs ventes.",
            "buyer": "Dirigeant ou responsable commercial",
            "duration": "10 jours ouvrés",
            "usage": "Offre principale de prospection. L’usage commercial du CRM reste à confirmer pour chaque compte.",
            "deliverables": [
                "Revue des opportunités ouvertes",
                "Suivi hebdomadaire dans le pipeline existant",
                "Relances avec contrôle humain",
                "Passation à l’équipe",
            ],
            "boundaries": [
                "Un pipeline et jusqu’à 100 opportunités existantes",
                "Pas de migration de CRM",
            ],
            "criteria": {
                "country": "FR",
                "min_employees": 20,
                "max_employees": 100,
                "crm": "HubSpot",
                "buyer_roles": ["Dirigeant", "Directeur commercial"],
                "process": "Suivi des opportunités ouvertes",
            },
        },
        "pricing": [
            {
                "name": "Installation",
                "min_cents": 400000,
                "max_cents": 400000,
                "period": "once",
                "note": "Montant fictif de démonstration.",
            },
            {
                "name": "Suivi optionnel",
                "min_cents": 50000,
                "max_cents": 50000,
                "period": "month",
                "note": "Option après installation.",
            },
        ],
        "source": {"title": "Catalogue de démonstration", "reference": "docs/DEMO.md"},
    },
    {
        "id": "process-sprint-v1",
        "name": "Sprint d’automatisation",
        "status": "network",
        "sort_order": 2,
        "summary": "Automatiser un processus répétitif dans les outils de l’équipe, avec contrôle humain et formation.",
        "brief": {
            "audience": "Équipes de PME confrontées à un processus répétitif clairement identifié.",
            "buyer": "Dirigeant ou responsable du processus",
            "duration": "15 jours",
            "usage": "Répondre aux demandes issues du réseau. Réservée aux campagnes de suivi du réseau.",
            "deliverables": [
                "Automatisation d’un processus",
                "Points de contrôle humain",
                "Suivi des erreurs",
                "Formation et transmission",
            ],
            "boundaries": ["Un processus et deux applications au maximum"],
            "criteria": {
                "country": "FR",
                "min_employees": 10,
                "max_employees": 100,
                "buyer_roles": ["Dirigeant", "Responsable des opérations"],
                "process": "Processus répétitif identifié",
            },
        },
        "pricing": [
            {
                "name": "Sprint",
                "min_cents": 600000,
                "max_cents": 600000,
                "period": "once",
                "note": "Montant fictif de démonstration.",
            }
        ],
        "source": {"title": "Catalogue de démonstration", "reference": "docs/DEMO.md"},
    },
    {
        "id": "inbound-desk-v1",
        "name": "Traitement des demandes entrantes",
        "status": "historical",
        "sort_order": 3,
        "summary": "Installer puis exploiter le traitement des demandes entrantes dans le CRM.",
        "brief": {
            "audience": "Cible historique : entreprises équipées de HubSpot recevant un volume élevé de demandes.",
            "buyer": "Dirigeant ou responsable commercial",
            "duration": "4 à 6 semaines d’installation",
            "usage": "Offre archivée. Conservée comme référence, indisponible pour une nouvelle campagne.",
            "deliverables": [
                "Traitement des demandes entrantes dans le CRM",
                "Installation puis exploitation continue",
            ],
            "boundaries": ["Offre retirée du catalogue actif."],
            "criteria": {"country": "FR", "crm": "HubSpot", "process": "Traitement des demandes entrantes"},
        },
        "pricing": [
            {
                "name": "Installation historique",
                "min_cents": 900000,
                "max_cents": 900000,
                "period": "once",
                "note": "Offre archivée.",
            }
        ],
        "source": {"title": "Catalogue de démonstration", "reference": "docs/DEMO.md"},
    },
    {
        "id": "automation-audit-v1",
        "name": "Audit des automatisations",
        "status": "experimental",
        "sort_order": 4,
        "summary": "Auditer, remettre en état puis surveiller les automatisations qui tournent déjà.",
        "brief": {
            "audience": "Entreprises avec des automatisations en fonctionnement et un responsable opérationnel identifié.",
            "buyer": "Dirigeant ou responsable des opérations",
            "duration": "Audit de 3 jours ; remise en état selon le périmètre",
            "usage": "Proposition à tester. Confirmer les automatisations en place et la responsabilité du suivi.",
            "deliverables": [
                "Audit",
                "Remise en état des automatisations retenues",
                "Surveillance récurrente",
            ],
            "boundaries": [
                "Trois prestations distinctes",
                "Périmètre de remise en état à chiffrer après l’audit",
                "Aucun CRM imposé",
            ],
            "criteria": {
                "country": "FR",
                "buyer_roles": ["Dirigeant", "Responsable des opérations"],
                "process": "Contrôle des automatisations existantes",
            },
        },
        "pricing": [
            {
                "name": "Audit",
                "min_cents": 200000,
                "max_cents": 200000,
                "period": "once",
                "note": "3 jours.",
            },
            {
                "name": "Remise en état",
                "min_cents": 300000,
                "max_cents": 900000,
                "period": "once",
                "note": "Selon le périmètre.",
            },
            {
                "name": "Surveillance",
                "min_cents": 40000,
                "max_cents": 90000,
                "period": "month",
                "note": "Selon le périmètre.",
            },
        ],
        "source": {"title": "Catalogue de démonstration", "reference": "docs/DEMO.md"},
    },
]


def seed(db):
    for definition in CATALOG:
        if not db.get(m.Offer, definition["id"]):
            db.add(m.Offer(**deepcopy(definition)))
    db.flush()
    for definition in CATALOG:
        if definition["status"] == "historical":
            continue
        if not db.scalar(select(m.ICP.id).where(m.ICP.offer_id == definition["id"]).limit(1)):
            db.add(
                m.ICP(
                    name=definition["name"] + " — France",
                    offer_id=definition["id"],
                    offer=definition["name"],
                    criteria=deepcopy(definition["brief"]["criteria"]),
                    exclusions=["Client existant", "Opposition", "Opportunité active", "Activité B2C"],
                    notes=definition["brief"]["usage"],
                )
            )


def campaign_reasons(db, offer_id, icp_id, purpose):
    offer = db.get(m.Offer, offer_id)
    icp = db.get(m.ICP, icp_id)
    if not offer:
        return ["Choisissez une offre du catalogue."]
    if not icp or icp.offer_id != offer.id:
        return ["Le profil cible doit dépendre de la même offre que la campagne."]
    if offer.status == "historical":
        return ["Cette offre est archivée et ne peut pas lancer de campagne."]
    if offer.status == "network" and purpose != "network":
        return ["Cette offre est réservée au suivi du réseau."]
    return []


def validate_campaign(db, data):
    reasons = campaign_reasons(db, data.offer_id, data.icp_id, data.purpose)
    if reasons:
        raise HTTPException(422, reasons[0])


def icp_values(db, data):
    offer = db.get(m.Offer, data.offer_id)
    if not offer:
        raise HTTPException(422, "Choisissez une offre du catalogue.")
    return {**data.model_dump(), "offer": offer.name}
