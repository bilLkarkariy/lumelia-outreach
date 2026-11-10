"""Repair NUL characters in historical JSON without deleting business records.

Revision ID: b814e623d447
Revises: a703f512c336
"""

import sqlalchemy as sa
from alembic import op

revision = "b814e623d447"
down_revision = "a703f512c336"
branch_labels = None
depends_on = None


def clean(value):
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd")
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {clean(key): clean(item) for key, item in value.items()}
    return value


def upgrade():
    bind = op.get_bind()
    metadata = sa.MetaData()
    for name in sa.inspect(bind).get_table_names():
        table = sa.Table(name, metadata, autoload_with=bind)
        columns = [column for column in table.columns if isinstance(column.type, sa.JSON)]
        keys = list(table.primary_key.columns)
        if not columns or not keys:
            continue
        query = sa.select(*keys, *columns).where(
            sa.or_(*[sa.func.strpos(sa.cast(column, sa.Text), r"\u0000") > 0 for column in columns])
        )
        for row in bind.execute(query).mappings():
            values = {
                column.name: clean(row[column.name])
                for column in columns
                if clean(row[column.name]) != row[column.name]
            }
            if values:
                bind.execute(
                    sa.update(table).where(sa.and_(*[key == row[key.name] for key in keys])).values(**values)
                )


def downgrade():
    # Unsupported control characters must not be reintroduced. The encrypted pre-migration backup retains the original.
    pass
