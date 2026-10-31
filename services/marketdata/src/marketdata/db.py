"""Схема БД рыночных данных.

Цены и бары только дописываются: исправление — новая строка, старая остаётся.
Действует последнее полученное значение; по истории видно, что система знала
в любой момент.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from am_common.outbox import outbox_table

metadata = MetaData()

PRICE = Numeric(18, 6)
PRICE_TYPES = "'vwap', 'last', 'bid', 'ask', 'close', 'market_price', 'admitted_quote'"

# Копия списка бумаг из справочника: цены принимаются, даже когда справочник недоступен.
instrument = Table(
    "instrument",
    metadata,
    Column("id", Uuid, primary_key=True),  # id бумаги в справочнике
    Column("secid", Text, nullable=False, unique=True),
    Column("isin", Text),
    Column("kind", Text, nullable=False),
    Column("short_name", Text, nullable=False),
    Column("board", Text),  # основной режим торгов — для запросов к ISS
    Column("seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # История ISS загружена по эту дату; захват — чтобы её грузил один экземпляр.
    Column("history_till", Date),
    Column("history_claimed_at", DateTime(timezone=True)),
    CheckConstraint("kind IN ('bond', 'fund_share')", name="instrument_kind"),
)

quote = Table(
    "quote",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column(
        "instrument_id",
        Uuid,
        ForeignKey("instrument.id", name="quote_instrument_fk"),
        nullable=False,
    ),
    Column("source", Text, nullable=False),
    Column("price_type", Text, nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("value", PRICE, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("batch_id", Uuid),  # пакет скрипта; у цен ISS — пусто
    CheckConstraint(f"price_type IN ({PRICE_TYPES})", name="quote_price_type"),
    CheckConstraint("value > 0", name="quote_value_positive"),
    Index("quote_lookup", "instrument_id", "price_type", "observed_at"),
)

bar = Table(
    "bar",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column(
        "instrument_id", Uuid, ForeignKey("instrument.id", name="bar_instrument_fk"), nullable=False
    ),
    Column("source", Text, nullable=False),
    Column("timeframe", Integer, nullable=False),  # минуты
    Column("start_at", DateTime(timezone=True), nullable=False),
    Column("open", PRICE, nullable=False),
    Column("high", PRICE, nullable=False),
    Column("low", PRICE, nullable=False),
    Column("close", PRICE, nullable=False),
    Column("volume", Numeric(20, 4)),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("batch_id", Uuid, nullable=False),
    CheckConstraint("timeframe BETWEEN 1 AND 240", name="bar_timeframe"),
    Index("bar_lookup", "instrument_id", "timeframe", "start_at"),
)


def _points(name: str) -> Table:
    """Точки ISS на дату: в истории — на дату торгов, в данных дня — на дату расчётов."""
    return Table(
        name,
        metadata,
        Column(
            "instrument_id",
            Uuid,
            ForeignKey("instrument.id", name=f"{name}_instrument_fk"),
            nullable=False,
        ),
        Column("as_of", Date, nullable=False),
        Column("source", Text, nullable=False),  # iss_history | iss_settlement
        Column("value", PRICE, nullable=False),
        Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        PrimaryKeyConstraint("instrument_id", "as_of", "source", name=f"{name}_pk"),
        CheckConstraint("source IN ('iss_history', 'iss_settlement')", name=f"{name}_source"),
    )


accrued_interest = _points("accrued_interest")
face_value = _points("face_value")

# Правило оценки бумаги с историей: оценка на прошлый момент берёт правило того времени.
price_rule = Table(
    "price_rule",
    metadata,
    Column(
        "instrument_id",
        Uuid,
        ForeignKey("instrument.id", name="price_rule_instrument_fk"),
        nullable=False,
    ),
    Column("valid_from", DateTime(timezone=True), nullable=False),
    Column("price_types", ARRAY(Text), nullable=False),
    Column("dema_timeframe", Integer),
    Column("dema_half_life", Numeric(10, 2)),
    Column("spread_threshold_bp", Integer, nullable=False),
    Column("author", Text, nullable=False),
    Column("comment", Text, nullable=False),
    PrimaryKeyConstraint("instrument_id", "valid_from", name="price_rule_pk"),
)

manual_price = Table(
    "manual_price",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column(
        "instrument_id",
        Uuid,
        ForeignKey("instrument.id", name="manual_price_instrument_fk"),
        nullable=False,
    ),
    Column("value", PRICE, nullable=False),
    Column("entered_at", DateTime(timezone=True), nullable=False),
    Column("author", Text, nullable=False),
    Column("comment", Text, nullable=False),
    CheckConstraint("value > 0", name="manual_price_value_positive"),
    Index("manual_price_lookup", "instrument_id", "entered_at"),
)

feed_batch = Table(
    "feed_batch",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("client", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("accepted", Integer, nullable=False),
    Column("rejected", JSONB, nullable=False),
    CheckConstraint("kind IN ('quotes', 'bars', 'rates')", name="feed_batch_kind"),
    Index("feed_batch_recent", "received_at"),
)

# Денежные ставки на дату: из ISS (source = 'iss') и от скрипта (source — код источника).
rate = Table(
    "rate",
    metadata,
    Column("kind", Text, nullable=False),
    Column("as_of", Date, nullable=False),
    Column("source", Text, nullable=False),
    Column("value", Numeric(10, 4), nullable=False),  # % годовых
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("batch_id", Uuid),  # пакет скрипта; у ставок ISS — пусто
    PrimaryKeyConstraint("kind", "as_of", "source", name="rate_pk"),
    CheckConstraint("kind IN ('key_rate', 'ruonia', 'rusfar')", name="rate_kind"),
)

# Опрос ISS внутри дня: одна запись на 5-минутный слот — его выполняет один экземпляр.
iss_poll = Table(
    "iss_poll",
    metadata,
    Column("slot", DateTime(timezone=True), primary_key=True),
    Column("finished_at", DateTime(timezone=True)),
    Column("polled", Integer, nullable=False, server_default="0"),
    Column("failed", Integer, nullable=False, server_default="0"),
)

outbox = outbox_table(metadata)
