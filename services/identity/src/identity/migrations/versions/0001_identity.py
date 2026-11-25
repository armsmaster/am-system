"""Приглашения, последние входы и outbox.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "invitation",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("username", sa.Text, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("invitation_user", "invitation", ["user_id", "created_at"])

    op.create_table(
        "last_login",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )

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
