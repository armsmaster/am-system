"""Схема БД лимитов: наборы лимитов фонда с версиями (design/09).

Набор не меняется и не удаляется: изменили политику — новая версия с датой начала
действия, прежние остаются в истории. Проверка дня D берёт версию, действующую на D.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    MetaData,
    Numeric,
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

# Проверенные дни фонда: с последнего проверенного сервис досчитывает пропущенные.
day_check = Table(
    "day_check",
    metadata,
    Column("fund_id", Uuid, primary_key=True),
    Column("day", Date, primary_key=True),
    Column("set_version", Integer, nullable=False),
    Column("checked_at", DateTime(timezone=True), nullable=False),
)

# Журнал нарушений (п. 8.6 политики, design/09): нарушение — проверка набора и её
# группа (эмитент, выпуск, бумага); открыто, пока проверка дня не покажет «в пределах».
breach = Table(
    "breach",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("fund_id", Uuid, nullable=False),
    Column("limit_code", Text, nullable=False),
    Column("label", Text, nullable=False),  # проверка лимита: «на эмитента»; без подписи — ""
    Column("check_type", Text, nullable=False),
    Column("key", Text, nullable=False),  # группа: эмитент, выпуск, бумага; показатель — value
    Column("name", Text, nullable=False),
    Column("reason", Text),  # у правил и условий покупки — что не так
    Column("first_day", Date, nullable=False),  # день нарушения
    Column("last_day", Date, nullable=False),  # последний день, когда нарушение видели
    Column("detected_on", Date, nullable=False),  # когда система его увидела
    Column("value", Numeric(20, 6)),
    Column("peak", Numeric(20, 6)),
    Column("maximum", Numeric(20, 6)),
    Column("minimum", Numeric(20, 6)),
    Column("suggested", Text, nullable=False),  # вид по правилу системы
    Column("kind", Text, nullable=False),  # вид сейчас: предложенный или подтверждённый
    Column("kind_by", Text),  # кто подтвердил вид; нет — предложение системы
    Column("deadline", Date, nullable=False),
    Column("status", Text, nullable=False),
    Column("eliminated_on", Date),
    Column("notes", JSONB, nullable=False),  # комментарии и отметки: кто, когда, что
    Column("extensions", JSONB, nullable=False),  # продления срока (п. 8.5)
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("kind IN ('active', 'passive')", name="breach_kind"),
    CheckConstraint("suggested IN ('active', 'passive')", name="breach_suggested"),
    CheckConstraint("status IN ('open', 'eliminated')", name="breach_status"),
    Index("breach_fund_status", "fund_id", "status"),
)

outbox = outbox_table(metadata)
