"""Сделки с версиями, очередь пересчёта, запуски ролла, снапшоты дня, outbox.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "trade",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("source", sa.Text),
        sa.Column("source_key", sa.Text),
        sa.UniqueConstraint("source", "source_key", name="trade_source_key"),
    )
    op.create_table(
        "trade_version",
        sa.Column(
            "trade_id",
            sa.Uuid,
            sa.ForeignKey("trade.id", name="trade_version_trade_fk"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("portfolio_id", sa.Uuid, nullable=False),
        sa.Column("instrument_id", sa.Uuid, nullable=False),
        sa.Column("instrument_kind", sa.Text, nullable=False),
        sa.Column("secid", sa.Text, nullable=False),
        sa.Column("short_name", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("quantity", sa.BigInteger, nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("face_value", sa.Numeric(18, 6)),
        sa.Column("accrued", sa.Numeric(18, 2)),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("fee", sa.Numeric(18, 2), nullable=False),
        sa.Column("trade_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("settle_date", sa.Date, nullable=False),
        sa.Column("venue", sa.Text, nullable=False),
        sa.Column("counterparty", sa.Text),
        sa.Column("external_id", sa.Text),
        sa.Column("comment", sa.Text),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trade_id", "number", name="trade_version_pk"),
        sa.CheckConstraint(
            "status IN ('entered', 'confirmed', 'cancelled')", name="trade_version_status"
        ),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="trade_version_side"),
        sa.CheckConstraint("venue IN ('exchange', 'otc')", name="trade_version_venue"),
        sa.CheckConstraint(
            "instrument_kind IN ('bond', 'fund_share')", name="trade_version_instrument_kind"
        ),
        sa.CheckConstraint(
            "quantity > 0 AND price > 0 AND fee >= 0", name="trade_version_positive"
        ),
        sa.CheckConstraint("settle_date >= trade_date", name="trade_version_dates"),
    )
    op.create_index("trade_version_portfolio", "trade_version", ["portfolio_id", "trade_date"])
    op.create_index("trade_version_instrument", "trade_version", ["instrument_id"])
    op.create_table(
        "recalc_queue",
        sa.Column("portfolio_id", sa.Uuid, primary_key=True),
        sa.Column("since", sa.Date, nullable=False),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "roll_run",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("portfolio_id", sa.Uuid, nullable=False),
        sa.Column("since", sa.Date, nullable=False),
        sa.Column("till", sa.Date, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "day_snapshot",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("portfolio_id", sa.Uuid, nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column(
            "run_id",
            sa.Uuid,
            sa.ForeignKey("roll_run.id", name="day_snapshot_run_fk"),
            nullable=False,
        ),
        sa.Column("state", postgresql.JSONB, nullable=False),
        sa.Column("replaced_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "day_snapshot_current",
        "day_snapshot",
        ["portfolio_id", "day"],
        unique=True,
        postgresql_where=sa.text("replaced_at IS NULL"),
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
