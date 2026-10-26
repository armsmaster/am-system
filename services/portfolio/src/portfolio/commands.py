"""Изменения: портфели, связи, счета, доступы.

Каждая команда — одна транзакция по одной схеме:

1. взять блокировку графа (изменения строго по одному);
2. загрузить граф и построить новое состояние;
3. проверить правила — при нарушении ничего не сохраняется;
4. сохранить изменение и событие для журнала (outbox) в той же транзакции.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.events import Actor, Event
from am_common.outbox import Outbox
from portfolio.db import outbox
from portfolio.directory import UserDirectory
from portfolio.domain import (
    Account,
    CashKind,
    Kind,
    NotFoundError,
    Portfolio,
    PortfolioGraph,
    RuleViolationError,
)
from portfolio.repository import Repository

SERVICE = "portfolio"


@dataclass(frozen=True, slots=True)
class NewPortfolio:
    code: str
    name: str
    kind: Kind
    is_fund: bool = False
    parent_ids: tuple[UUID, ...] = ()
    account_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewAccount:
    fund_id: UUID
    code: str
    name: str
    cash_kind: CashKind
    institution: str
    cash_account: str
    depo_account: str | None
    stock_market: bool
    derivatives_market: bool


class Commands:
    def __init__(self, session: AsyncSession, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._repo = Repository(session)
        self._outbox = Outbox(outbox)

    # --- портфели --------------------------------------------------------------

    async def create_portfolio(self, command: NewPortfolio) -> Portfolio:
        async with self._session.begin():
            graph = await self._locked_graph()
            for parent_id in command.parent_ids:
                graph.portfolio(parent_id)
            if command.account_id is not None:
                graph.account(command.account_id)
            created = Portfolio(
                id=uuid4(),
                code=command.code,
                name=command.name,
                kind=command.kind,
                is_fund=command.is_fund,
                account_id=command.account_id,
            )
            graph.with_portfolio(created, command.parent_ids).validate()

            await self._repo.save_portfolio(created)
            for parent_id in command.parent_ids:
                await self._repo.add_link(parent_id, created.id)
            await self._emit(
                "portfolio.created",
                "portfolio",
                created.id,
                {
                    "after": portfolio_state(created, graph),
                    "parents": [_ref(graph.portfolio(p)) for p in command.parent_ids],
                },
            )
        return created

    async def update_portfolio(
        self, portfolio_id: UUID, *, code: str | None = None, name: str | None = None
    ) -> Portfolio:
        changes = {k: v for k, v in {"code": code, "name": name}.items() if v is not None}
        async with self._session.begin():
            graph = await self._locked_graph()
            before = graph.portfolio(portfolio_id)
            after = replace(before, **changes)  # type: ignore[arg-type]
            if after == before:
                return before
            graph.with_portfolio(after).validate()
            await self._repo.save_portfolio(after)
            await self._emit(
                "portfolio.updated",
                "portfolio",
                portfolio_id,
                {"before": portfolio_state(before, graph), "after": portfolio_state(after, graph)},
            )
        return after

    async def link(self, parent_id: UUID, child_id: UUID) -> None:
        async with self._session.begin():
            graph = await self._locked_graph()
            if (parent_id, child_id) in graph.links:
                return
            graph.with_link(parent_id, child_id).validate()
            await self._repo.add_link(parent_id, child_id)
            await self._emit(
                "portfolio.linked",
                "portfolio",
                child_id,
                {
                    "parent": _ref(graph.portfolio(parent_id)),
                    "child": _ref(graph.portfolio(child_id)),
                },
            )

    async def unlink(self, parent_id: UUID, child_id: UUID) -> None:
        async with self._session.begin():
            graph = await self._locked_graph()
            graph.without_link(parent_id, child_id).validate()
            await self._repo.remove_link(parent_id, child_id)
            await self._emit(
                "portfolio.unlinked",
                "portfolio",
                child_id,
                {
                    "parent": _ref(graph.portfolio(parent_id)),
                    "child": _ref(graph.portfolio(child_id)),
                },
            )

    async def set_account(self, portfolio_id: UUID, account_id: UUID) -> None:
        async with self._session.begin():
            graph = await self._locked_graph()
            graph.account(account_id)
            before = graph.portfolio(portfolio_id)
            await self._save_changed(
                graph, before, replace(before, account_id=account_id), "portfolio.account_changed"
            )

    async def set_treasury(self, fund_id: UUID, portfolio_id: UUID | None) -> None:
        async with self._session.begin():
            graph = await self._locked_graph()
            if portfolio_id is not None:
                graph.portfolio(portfolio_id)
            before = graph.portfolio(fund_id)
            await self._save_changed(
                graph, before, replace(before, treasury_id=portfolio_id), "fund.treasury_changed"
            )

    async def _save_changed(
        self, graph: PortfolioGraph, before: Portfolio, after: Portfolio, event: str
    ) -> None:
        if after == before:
            return
        graph.with_portfolio(after).validate()
        await self._repo.save_portfolio(after)
        await self._emit(
            event,
            "portfolio",
            after.id,
            {"before": portfolio_state(before, graph), "after": portfolio_state(after, graph)},
        )

    # --- счета -----------------------------------------------------------------

    async def create_account(self, command: NewAccount) -> Account:
        async with self._session.begin():
            graph = await self._locked_graph()
            graph.portfolio(command.fund_id)
            created = Account(id=uuid4(), **_fields(command))
            graph.with_account(created).validate()
            await self._repo.save_account(created)
            await self._emit(
                "account.created", "account", created.id, {"after": account_state(created, graph)}
            )
        return created

    async def update_account(self, account_id: UUID, **changes: Any) -> Account:
        async with self._session.begin():
            graph = await self._locked_graph()
            before = graph.account(account_id)
            after = replace(before, **changes)
            if after == before:
                return before
            graph.with_account(after).validate()
            await self._repo.save_account(after)
            await self._emit(
                "account.updated",
                "account",
                account_id,
                {"before": account_state(before, graph), "after": account_state(after, graph)},
            )
        return after

    # --- доступы ---------------------------------------------------------------

    async def grant(self, subject: str, portfolio_id: UUID, directory: UserDirectory) -> None:
        # Пользователь должен существовать в Keycloak; его логин попадёт в журнал.
        user = await directory.user(subject)
        if user is None:
            raise NotFoundError("user", subject)
        async with self._session.begin():
            graph = await self._locked_graph()
            target = graph.portfolio(portfolio_id)
            if target.kind is not Kind.COMPOUND:
                raise RuleViolationError(
                    "grant_not_compound",
                    f"Доступ выдаётся на compound, а «{target.code}» — physical.",
                )
            if portfolio_id in await self._repo.grants_of(subject):
                return
            await self._repo.add_grant(subject, portfolio_id, self._actor.subject)
            await self._emit(
                "access.granted",
                "user",
                subject,
                {
                    "user": {"subject": subject, "username": user.username},
                    "portfolio": _ref(target),
                },
            )

    async def revoke(self, subject: str, portfolio_id: UUID) -> None:
        async with self._session.begin():
            graph = await self._locked_graph()
            target = graph.portfolio(portfolio_id)
            if portfolio_id not in await self._repo.grants_of(subject):
                raise NotFoundError("grant", (subject, portfolio_id))
            await self._repo.remove_grant(subject, portfolio_id)
            await self._emit(
                "access.revoked",
                "user",
                subject,
                {"user": {"subject": subject}, "portfolio": _ref(target)},
            )

    # --- вспомогательное -------------------------------------------------------

    async def _locked_graph(self) -> PortfolioGraph:
        await self._repo.lock_graph()
        return await self._repo.load_graph()

    async def _emit(
        self, event_type: str, entity_type: str, entity_id: object, data: dict[str, Any]
    ) -> None:
        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type=event_type,
                actor=self._actor,
                entity_type=entity_type,
                entity_id=str(entity_id),
                data=data,
            ),
        )


def portfolio_state(item: Portfolio, graph: PortfolioGraph) -> dict[str, Any]:
    """Состояние портфеля для журнала: идентификаторы и коды на момент действия."""
    return {
        "id": str(item.id),
        "code": item.code,
        "name": item.name,
        "kind": item.kind.value,
        "is_fund": item.is_fund,
        "account_id": _str(item.account_id),
        "account_code": _account_code(graph, item.account_id),
        "treasury_id": _str(item.treasury_id),
        "treasury_code": _portfolio_code(graph, item.treasury_id),
    }


def account_state(item: Account, graph: PortfolioGraph) -> dict[str, Any]:
    """Состояние счёта для журнала."""
    return {
        "id": str(item.id),
        "fund_id": str(item.fund_id),
        "fund_code": _portfolio_code(graph, item.fund_id),
        "code": item.code,
        "name": item.name,
        "cash_kind": item.cash_kind.value,
        "institution": item.institution,
        "cash_account": item.cash_account,
        "depo_account": item.depo_account,
        "stock_market": item.stock_market,
        "derivatives_market": item.derivatives_market,
    }


def _account_code(graph: PortfolioGraph, account_id: UUID | None) -> str | None:
    return None if account_id is None else graph.account(account_id).code


def _portfolio_code(graph: PortfolioGraph, portfolio_id: UUID | None) -> str | None:
    return None if portfolio_id is None else graph.portfolio(portfolio_id).code


def _ref(item: Portfolio) -> dict[str, str]:
    return {"id": str(item.id), "code": item.code}


def _str(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _fields(command: NewAccount) -> dict[str, Any]:
    return {
        "fund_id": command.fund_id,
        "code": command.code,
        "name": command.name,
        "cash_kind": command.cash_kind,
        "institution": command.institution,
        "cash_account": command.cash_account,
        "depo_account": command.depo_account,
        "stock_market": command.stock_market,
        "derivatives_market": command.derivatives_market,
    }
