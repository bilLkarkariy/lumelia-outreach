"""Keep unknown inbound callers without inventing a company."""

import sqlalchemy as sa
from alembic import op

revision = "c925f734e558"
down_revision = "b814e623d447"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("call_sessions", "account_id", existing_type=sa.String(36), nullable=True)


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM call_sessions WHERE account_id IS NULL")):
        raise RuntimeError("Rattacher les appels inconnus avant la rétrogradation ; aucun appel supprimé.")
    op.alter_column("call_sessions", "account_id", existing_type=sa.String(36), nullable=False)
