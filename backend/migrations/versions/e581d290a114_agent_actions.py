"""Add the isolated agent queue, credentials and human notifications."""

import sqlalchemy as sa
from alembic import op

revision = "e581d290a114"
down_revision = "d492bc17e103"
branch_labels = None
depends_on = None


def identity():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "agent_clients",
        *identity(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("state", sa.JSON, nullable=False),
    )
    op.create_table(
        "agent_actions",
        *identity(),
        sa.Column("dedupe_key", sa.String(250), nullable=False, unique=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(220), nullable=False),
        sa.Column("executor", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        *[
            sa.Column(name + "_id", sa.String(36), sa.ForeignKey(table + ".id", ondelete=deletion))
            for name, table, deletion in [
                ("contact", "contacts", "CASCADE"),
                ("account", "accounts", "CASCADE"),
                ("campaign", "campaigns", "SET NULL"),
                ("task", "tasks", "CASCADE"),
                ("event", "events", "CASCADE"),
                ("client", "agent_clients", "SET NULL"),
            ]
        ],
        sa.Column("lease_hash", sa.String(64), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("target_url", sa.String(700), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("result", sa.JSON, nullable=False),
        sa.Column("human_reason", sa.Text, nullable=False),
        sa.Column("last_error", sa.Text, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("completion_key", sa.String(64)),
    )
    op.create_index("ix_agent_actions_queue", "agent_actions", ["status", "next_attempt_at", "priority"])
    op.create_table(
        "action_notifications",
        *identity(),
        sa.Column(
            "action_id", sa.String(36), sa.ForeignKey("agent_actions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("dedupe_key", sa.String(300), nullable=False, unique=True),
        sa.Column("title", sa.String(220), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("action_notifications")
    op.drop_table("agent_actions")
    op.drop_table("agent_clients")
