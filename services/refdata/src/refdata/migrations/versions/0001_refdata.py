"""Инструменты, версии, торговый календарь, ежедневная синхронизация и outbox.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "instrument",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("secid", sa.Text, nullable=False, unique=True),
        sa.Column("isin", sa.Text),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("short_name", sa.Text, nullable=False),
        sa.Column("added_by", sa.Text, nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("iss_snapshot", postgresql.JSONB),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_error", sa.Text),
        sa.CheckConstraint("kind IN ('bond', 'fund_share')", name="instrument_kind"),
    )
    op.create_table(
        "instrument_version",
        sa.Column(
            "instrument_id",
            sa.Uuid,
            sa.ForeignKey("instrument.id", name="instrument_version_instrument_fk"),
            primary_key=True,
        ),
        sa.Column("number", sa.Integer, primary_key=True),
        sa.Column("valid_from", sa.Date),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("pinned", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False),
        sa.CheckConstraint("source IN ('iss', 'manual')", name="instrument_version_source"),
    )
    op.create_table(
        "trading_day",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("is_trading", sa.Boolean, nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("updated_by", sa.Text, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "sync_run",
        sa.Column("run_date", sa.Date, primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("checked", sa.Integer, nullable=False, server_default="0"),
        sa.Column("changed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer, nullable=False, server_default="0"),
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
