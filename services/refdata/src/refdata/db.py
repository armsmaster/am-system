"""Схема БД справочника."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from am_common.outbox import outbox_table

metadata = MetaData()

instrument = Table(
    "instrument",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("secid", Text, nullable=False, unique=True),
    Column("isin", Text),
    Column("kind", Text, nullable=False),
    # Название и краткое название действующей версии — для поиска и списков.
    Column("name", Text, nullable=False),
    Column("short_name", Text, nullable=False),
    Column("added_by", Text, nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # Как бумагу видит ISS сейчас — для сравнения с закреплёнными вручную полями.
    Column("iss_snapshot", JSONB),
    Column("last_synced_at", DateTime(timezone=True)),
    Column("last_sync_error", Text),
    CheckConstraint("kind IN ('bond', 'fund_share')", name="instrument_kind"),
)

instrument_version = Table(
    "instrument_version",
    metadata,
    Column(
        "instrument_id",
        Uuid,
        ForeignKey("instrument.id", name="instrument_version_instrument_fk"),
        primary_key=True,
    ),
    Column("number", Integer, primary_key=True),
    Column("valid_from", Date),
    Column("source", Text, nullable=False),
    Column("author", Text, nullable=False),
    Column("comment", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("pinned", ARRAY(Text), nullable=False),
    # Полный снимок параметров и графиков (refdata.domain.Params).
    Column("params", JSONB, nullable=False),
    CheckConstraint("source IN ('iss', 'manual')", name="instrument_version_source"),
)

trading_day = Table(
    "trading_day",
    metadata,
    Column("day", Date, primary_key=True),
    Column("is_trading", Boolean, nullable=False),
    Column("comment", Text),
    Column("updated_by", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Ежедневная синхронизация: одна запись на день — её выполняет один экземпляр сервиса.
sync_run = Table(
    "sync_run",
    metadata,
    Column("run_date", Date, primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("checked", Integer, nullable=False, server_default="0"),
    Column("changed", Integer, nullable=False, server_default="0"),
    Column("failed", Integer, nullable=False, server_default="0"),
)

outbox = outbox_table(metadata)
