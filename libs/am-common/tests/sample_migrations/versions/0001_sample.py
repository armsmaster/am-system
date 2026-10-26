"""Тестовая миграция: одна таблица."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table("sample", sa.Column("id", sa.Integer, primary_key=True))
