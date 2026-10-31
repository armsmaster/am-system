"""Часть 6.1: снапшоты получают оценку — все фонды пересчитываются с начала истории.

Схема не меняется: оценка и результат дня хранятся в JSON снапшота. Прежние
снапшоты без оценки замещаются новыми, как при любом пересчёте (решение 8).

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO recalc_queue (fund_id, since, marked_at)
        SELECT fund_id, DATE '2000-01-01', now()
        FROM (SELECT fund_id FROM trade_version UNION SELECT fund_id FROM operation_version) AS f
        ON CONFLICT (fund_id) DO UPDATE SET since = EXCLUDED.since, marked_at = EXCLUDED.marked_at
        """
    )
