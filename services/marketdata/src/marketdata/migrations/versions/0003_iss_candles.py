"""Часовые свечи ISS (решение 153): дата погашения бумаги, бары без пакета скрипта,
одна запись на свечу ISS, слоты опроса свечей.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    # Живая бумага — ещё не погашена; дату даёт справочник.
    op.add_column("instrument", sa.Column("maturity_date", sa.Date))
    # У свечей ISS пакета скрипта нет.
    op.alter_column("bar", "batch_id", existing_type=sa.Uuid, nullable=True)
    # Свеча ISS после закрытия не меняется: повторная загрузка её не дублирует.
    op.create_index(
        "bar_iss_candle",
        "bar",
        ["instrument_id", "timeframe", "start_at"],
        unique=True,
        postgresql_where=sa.text("source = 'iss'"),
    )
    op.create_table(
        "candle_poll",
        sa.Column("slot", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("polled", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer, nullable=False, server_default="0"),
    )
