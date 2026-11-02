"""Шаг 8.2: граница закрытого периода фонда (design/08).

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    op.create_table(
        "fund_closing",
        sa.Column("seq", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("fund_id", sa.Uuid, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("first_day", sa.Date, nullable=False),
        sa.Column("last_day", sa.Date, nullable=False),
        sa.Column("closed_through", sa.Date, nullable=False),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('close', 'reopen')", name="fund_closing_action"),
        sa.CheckConstraint("first_day <= last_day", name="fund_closing_days"),
    )
    op.create_index("fund_closing_fund", "fund_closing", ["fund_id", "seq"])
