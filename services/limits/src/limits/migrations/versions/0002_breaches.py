"""Журнал нарушений и проверенные дни (часть 9.3).

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.create_table(
        "day_check",
        sa.Column("fund_id", sa.Uuid, primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("set_version", sa.Integer, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "breach",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("fund_id", sa.Uuid, nullable=False),
        sa.Column("limit_code", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("check_type", sa.Text, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("first_day", sa.Date, nullable=False),
        sa.Column("last_day", sa.Date, nullable=False),
        sa.Column("detected_on", sa.Date, nullable=False),
        sa.Column("value", sa.Numeric(20, 6)),
        sa.Column("peak", sa.Numeric(20, 6)),
        sa.Column("maximum", sa.Numeric(20, 6)),
        sa.Column("minimum", sa.Numeric(20, 6)),
        sa.Column("suggested", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("kind_by", sa.Text),
        sa.Column("deadline", sa.Date, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("eliminated_on", sa.Date),
        sa.Column("notes", postgresql.JSONB, nullable=False),
        sa.Column("extensions", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('active', 'passive')", name="breach_kind"),
        sa.CheckConstraint("suggested IN ('active', 'passive')", name="breach_suggested"),
        sa.CheckConstraint("status IN ('open', 'eliminated')", name="breach_status"),
    )
    op.create_index("breach_fund_status", "breach", ["fund_id", "status"])
