"""Схема БД сделок и позиций.

Сделка не меняется и не удаляется: правка, подтверждение и отмена — новая
версия. Снапшот дня не удаляется при пересчёте, а помечается замещённым
(решение 8): видно, что система показывала до исправления.
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
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from am_common.outbox import outbox_table

metadata = MetaData()

trade = Table(
    "trade",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Text, nullable=False),
    # Ключ импорта (часть 5.3): источник и номер сделки в нём. Ручной ввод — пусто.
    Column("source", Text),
    Column("source_key", Text),
    UniqueConstraint("source", "source_key", name="trade_source_key"),
)

trade_version = Table(
    "trade_version",
    metadata,
    Column("trade_id", Uuid, ForeignKey("trade.id", name="trade_version_trade_fk"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("portfolio_id", Uuid, nullable=False),
    Column("instrument_id", Uuid, nullable=False),
    # Как бумага называлась при вводе — для списков без похода в справочник.
    Column("instrument_kind", Text, nullable=False),
    Column("secid", Text, nullable=False),
    Column("short_name", Text, nullable=False),
    Column("side", Text, nullable=False),
    Column("quantity", BigInteger, nullable=False),
    Column("price", Numeric(18, 6), nullable=False),
    Column("face_value", Numeric(18, 6)),  # облигация: номинал на дату расчётов
    Column("accrued", Numeric(18, 2)),  # облигация: НКД на бумагу
    Column("amount", Numeric(20, 2), nullable=False),
    Column("fee", Numeric(18, 2), nullable=False),
    Column("trade_at", DateTime(timezone=True), nullable=False),
    Column("trade_date", Date, nullable=False),  # по Москве
    Column("settle_date", Date, nullable=False),
    Column("venue", Text, nullable=False),
    Column("counterparty", Text),
    Column("external_id", Text),  # номер сделки у биржи или брокера
    Column("comment", Text),
    Column("author", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("trade_id", "number", name="trade_version_pk"),
    CheckConstraint("status IN ('entered', 'confirmed', 'cancelled')", name="trade_version_status"),
    CheckConstraint("side IN ('buy', 'sell')", name="trade_version_side"),
    CheckConstraint("venue IN ('exchange', 'otc')", name="trade_version_venue"),
    CheckConstraint(
        "instrument_kind IN ('bond', 'fund_share')", name="trade_version_instrument_kind"
    ),
    CheckConstraint("quantity > 0 AND price > 0 AND fee >= 0", name="trade_version_positive"),
    CheckConstraint("settle_date >= trade_date", name="trade_version_dates"),
    Index("trade_version_portfolio", "portfolio_id", "trade_date"),
    Index("trade_version_instrument", "instrument_id"),
)

# Очередь пересчёта: портфель и дата, с которой его состояние могло измениться.
recalc_queue = Table(
    "recalc_queue",
    metadata,
    Column("portfolio_id", Uuid, primary_key=True),
    Column("since", Date, nullable=False),
    Column("marked_at", DateTime(timezone=True), nullable=False),
    # Захват: пересчитывает один экземпляр; упавший через срок заберёт другой.
    Column("claimed_at", DateTime(timezone=True)),
)

roll_run = Table(
    "roll_run",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("portfolio_id", Uuid, nullable=False),
    Column("since", Date, nullable=False),
    Column("till", Date, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=False),
)

day_snapshot = Table(
    "day_snapshot",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("portfolio_id", Uuid, nullable=False),
    Column("day", Date, nullable=False),
    Column("run_id", Uuid, ForeignKey("roll_run.id", name="day_snapshot_run_fk"), nullable=False),
    Column("state", JSONB, nullable=False),
    Column("replaced_at", DateTime(timezone=True)),
    Index(
        "day_snapshot_current",
        "portfolio_id",
        "day",
        unique=True,
        postgresql_where=text("replaced_at IS NULL"),
    ),
)

outbox = outbox_table(metadata)
