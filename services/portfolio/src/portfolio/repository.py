"""Чтение и запись портфелей, счетов и доступов в БД."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from portfolio.db import access_grant, account, portfolio, portfolio_link
from portfolio.domain import Account, CashKind, Kind, Portfolio, PortfolioGraph


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_graph(self) -> None:
        """Изменения графа — строго по одному (блокировка до конца транзакции).

        Иначе два администратора могли бы одновременно сделать два изменения,
        каждое из которых допустимо само по себе, а вместе нарушают правила.
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('portfolio.graph'))")
        )

    async def load_graph(self) -> PortfolioGraph:
        portfolios = [
            Portfolio(
                id=row.id,
                code=row.code,
                name=row.name,
                kind=Kind(row.kind),
                is_fund=row.is_fund,
                account_id=row.account_id,
                treasury_id=row.treasury_id,
            )
            for row in await self._session.execute(select(portfolio))
        ]
        links = [
            (row.parent_id, row.child_id)
            for row in await self._session.execute(select(portfolio_link))
        ]
        accounts = [
            Account(
                id=row.id,
                fund_id=row.fund_id,
                code=row.code,
                name=row.name,
                cash_kind=CashKind(row.cash_kind),
                institution=row.institution,
                cash_account=row.cash_account,
                depo_account=row.depo_account,
                stock_market=row.stock_market,
                derivatives_market=row.derivatives_market,
            )
            for row in await self._session.execute(select(account))
        ]
        return PortfolioGraph(portfolios, links, accounts)

    async def save_portfolio(self, item: Portfolio) -> None:
        values = {
            "code": item.code,
            "name": item.name,
            "kind": item.kind.value,
            "is_fund": item.is_fund,
            "account_id": item.account_id,
            "treasury_id": item.treasury_id,
        }
        await self._session.execute(
            insert(portfolio)
            .values(id=item.id, **values)
            .on_conflict_do_update(
                index_elements=[portfolio.c.id], set_={**values, "updated_at": func.now()}
            )
        )

    async def add_link(self, parent_id: UUID, child_id: UUID) -> None:
        await self._session.execute(
            insert(portfolio_link).values(parent_id=parent_id, child_id=child_id)
        )

    async def remove_link(self, parent_id: UUID, child_id: UUID) -> None:
        await self._session.execute(
            delete(portfolio_link).where(
                portfolio_link.c.parent_id == parent_id, portfolio_link.c.child_id == child_id
            )
        )

    async def save_account(self, item: Account) -> None:
        values = {
            "fund_id": item.fund_id,
            "code": item.code,
            "name": item.name,
            "cash_kind": item.cash_kind.value,
            "institution": item.institution,
            "cash_account": item.cash_account,
            "depo_account": item.depo_account,
            "stock_market": item.stock_market,
            "derivatives_market": item.derivatives_market,
        }
        await self._session.execute(
            insert(account)
            .values(id=item.id, **values)
            .on_conflict_do_update(
                index_elements=[account.c.id], set_={**values, "updated_at": func.now()}
            )
        )

    async def grants(self) -> dict[str, set[UUID]]:
        """Все выданные доступы: пользователь → портфели."""
        result: dict[str, set[UUID]] = defaultdict(set)
        for row in await self._session.execute(select(access_grant)):
            result[row.subject].add(row.portfolio_id)
        return dict(result)

    async def grants_of(self, subject: str) -> set[UUID]:
        rows = await self._session.execute(
            select(access_grant.c.portfolio_id).where(access_grant.c.subject == subject)
        )
        return {row.portfolio_id for row in rows}

    async def add_grant(self, subject: str, portfolio_id: UUID, granted_by: str) -> None:
        await self._session.execute(
            insert(access_grant).values(
                subject=subject, portfolio_id=portfolio_id, granted_by=granted_by
            )
        )

    async def remove_grant(self, subject: str, portfolio_id: UUID) -> None:
        await self._session.execute(
            delete(access_grant).where(
                access_grant.c.subject == subject, access_grant.c.portfolio_id == portfolio_id
            )
        )
