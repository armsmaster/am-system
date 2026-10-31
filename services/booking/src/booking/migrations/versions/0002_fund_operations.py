"""Часть 5.2: фонд у сделок и снапшотов, операции фонда и переводы, очередь по фондам.

Пересчёт — по фонду целиком: перевод связывает портфели фонда. Работающих
фондов нет (решение 41), поэтому у прежних строк фондом считается сам
портфель; данные окружения разработки поправляются вручную.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.add_column("trade_version", sa.Column("fund_id", sa.Uuid))
    op.execute("UPDATE trade_version SET fund_id = portfolio_id")
    op.alter_column("trade_version", "fund_id", nullable=False)
    op.add_column("day_snapshot", sa.Column("fund_id", sa.Uuid))
    op.execute("UPDATE day_snapshot SET fund_id = portfolio_id")
    op.alter_column("day_snapshot", "fund_id", nullable=False)
    op.create_index("trade_version_fund", "trade_version", ["fund_id"])
    op.create_index("day_snapshot_fund", "day_snapshot", ["fund_id", "day"])
    op.alter_column("roll_run", "portfolio_id", new_column_name="fund_id")
    # Очередь — рабочее состояние, не история: пересоздаём её по фондам.
    op.drop_table("recalc_queue")
    op.create_table(
        "recalc_queue",
        sa.Column("fund_id", sa.Uuid, primary_key=True),
        sa.Column("since", sa.Date, nullable=False),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "operation",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
    )
    op.create_table(
        "operation_version",
        sa.Column(
            "operation_id",
            sa.Uuid,
            sa.ForeignKey("operation.id", name="operation_version_operation_fk"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("fund_id", sa.Uuid, nullable=False),
        sa.Column("portfolio_id", sa.Uuid, nullable=False),
        sa.Column("receiver_id", sa.Uuid),
        sa.Column("op_date", sa.Date, nullable=False),
        sa.Column("amount", sa.Numeric(20, 2)),
        sa.Column("units", sa.Numeric(20, 5)),
        sa.Column("planned_date", sa.Date),
        sa.Column("settled_date", sa.Date),
        sa.Column("expense_kind", sa.Text),
        sa.Column("instrument_id", sa.Uuid),
        sa.Column("instrument_kind", sa.Text),
        sa.Column("secid", sa.Text),
        sa.Column("short_name", sa.Text),
        sa.Column("quantity", sa.BigInteger),
        sa.Column("comment", sa.Text),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("operation_id", "number", name="operation_version_pk"),
        sa.CheckConstraint("status IN ('active', 'cancelled')", name="operation_version_status"),
        sa.CheckConstraint(
            "kind IN ('unit_issue', 'unit_redemption', 'expense_accrual', 'expense_payment', "
            "'cash_transfer', 'security_transfer')",
            name="operation_version_kind",
        ),
        sa.CheckConstraint(
            "(amount IS NULL OR amount > 0) AND (units IS NULL OR units > 0) "
            "AND (quantity IS NULL OR quantity > 0)",
            name="operation_version_positive",
        ),
    )
    op.create_index("operation_version_fund", "operation_version", ["fund_id", "op_date"])
