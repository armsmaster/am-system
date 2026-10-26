"""Предметная модель портфелей и правила графа.

Чистый код без ввода-вывода: на вход — состояние графа, на выход — либо
«правила соблюдены», либо нарушенное правило с кодом и понятным
сообщением. Изменение графа строится как новый граф (``with_*``),
проверяется (``validate``) и только потом сохраняется.

Правила — в docs/design/01-portfolio-audit.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID


class Kind(StrEnum):
    COMPOUND = "compound"  # содержит только портфели
    PHYSICAL = "physical"  # содержит только сделки


class CashKind(StrEnum):
    BROKER = "broker"
    BANK = "bank"


class RuleViolationError(Exception):
    """Изменение нарушает правило предметной области."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotFoundError(Exception):
    def __init__(self, what: str, key: object) -> None:
        super().__init__(f"{what} {key} not found")
        self.what = what
        self.key = key


@dataclass(frozen=True, slots=True)
class Portfolio:
    id: UUID
    code: str
    name: str
    kind: Kind
    is_fund: bool = False
    account_id: UUID | None = None  # только physical
    treasury_id: UUID | None = None  # только фонд

    def __post_init__(self) -> None:
        if self.is_fund and self.kind is not Kind.COMPOUND:
            raise RuleViolationError("fund_not_compound", "Фондом может быть только compound.")
        if self.account_id is not None and self.kind is not Kind.PHYSICAL:
            raise RuleViolationError("account_on_compound", "Счёт бывает только у physical.")
        if self.treasury_id is not None and not self.is_fund:
            raise RuleViolationError(
                "treasury_on_non_fund", "Казначейский портфель назначается только фонду."
            )


@dataclass(frozen=True, slots=True)
class Account:
    """Пара счетов: денежный и (не всегда) счёт депо."""

    id: UUID
    fund_id: UUID
    code: str
    name: str
    cash_kind: CashKind
    institution: str  # брокер или банк
    cash_account: str
    depo_account: str | None
    stock_market: bool
    derivatives_market: bool


class PortfolioGraph:
    """Портфели, связи «родитель → потомок» и счета — целиком.

    Граф маленький (десятки узлов), поэтому правила проверяются по нему
    целиком после каждого изменения.
    """

    def __init__(
        self,
        portfolios: Iterable[Portfolio],
        links: Iterable[tuple[UUID, UUID]],
        accounts: Iterable[Account] = (),
    ) -> None:
        self._portfolios: dict[UUID, Portfolio] = {p.id: p for p in portfolios}
        self._links: frozenset[tuple[UUID, UUID]] = frozenset(links)
        self._accounts: dict[UUID, Account] = {a.id: a for a in accounts}
        self._parents: dict[UUID, set[UUID]] = {pid: set() for pid in self._portfolios}
        self._children: dict[UUID, set[UUID]] = {pid: set() for pid in self._portfolios}
        for parent, child in self._links:
            self._parents.setdefault(child, set()).add(parent)
            self._children.setdefault(parent, set()).add(child)

    # --- чтение -------------------------------------------------------------

    @property
    def portfolios(self) -> Mapping[UUID, Portfolio]:
        return self._portfolios

    @property
    def accounts(self) -> Mapping[UUID, Account]:
        return self._accounts

    @property
    def links(self) -> frozenset[tuple[UUID, UUID]]:
        return self._links

    def portfolio(self, portfolio_id: UUID) -> Portfolio:
        try:
            return self._portfolios[portfolio_id]
        except KeyError:
            raise NotFoundError("portfolio", portfolio_id) from None

    def account(self, account_id: UUID) -> Account:
        try:
            return self._accounts[account_id]
        except KeyError:
            raise NotFoundError("account", account_id) from None

    def parents(self, portfolio_id: UUID) -> frozenset[UUID]:
        return frozenset(self._parents.get(portfolio_id, ()))

    def children(self, portfolio_id: UUID) -> frozenset[UUID]:
        return frozenset(self._children.get(portfolio_id, ()))

    def ancestors(self, portfolio_id: UUID) -> set[UUID]:
        return self._walk(portfolio_id, self._parents)

    def descendants(self, portfolio_id: UUID) -> set[UUID]:
        return self._walk(portfolio_id, self._children)

    def fund_of(self, portfolio_id: UUID) -> UUID | None:
        """Фонд портфеля: он сам, если это фонд, или единственный фонд среди предков."""
        if self.portfolio(portfolio_id).is_fund:
            return portfolio_id
        funds = self._fund_ancestors(portfolio_id)
        return next(iter(funds)) if len(funds) == 1 else None

    def visible(self, granted: Iterable[UUID]) -> set[UUID]:
        """Что видит пользователь с доступом к ``granted``: они сами и все их потомки."""
        result: set[UUID] = set()
        for portfolio_id in granted:
            if portfolio_id in self._portfolios:
                result |= {portfolio_id} | self.descendants(portfolio_id)
        return result

    # --- изменения: возвращают новый граф ------------------------------------

    def with_portfolio(
        self, portfolio: Portfolio, parent_ids: Iterable[UUID] = ()
    ) -> PortfolioGraph:
        portfolios = {**self._portfolios, portfolio.id: portfolio}
        links = self._links | {(parent_id, portfolio.id) for parent_id in parent_ids}
        return PortfolioGraph(portfolios.values(), links, self._accounts.values())

    def with_link(self, parent_id: UUID, child_id: UUID) -> PortfolioGraph:
        self.portfolio(parent_id)
        self.portfolio(child_id)
        return PortfolioGraph(
            self._portfolios.values(),
            self._links | {(parent_id, child_id)},
            self._accounts.values(),
        )

    def without_link(self, parent_id: UUID, child_id: UUID) -> PortfolioGraph:
        if (parent_id, child_id) not in self._links:
            raise NotFoundError("link", (parent_id, child_id))
        return PortfolioGraph(
            self._portfolios.values(),
            self._links - {(parent_id, child_id)},
            self._accounts.values(),
        )

    def with_changes(self, portfolio_id: UUID, **changes: object) -> PortfolioGraph:
        updated = replace(self.portfolio(portfolio_id), **changes)  # type: ignore[arg-type]
        return self.with_portfolio(updated)

    def with_account(self, account: Account) -> PortfolioGraph:
        accounts = {**self._accounts, account.id: account}
        return PortfolioGraph(self._portfolios.values(), self._links, accounts.values())

    # --- правила -------------------------------------------------------------

    def validate(self) -> None:
        """Проверяет все правила; первое нарушенное — исключение ``RuleViolationError``."""
        # Порядок важен: сначала структура графа, затем то, что из неё следует,
        # — так сообщение указывает на исходную причину, а не на следствие.
        self._check_links()
        self._check_no_cycles()
        self._check_unique_codes()
        for account in self._accounts.values():
            self._check_account(account)
        funds = [p for p in self._sorted() if p.is_fund]
        for fund in funds:
            self._check_fund_nesting(fund)
        for portfolio in self._sorted():
            if portfolio.kind is Kind.PHYSICAL:
                self._check_physical(portfolio)
        for fund in funds:
            self._check_treasury(fund)

    def _check_links(self) -> None:
        for parent_id, child_id in sorted(self._links):
            parent, child = self.portfolio(parent_id), self.portfolio(child_id)
            if parent_id == child_id:
                raise RuleViolationError(
                    "self_link", f"Портфель {_q(child)} не может входить сам в себя."
                )
            if parent.kind is not Kind.COMPOUND:
                raise RuleViolationError(
                    "parent_not_compound",
                    f"Портфель {_q(child)} нельзя включить в {_q(parent)}: "
                    "включать портфели можно только в compound.",
                )

    def _check_no_cycles(self) -> None:
        done: set[UUID] = set()
        for start in sorted(self._portfolios):
            if start in done:
                continue
            # Обход в глубину с явным стеком: путь от start до текущего узла.
            path: list[UUID] = [start]
            on_path = {start}
            stack = [iter(sorted(self.children(start)))]
            while stack:
                child = next(stack[-1], None)
                if child is None:
                    # Все потомки узла пройдены — снимаем его с пути.
                    stack.pop()
                    node = path.pop()
                    on_path.discard(node)
                    done.add(node)
                    continue
                if child in on_path:
                    cycle = [*path[path.index(child) :], child]
                    names = " → ".join(self._portfolios[node].code for node in cycle)
                    raise RuleViolationError("cycle", f"Связи образуют цикл: {names}.")
                if child not in done:
                    path.append(child)
                    on_path.add(child)
                    stack.append(iter(sorted(self.children(child))))

    def _check_unique_codes(self) -> None:
        for what, codes in (
            ("портфеля", [p.code for p in self._portfolios.values()]),
            ("счёта", [a.code for a in self._accounts.values()]),
        ):
            seen: set[str] = set()
            for code in codes:
                key = code.casefold()
                if key in seen:
                    raise RuleViolationError("duplicate_code", f"Код {what} «{code}» уже занят.")
                seen.add(key)

    def _check_account(self, account: Account) -> None:
        fund = self._portfolios.get(account.fund_id)
        if fund is None or not fund.is_fund:
            raise RuleViolationError(
                "account_not_fund", f"Счёт «{account.code}» должен принадлежать фонду."
            )

    def _check_physical(self, portfolio: Portfolio) -> None:
        if not self.parents(portfolio.id):
            raise RuleViolationError(
                "physical_without_parent",
                f"Physical {_q(portfolio)} должен входить хотя бы в один compound.",
            )
        funds = self._fund_ancestors(portfolio.id)
        if len(funds) != 1:
            found = ", ".join(sorted(self._portfolios[f].code for f in funds)) or "нет"
            raise RuleViolationError(
                "physical_fund_count",
                f"Physical {_q(portfolio)} должен принадлежать ровно одному фонду "
                f"(сейчас: {found}).",
            )
        if portfolio.account_id is None:
            raise RuleViolationError(
                "physical_without_account", f"У physical {_q(portfolio)} должна быть пара счетов."
            )
        account = self.account(portfolio.account_id)
        (fund_id,) = funds
        if account.fund_id != fund_id:
            raise RuleViolationError(
                "account_of_other_fund",
                f"Счёт «{account.code}» принадлежит другому фонду, чем {_q(portfolio)}.",
            )

    def _check_fund_nesting(self, fund: Portfolio) -> None:
        outer = self._fund_ancestors(fund.id)
        if outer:
            names = ", ".join(sorted(self._portfolios[f].code for f in outer))
            raise RuleViolationError(
                "fund_inside_fund", f"Фонд {_q(fund)} не может входить в другой фонд ({names})."
            )

    def _check_treasury(self, fund: Portfolio) -> None:
        if fund.treasury_id is not None:
            treasury = self.portfolio(fund.treasury_id)
            if treasury.kind is not Kind.PHYSICAL or self.fund_of(treasury.id) != fund.id:
                raise RuleViolationError(
                    "treasury_not_in_fund",
                    f"Казначейским портфелем фонда {_q(fund)} может быть только "
                    "physical этого фонда.",
                )

    # --- вспомогательное -----------------------------------------------------

    def _sorted(self) -> list[Portfolio]:
        # Детерминированный порядок проверки — по коду.
        return sorted(self._portfolios.values(), key=lambda p: p.code)

    def _fund_ancestors(self, portfolio_id: UUID) -> set[UUID]:
        return {a for a in self.ancestors(portfolio_id) if self._portfolios[a].is_fund}

    @staticmethod
    def _walk(start: UUID, edges: Mapping[UUID, set[UUID]]) -> set[UUID]:
        seen: set[UUID] = set()
        pending = list(edges.get(start, ()))
        while pending:
            node = pending.pop()
            if node not in seen:
                seen.add(node)
                pending.extend(edges.get(node, ()))
        return seen


def _q(portfolio: Portfolio) -> str:
    return f"«{portfolio.code}»"
