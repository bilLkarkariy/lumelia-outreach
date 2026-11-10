"""Preserve email content and conversation reply metadata; additive for old workers."""

import sqlalchemy as sa
from alembic import op

revision = "d492bc17e103"
down_revision = "c31ab64e9207"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind, default in [
        ("purpose", sa.String(20), "sequence"),
        ("recipient_email", sa.String(320), ""),
        ("in_reply_to", sa.String(998), ""),
        ("reference_ids", sa.JSON, "[]"),
        ("sent_body", sa.Text, ""),
    ]:
        op.add_column("messages", sa.Column(name, kind, nullable=False, server_default=default))
    op.add_column("messages", sa.Column("reply_event_id", sa.String(36), nullable=True))
    op.create_index("ix_events_contact_created", "events", ["contact_id", "created_at"])
    op.create_index("ix_messages_contact_created", "messages", ["contact_id", "created_at"])


def downgrade():
    op.drop_index("ix_messages_contact_created", table_name="messages")
    op.drop_index("ix_events_contact_created", table_name="events")
    for name in ["reply_event_id", "sent_body", "reference_ids", "in_reply_to", "recipient_email", "purpose"]:
        op.drop_column("messages", name)
