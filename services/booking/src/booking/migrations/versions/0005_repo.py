"""Шаг 7.1: сделки РЕПО с НКЦ и компенсационные взносы (design/07).

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"

# Ограничения — копией, а не из booking.db: миграция не меняется вместе с кодом.
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


def upgrade() -> None:
    op.add_column(
        "trade_version", sa.Column("kind", sa.Text, nullable=False, server_default="outright")
    )
    op.add_column("trade_version", sa.Column("repo_rate", sa.Numeric(9, 6)))
    op.add_column("trade_version", sa.Column("second_date", sa.Date))
    op.add_column("trade_version", sa.Column("second_amount", sa.Numeric(20, 2)))
    op.alter_column("trade_version", "instrument_id", nullable=True)
    for name in ("trade_version_instrument_kind", "trade_version_positive"):
        op.drop_constraint(name, "trade_version", type_="check")
    op.create_check_constraint(
        "trade_version_instrument_kind",
        "trade_version",
        "instrument_kind IN ('bond', 'fund_share', 'ksu')",
    )
    op.create_check_constraint("trade_version_positive", "trade_version", TRADE_POSITIVE)
    op.create_check_constraint("trade_version_kind", "trade_version", TRADE_KIND)

    op.add_column("operation_version", sa.Column("trade_id", sa.Uuid))
    op.add_column("operation_version", sa.Column("direction", sa.Text))
    op.drop_constraint("operation_version_kind", "operation_version", type_="check")
    op.create_check_constraint("operation_version_kind", "operation_version", OPERATION_KINDS)
    op.create_check_constraint(
        "operation_version_contribution",
        "operation_version",
        "kind <> 'repo_contribution' OR (trade_id IS NOT NULL AND direction IN ('in', 'out'))",
    )
