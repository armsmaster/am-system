"""Схема БД сервиса портфелей.

Правила графа проверяет предметная модель (domain.py). Ограничения в базе —
вторая линия защиты от ошибок в коде, а не основная.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    Table,
    Text,
    Uuid,
    func,
)

from am_common.outbox import outbox_table

metadata = MetaData()


def _timestamps() -> list[Column[Any]]:
    return [
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ]


portfolio = Table(
    "portfolio",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("code", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("is_fund", Boolean, nullable=False),
    # Портфель и счёт ссылаются друг на друга (счёт — на фонд), поэтому
    # внешний ключ на счёт добавляется после создания обеих таблиц.
    Column(
        "account_id",
        Uuid,
        ForeignKey("account.id", name="portfolio_account_fk", use_alter=True),
    ),
    Column("treasury_id", Uuid, ForeignKey("portfolio.id", name="portfolio_treasury_fk")),
    *_timestamps(),
    CheckConstraint("kind IN ('compound', 'physical')", name="portfolio_kind"),
    CheckConstraint("NOT is_fund OR kind = 'compound'", name="portfolio_fund_is_compound"),
    CheckConstraint(
        "(kind = 'physical') = (account_id IS NOT NULL)", name="portfolio_account_iff_physical"
    ),
    CheckConstraint("treasury_id IS NULL OR is_fund", name="portfolio_treasury_only_fund"),
)

portfolio_link = Table(
    "portfolio_link",
    metadata,
    Column(
        "parent_id",
        Uuid,
        ForeignKey("portfolio.id", name="portfolio_link_parent_fk"),
        primary_key=True,
    ),
    Column(
        "child_id",
        Uuid,
        ForeignKey("portfolio.id", name="portfolio_link_child_fk"),
        primary_key=True,
    ),
    CheckConstraint("parent_id <> child_id", name="portfolio_link_not_self"),
)

account = Table(
    "account",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("fund_id", Uuid, ForeignKey("portfolio.id", name="account_fund_fk"), nullable=False),
    Column("code", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("cash_kind", Text, nullable=False),
    Column("institution", Text, nullable=False),
    Column("cash_account", Text, nullable=False),
    Column("depo_account", Text),
    Column("stock_market", Boolean, nullable=False),
    Column("derivatives_market", Boolean, nullable=False),
    *_timestamps(),
    CheckConstraint("cash_kind IN ('broker', 'bank')", name="account_cash_kind"),
)

access_grant = Table(
    "access_grant",
    metadata,
    Column("subject", Text, primary_key=True),
    Column(
        "portfolio_id",
        Uuid,
        ForeignKey("portfolio.id", name="access_grant_portfolio_fk"),
        primary_key=True,
    ),
    Column("granted_by", Text, nullable=False),
    Column("granted_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

outbox = outbox_table(metadata)
