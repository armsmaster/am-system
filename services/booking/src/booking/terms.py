"""Выплаты по бумаге из условий выпуска справочника (design/05, «Купоны, амортизации,
погашение») и условия на дату по версиям бумаги."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from am_common.calendar import previous_trading_day
from am_quant.spread import FloaterTerms, floater_coupon
from booking.roll import HUNDRED, KOPECK, ZERO, Forecast, Payment, Terms

NO_TERMS = Terms()
BASIS_POINTS = Decimal(10_000)


def payments(
    params: Mapping[str, Any],
    exceptions: Mapping[date, bool],
    rates: Mapping[str, Decimal] | None = None,
) -> tuple[Payment, ...]:
    """Купоны и погашения номинала по датам выплат.

    Дата фиксации — из справочника; нет её — торговый день перед выплатой
    (вопрос 90). Погашение без строки в графике — остаток номинала в дату
    погашения. У облигации с индексируемым номиналом непогашенный номинал
    не считаем: суммы её выплат известны только в день выплаты.

    ``rates`` — текущие ставки индексов, % годовых: с ними необъявленный купон
    флоатера с параметрами в справочнике получает прогноз (решение 14).
    """
    face = _decimal(params.get("initial_face_value"))
    maturity = _date(params.get("maturity_date"))
    indexed = params.get("nominal_indexation", "none") != "none"
    coupons = {date.fromisoformat(c["date"]): c for c in params.get("coupons", [])}
    amortizations = {date.fromisoformat(a["date"]): a for a in params.get("amortizations", [])}
    days = sorted(set(coupons) | set(amortizations) | ({maturity} if maturity else set()))

    paid: Decimal | None = ZERO
    repaid: list[tuple[date, Decimal]] = []
    result: list[Payment] = []
    for day in days:
        coupon = coupons.get(day)
        row = amortizations.get(day)
        final = day == maturity or bool(row and row.get("is_maturity"))
        principal = _principal(row, face, paid, final)
        paid = None if paid is None or principal is None else paid + principal
        if principal:
            repaid.append((day, principal))
        outstanding = (
            face - paid
            if face is not None and paid is not None and principal and not final and not indexed
            else None
        )
        record = _date(coupon.get("record_date")) if coupon else None
        amount = None if coupon is None else _decimal(coupon.get("amount"))
        forecast = None
        if coupon is not None and amount is None and rates and face is not None and not indexed:
            found = _forecast(params, coupon, rates, face - _repaid_by(repaid, coupon))
            if found is not None:
                amount, forecast = found
        result.append(
            Payment(
                pay_date=day,
                record_date=record or previous_trading_day(day - timedelta(days=1), exceptions),
                has_coupon=coupon is not None,
                coupon=amount,
                principal=principal,
                outstanding=outstanding,
                final=final,
                forecast=forecast,
            )
        )
    return tuple(result)


def _repaid_by(repaid: list[tuple[date, Decimal]], coupon: Mapping[str, Any]) -> Decimal:
    """Погашено номинала к началу купонного периода — купон идёт от остатка."""
    start = _date(coupon.get("start_date"))
    return sum((amount for day, amount in repaid if start is not None and day <= start), ZERO)


def _forecast(
    params: Mapping[str, Any],
    coupon: Mapping[str, Any],
    rates: Mapping[str, Decimal],
    outstanding: Decimal,
) -> tuple[Decimal, Forecast] | None:
    """Купон на бумагу по последней известной ставке индекса, до копейки. Нет параметров
    флоатера, начала периода или ставки индекса — прогноза нет."""
    floater = params.get("floater")
    start = _date(coupon.get("start_date"))
    if floater is None or start is None or floater["rate"] not in rates:
        return None
    value = rates[floater["rate"]]
    margin_bp = Decimal(str(floater["margin_bp"]))
    capitalized = bool(floater.get("capitalized", False))
    terms = FloaterTerms(value / HUNDRED, margin_bp / BASIS_POINTS, capitalized)
    days = (date.fromisoformat(coupon["date"]) - start).days
    amount = floater_coupon(outstanding, days, terms).quantize(KOPECK, rounding=ROUND_HALF_UP)
    return amount, Forecast(floater["rate"], value, margin_bp, capitalized)


def _principal(
    row: Mapping[str, Any] | None, face: Decimal | None, paid: Decimal | None, final: bool
) -> Decimal | None:
    """Погашение номинала на бумагу: 0 — его нет, None — сумма не объявлена."""
    if row is None:
        if not final:
            return ZERO
        return None if face is None or paid is None else face - paid
    amount = _decimal(row.get("amount"))
    if amount is not None:
        return amount
    percent = _decimal(row.get("percent"))
    if percent is not None and face is not None:
        return percent * face / HUNDRED
    return None


@dataclass(frozen=True, slots=True)
class InstrumentHistory:
    """Бумага и все версии её условий: дата начала (None — с начала обращения) и условия."""

    id: UUID
    kind: str
    secid: str
    short_name: str
    versions: Sequence[tuple[date | None, Mapping[str, Any]]]

    def version_on(self, day: date) -> tuple[date | None, Mapping[str, Any]] | None:
        """Версия на дату: наибольшая дата начала не позже ``day``."""
        eligible = [(v, p) for v, p in self.versions if v is None or v <= day]
        if not eligible:
            return None
        return max(eligible, key=lambda item: item[0] or date.min)


class TermsBook:
    """Условия бумаг на дату для ролла: версия справочника на дату → выплаты."""

    def __init__(
        self,
        histories: Mapping[UUID, InstrumentHistory],
        exceptions: Mapping[date, bool],
        rates: Mapping[str, Decimal] | None = None,
    ) -> None:
        """``rates`` — ставки индексов для прогноза купонов флоатеров: только у лестницы."""
        self._histories = histories
        self._exceptions = exceptions
        self._rates = rates
        self._cache: dict[tuple[UUID, date | None], Terms] = {}

    def __call__(self, instrument_id: UUID, day: date) -> Terms:
        history = self._histories.get(instrument_id)
        version = None if history is None else history.version_on(day)
        if version is None:
            return NO_TERMS
        key = (instrument_id, version[0])
        if key not in self._cache:
            params = version[1]
            self._cache[key] = Terms(
                payments(params, self._exceptions, self._rates),
                face=_decimal(params.get("initial_face_value")),
                indexed=params.get("nominal_indexation", "none") != "none",
            )
        return self._cache[key]


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _date(value: Any) -> date | None:
    return None if value is None else date.fromisoformat(value)
