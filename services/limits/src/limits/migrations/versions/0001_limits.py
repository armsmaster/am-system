"""Наборы лимитов фонда с версиями, outbox.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "limit_set",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("fund_id", sa.Uuid, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("body", postgresql.JSONB, nullable=False),
        sa.Column("comment", sa.Text, nullable=False),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fund_id", "version", name="limit_set_version"),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.Uuid, nullable=False, unique=True),
        sa.Column("stream", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "outbox_unpublished", "outbox", ["id"], postgresql_where=sa.text("published_at IS NULL")
    )
