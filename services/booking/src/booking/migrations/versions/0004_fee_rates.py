"""Часть 6.2: ставки комиссий от СЧА и выплата вознаграждения за период.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.create_table(
        "fee_rate",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("fund_id", sa.Uuid, nullable=False),
        sa.Column("portfolio_id", sa.Uuid, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("rate", sa.Numeric(9, 6), nullable=False),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('management', 'specdep', 'registrar', 'other')", name="fee_rate_kind"
        ),
        sa.CheckConstraint("rate >= 0", name="fee_rate_not_negative"),
    )
    op.create_index("fee_rate_fund", "fee_rate", ["fund_id", "valid_from"])
    op.add_column("operation_version", sa.Column("fee_kind", sa.Text))
    op.add_column("operation_version", sa.Column("period_end", sa.Date))
    op.drop_constraint("operation_version_kind", "operation_version", type_="check")
    op.create_check_constraint(
        "operation_version_kind",
        "operation_version",
        "kind IN ('unit_issue', 'unit_redemption', 'expense_accrual', 'expense_payment', "
        "'cash_transfer', 'security_transfer', 'fee_payment')",
    )
