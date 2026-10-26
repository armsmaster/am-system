"""Правила графа портфелей. Каждое правило — отдельный тест с понятным сценарием.

Типовая структура, от которой отталкиваются тесты:

    ВСЕ (представление)
    ├── F1 (фонд) ── счёт A1
    │   ├── F1-BONDS (compound)
    │   │   └── F1-OFZ (physical, A1)
    │   └── F1-TREASURY (physical, A1) ← казначейский портфель F1
    └── F2 (фонд) ── счёт A2
        └── F2-MAIN (physical, A2)
    BOOK (представление трейдера) ── F1-OFZ, F2-MAIN
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from portfolio.domain import (
    Account,
    CashKind,
    Kind,
    NotFoundError,
    Portfolio,
    PortfolioGraph,
    RuleViolationError,
)


def uid(code: str) -> UUID:
    return uuid5(NAMESPACE_URL, code)


def compound(code: str, *, fund: bool = False, treasury: str | None = None) -> Portfolio:
    return Portfolio(
        id=uid(code),
        code=code,
        name=f"Портфель {code}",
        kind=Kind.COMPOUND,
        is_fund=fund,
        treasury_id=uid(treasury) if treasury else None,
    )


def physical(code: str, account: str | None) -> Portfolio:
    return Portfolio(
        id=uid(code),
        code=code,
        name=f"Портфель {code}",
        kind=Kind.PHYSICAL,
        account_id=uid(account) if account else None,
    )


def account(code: str, fund: str) -> Account:
    return Account(
        id=uid(code),
        fund_id=uid(fund),
        code=code,
        name=f"Счёт {code}",
        cash_kind=CashKind.BROKER,
        institution="Брокер",
        cash_account="40701810000000000001",
        depo_account="ДЕПО-1",
        stock_market=True,
        derivatives_market=False,
    )


def link(parent: str, child: str) -> tuple[UUID, UUID]:
    return uid(parent), uid(child)


def typical() -> PortfolioGraph:
    return PortfolioGraph(
        [
            compound("ВСЕ"),
            compound("F1", fund=True, treasury="F1-TREASURY"),
            compound("F1-BONDS"),
            physical("F1-OFZ", "A1"),
            physical("F1-TREASURY", "A1"),
            compound("F2", fund=True),
            physical("F2-MAIN", "A2"),
            compound("BOOK"),
        ],
        [
            link("ВСЕ", "F1"),
            link("ВСЕ", "F2"),
            link("F1", "F1-BONDS"),
            link("F1-BONDS", "F1-OFZ"),
            link("F1", "F1-TREASURY"),
            link("F2", "F2-MAIN"),
            link("BOOK", "F1-OFZ"),
            link("BOOK", "F2-MAIN"),
        ],
        [account("A1", "F1"), account("A2", "F2")],
    )


def violation(graph: PortfolioGraph) -> RuleViolationError:
    with pytest.raises(RuleViolationError) as caught:
        graph.validate()
    return caught.value


# --- корректная структура ------------------------------------------------------


def test_typical_structure_is_valid() -> None:
    typical().validate()


def test_fund_of_each_portfolio() -> None:
    graph = typical()

    assert graph.fund_of(uid("F1-OFZ")) == uid("F1")
    assert graph.fund_of(uid("F1-BONDS")) == uid("F1")
    assert graph.fund_of(uid("F1")) == uid("F1")
    # Представления стоят над фондами — своего фонда у них нет.
    assert graph.fund_of(uid("BOOK")) is None
    assert graph.fund_of(uid("ВСЕ")) is None


def test_relatives() -> None:
    graph = typical()

    assert graph.parents(uid("F1-OFZ")) == {uid("F1-BONDS"), uid("BOOK")}
    assert graph.children(uid("BOOK")) == {uid("F1-OFZ"), uid("F2-MAIN")}
    assert graph.ancestors(uid("F1-OFZ")) == {uid("F1-BONDS"), uid("F1"), uid("ВСЕ"), uid("BOOK")}
    assert graph.descendants(uid("F1")) == {uid("F1-BONDS"), uid("F1-OFZ"), uid("F1-TREASURY")}


def test_access_to_compound_covers_all_descendants() -> None:
    graph = typical()

    assert graph.visible([uid("F1-BONDS")]) == {uid("F1-BONDS"), uid("F1-OFZ")}
    assert graph.visible([uid("BOOK"), uid("F2")]) == {
        uid("BOOK"),
        uid("F1-OFZ"),
        uid("F2"),
        uid("F2-MAIN"),
    }
    # Доступ к удалённому из графа портфелю ничего не открывает.
    assert graph.visible([uid("НЕТ")]) == set()


def test_changes_produce_a_new_graph() -> None:
    graph = typical()

    renamed = graph.with_changes(uid("F1-OFZ"), name="ОФЗ")
    relinked = graph.without_link(*link("BOOK", "F2-MAIN"))
    extended = graph.with_account(account("A3", "F2"))

    assert renamed.portfolio(uid("F1-OFZ")).name == "ОФЗ"
    assert graph.portfolio(uid("F1-OFZ")).name == "Портфель F1-OFZ"
    assert uid("F2-MAIN") not in relinked.children(uid("BOOK"))
    assert extended.account(uid("A3")).fund_id == uid("F2")
    relinked.validate()
    extended.validate()


def test_new_physical_needs_a_parent_and_an_account_of_its_fund() -> None:
    graph = typical().with_portfolio(physical("F2-REPO", "A2"), [uid("F2")])

    graph.validate()
    assert graph.fund_of(uid("F2-REPO")) == uid("F2")


# --- нарушения правил ----------------------------------------------------------


def test_portfolio_cannot_contain_itself() -> None:
    error = violation(typical().with_link(uid("BOOK"), uid("BOOK")))

    assert error.code == "self_link"


def test_only_compound_can_be_a_parent() -> None:
    error = violation(typical().with_link(uid("F1-OFZ"), uid("F2-MAIN")))

    assert error.code == "parent_not_compound"
    assert "«F2-MAIN» нельзя включить в «F1-OFZ»" in error.message


def test_no_cycles() -> None:
    error = violation(typical().with_link(uid("F1-BONDS"), uid("ВСЕ")))

    assert error.code == "cycle"
    assert "ВСЕ → F1 → F1-BONDS → ВСЕ" in error.message


@pytest.mark.parametrize(
    "graph",
    [
        typical().with_portfolio(compound("f1-bonds")),
        typical().with_account(account("a1", "F1")),
    ],
    ids=["portfolio", "account"],
)
def test_codes_are_unique_regardless_of_case(graph: PortfolioGraph) -> None:
    assert violation(graph).code == "duplicate_code"


def test_account_belongs_to_a_fund() -> None:
    error = violation(typical().with_account(account("A9", "F1-BONDS")))

    assert error.code == "account_not_fund"


def test_physical_keeps_at_least_one_parent() -> None:
    graph = typical().without_link(*link("F2", "F2-MAIN")).without_link(*link("BOOK", "F2-MAIN"))

    assert violation(graph).code == "physical_without_parent"


def test_physical_cannot_belong_to_two_funds() -> None:
    error = violation(typical().with_link(uid("F2"), uid("F1-OFZ")))

    assert error.code == "physical_fund_count"
    assert "сейчас: F1, F2" in error.message


def test_physical_cannot_live_outside_funds() -> None:
    graph = typical().with_portfolio(physical("LOOSE", "A1"), [uid("BOOK")])

    error = violation(graph)

    assert error.code == "physical_fund_count"
    assert "сейчас: нет" in error.message


def test_physical_needs_an_account() -> None:
    graph = typical().with_portfolio(physical("F1-NEW", None), [uid("F1")])

    assert violation(graph).code == "physical_without_account"


def test_physical_uses_an_account_of_its_own_fund() -> None:
    graph = typical().with_changes(uid("F1-OFZ"), account_id=uid("A2"))

    error = violation(graph)

    assert error.code == "account_of_other_fund"
    assert "«A2»" in error.message


def test_fund_cannot_be_inside_another_fund() -> None:
    error = violation(typical().with_link(uid("F1-BONDS"), uid("F2")))

    assert error.code == "fund_inside_fund"
    assert "(F1)" in error.message


@pytest.mark.parametrize("treasury", ["F2-MAIN", "F1-BONDS"], ids=["other fund", "compound"])
def test_treasury_is_a_physical_of_the_same_fund(treasury: str) -> None:
    graph = typical().with_changes(uid("F1"), treasury_id=uid(treasury))

    assert violation(graph).code == "treasury_not_in_fund"


@pytest.mark.parametrize(
    ("build", "code"),
    [
        (lambda: Portfolio(uid("X"), "X", "X", Kind.PHYSICAL, is_fund=True), "fund_not_compound"),
        (
            lambda: Portfolio(uid("X"), "X", "X", Kind.COMPOUND, account_id=uid("A1")),
            "account_on_compound",
        ),
        (
            lambda: Portfolio(uid("X"), "X", "X", Kind.COMPOUND, treasury_id=uid("Y")),
            "treasury_on_non_fund",
        ),
    ],
)
def test_portfolio_fields_match_its_kind(build: object, code: str) -> None:
    with pytest.raises(RuleViolationError) as caught:
        build()  # type: ignore[operator]

    assert caught.value.code == code


# --- обращение к несуществующему ----------------------------------------------


def test_unknown_things_are_reported() -> None:
    graph = typical()

    with pytest.raises(NotFoundError) as portfolio_error:
        graph.portfolio(uid("НЕТ"))
    with pytest.raises(NotFoundError):
        graph.account(uid("НЕТ"))
    with pytest.raises(NotFoundError):
        graph.with_link(uid("ВСЕ"), uid("НЕТ"))
    with pytest.raises(NotFoundError) as link_error:
        graph.without_link(uid("BOOK"), uid("F1"))

    assert portfolio_error.value.what == "portfolio"
    assert link_error.value.what == "link"
    assert graph.links == typical().links
    assert set(graph.portfolios) == set(typical().portfolios)
    assert set(graph.accounts) == {uid("A1"), uid("A2")}
