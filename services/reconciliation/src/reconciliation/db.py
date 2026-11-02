"""Схема БД сверки: пакеты выписок как пришли, выписки по счетам, принятые расхождения.

Выписка не меняется и не удаляется: новая выписка за тот же счёт и день
становится действующей, прежние остаются в истории (решение 119). Принятое
расхождение хранит строку сверки, какой её приняли: строка изменилась —
принятие не действует (решение 123).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    Numeric,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

from am_common.outbox import outbox_table

metadata = MetaData()

# Пакет, как его прислал скрипт брокера или ввело сопровождение: по нему видно,
# что именно пришло.
statement_batch = Table(
    "statement_batch",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("source", Text, nullable=False),  # источник пакета; ручной ввод — manual
    Column("payload", JSONB, nullable=False),
    Column("received_by", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)

statement = Table(
    "statement",
    metadata,
    Column("id", Uuid, primary_key=True),
    # Порядок прихода: из двух выписок за счёт и день действует пришедшая позже.
    Column("seq", BigInteger, Identity(), nullable=False),
    Column(
        "batch_id",
        Uuid,
        ForeignKey("statement_batch.id", name="statement_batch_fk"),
        nullable=False,
    ),
    Column("account_id", Uuid, nullable=False),
    Column("fund_id", Uuid, nullable=False),
    Column("day", Date, nullable=False),
    Column("opening_cash", Numeric(20, 2), nullable=False),
    Column("closing_cash", Numeric(20, 2), nullable=False),
    # Сделки и движения — как в выписке, с бумагой из справочника.
    Column("trades", JSONB, nullable=False),
    Column("movements", JSONB, nullable=False),
    Column("received_by", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Index("statement_account_day", "account_id", "day", "seq"),
)

acceptance = Table(
    "acceptance",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("seq", BigInteger, Identity(), nullable=False),  # действует позднее принятие
    Column("account_id", Uuid, nullable=False),
    Column("fund_id", Uuid, nullable=False),
    Column("day", Date, nullable=False),
    Column("line", Text, nullable=False),  # ключ строки сверки
    Column("signature", JSONB, nullable=False),  # строка, какой её приняли
    Column("comment", Text, nullable=False),
    Column("accepted_by", Text, nullable=False),
    Column("accepted_at", DateTime(timezone=True), nullable=False),
    Index("acceptance_account_day", "account_id", "day"),
)

outbox = outbox_table(metadata)
