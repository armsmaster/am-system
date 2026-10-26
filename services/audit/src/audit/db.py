"""Схема БД журнала."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, MetaData, Table, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

# Только дозапись: UPDATE, DELETE и TRUNCATE запрещены триггерами (см. миграцию).
audit_event = Table(
    "audit_event",
    metadata,
    Column("event_id", Uuid, primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("service", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("actor_subject", Text, nullable=False),
    Column("actor_username", Text, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("data", JSONB, nullable=False),
    Column("comment", Text),
    Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # Btree читается в обе стороны — «новые сверху» не требует DESC в индексе.
    Index("audit_event_by_time", "occurred_at", "event_id"),
    Index("audit_event_by_entity", "entity_type", "entity_id", "occurred_at"),
    Index("audit_event_by_actor", "actor_subject", "occurred_at"),
)
