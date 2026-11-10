"""Схема БД сделок и позиций.

Сделка не меняется и не удаляется: правка, подтверждение и отмена — новая
версия. Сделка РЕПО (design/07) — та же сделка с видом ``repo``: сторона —
первой части (``sell`` — прямое, ``buy`` — обратное), сумма и дата расчётов —
первой части, ставка и вторая часть — в своих полях. У РЕПО с КСУ бумаги нет.

Снапшот дня не удаляется при пересчёте, а помечается замещённым (решение 8):
видно, что система показывала до исправления.
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

# У КСУ нет ни количества, ни цены: РЕПО с КСУ — только деньги (решение 117).
TRADE_POSITIVE = (
    "fee >= 0 AND amount > 0 AND CASE WHEN instrument_kind = 'ksu' "
    "THEN instrument_id IS NULL AND quantity = 0 AND price = 0 "
    "ELSE instrument_id IS NOT NULL AND quantity > 0 AND price > 0 END"
)
TRADE_KIND = (
    "CASE kind "
    "WHEN 'outright' THEN instrument_kind <> 'ksu' AND repo_rate IS NULL "
    "AND second_date IS NULL AND second_amount IS NULL "
    "WHEN 'repo' THEN repo_rate IS NOT NULL AND second_date > settle_date "
    "AND second_amount > 0 "
    "ELSE false END"
)
OPERATION_KINDS = (
    "kind IN ('unit_issue', 'unit_redemption', 'expense_accrual', 'expense_payment', "
    "'cash_transfer', 'security_transfer', 'fee_payment', 'repo_contribution')"
)

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
    # Фонд портфеля: пересчёт — по фонду целиком (переводы связывают его портфели).
    Column("fund_id", Uuid, nullable=False),
    Column("portfolio_id", Uuid, nullable=False),
    Column("instrument_id", Uuid),  # у РЕПО с КСУ бумаги нет
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
    Column("kind", Text, nullable=False, server_default="outright"),
    Column("repo_rate", Numeric(9, 6)),  # РЕПО: ставка, % годовых
    Column("second_date", Date),  # РЕПО: расчёты по второй части
    Column("second_amount", Numeric(20, 2)),  # РЕПО: сумма второй части по отчёту брокера
    Column("comment", Text),
    Column("author", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("trade_id", "number", name="trade_version_pk"),
    CheckConstraint("status IN ('entered', 'confirmed', 'cancelled')", name="trade_version_status"),
    CheckConstraint("side IN ('buy', 'sell')", name="trade_version_side"),
    CheckConstraint("venue IN ('exchange', 'otc')", name="trade_version_venue"),
    CheckConstraint(
        "instrument_kind IN ('bond', 'fund_share', 'ksu')", name="trade_version_instrument_kind"
    ),
    CheckConstraint(TRADE_POSITIVE, name="trade_version_positive"),
    CheckConstraint("settle_date >= trade_date", name="trade_version_dates"),
    CheckConstraint(TRADE_KIND, name="trade_version_kind"),
    Index("trade_version_portfolio", "portfolio_id", "trade_date"),
    Index("trade_version_instrument", "instrument_id"),
    Index("trade_version_fund", "fund_id"),
)

# Операции фонда и переводы (design/05, «Операции фонда и переводы»). Как и сделка,
# операция не меняется: отметка расчёта по паям и отмена — новая версия.
operation = Table(
    "operation",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Text, nullable=False),
)

operation_version = Table(
    "operation_version",
    metadata,
    Column(
        "operation_id",
        Uuid,
        ForeignKey("operation.id", name="operation_version_operation_fk"),
        nullable=False,
    ),
    Column("number", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("fund_id", Uuid, nullable=False),
    # Паи и расходы — казначейский портфель фонда; перевод — отправитель.
    Column("portfolio_id", Uuid, nullable=False),
    Column("receiver_id", Uuid),  # перевод: получатель
    Column("op_date", Date, nullable=False),
    Column("amount", Numeric(20, 2)),  # ₽
    Column("units", Numeric(20, 5)),  # паи (решение 50)
    Column("planned_date", Date),  # паи: плановая дата расчёта
    Column("settled_date", Date),  # паи: расчёт отмечен этой датой (решение 42)
    Column("expense_kind", Text),
    Column("fee_kind", Text),  # выплата вознаграждения: вид комиссии от СЧА
    Column("period_end", Date),  # выплата вознаграждения: конец оплаченного периода
    Column("trade_id", Uuid),  # компенсационный взнос: сделка РЕПО
    Column("direction", Text),  # компенсационный взнос: in — фонду, out — от фонда
    Column("instrument_id", Uuid),  # перевод бумаг
    Column("instrument_kind", Text),
    Column("secid", Text),
    Column("short_name", Text),
    Column("quantity", BigInteger),
    Column("comment", Text),
    Column("author", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("operation_id", "number", name="operation_version_pk"),
    CheckConstraint("status IN ('active', 'cancelled')", name="operation_version_status"),
    CheckConstraint(OPERATION_KINDS, name="operation_version_kind"),
    CheckConstraint(
        "kind <> 'repo_contribution' OR (trade_id IS NOT NULL AND direction IN ('in', 'out'))",
        name="operation_version_contribution",
    ),
    CheckConstraint(
        "(amount IS NULL OR amount > 0) AND (units IS NULL OR units > 0) "
        "AND (quantity IS NULL OR quantity > 0)",
        name="operation_version_positive",
    ),
    Index("operation_version_fund", "fund_id", "op_date"),
)

# Ставки комиссий от СЧА (design/06, решение 21): строки только дописываются.
# Ставка на день — последняя строка с датой начала не позже дня. Начисление идёт
# в казначейский портфель, назначенный фонду, когда ставку ввели.
fee_rate = Table(
    "fee_rate",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("fund_id", Uuid, nullable=False),
    Column("portfolio_id", Uuid, nullable=False),
    Column("kind", Text, nullable=False),
    Column("rate", Numeric(9, 6), nullable=False),  # % годовых
    Column("valid_from", Date, nullable=False),
    Column("comment", Text),
    Column("author", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "kind IN ('management', 'specdep', 'registrar', 'other')", name="fee_rate_kind"
    ),
    CheckConstraint("rate >= 0", name="fee_rate_not_negative"),
    Index("fee_rate_fund", "fund_id", "valid_from"),
)

# Закрытый период (design/08): закрытия и повторные открытия только дописываются;
# граница фонда — по последней строке.
# Тариф брокера на счёт (решение 151): ставки комиссии, которые загрузка из отчёта
# QUIK подставляет в сделки. Строки только дописываются; тариф на день — последняя
# строка с датой начала не позже дня.
broker_tariff = Table(
    "broker_tariff",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("account_id", Uuid, nullable=False),
    Column("outright_rate", Numeric(9, 6), nullable=False),  # % от оборота без НКД
    Column("repo_rate", Numeric(9, 6), nullable=False),  # % годовых от суммы первой части
    Column("valid_from", Date, nullable=False),
    Column("comment", Text),
    Column("author", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("outright_rate >= 0 AND repo_rate >= 0", name="broker_tariff_not_negative"),
    Index("broker_tariff_account", "account_id", "valid_from"),
)

fund_closing = Table(
    "fund_closing",
    metadata,
    Column("seq", BigInteger, Identity(), primary_key=True),
    Column("fund_id", Uuid, nullable=False),
    Column("action", Text, nullable=False),
    # Закрытие — какие дни закрыло; открытие — какие открыло.
    Column("first_day", Date, nullable=False),
    Column("last_day", Date, nullable=False),
    Column("closed_through", Date, nullable=False),  # граница после действия
    Column("author", Text, nullable=False),
    Column("comment", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("action IN ('close', 'reopen')", name="fund_closing_action"),
    CheckConstraint("first_day <= last_day", name="fund_closing_days"),
    Index("fund_closing_fund", "fund_id", "seq"),
)

# Очередь пересчёта: фонд и дата, с которой его состояние могло измениться.
recalc_queue = Table(
    "recalc_queue",
    metadata,
    Column("fund_id", Uuid, primary_key=True),
    Column("since", Date, nullable=False),
    Column("marked_at", DateTime(timezone=True), nullable=False),
    # Захват: пересчитывает один экземпляр; упавший через срок заберёт другой.
    Column("claimed_at", DateTime(timezone=True)),
)

roll_run = Table(
    "roll_run",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("fund_id", Uuid, nullable=False),
    Column("since", Date, nullable=False),
    Column("till", Date, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=False),
)

day_snapshot = Table(
    "day_snapshot",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("fund_id", Uuid, nullable=False),
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
    Index("day_snapshot_fund", "fund_id", "day"),
)

outbox = outbox_table(metadata)
