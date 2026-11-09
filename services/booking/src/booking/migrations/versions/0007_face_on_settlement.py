"""Решение 149: номинал в оценке — на дату расчётов T+1, погашение после даты фиксации —
к получению. Снапшоты посчитаны по прежнему правилу: все фонды — в очередь пересчёта.
Воркер начинает с первого открытого дня: закрытые дни не пересчитываются (решение 128).

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO recalc_queue (fund_id, since, marked_at)
        SELECT fund_id, DATE '2000-01-01', now()
        FROM (SELECT fund_id FROM trade_version UNION SELECT fund_id FROM operation_version) AS f
        ON CONFLICT (fund_id) DO UPDATE SET since = EXCLUDED.since, marked_at = EXCLUDED.marked_at
        """
    )
