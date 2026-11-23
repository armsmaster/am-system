"""Решение 159: переговорные сделки (РПС) — площадка и тариф брокера.

Уже заведённым тарифам ставка РПС — 0,02%, как по умолчанию у брокера.
Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade() -> None:
    op.add_column(
        "broker_tariff",
        sa.Column("rps_rate", sa.Numeric(9, 6), nullable=False, server_default="0.02"),
    )
    op.alter_column("broker_tariff", "rps_rate", server_default=None)
    op.drop_constraint("broker_tariff_not_negative", "broker_tariff", type_="check")
    op.create_check_constraint(
        "broker_tariff_not_negative",
        "broker_tariff",
        "outright_rate >= 0 AND repo_rate >= 0 AND rps_rate >= 0",
    )
    op.drop_constraint("trade_version_venue", "trade_version", type_="check")
    op.create_check_constraint(
        "trade_version_venue", "trade_version", "venue IN ('exchange', 'negotiated', 'otc')"
    )
