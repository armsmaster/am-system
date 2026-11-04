"""Схема БД лимитов: наборы лимитов фонда с версиями (design/09).

Набор не меняется и не удаляется: изменили политику — новая версия с датой начала
действия, прежние остаются в истории. Проверка дня D берёт версию, действующую на D.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

from am_common.outbox import outbox_table

metadata = MetaData()

limit_set = Table(
    "limit_set",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("fund_id", Uuid, nullable=False),
    Column("version", Integer, nullable=False),
    Column("valid_from", Date, nullable=False),
    # Документ, лимиты и настройки — limits.model.SetBody.
    Column("body", JSONB, nullable=False),
    Column("comment", Text, nullable=False),
    Column("author", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("fund_id", "version", name="limit_set_version"),
)

outbox = outbox_table(metadata)
