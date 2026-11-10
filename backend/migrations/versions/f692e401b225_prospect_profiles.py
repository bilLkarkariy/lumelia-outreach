"""A prospect exists before a named contact; preserve all collected accounts."""

import sqlalchemy as sa
from alembic import op

revision = "f692e401b225"
down_revision = "e581d290a114"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prospect_profiles",
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("facts", sa.JSON, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True)),
        sa.Column("next_action", sa.Text, nullable=False),
        sa.Column("call_brief", sa.Text, nullable=False),
        sa.Column("error", sa.Text, nullable=False),
    )
    op.create_index("ix_prospect_profiles_status", "prospect_profiles", ["status"])
    op.create_index("ix_prospect_profiles_score", "prospect_profiles", ["score"])
    op.execute("""INSERT INTO prospect_profiles(account_id,status,score,facts,attempts,next_action,call_brief,error)
        SELECT id, CASE WHEN lifecycle <> 'prospect' THEN 'excluded' ELSE 'pending' END,
        0, '{}', 0, 'Qualifier l’entreprise et identifier un interlocuteur', '', '' FROM accounts""")


def downgrade():
    op.drop_table("prospect_profiles")
