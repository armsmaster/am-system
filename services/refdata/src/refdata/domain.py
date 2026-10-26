"""Предметная модель справочника: параметры бумаги, версии и их правила.

Чистый код без ввода-вывода. Правила — в docs/design/02-refdata.md.

Версия бумаги — полный снимок параметров и графиков. Какой была бумага
на дату D — версия с наибольшей датой начала не позже D.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class InstrumentKind(StrEnum):
    BOND = "bond"
    FUND_SHARE = "fund_share"  # пай биржевого фонда


class CouponKind(StrEnum):
    FIXED = "fixed"
    FLOATER = "floater"
    UNKNOWN = "unknown"  # ISS не сообщил — уточняет сопровождение


class RateKind(StrEnum):
    """Плавающая ставка флоатера."""

    KEY_RATE = "key_rate"
    RUONIA = "ruonia"
    RUSFAR = "rusfar"
    OTHER = "other"


class NominalIndexation(StrEnum):
    NONE = "none"
    RUONIA = "ruonia"
    UNKNOWN = "unknown"  # ISS говорит «номинал индексируемый», но не говорит на что


class OfferKind(StrEnum):
    PUT = "put"  # держатель может предъявить бумагу к выкупу
    CALL = "call"  # эмитент может погасить досрочно


class RuleViolationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Coupon(_Frozen):
    date: dt.date
    start_date: dt.date | None = None
    record_date: dt.date | None = None
    rate: Decimal | None = None  # % годовых; None — ещё неизвестна
    # На одну бумагу в валюте номинала. У линкера — только для наступивших выплат.
    amount: Decimal | None = None


class Amortization(_Frozen):
    date: dt.date
    percent: Decimal | None = None  # % от начального номинала
    amount: Decimal | None = None
    is_maturity: bool = False  # погашение остатка номинала


class Offer(_Frozen):
    date: dt.date
    kind: OfferKind = OfferKind.PUT
    window_start: dt.date | None = None  # приём заявок
    window_end: dt.date | None = None
    price: Decimal | None = None  # % от номинала
    iss_type: str | None = None  # как назвал оферту ISS


class FloaterTerms(_Frozen):
    """Входы расчёта вменённого спреда флоатера (решение 15). Заполняет сопровождение."""

    rate: RateKind
    margin_bp: Decimal  # маржа к плавающей ставке, базисные пункты
    capitalized: bool  # капитализируется ли ставка внутри купонного периода


class Params(_Frozen):
    """Условия выпуска бумаги в одной версии.

    Значения на дату — номинал, НКД, цены — сюда не входят: они меняются
    каждый день, их ведут рыночные данные рядами по датам (решение 63).
    Номинал обычной облигации на дату следует из начального номинала
    и графика амортизаций.
    """

    name: str
    short_name: str
    board: str  # основной режим торгов
    currency: str
    issuer: str | None = None
    initial_face_value: Decimal | None = None
    issue_date: date | None = None
    maturity_date: date | None = None
    coupon_kind: CouponKind | None = None  # None — не облигация
    coupon_frequency: int | None = None
    nominal_indexation: NominalIndexation = NominalIndexation.NONE
    coupons: tuple[Coupon, ...] = ()
    amortizations: tuple[Amortization, ...] = ()
    offers: tuple[Offer, ...] = ()
    floater: FloaterTerms | None = None


# Поля, которые знает только сопровождение: ISS их никогда не заполняет,
# поэтому синхронизация их не трогает и закреплять их не нужно.
MANUAL_ONLY = frozenset({"floater"})
FIELDS = tuple(Params.model_fields)


class Version(_Frozen):
    number: int
    valid_from: date | None  # None — с начала обращения
    source: Literal["iss", "manual"]
    author: str
    comment: str | None
    created_at: datetime
    pinned: frozenset[str] = frozenset()
    params: Params


class Alert(_Frozen):
    """Предупреждение в карточке бумаги."""

    code: str
    message: str


def version_on(versions: Iterable[Version], day: date) -> Version | None:
    """Версия, действующая на дату: наибольшая дата начала не позже ``day``."""
    eligible = [v for v in versions if v.valid_from is None or v.valid_from <= day]
    if not eligible:
        return None
    return max(eligible, key=lambda v: (v.valid_from or date.min, v.number))


def issue_terms(params: Params, today: date) -> Params:
    """Только условия выпуска: без сумм, которые ISS прогнозирует по текущему номиналу.

    У облигации с индексируемым номиналом сумма купона и погашения зависит
    от номинала в день выплаты. ISS каждый день пересчитывает будущие суммы
    на сегодняшний номинал — это прогноз, а не условие выпуска. Справочник
    хранит сумму платежа, только когда день выплаты наступил.
    """
    if params.nominal_indexation is NominalIndexation.NONE:
        return params
    return params.model_copy(
        update={
            "coupons": tuple(
                c if c.date <= today else c.model_copy(update={"amount": None})
                for c in params.coupons
            ),
            # Доля погашения у ISS — от начального номинала, поэтому тоже растёт с индексом.
            "amortizations": tuple(
                a if a.date <= today else a.model_copy(update={"amount": None, "percent": None})
                for a in params.amortizations
            ),
        }
    )


def changed_fields(before: Params, after: Params) -> list[str]:
    return [name for name in FIELDS if getattr(before, name) != getattr(after, name)]


def merge_from_iss(current: Version, from_iss: Params) -> Params:
    """Новые данные ISS поверх текущей версии: закреплённое и ручное не трогаем."""
    keep = set(current.pinned) | MANUAL_ONLY
    return from_iss.model_copy(update={name: getattr(current.params, name) for name in keep})


def apply_edit(
    current: Version,
    changes: Mapping[str, Any],
    unpin: Iterable[str],
    iss_snapshot: Params | None,
) -> tuple[Params, frozenset[str]]:
    """Ручная правка: изменённые поля закрепляются, снятые с закрепления берутся из ISS."""
    unknown = (set(changes) | set(unpin)) - set(FIELDS)
    if unknown:
        raise RuleViolationError(
            "unknown_field", f"Неизвестные поля: {', '.join(sorted(unknown))}."
        )
    unpinned = set(unpin)
    if unpinned & set(changes):
        raise RuleViolationError(
            "edit_and_unpin", "Поле нельзя одновременно изменить и снять с закрепления."
        )
    if unpinned and iss_snapshot is None:
        raise RuleViolationError(
            "no_iss_snapshot", "Нет данных ISS — снять закрепление пока нельзя."
        )

    values = current.params.model_dump()
    values.update(changes)
    for name in unpinned:
        values[name] = getattr(iss_snapshot, name)
    try:
        params = Params.model_validate(values)
    except ValidationError as exc:
        raise RuleViolationError(
            "invalid_params", f"Неверные значения: {exc.errors()[0]['msg']}"
        ) from exc

    changed = {name for name in changes if getattr(params, name) != getattr(current.params, name)}
    pinned = (set(current.pinned) | (changed - MANUAL_ONLY)) - unpinned
    return params, frozenset(pinned)


def check_new_version_date(latest: Version, valid_from: date | None) -> None:
    """Даты версий не убывают с номером: «версия на дату» остаётся однозначной."""
    if valid_from is None:
        raise RuleViolationError(
            "valid_from_required", "Укажите, с какой даты действует новая версия."
        )
    if latest.valid_from is not None and valid_from < latest.valid_from:
        raise RuleViolationError(
            "valid_from_too_early",
            f"Новая версия не может начинаться раньше действующей ({latest.valid_from:%d.%m.%Y}).",
        )


def alerts(params: Params, pinned: Iterable[str], iss_snapshot: Params | None) -> list[Alert]:
    """Что мешает считать бумагу или требует внимания сопровождения."""
    result: list[Alert] = []
    if params.coupon_kind is CouponKind.UNKNOWN:
        result.append(
            Alert(
                code="coupon_kind_unknown",
                message="ISS не сообщил тип купона — укажите: фиксированный или флоатер.",
            )
        )
    if params.coupon_kind is CouponKind.FLOATER and params.floater is None:
        result.append(
            Alert(
                code="floater_terms_missing",
                message="Не заполнены параметры флоатера — спред по бумаге не посчитать.",
            )
        )
    if params.nominal_indexation is NominalIndexation.UNKNOWN:
        result.append(
            Alert(
                code="indexation_unknown",
                message="Номинал индексируемый — укажите, на какой индекс.",
            )
        )
    if iss_snapshot is not None:
        for name in sorted(pinned):
            if getattr(params, name) != getattr(iss_snapshot, name):
                result.append(
                    Alert(
                        code="differs_from_iss",
                        message=(
                            f"Поле «{FIELD_LABELS[name]}» закреплено вручную и расходится с ISS."
                        ),
                    )
                )
    return result


FIELD_LABELS: Mapping[str, str] = {
    "name": "Название",
    "short_name": "Краткое название",
    "board": "Режим торгов",
    "currency": "Валюта",
    "issuer": "Эмитент",
    "initial_face_value": "Начальный номинал",
    "issue_date": "Дата выпуска",
    "maturity_date": "Дата погашения",
    "coupon_kind": "Тип купона",
    "coupon_frequency": "Купонов в год",
    "nominal_indexation": "Индексация номинала",
    "coupons": "Купоны",
    "amortizations": "Амортизации",
    "offers": "Оферты",
    "floater": "Параметры флоатера",
}


def journal_view(params: Params, names: Sequence[str] = FIELDS) -> dict[str, Any]:
    """Параметры для журнала: поля как есть, графики — числом записей."""
    view: dict[str, Any] = {}
    for name in names:
        value = getattr(params, name)
        if isinstance(value, tuple):
            view[name] = f"{len(value)} зап."
        elif isinstance(value, BaseModel):
            view[name] = value.model_dump(mode="json")
        elif value is None or isinstance(value, bool | str | int):
            view[name] = value
        else:
            view[name] = str(value)
    return view
