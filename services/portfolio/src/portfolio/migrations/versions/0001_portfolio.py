"""Портфели, связи, счета, доступы и outbox.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "portfolio",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("is_fund", sa.Boolean, nullable=False),
        sa.Column("account_id", sa.Uuid),
        sa.Column(
            "treasury_id", sa.Uuid, sa.ForeignKey("portfolio.id", name="portfolio_treasury_fk")
        ),
        *_timestamps(),
        sa.CheckConstraint("kind IN ('compound', 'physical')", name="portfolio_kind"),
        sa.CheckConstraint("NOT is_fund OR kind = 'compound'", name="portfolio_fund_is_compound"),
        sa.CheckConstraint(
            "(kind = 'physical') = (account_id IS NOT NULL)",
            name="portfolio_account_iff_physical",
        ),
        sa.CheckConstraint("treasury_id IS NULL OR is_fund", name="portfolio_treasury_only_fund"),
    )
    op.create_table(
        "account",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "fund_id",
            sa.Uuid,
            sa.ForeignKey("portfolio.id", name="account_fund_fk"),
            nullable=False,
        ),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("cash_kind", sa.Text, nullable=False),
        sa.Column("institution", sa.Text, nullable=False),
        sa.Column("cash_account", sa.Text, nullable=False),
        sa.Column("depo_account", sa.Text),
        sa.Column("stock_market", sa.Boolean, nullable=False),
        sa.Column("derivatives_market", sa.Boolean, nullable=False),
        *_timestamps(),
        sa.CheckConstraint("cash_kind IN ('broker', 'bank')", name="account_cash_kind"),
    )
    # Портфель и счёт ссылаются друг на друга — ключ добавляем после обеих таблиц.
    op.create_foreign_key("portfolio_account_fk", "portfolio", "account", ["account_id"], ["id"])

    op.create_table(
        "portfolio_link",
        sa.Column(
            "parent_id",
            sa.Uuid,
            sa.ForeignKey("portfolio.id", name="portfolio_link_parent_fk"),
            primary_key=True,
        ),
        sa.Column(
            "child_id",
            sa.Uuid,
            sa.ForeignKey("portfolio.id", name="portfolio_link_child_fk"),
            primary_key=True,
        ),
        sa.CheckConstraint("parent_id <> child_id", name="portfolio_link_not_self"),
    )
    op.create_table(
        "access_grant",
        sa.Column("subject", sa.Text, primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.Uuid,
            sa.ForeignKey("portfolio.id", name="access_grant_portfolio_fk"),
            primary_key=True,
        ),
        sa.Column("granted_by", sa.Text, nullable=False),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
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
