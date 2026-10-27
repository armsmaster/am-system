"""Цена для оценки: правило бумаги, ручная цена, устаревшие цены.

Чистый код без ввода-вывода. Правила — в docs/design/03-marketdata.md,
раздел «Методика», и решения 65, 69–71, 74.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from am_common.clock import MOSCOW

ISS = "iss"  # источник — MOEX ISS
MANUAL = "manual"  # ручная цена трейдинга
MAX_TIMEFRAME = 240  # минут: самый длинный бар — 4 часа


class RuleViolationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PriceType(StrEnum):
    VWAP = "vwap"  # средневзвешенная с начала торгового дня
    LAST = "last"
    BID = "bid"
    ASK = "ask"
    CLOSE = "close"
    MARKET_PRICE = "market_price"  # рыночная цена биржи
    ADMITTED_QUOTE = "admitted_quote"  # признаваемая котировка
    DEMA = "dema"  # считает сам сервис по OHLC-барам


# Что может прислать скрипт: всё, кроме DEMA.
FEED_TYPES = tuple(kind for kind in PriceType if kind is not PriceType.DEMA)

PRICE_LABELS: Mapping[str, str] = {
    PriceType.VWAP: "VWAP",
    PriceType.LAST: "последняя сделка",
    PriceType.BID: "bid",
    PriceType.ASK: "ask",
    PriceType.CLOSE: "закрытие",
    PriceType.MARKET_PRICE: "рыночная цена",
    PriceType.ADMITTED_QUOTE: "признаваемая котировка",
    PriceType.DEMA: "DEMA",
    MANUAL: "ручная цена",
}


@dataclass(frozen=True, slots=True)
class Observation:
    """Цена одного типа из одного источника на момент."""

    type: PriceType
    source: str
    time: datetime
    value: Decimal


@dataclass(frozen=True, slots=True)
class Rule:
    """Правило оценки бумаги (решение 65): какие цены и в каком порядке."""

    types: tuple[PriceType, ...] = (PriceType.VWAP,)
    dema_timeframe: int | None = None  # минуты: по барам какого таймфрейма считать DEMA
    dema_half_life: Decimal | None = None  # минуты
    spread_threshold_bp: int = 50  # подсветка разброса цен в интерфейсе

    def check(self) -> None:
        if not self.types:
            raise RuleViolationError("no_price_types", "В правиле нужен хотя бы один тип цены.")
        if len(set(self.types)) != len(self.types):
            raise RuleViolationError("duplicate_price_type", "Тип цены в правиле повторяется.")
        dema_set = self.dema_timeframe is not None or self.dema_half_life is not None
        if PriceType.DEMA in self.types or dema_set:
            if self.dema_timeframe is None or self.dema_half_life is None:
                raise RuleViolationError(
                    "dema_settings_required",
                    "Для DEMA укажите таймфрейм баров и период полураспада.",
                )
            if not 1 <= self.dema_timeframe <= MAX_TIMEFRAME:
                raise RuleViolationError("bad_timeframe", "Таймфрейм баров — от 1 до 240 минут.")
            if self.dema_half_life <= 0:
                raise RuleViolationError(
                    "bad_half_life", "Период полураспада должен быть больше нуля."
                )
        if self.spread_threshold_bp < 0:
            raise RuleViolationError("bad_threshold", "Порог разброса не может быть меньше нуля.")


DEFAULT_RULE = Rule()


@dataclass(frozen=True, slots=True)
class ManualPrice:
    """Ручная цена трейдинга: действует до конца торгового дня — и только (решение 70)."""

    value: Decimal
    entered_at: datetime
    author: str
    comment: str

    def valid_at(self, at: datetime) -> bool:
        return self.entered_at <= at and moscow_date(self.entered_at) == moscow_date(at)


@dataclass(frozen=True, slots=True)
class Choice:
    """Цена для оценки и откуда она."""

    value: Decimal
    type: str  # тип цены или "manual"
    source: str
    time: datetime
    stale: bool
    reason: str


def moscow_date(moment: datetime) -> date:
    return moment.astimezone(MOSCOW).date()


def latest(observations: Iterable[Observation]) -> Observation | None:
    """Самая поздняя цена; при равенстве времени — не из ISS: скрипт свежее."""
    return max(observations, key=lambda o: (o.time, o.source != ISS), default=None)


def choose_price(
    *,
    at: datetime,
    rule: Rule,
    latest_by_type: Mapping[PriceType, Observation],
    manual: ManualPrice | None,
    trading_day: date,
) -> Choice | None:
    """Цена на момент ``at``: ручная, иначе первый тип правила с ценой за сегодня.

    «Сегодня» — торговый день момента ``at``: в субботу это пятница. Если ни
    у одного типа нет цены за сегодня, берём последнюю известную цену первого
    типа и помечаем её устаревшей. ``None`` — цен этого типа не было никогда.
    """
    if manual is not None and manual.valid_at(at):
        return Choice(
            value=manual.value,
            type=MANUAL,
            source=MANUAL,
            time=manual.entered_at,
            stale=False,
            reason=f"ручная цена до конца дня: {manual.comment}",
        )
    for position, kind in enumerate(rule.types):
        found = latest_by_type.get(kind)
        if found is not None and found.time <= at and moscow_date(found.time) >= trading_day:
            if position == 0:
                reason = f"по правилу: {PRICE_LABELS[kind]}"
            else:
                missing = ", ".join(PRICE_LABELS[t] for t in rule.types[:position])
                reason = f"за сегодня нет: {missing} — берём {PRICE_LABELS[kind]}"
            return Choice(found.value, kind, found.source, found.time, False, reason)

    first = latest_by_type.get(rule.types[0])
    if first is None or first.time > at:
        return None
    return Choice(
        value=first.value,
        type=first.type,
        source=first.source,
        time=first.time,
        stale=True,
        reason=(
            f"за сегодня цен нет — последняя известная {PRICE_LABELS[first.type]} "
            f"от {moscow_date(first.time):%d.%m.%Y}"
        ),
    )


def spread_bp(values: Iterable[Decimal], base: Decimal) -> Decimal | None:
    """Разброс цен в базисных пунктах от цены оценки; меньше двух цен — нет разброса."""
    prices = list(values)
    if len(prices) < 2:  # noqa: PLR2004 — разброс есть только у двух цен и больше
        return None
    return ((max(prices) - min(prices)) / base * 10_000).quantize(Decimal("0.1"))
