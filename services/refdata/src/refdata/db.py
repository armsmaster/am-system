"""Схема БД справочника."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
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
    # Эмитент и тип бумаги ISS (ofz_bond, exchange_bond…) — по последней сверке с ISS.
    Column("issuer_id", Uuid, ForeignKey("issuer.id", name="instrument_issuer_fk")),
    Column("iss_type", Text),
    CheckConstraint("kind IN ('bond', 'fund_share')", name="instrument_kind"),
)

# Эмитент — по коду эмитента ISS (EMITTER_ID): к нему привязаны все его выпуски.
issuer = Table(
    "issuer",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("iss_id", Integer, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("inn", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Рейтинги выпусков и эмитентов (design/09): записи только дописываются, ошибочную
# запись отменяют с комментарием. Порядок записей одной даты — по seq.
rating = Table(
    "rating",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("seq", BigInteger, Identity(), nullable=False),
    Column("instrument_id", Uuid, ForeignKey("instrument.id", name="rating_instrument_fk")),
    Column("issuer_id", Uuid, ForeignKey("issuer.id", name="rating_issuer_fk")),
    # assigned — присвоен или изменён, withdrawn — отозван, unrated — рейтинга нет ни у кого.
    Column("action", Text, nullable=False),
    Column("agency", Text),
    Column("rating", Text),  # запись агентства: AA-(RU)
    Column("grade", Text),  # ступень общей шкалы: AA-
    Column("valid_from", Date, nullable=False),
    Column("comment", Text),
    Column("author", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("cancelled_by", Text),
    Column("cancelled_at", DateTime(timezone=True)),
    Column("cancel_comment", Text),
    CheckConstraint("(instrument_id IS NULL) <> (issuer_id IS NULL)", name="rating_subject"),
    CheckConstraint("action IN ('assigned', 'withdrawn', 'unrated')", name="rating_action"),
    CheckConstraint(
        "(action = 'assigned') = (rating IS NOT NULL AND grade IS NOT NULL)", name="rating_value"
    ),
    CheckConstraint("(action = 'unrated') = (agency IS NULL)", name="rating_agency"),
    Index("rating_instrument", "instrument_id", "seq"),
    Index("rating_issuer", "issuer_id", "seq"),
)

# Классификаторы — признаки бумаг и эмитентов, которых нет в ISS: очерёдность,
# секьюритизация, банк ли эмитент (design/09). Ведёт риск-менеджмент.
classifier = Table(
    "classifier",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("target", Text, nullable=False),
    Column("description", Text),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("target IN ('instrument', 'issuer')", name="classifier_target"),
)

classifier_value = Table(
    "classifier_value",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column(
        "classifier_id",
        Uuid,
        ForeignKey("classifier.id", name="classifier_value_classifier_fk"),
        nullable=False,
    ),
    Column("name", Text, nullable=False),
    Column("position", Integer, nullable=False),
    UniqueConstraint("classifier_id", "name", name="classifier_value_name"),
)

# Значение классификатора у бумаги или эмитента с даты; без даты — с начала обращения.
classification = Table(
    "classification",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("seq", BigInteger, Identity(), nullable=False),
    Column(
        "classifier_id",
        Uuid,
        ForeignKey("classifier.id", name="classification_classifier_fk"),
        nullable=False,
    ),
    Column("instrument_id", Uuid, ForeignKey("instrument.id", name="classification_instrument_fk")),
    Column("issuer_id", Uuid, ForeignKey("issuer.id", name="classification_issuer_fk")),
    Column(
        "value_id",
        Uuid,
        ForeignKey("classifier_value.id", name="classification_value_fk"),
        nullable=False,
    ),
    Column("valid_from", Date),
    Column("comment", Text),
    Column("author", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("cancelled_by", Text),
    Column("cancelled_at", DateTime(timezone=True)),
    Column("cancel_comment", Text),
    CheckConstraint(
        "(instrument_id IS NULL) <> (issuer_id IS NULL)", name="classification_subject"
    ),
    Index("classification_instrument", "instrument_id", "seq"),
    Index("classification_issuer", "issuer_id", "seq"),
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
