"""Чтение с учётом прав: что видит конкретный пользователь."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.auth import Principal, Role
from portfolio.domain import Account, Kind, Portfolio, PortfolioGraph
from portfolio.repository import Repository


@dataclass(frozen=True, slots=True)
class PortfolioView:
    portfolio: Portfolio
    parent_ids: list[UUID]
    fund_id: UUID | None


@dataclass(frozen=True, slots=True)
class Visibility:
    graph: PortfolioGraph
    visible: set[UUID]
    sees_everything: bool


async def visibility(session: AsyncSession, principal: Principal) -> Visibility:
    """Админ видит всё; скрипты сделок и выписок — тоже: внешняя система присылает
    сделки, а брокер — выписки всех фондов (вопрос 100). Остальные — выданные им
    compound и их потомков."""
    repo = Repository(session)
    graph = await repo.load_graph()
    if any(principal.has_role(r) for r in (Role.ADMIN, Role.TRADE_FEED, Role.STATEMENT_FEED)):
        return Visibility(graph, set(graph.portfolios), sees_everything=True)
    granted = await repo.grants_of(principal.subject)
    return Visibility(graph, graph.visible(granted), sees_everything=False)


def portfolio_views(scope: Visibility) -> list[PortfolioView]:
    graph = scope.graph
    return [
        PortfolioView(
            portfolio=item,
            # Родители, которых пользователь не видит, не раскрываются.
            parent_ids=sorted(p for p in graph.parents(item.id) if p in scope.visible),
            fund_id=graph.fund_of(item.id),
        )
        for item in sorted(graph.portfolios.values(), key=lambda p: p.code)
        if item.id in scope.visible
    ]


def visible_accounts(scope: Visibility) -> list[Account]:
    """Счета видимых фондов и счета, которыми пользуются видимые physical."""
    graph = scope.graph
    used = {
        p.account_id
        for p in graph.portfolios.values()
        if p.id in scope.visible and p.kind is Kind.PHYSICAL
    }
    return sorted(
        (a for a in graph.accounts.values() if a.fund_id in scope.visible or a.id in used),
        key=lambda a: a.code,
    )
