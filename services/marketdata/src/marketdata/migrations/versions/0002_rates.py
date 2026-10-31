"""Денежные ставки: RUONIA, ключевая ставка, RUSFAR; пакеты ставок от скрипта.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.create_table(
        "rate",
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("value", sa.Numeric(10, 4), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("batch_id", sa.Uuid),
        sa.PrimaryKeyConstraint("kind", "as_of", "source", name="rate_pk"),
        sa.CheckConstraint("kind IN ('key_rate', 'ruonia', 'rusfar')", name="rate_kind"),
    )
    op.drop_constraint("feed_batch_kind", "feed_batch", type_="check")
    op.create_check_constraint(
        "feed_batch_kind", "feed_batch", "kind IN ('quotes', 'bars', 'rates')"
    )
