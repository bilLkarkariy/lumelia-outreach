"""Fill an empty workspace with fictional demo data.

    python -m scripts.seed_demo

The script drives the product instead of writing rows: records are created through the HTTP API of
the running application, queued emails are handled by the worker loop in simulation mode, and
replies are logged through the endpoint the UI uses to record an answer by hand (a simulated email
never leaves the machine, so nothing can come back over IMAP).

Two steps have no HTTP entry point and call the application code in process:

* `qualify` hands fictional website pages to the pipeline function the worker calls after fetching
  a company site, so scores and statuses come from the real assessment;
* `leave_uncertain_purchase` reserves budget with the real reservation function, then marks the job
  the way the worker does when it dies between the request and the answer. Only a live provider
  call can produce that state.

Companies, people, domains and phone numbers are invented: domains live under example.com/.org and
phone numbers in the +33 1 99 00 range reserved for fiction. Amounts are placeholders, not results.
Nothing here opens a connection to anything but the local API.
"""

import os
import sys

import httpx
from sqlalchemy import func, select

from app import domain as d
from app import mail, prospect_pipeline
from app import models as m
from app.db import SessionLocal
from app.offers import PRIMARY_ID
from app.worker import tick

API_URL = os.environ.get("LUMELIA_DEMO_URL", "http://127.0.0.1:8000")
PASSWORD = os.environ.get("LUMELIA_DEMO_PASSWORD", "demo-password-1234")
SOURCE_NOTE = "Donnée fictive de démonstration."
PLACEHOLDER_SECRET = "demo-placeholder"  # Stored encrypted like a real credential, never used.

SEQUENCE = [
    {
        "channel": "email",
        "day": 0,
        "subject": "Suivi de vos propositions chez {{societe}}",
        "body": (
            "Bonjour {{prenom}},\n\n"
            "En tant que {{role}} chez {{societe}}, vous suivez sans doute plusieurs propositions "
            "restées sans réponse. Nous aidons des équipes comme la vôtre à reprendre ces dossiers "
            "chaque semaine, directement dans leur CRM.\n\n"
            "Seriez-vous ouvert à un échange de vingt minutes ?\n\n"
            "Bien cordialement,\nCamille"
        ),
    },
    {
        "channel": "linkedin",
        "day": 2,
        "body": "Bonjour {{prenom}}, je vous ai écrit au sujet du suivi des propositions chez {{societe}}.",
    },
    {
        "channel": "email",
        "day": 4,
        "subject": "Re: Suivi de vos propositions chez {{societe}}",
        "body": (
            "Bonjour {{prenom}},\n\n"
            "Je me permets de revenir vers vous. Un exemple concret : une revue hebdomadaire des "
            "opportunités ouvertes, avec une relance préparée pour chacune.\n\n"
            "Est-ce un sujet pour {{societe}} en ce moment ?\n\n"
            "Bien cordialement,\nCamille"
        ),
    },
    {
        "channel": "phone",
        "day": 7,
        "body": "Appeler {{prenom}} ({{role}}) : valider si le suivi des propositions est un sujet ce trimestre.",
    },
    {
        "channel": "email",
        "day": 10,
        "subject": "Dernier message — {{societe}}",
        "body": (
            "Bonjour {{prenom}},\n\n"
            "Sans retour de votre part, je ne vous relancerai plus. Si le sujet redevient d’actualité, "
            "il suffit de répondre à ce message.\n\n"
            "Bien cordialement,\nCamille"
        ),
    },
]

# name, domain, industry, employees, CRM signal
ACCOUNTS = [
    ("Orvalis Conseil", "orvalis-conseil.example.com", "Conseil en management", 42, "confirmed"),
    ("Tessandre Formation", "tessandre-formation.example.com", "Formation professionnelle", 28, "confirmed"),
    ("Belmorin Ingénierie", "belmorin-ingenierie.example.com", "Bureau d’études", 64, "signal"),
    ("Sarvène Expertise", "sarvene-expertise.example.com", "Expertise comptable", 35, "confirmed"),
    ("Lindorel RH", "lindorel-rh.example.com", "Recrutement", 22, "signal"),
    ("Maravel Études", "maravel-etudes.example.com", "Études de marché", 31, "confirmed"),
    ("Quivane Logistique", "quivane-logistique.example.com", "Logistique B2B", 88, "unknown"),
    ("Ardelys Courtage", "ardelys-courtage.example.com", "Courtage en assurance", 26, "confirmed"),
    ("Pontavel Juridique", "pontavel-juridique.example.com", "Services juridiques", 47, "signal"),
    ("Noviac Services", "noviac-services.example.com", "Services informatiques", 55, "confirmed"),
    ("Vescaut Industrie", "vescaut-industrie.example.com", "Maintenance industrielle", 140, "absent"),
    ("Colvène Architecture", "colvene-architecture.example.com", "Architecture", 18, "unknown"),
]
RESEARCHED = 11  # The last company stays untouched: the pipeline shows it as waiting for research.

# account index, first name, last name, role, has a verified email
PEOPLE = [
    (0, "Inès", "Morvan", "Directrice commerciale", True),
    (1, "Julien", "Castel", "Dirigeant", True),
    (2, "Sarah", "Belcourt", "Responsable commerciale", True),
    (3, "Thomas", "Rivaux", "Associé", True),
    (4, "Léa", "Fontenay", "Dirigeante", True),
    (5, "Marc", "Delorme", "Directeur commercial", True),
    (6, "Élodie", "Vasseur", "Responsable des opérations", True),
    (7, "Antoine", "Mercœur", "Dirigeant", True),
    (8, "Nadia", "Kerbrat", "Directrice du développement", False),
    (9, "Paul", "Séverin", "Directeur commercial", False),
]
INES, JULIEN, SARAH, THOMAS, LEA, MARC, ELODIE, ANTOINE, NADIA, PAUL = range(10)

# Contacts whose whole sequence is validated, so the worker simulates the first email. Élodie keeps
# drafts to validate and Antoine is left unprepared: the demo script does both by hand.
SEQUENCED = (INES, JULIEN, SARAH, THOMAS, LEA, MARC)

MAILBOXES = [
    ("camille@outreach-one.example.org", "mail.example.org"),
    ("camille@outreach-two.example.org", "mail.example.net"),
]


def main():
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(m.Account)):
            print("Demo data already present; nothing to do.")
            return
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        login = client.post("/api/auth/login", json={"password": PASSWORD})
        if login.status_code != 200:
            sys.exit("LUMELIA_DEMO_PASSWORD does not match LUMELIA_ACCESS_PASSWORD_HASH.")
        # Carried by hand: a cookie jar would drop the session cookie when it is flagged Secure.
        client.headers["Cookie"] = "lumelia_session=" + login.cookies["lumelia_session"]
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        seed(Api(client))
    print("Demo data ready.")


class Api:
    """Thin wrapper that fails loudly: a seed that half-succeeds is worse than none."""

    def __init__(self, client):
        self.client = client

    def call(self, method, path, body=None):
        response = self.client.request(method, "/api" + path, json=body)
        if response.status_code not in (200, 201):
            sys.exit(f"{method} {path} -> {response.status_code}: {response.text}")
        return response

    def get(self, path):
        return self.call("GET", path).json()

    def post(self, path, body=None):
        return self.call("POST", path, body).json()

    def patch(self, path, body):
        return self.call("PATCH", path, body).json()


def seed(api):
    campaigns = create_campaigns(api)
    accounts = [
        api.post(
            "/accounts",
            {
                "name": name,
                "domain": domain,
                "industry": industry,
                "employee_count": employees,
                "hubspot_status": crm,
            },
        )
        for name, domain, industry, employees, crm in ACCOUNTS
    ]
    contacts = [
        create_contact(api, accounts, campaigns, index, person) for index, person in enumerate(PEOPLE)
    ]
    # Registers one pipeline profile per company. Automatic research stays off: it fetches websites.
    api.post(
        "/prospect-pipeline/config",
        {"campaign_id": campaigns[0]["id"], "enabled": False, "auto_enrich": False},
    )
    qualify(accounts)
    configure_sending(api)
    configure_budgets(api, campaigns)
    run_sequences(api, campaigns, contacts)
    record_answers(api, contacts)
    leave_uncertain_purchase(contacts[PAUL]["id"])


def create_campaigns(api):
    icp = next(row for row in api.get("/icps") if row["offer_id"] == PRIMARY_ID)
    group = api.post(
        "/campaign-groups",
        {"name": "Revue du pipeline — PME de services", "offer_id": PRIMARY_ID, "notes": SOURCE_NOTE},
    )
    return [
        api.post(
            "/campaigns",
            {
                "name": name,
                "niche": niche,
                "group_id": group["id"],
                "icp_id": icp["id"],
                "offer_id": PRIMARY_ID,
                "budget_cents": 15000,
                "daily_limit": 20,
                "sequence": sequence,
            },
        )
        for name, niche, sequence in [
            ("Cabinets de conseil et d’expertise", "Conseil et expertise", SEQUENCE),
            ("Services aux entreprises", "Services B2B", [SEQUENCE[0], SEQUENCE[3], SEQUENCE[4]]),
        ]
    ]


def create_contact(api, accounts, campaigns, index, person):
    account_index, first, last, role, verified = person
    account = accounts[account_index]
    local = (first[0] + "." + last).lower().translate(str.maketrans("éèœï", "eeoi", ""))
    return api.post(
        "/contacts",
        {
            "account_id": account["id"],
            "campaign_id": campaigns[index % 2]["id"],
            "first_name": first,
            "last_name": last,
            "role": role,
            "email": f"{local}@{account['domain']}" if verified else None,
            "email_status": "valid" if verified else "unknown",
            "phone": f"+3319900{index + 10:04d}",
            "phone_type": "switchboard",
            "phone_status": "appropriate",
            "linkedin_url": f"https://www.linkedin.com/in/demo-{local.replace('.', '-')}",
            "professional_relevance": "Suit les propositions commerciales de l’équipe.",
            "sourcing_note": SOURCE_NOTE,
            "information_status": "planned",
        },
    )


def qualify(accounts):
    """Assess each company from a fictional page of its website, as the worker does after a fetch."""
    with SessionLocal() as db:
        d.state(db, lock=True)
        for account, (name, domain, industry, _, _) in list(zip(accounts, ACCOUNTS, strict=True))[
            :RESEARCHED
        ]:
            url = f"https://{domain}/"
            text = (
                f"{name} — {industry}. Nous accompagnons les directions générales et commerciales : "
                "nos services sont conçus pour les entreprises. " + SOURCE_NOTE
            )
            collected = {
                "pages": [{"url": url, "text": text}],
                "emails": {f"contact@{domain}": url},
                "sirens": {},
                "errors": [],
            }
            prospect_pipeline.apply_public(db, account["id"], collected, None)
        db.commit()


def configure_sending(api):
    """Two dedicated mailboxes and their shared ceilings. They stay unverified: checking connects."""
    for address, host in MAILBOXES:
        api.post(
            "/senders",
            {
                "provider_group": host,
                "config": {
                    "host": "smtp." + host,
                    "username": address,
                    "password": PLACEHOLDER_SECRET,
                    "from_email": address,
                    "from_name": "Camille (démo)",
                    "legal_footer": "Société de démonstration — 1 rue de l’Exemple, 75000 Paris.",
                    "privacy_url": "https://example.com/privacy",
                    "dns_verified": True,
                    "mailboxes": [
                        {
                            "host": "imap." + host,
                            "username": address,
                            "password": PLACEHOLDER_SECRET,
                            "address": address,
                        }
                    ],
                },
            },
        )
        api.post(
            "/email-limits",
            {"scope": "domain", "key": address.split("@")[1], "daily_limit": 40, "note": SOURCE_NOTE},
        )
        api.post("/email-limits", {"scope": "provider", "key": host, "daily_limit": 60, "note": SOURCE_NOTE})


def configure_budgets(api, campaigns):
    api.patch("/state", {"monthly_limit_cents": 30000})
    for provider, unit_cost in [("hunter", 10), ("fullenrich", 100)]:
        api.post(
            "/integrations/" + provider,
            {
                "config": {"api_key": PLACEHOLDER_SECRET},
                "unit_cost_cents": unit_cost,
                "monthly_limit_cents": 5000,
            },
        )
    for campaign in campaigns:
        api.post(f"/campaigns/{campaign['id']}/approve-budget")
    api.post(
        "/expenses",
        {
            "campaign_id": campaigns[0]["id"],
            "category": "enrichment",
            "amount_cents": 1200,
            "note": "Vérification d’adresses. " + SOURCE_NOTE,
        },
    )
    api.post(
        "/expenses",
        {"category": "mailbox", "amount_cents": 900, "note": "Boîtes dédiées. " + SOURCE_NOTE},
    )


def run_sequences(api, campaigns, contacts):
    """Prepare, validate and activate, then let the worker simulate what is due."""
    prepared = [contacts[index]["id"] for index in (*SEQUENCED, ELODIE)]
    api.post("/batches/prepare", {"contact_ids": prepared})
    api.post("/batches/approve", {"contact_ids": prepared})
    for index in SEQUENCED:
        for message in api.get("/messages?contact_id=" + contacts[index]["id"]):
            api.post(f"/messages/{message['id']}/approve")
    for campaign in campaigns:
        api.post(f"/campaigns/{campaign['id']}/activate")
    api.patch("/state", {"paused": False})
    while tick():
        pass


def record_answers(api, contacts):
    """One answer of each kind; each goes through the rule that stops the follow-ups."""
    answers = [
        (
            INES,
            "positive_reply",
            "Re: Suivi de vos propositions chez Orvalis Conseil\n"
            "Bonjour Camille, le sujet tombe bien : nous avons une quinzaine de propositions en attente. "
            "Je suis disponible jeudi matin pour en parler.",
        ),
        (
            JULIEN,
            "out_of_office",
            "Réponse automatique : absent\nJe suis absent jusqu’à lundi et répondrai à mon retour.",
        ),
        (
            SARAH,
            "bounce",
            "Undelivered Mail Returned to Sender\n550 5.1.1 Adresse inconnue : s.belcourt@belmorin-ingenierie.example.com",
        ),
    ]
    for index, kind, summary in answers:
        api.post(
            f"/contacts/{contacts[index]['id']}/events",
            {"kind": kind, "channel": "email", "summary": summary, "external_id": f"demo-{kind}"},
        )
    api.post(
        "/opportunities",
        {"contact_id": contacts[INES]["id"], "title": "Revue du pipeline — Orvalis Conseil"},
    )
    # The opt-out goes through the public link carried by every email.
    api.call("POST", "/unsubscribe/" + mail.unsubscribe_token(contacts[THOMAS]["id"]))


def leave_uncertain_purchase(contact_id):
    """An enrichment request whose answer never came back: budget stays reserved until a human reconciles."""
    with SessionLocal() as db:
        contact = d.get(db, m.Contact, contact_id)
        account = d.get(db, m.Account, contact.account_id)
        campaign = d.get(db, m.Campaign, contact.campaign_id)
        integration = db.scalar(select(m.Integration).where(m.Integration.provider == "fullenrich"))
        job = m.Job(
            kind="enrich",
            provider=integration.provider,
            contact_id=contact.id,
            campaign_id=campaign.id,
            idempotency_key=f"enrich:{integration.provider}:{contact.id}:demo",
            payload={
                "identity": {
                    "first_name": contact.first_name,
                    "last_name": contact.last_name,
                    "domain": account.domain,
                    "company_name": account.name,
                    "linkedin_url": contact.linkedin_url,
                    "email": contact.email,
                },
                "fields": ["email"],
            },
        )
        db.add(job)
        db.flush()
        d.reserve_budget(db, campaign, integration, job, integration.unit_cost_cents)
        job.status = "uncertain"
        job.error = (
            "Worker interrompu après demande : rapprocher le fournisseur avant toute nouvelle opération."
        )
        db.commit()


if __name__ == "__main__":
    main()
