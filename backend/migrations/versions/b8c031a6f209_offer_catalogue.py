"""Offer catalogue and required campaign ownership; preserve every existing ICP.

Revision ID: b8c031a6f209
Revises: 3ae5f6ffedc6
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "b8c031a6f209"
down_revision = "3ae5f6ffedc6"
branch_labels = None
depends_on = None

# Frozen initial catalogue: independent from later application changes.
CATALOG = [
    {
        "id": "pipeline-review-v1",
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


def upgrade():
    table = op.create_table(
        "offers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("brief", sa.JSON(), nullable=False),
        sa.Column("pricing", sa.JSON(), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
    )
    at = datetime.now(UTC)
    op.bulk_insert(table, [{**row, "created_at": at, "updated_at": at} for row in CATALOG])
    op.add_column("icps", sa.Column("offer_id", sa.String(36), nullable=True))
    op.add_column("campaigns", sa.Column("offer_id", sa.String(36), nullable=True))
    op.add_column(
        "campaigns", sa.Column("purpose", sa.String(30), server_default="cold_outreach", nullable=False)
    )
    conn = op.get_bind()
    names = {row["name"].casefold(): row["id"] for row in CATALOG}
    for icp in conn.execute(sa.text("SELECT id, offer FROM icps")).mappings():
        offer_id = names.get(icp["offer"].strip().casefold())
        if not offer_id:
            # Unrecognised legacy offers are retained, never silently reassigned.
            offer_id = icp["id"]
            conn.execute(
                table.insert().values(
                    id=offer_id,
                    name=icp["offer"] or "Offre historique",
                    status="historical",
                    sort_order=99,
                    summary="Offre conservée depuis un profil antérieur au catalogue.",
                    brief={"usage": "À requalifier avant une nouvelle campagne."},
                    pricing=[],
                    source={"title": "Profil existant"},
                    created_at=at,
                    updated_at=at,
                )
            )
        conn.execute(
            sa.text("UPDATE icps SET offer_id = :offer WHERE id = :id"), {"offer": offer_id, "id": icp["id"]}
        )
    conn.execute(
        sa.text("UPDATE campaigns SET offer_id = icps.offer_id FROM icps WHERE campaigns.icp_id = icps.id")
    )
    conn.execute(sa.text("UPDATE campaigns SET purpose = 'network' WHERE offer_id = 'process-sprint-v1'"))
    op.alter_column("icps", "offer_id", nullable=False)
    op.alter_column("campaigns", "offer_id", nullable=False)
    op.create_foreign_key("fk_icps_offer", "icps", "offers", ["offer_id"], ["id"])
    op.create_foreign_key("fk_campaigns_offer", "campaigns", "offers", ["offer_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_campaigns_offer", "campaigns", type_="foreignkey")
    op.drop_constraint("fk_icps_offer", "icps", type_="foreignkey")
    op.drop_column("campaigns", "purpose")
    op.drop_column("campaigns", "offer_id")
    op.drop_column("icps", "offer_id")
    op.drop_table("offers")
