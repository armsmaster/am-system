"""Бумаги, цены, бары, точки НКД и номинала, правила оценки, ручные цены, outbox.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None

PRICE = sa.Numeric(18, 6)
PRICE_TYPES = "'vwap', 'last', 'bid', 'ask', 'close', 'market_price', 'admitted_quote'"


def _received_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _instrument_id(name: str) -> sa.Column[sa.Uuid]:
    return sa.Column(
        "instrument_id",
        sa.Uuid,
        sa.ForeignKey("instrument.id", name=f"{name}_instrument_fk"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "instrument",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("secid", sa.Text, nullable=False, unique=True),
        sa.Column("isin", sa.Text),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("short_name", sa.Text, nullable=False),
        sa.Column("board", sa.Text),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("history_till", sa.Date),
        sa.Column("history_claimed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('bond', 'fund_share')", name="instrument_kind"),
    )
    op.create_table(
        "quote",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        _instrument_id("quote"),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("price_type", sa.Text, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", PRICE, nullable=False),
        _received_at(),
        sa.Column("batch_id", sa.Uuid),
        sa.CheckConstraint(f"price_type IN ({PRICE_TYPES})", name="quote_price_type"),
        sa.CheckConstraint("value > 0", name="quote_value_positive"),
    )
    op.create_index("quote_lookup", "quote", ["instrument_id", "price_type", "observed_at"])
    op.create_table(
        "bar",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        _instrument_id("bar"),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("timeframe", sa.Integer, nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", PRICE, nullable=False),
        sa.Column("high", PRICE, nullable=False),
        sa.Column("low", PRICE, nullable=False),
        sa.Column("close", PRICE, nullable=False),
        sa.Column("volume", sa.Numeric(20, 4)),
        _received_at(),
        sa.Column("batch_id", sa.Uuid, nullable=False),
        sa.CheckConstraint("timeframe BETWEEN 1 AND 240", name="bar_timeframe"),
    )
    op.create_index("bar_lookup", "bar", ["instrument_id", "timeframe", "start_at"])
    for name in ("accrued_interest", "face_value"):
        op.create_table(
            name,
            _instrument_id(name),
            sa.Column("as_of", sa.Date, nullable=False),
            sa.Column("source", sa.Text, nullable=False),
            sa.Column("value", PRICE, nullable=False),
            _received_at(),
            sa.PrimaryKeyConstraint("instrument_id", "as_of", "source", name=f"{name}_pk"),
            sa.CheckConstraint(
                "source IN ('iss_history', 'iss_settlement')", name=f"{name}_source"
            ),
        )
    op.create_table(
        "price_rule",
        _instrument_id("price_rule"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_types", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("dema_timeframe", sa.Integer),
        sa.Column("dema_half_life", sa.Numeric(10, 2)),
        sa.Column("spread_threshold_bp", sa.Integer, nullable=False),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("comment", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("instrument_id", "valid_from", name="price_rule_pk"),
    )
    op.create_table(
        "manual_price",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        _instrument_id("manual_price"),
        sa.Column("value", PRICE, nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("comment", sa.Text, nullable=False),
        sa.CheckConstraint("value > 0", name="manual_price_value_positive"),
    )
    op.create_index("manual_price_lookup", "manual_price", ["instrument_id", "entered_at"])
    op.create_table(
        "feed_batch",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("client", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted", sa.Integer, nullable=False),
        sa.Column("rejected", postgresql.JSONB, nullable=False),
        sa.CheckConstraint("kind IN ('quotes', 'bars')", name="feed_batch_kind"),
    )
    op.create_index("feed_batch_recent", "feed_batch", ["received_at"])
    op.create_table(
        "iss_poll",
        sa.Column("slot", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("polled", sa.Integer, nullable=False, server_default="0"),
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
