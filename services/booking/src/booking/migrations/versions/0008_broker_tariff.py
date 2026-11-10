"""Решение 151: тарифы брокера на счёт для загрузки сделок из отчёта QUIK.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    op.create_table(
        "broker_tariff",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("account_id", sa.Uuid, nullable=False),
        sa.Column("outright_rate", sa.Numeric(9, 6), nullable=False),
        sa.Column("repo_rate", sa.Numeric(9, 6), nullable=False),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outright_rate >= 0 AND repo_rate >= 0", name="broker_tariff_not_negative"
        ),
    )
    op.create_index("broker_tariff_account", "broker_tariff", ["account_id", "valid_from"])
