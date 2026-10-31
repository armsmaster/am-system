"""Облигация для расчётов: график платежей, горизонт и денежные потоки до него.

Горизонт (вопрос 78) — ближайшая оферта, которую ещё можно предъявить, или
погашение. Потоки — после даты расчётов до горизонта включительно.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal(0)
HUNDRED = Decimal(100)


class QuantError(Exception):
    """Расчёт невозможен: чего-то не хватает в условиях бумаги или в данных."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Coupon:
    start: date
    end: date  # дата выплаты
    amount: Decimal | None = None  # None — не объявлен
    rate: Decimal | None = None  # ставка купона, доля годовых; по ней считается купон линкера

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass(frozen=True, slots=True)
class Principal:
    date: date
    amount: Decimal  # выплата номинала: амортизация или погашение


@dataclass(frozen=True, slots=True)
class Offer:
    date: date
    price: Decimal = HUNDRED  # % от непогашенного номинала
    window_end: dt.date | None = None  # последний день приёма заявок


@dataclass(frozen=True, slots=True)
class Bond:
    face_value: Decimal  # начальный номинал
    maturity: date
    coupons: tuple[Coupon, ...] = ()
    principal: tuple[Principal, ...] = ()
    offers: tuple[Offer, ...] = ()

    def outstanding(self, day: date) -> Decimal:
        """Непогашенный номинал после выплат по ``day`` включительно."""
        return self.face_value - sum((p.amount for p in self.principal if p.date <= day), ZERO)


class HorizonKind(StrEnum):
    OFFER = "offer"
    MATURITY = "maturity"


@dataclass(frozen=True, slots=True)
class Horizon:
    kind: HorizonKind
    date: date
    price: Decimal = HUNDRED  # % от непогашенного номинала


class FlowKind(StrEnum):
    COUPON = "coupon"
    PRINCIPAL = "principal"  # амортизация или погашение
    OFFER = "offer"  # выкуп по оферте


@dataclass(frozen=True, slots=True)
class CashFlow:
    date: date
    amount: Decimal
    kind: FlowKind


# Сумма купона на бумагу; None — купон не объявлен и не прогнозируется.
CouponRule = Callable[[Coupon], Decimal | None]
# Непогашенный номинал на дату.
NominalRule = Callable[[date], Decimal]


def horizons(bond: Bond, settle: date) -> list[Horizon]:
    """Ближайшая оферта, которую ещё можно предъявить, и погашение; у погашенной — ничего."""
    ahead = [
        o
        for o in bond.offers
        if settle < o.date < bond.maturity and (o.window_end is None or o.window_end >= settle)
    ]
    result: list[Horizon] = []
    if ahead:
        offer = min(ahead, key=lambda o: o.date)
        result.append(Horizon(HorizonKind.OFFER, offer.date, offer.price))
    if settle < bond.maturity:
        result.append(Horizon(HorizonKind.MATURITY, bond.maturity))
    return result


def cash_flows(
    bond: Bond,
    settle: date,
    horizon: Horizon,
    coupon: CouponRule,
    nominal: NominalRule | None = None,
) -> tuple[CashFlow, ...]:
    """Потоки после даты расчётов до горизонта включительно.

    Купон без суммы до горизонта — ошибка: доходность на выдуманном купоне —
    не доходность. На горизонте держатель получает остаток номинала по цене
    горизонта и НКД периода, в который попал горизонт, — если купон этого
    периода известен или прогнозируется. Если не известен, НКД не учитываем:
    так считает биржа (сверка РЖД БО 001Р-06R в design/04).
    """
    remaining = nominal or bond.outstanding
    flows: list[CashFlow] = []
    for c in bond.coupons:
        if settle < c.end <= horizon.date:
            amount = coupon(c)
            if amount is None:
                raise QuantError(
                    "coupon_unknown",
                    f"Купон {c.end:%d.%m.%Y} не объявлен — "
                    f"до {horizon.date:%d.%m.%Y} не посчитать.",
                )
            flows.append(CashFlow(c.end, amount, FlowKind.COUPON))
    flows += [
        CashFlow(p.date, p.amount, FlowKind.PRINCIPAL)
        for p in bond.principal
        if settle < p.date <= horizon.date
    ]
    rest = remaining(horizon.date) * horizon.price / HUNDRED
    current = next((c for c in bond.coupons if c.start < horizon.date < c.end), None)
    if current is not None and (amount := coupon(current)) is not None:
        rest += amount * (horizon.date - current.start).days / current.days
    if rest > 0:
        kind = FlowKind.OFFER if horizon.kind is HorizonKind.OFFER else FlowKind.PRINCIPAL
        flows.append(CashFlow(horizon.date, rest, kind))
    if not flows:
        raise QuantError("no_flows", "После даты расчётов у бумаги нет платежей.")
    return tuple(sorted(flows, key=lambda f: f.date))


def announced(coupon: Coupon) -> Decimal | None:
    """Купон облигации с фиксированным купоном: сумма из графика."""
    return coupon.amount
