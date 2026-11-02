"""Пакеты выписок, выписки по счетам, принятые расхождения, outbox.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "statement_batch",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("received_by", sa.Text, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "statement",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column(
            "batch_id",
            sa.Uuid,
            sa.ForeignKey("statement_batch.id", name="statement_batch_fk"),
            nullable=False,
        ),
        sa.Column("account_id", sa.Uuid, nullable=False),
        sa.Column("fund_id", sa.Uuid, nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("opening_cash", sa.Numeric(20, 2), nullable=False),
        sa.Column("closing_cash", sa.Numeric(20, 2), nullable=False),
        sa.Column("trades", postgresql.JSONB, nullable=False),
        sa.Column("movements", postgresql.JSONB, nullable=False),
        sa.Column("received_by", sa.Text, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("statement_account_day", "statement", ["account_id", "day", "seq"])
    op.create_table(
        "acceptance",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("account_id", sa.Uuid, nullable=False),
        sa.Column("fund_id", sa.Uuid, nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("line", sa.Text, nullable=False),
        sa.Column("signature", postgresql.JSONB, nullable=False),
        sa.Column("comment", sa.Text, nullable=False),
        sa.Column("accepted_by", sa.Text, nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("acceptance_account_day", "acceptance", ["account_id", "day"])
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.Uuid, nullable=False, unique=True),
        sa.Column("stream", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "outbox_unpublished", "outbox", ["id"], postgresql_where=sa.text("published_at IS NULL")
    )
