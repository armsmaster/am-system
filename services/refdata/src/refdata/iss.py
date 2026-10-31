"""MOEX ISS: поиск бумаги по коду и разбор её параметров.

Разбор — чистые функции над ответами ISS (их проверяют тесты на записанных
ответах настоящего ISS). Клиент только делает запросы и передаёт ответы
в разбор.

Используется только открытая часть ISS (решение 61): описание бумаги
и график платежей (bondization).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx2

from refdata.domain import (
    Amortization,
    Coupon,
    CouponKind,
    InstrumentKind,
    IssFloater,
    NominalIndexation,
    Offer,
    OfferKind,
    Params,
    RateKind,
)

# Группы ISS, которые поддерживает справочник.
_KINDS = {"stock_bonds": InstrumentKind.BOND, "stock_ppif": InstrumentKind.FUND_SHARE}
# В ISS рубль обозначен старым кодом SUR.
_CURRENCIES = {"SUR": "RUB", "RUR": "RUB"}
# Коды ставок флоатеров в ISS, которые мы видели на настоящих бумагах; прочие — «другая».
_BENCHMARKS = {"RREFKEYR": RateKind.KEY_RATE, "RUONIA": RateKind.RUONIA}


class IssError(Exception):
    """ISS недоступен или ответил не так, как ожидалось."""


@dataclass(frozen=True, slots=True)
class Candidate:
    """Строка результата поиска ISS."""

    secid: str
    isin: str | None
    short_name: str
    name: str
    type_name: str | None
    group: str | None
    board: str | None
    issuer: str | None


class NotFoundInIssError(Exception):
    """Бумаги с таким кодом нет; ``candidates`` — что нашёл поиск."""

    def __init__(self, code: str, candidates: list[Candidate]) -> None:
        super().__init__(f"{code} not found in ISS")
        self.code = code
        self.candidates = candidates


class UnsupportedSecurityError(Exception):
    def __init__(self, secid: str, type_name: str | None) -> None:
        super().__init__(f"{secid}: unsupported security type {type_name}")
        self.secid = secid
        self.type_name = type_name


@dataclass(frozen=True, slots=True)
class Security:
    """Бумага, как её видит ISS."""

    secid: str
    isin: str | None
    kind: InstrumentKind
    params: Params


# --- разбор ---------------------------------------------------------------------


def rows(payload: Mapping[str, Any], block: str) -> list[dict[str, Any]]:
    """Таблица ISS ``{columns, data}`` → список словарей."""
    table = payload.get(block)
    if not isinstance(table, Mapping):
        raise IssError(f"ISS response has no block {block!r}")
    columns = table["columns"]
    return [dict(zip(columns, row, strict=True)) for row in table["data"]]


def parse_search(payload: Mapping[str, Any]) -> list[Candidate]:
    return [
        Candidate(
            secid=row["secid"],
            isin=row.get("isin"),
            short_name=row.get("shortname") or row["secid"],
            name=row.get("name") or row["secid"],
            type_name=row.get("type"),
            group=row.get("group"),
            board=row.get("primary_boardid"),
            issuer=row.get("emitent_title"),
        )
        for row in rows(payload, "securities")
    ]


def pick(code: str, candidates: Iterable[Candidate]) -> Candidate:
    """Точное совпадение по коду биржи или ISIN — без угадывания."""
    wanted = code.strip().upper()
    found = list(candidates)
    for candidate in found:
        if wanted in {candidate.secid.upper(), (candidate.isin or "").upper()}:
            return candidate
    raise NotFoundInIssError(code, found)


def parse_security(
    candidate: Candidate,
    description: Mapping[str, Any],
    bondization: Mapping[str, Any] | None,
) -> Security:
    desc = {row["name"]: row["value"] for row in rows(description, "description")}
    group = desc.get("GROUP") or candidate.group
    kind = _KINDS.get(group or "")
    if kind is None:
        raise UnsupportedSecurityError(candidate.secid, desc.get("TYPENAME") or candidate.type_name)

    boards = rows(description, "boards")
    primary = next((b for b in boards if b.get("is_primary") == 1), None)
    board = (primary or {}).get("boardid") or candidate.board or ""
    currency_code = desc.get("FACEUNIT") or (primary or {}).get("currencyid") or "SUR"

    params = Params(
        name=desc.get("NAME") or candidate.name,
        short_name=desc.get("SHORTNAME") or candidate.short_name,
        board=board,
        currency=_CURRENCIES.get(currency_code, currency_code),
        issuer=candidate.issuer,
        initial_face_value=_decimal(desc.get("INITIALFACEVALUE")),
        issue_date=_date(desc.get("ISSUEDATE")),
        maturity_date=_date(desc.get("MATDATE")),
        coupon_kind=_coupon_kind(desc) if kind is InstrumentKind.BOND else None,
        coupon_frequency=_int(desc.get("COUPONFREQUENCY")),
        nominal_indexation=_indexation(desc.get("FACEVALUE_TYPE")),
        iss_floater=_iss_floater(desc),
        **(_schedules(bondization) if bondization is not None else {}),
    )
    return Security(
        secid=desc.get("SECID") or candidate.secid,
        isin=desc.get("ISIN") or candidate.isin,
        kind=kind,
        params=params,
    )


def _coupon_kind(desc: Mapping[str, Any]) -> CouponKind:
    """Тип купона по описанию ISS. Неясно — не угадываем, а просим уточнить."""
    text = f"{desc.get('BOND_TYPE') or ''} {desc.get('COUPON_DETAILS') or ''}".lower()
    if "флоатер" in text or "плава" in text:
        return CouponKind.FLOATER
    if "фиксирован" in text:
        return CouponKind.FIXED
    return CouponKind.UNKNOWN


def _iss_floater(desc: Mapping[str, Any]) -> IssFloater | None:
    """Ставка и маржа флоатера из описания ISS; маржа в ISS — в процентах."""
    benchmark = desc.get("COUPON_BENCHMARK")
    if not benchmark:
        return None
    spread = _decimal(desc.get("COUPON_BENCHMARK_SPREAD"))
    return IssFloater(
        benchmark=benchmark,
        rate=_BENCHMARKS.get(benchmark, RateKind.OTHER),
        margin_bp=None if spread is None else spread * 100,
    )


def _indexation(facevalue_type: str | None) -> NominalIndexation:
    if facevalue_type is None or facevalue_type.lower().startswith("фиксирован"):
        return NominalIndexation.NONE
    return NominalIndexation.UNKNOWN


def _schedules(payload: Mapping[str, Any]) -> dict[str, Any]:
    coupons = [
        Coupon(
            date=_required_date(row["coupondate"]),
            start_date=_date(row.get("startdate")),
            record_date=_date(row.get("recorddate")),
            rate=_decimal(row.get("valueprc")),
            amount=_decimal(row.get("value")),
        )
        for row in rows(payload, "coupons")
    ]
    amortizations = [
        Amortization(
            date=_required_date(row["amortdate"]),
            percent=_decimal(row.get("valueprc")),
            amount=_decimal(row.get("value")),
            is_maturity=row.get("data_source") == "maturity",
        )
        for row in rows(payload, "amortizations")
    ]
    # Строка «Оферта/Погашение» с датой 0000-00-00 — заглушка ISS, а не оферта.
    offers = [
        Offer(
            date=offer_date,
            kind=_offer_kind(row.get("offertype")),
            window_start=_date(row.get("offerdatestart")),
            window_end=_date(row.get("offerdateend")),
            price=_decimal(row.get("price")),
            iss_type=row.get("offertype"),
        )
        for row in rows(payload, "offers")
        if (offer_date := _date(row["offerdate"])) is not None
    ]
    return {
        "coupons": tuple(sorted(coupons, key=lambda c: c.date)),
        "amortizations": tuple(sorted(set(amortizations), key=lambda a: a.date)),
        "offers": tuple(sorted(offers, key=lambda o: o.date)),
    }


def _offer_kind(iss_type: str | None) -> OfferKind:
    text = (iss_type or "").lower()
    return OfferKind.CALL if "колл" in text or "call" in text else OfferKind.PUT


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise IssError(f"not a number: {value!r}") from exc


def _int(value: Any) -> int | None:
    number = _decimal(value)
    return None if number is None else int(number)


def _date(value: Any) -> date | None:
    # Пустые даты ISS отдаёт как None или 0000-00-00.
    if not value or value == "0000-00-00":
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise IssError(f"not a date: {value!r}") from exc


def _required_date(value: Any) -> date:
    parsed = _date(value)
    if parsed is None:
        raise IssError("schedule row without a date")
    return parsed


# --- клиент ---------------------------------------------------------------------


class IssClient:
    def __init__(self, http: httpx2.AsyncClient, base_url: str, *, timeout: float = 20.0) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def find(self, code: str) -> Security:
        """Бумага по коду биржи или ISIN: поиск → описание → график платежей."""
        candidates = parse_search(await self._get("securities.json", q=code.strip()))
        candidate = pick(code, candidates)
        description = await self._get(f"securities/{candidate.secid}.json")
        group = {r["name"]: r["value"] for r in rows(description, "description")}.get("GROUP")
        bondization = None
        if _KINDS.get(group or candidate.group or "") is InstrumentKind.BOND:
            bondization = await self._get(
                f"statistics/engines/stock/markets/bonds/bondization/{candidate.secid}.json",
                limit="unlimited",
            )
        return parse_security(candidate, description, bondization)

    async def _get(self, path: str, **params: str) -> dict[str, Any]:
        try:
            response = await self._http.get(
                f"{self._base}/{path}",
                params={"iss.meta": "off", **params},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise IssError(f"ISS request {path} failed: {exc}") from exc
        _check_complete(payload)
        return payload


def _check_complete(payload: Mapping[str, Any]) -> None:
    """ISS отдаёт таблицы страницами. С limit=unlimited должна прийти одна страница."""
    for name, block in payload.items():
        if name.endswith(".cursor") and isinstance(block, Mapping):
            for cursor in rows(payload, name):
                if cursor["INDEX"] + cursor["PAGESIZE"] < cursor["TOTAL"]:
                    raise IssError(f"ISS returned an incomplete {name[:-7]} table")
