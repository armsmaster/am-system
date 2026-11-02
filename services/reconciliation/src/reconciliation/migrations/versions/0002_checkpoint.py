"""Шаг 8.2: точки отсчёта сверки на закрытых днях (design/08).

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.create_table(
        "checkpoint",
        sa.Column("seq", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("account_id", sa.Uuid, nullable=False),
        sa.Column("fund_id", sa.Uuid, nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("waiting", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("checkpoint_account_day", "checkpoint", ["account_id", "day", "seq"])
