"""Group campaigns by offer and preserve existing sequences and contacts."""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "c31ab64e9207"
down_revision = "b8c031a6f209"
branch_labels = None
depends_on = None


def upgrade():
    groups = op.create_table(
        "campaign_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("offer_id", sa.String(36), sa.ForeignKey("offers.id"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
    )
    op.add_column("campaigns", sa.Column("group_id", sa.String(36), nullable=True))
    op.add_column("campaigns", sa.Column("niche", sa.String(160), server_default="", nullable=False))
    op.create_foreign_key("fk_campaigns_group", "campaigns", "campaign_groups", ["group_id"], ["id"])
    op.create_index("ix_campaigns_group_id", "campaigns", ["group_id"])
    conn = op.get_bind()
    at = datetime.now(UTC)
    for offer in conn.execute(
        sa.text(
            "SELECT DISTINCT offers.id, offers.name FROM offers JOIN campaigns ON campaigns.offer_id = offers.id"
        )
    ).mappings():
        group_id = str(uuid.uuid4())
        conn.execute(
            groups.insert().values(
                id=group_id,
                name=offer["name"],
                offer_id=offer["id"],
                notes="Campagnes existantes conservées ; niches à préciser.",
                created_at=at,
                updated_at=at,
            )
        )
        conn.execute(
            sa.text("UPDATE campaigns SET group_id = :group, niche = :niche WHERE offer_id = :offer"),
            {"group": group_id, "niche": "Ciblage initial", "offer": offer["id"]},
        )


def downgrade():
    op.drop_index("ix_campaigns_group_id", table_name="campaigns")
    op.drop_constraint("fk_campaigns_group", "campaigns", type_="foreignkey")
    op.drop_column("campaigns", "niche")
    op.drop_column("campaigns", "group_id")
    op.drop_table("campaign_groups")
