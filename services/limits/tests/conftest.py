"""Фонд «Доход 2» из примера методики шага 9, набор лимитов его политики и подменные
соседи: портфели, сделки, справочник.

Суммы — как в таблице раздела «Сквозной пример»: активы 2 800 млн ₽, обязательства
1 800 млн ₽ (прямое РЕПО под КСУ 1 795 и начисленное 5), СЧА 1 000 млн ₽.
Эмитенты, рейтинги и стоимости условные. Тесты берут данные примера через
фикстуру ``model`` — этот модуль.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import ModuleType
from typing import Any
from uuid import UUID

import httpx2
import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from am_common.clock import MOSCOW
from am_common.migrations import upgrade
from am_common.testing import TEST_AUDIENCE, TEST_ISSUER, TEST_JWKS_URL, TokenFactory
from limits.main import build_app
from limits.settings import LimitsSettings

MLN = 1_000_000
SATURDAY = 5


def _id(prefix: str, number: int) -> UUID:
    return UUID(f"00000000-0000-0000-{prefix}-{number:012d}")


FUND = _id("f000", 1)
OTHER_FUND = _id("f000", 2)
BONDS = _id("b000", 1)

# Классификаторы справочника и их значения.
SENIORITY, SENIOR, SUBORDINATED = _id("c000", 1), _id("c100", 1), _id("c100", 2)
SECURITIZATION, NOT_SECURITIZED, ICB = _id("c000", 2), _id("c200", 1), _id("c200", 2)
TRANCHE, SENIOR_TRANCHE, OTHER_TRANCHE = _id("c000", 3), _id("c300", 1), _id("c300", 2)
ISSUER_KIND, BANK, OTHERS = _id("c000", 4), _id("c400", 1), _id("c400", 2)
NCC, NCC_YES, NCC_NO = _id("c000", 5), _id("c500", 1), _id("c500", 2)

# Эмитенты.
MINFIN, ISSUER_A, ISSUER_E, VEB = (_id("e000", n) for n in (1, 2, 3, 4))
ISSUER_B, ISSUER_G, ISSUER_V, BANK_D = (_id("e000", n) for n in (5, 6, 7, 8))
DOM_AGENT, VIM = _id("e000", 9), _id("e000", 10)

ISSUER_NAMES = {
    MINFIN: "Минфин России",
    ISSUER_A: "Эмитент А",
    ISSUER_E: "Эмитент Е",
    VEB: "ВЭБ.РФ",
    ISSUER_B: "Эмитент Б",
    ISSUER_G: "Эмитент Г",
    ISSUER_V: "Эмитент В",
    BANK_D: "Банк Д",
    DOM_AGENT: "Ипотечный агент",
    VIM: "ВИМ Инвестиции",
}


def _paper(
    number: int,
    secid: str,
    issuer: UUID,
    *,
    iss_type: str = "exchange_bond",
    coupon: str | None = "floater",
    kind: str = "bond",
    classes: dict[UUID, UUID] | None = None,
) -> dict[str, Any]:
    return {
        "id": _id("a000", number),
        "secid": secid,
        "issuer": issuer,
        "iss_type": iss_type,
        "coupon_kind": coupon,
        "kind": kind,
        # Старший долг без секьюритизации, НКЦ принимает в обеспечение.
        "classes": {SENIORITY: SENIOR, SECURITIZATION: NOT_SECURITIZED, NCC: NCC_YES}
        if classes is None
        else classes,
    }


PAPERS = [
    _paper(1, "OFZ-PK", MINFIN, iss_type="ofz_bond", classes={NCC: NCC_YES}),
    _paper(2, "OFZ-PD", MINFIN, iss_type="ofz_bond", coupon="fixed", classes={NCC: NCC_YES}),
    _paper(3, "A-FLOAT", ISSUER_A),
    _paper(4, "E-FLOAT", ISSUER_E),
    _paper(5, "VEB-FLOAT", VEB),
    _paper(6, "B-FLOAT", ISSUER_B),
    _paper(7, "G-FLOAT", ISSUER_G),
    _paper(8, "V-FLOAT", ISSUER_V),
    _paper(9, "D-FLOAT", BANK_D),
    _paper(
        10,
        "D-SUB",
        BANK_D,
        coupon="fixed",
        classes={SENIORITY: SUBORDINATED, SECURITIZATION: NOT_SECURITIZED, NCC: NCC_YES},
    ),
    *(
        _paper(
            10 + n,
            f"ICB-{n}",
            DOM_AGENT,
            iss_type="corporate_bond",
            coupon="fixed",
            classes={
                SENIORITY: SENIOR,
                SECURITIZATION: ICB,
                TRANCHE: SENIOR_TRANCHE,
                NCC: NCC_YES,
            },
        )
        for n in (1, 2, 3, 4)
    ),
    _paper(15, "LQDT", VIM, iss_type="exchange_ppif", coupon=None, kind="fund_share", classes={}),
    _paper(16, "SBMM", VIM, iss_type="exchange_ppif", coupon=None, kind="fund_share", classes={}),
]
PAPER = {p["secid"]: p for p in PAPERS}

# Позиции 10.11: стоимость, млн ₽, и MD для лимитов, лет (таблица методики).
HOLDINGS: dict[str, tuple[str, str]] = {
    "OFZ-PK": ("700", "0.10"),
    "OFZ-PD": ("200", "1.80"),
    "A-FLOAT": ("270", "0.05"),
    "E-FLOAT": ("250", "0.05"),
    "VEB-FLOAT": ("400", "0.12"),
    "B-FLOAT": ("250", "0.05"),
    "G-FLOAT": ("75", "0.05"),
    "V-FLOAT": ("60", "0.05"),
    "D-FLOAT": ("200", "0.08"),
    "D-SUB": ("60", "2.50"),
    "ICB-1": ("65", "2.00"),
    "ICB-2": ("65", "2.00"),
    "ICB-3": ("65", "2.00"),
    "ICB-4": ("65", "2.00"),
    "LQDT": ("50", "0"),
}
CASH = "25"


def _issuer_ratings(day: date) -> dict[UUID, list[tuple[str, str]]]:
    """Рейтинги эмитентов на день: Эмитент Г теряет AA+ у АКРА 11.11, у Эксперт РА 12.11."""
    g_acra = "AA+" if day < date(2026, 11, 11) else "AA-"
    g_expert = "AA+" if day < date(2026, 11, 12) else "AA-"
    return {
        ISSUER_A: [("acra", "AAA")],
        ISSUER_E: [("expert_ra", "AAA")],
        VEB: [("acra", "AAA")],
        ISSUER_B: [("acra", "AA+")],
        ISSUER_G: [("acra", g_acra), ("expert_ra", g_expert)],
        ISSUER_V: [("nkr", "AA")],
        BANK_D: [("acra", "AA+")],
    }


GRADES = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "D"]


def _rated(ratings: list[tuple[str, str]] | None, *, unrated: bool = False) -> dict[str, Any]:
    if ratings:
        return {
            "status": "rated",
            "ratings": [
                {
                    "agency": agency,
                    "agency_name": agency,
                    "rating": grade,
                    "grade": grade,
                    "since": "2026-06-01",
                }
                for agency, grade in ratings
            ],
        }
    return {"status": "unrated" if unrated else "unknown", "ratings": []}


def attributes(day: date, blank: frozenset[UUID] = frozenset()) -> dict[str, Any]:
    """Ответ справочника GET /attributes на день; blank — эмитенты без записей рейтингов."""
    issuer_ratings = {k: v for k, v in _issuer_ratings(day).items() if k not in blank}
    issuer_classes = {issuer: {ISSUER_KIND: OTHERS} for issuer in ISSUER_NAMES}
    issuer_classes[BANK_D] = {ISSUER_KIND: BANK}
    return {
        "date": day.isoformat(),
        "classifiers": [
            _classifier(
                SENIORITY,
                "Очерёдность",
                "instrument",
                SENIOR,
                "старший долг",
                SUBORDINATED,
                "субординированная",
            ),
            _classifier(
                SECURITIZATION,
                "Секьюритизация",
                "instrument",
                NOT_SECURITIZED,
                "нет",
                ICB,
                "ИЦБ и прочие",
            ),
            _classifier(
                TRANCHE, "Транш", "instrument", SENIOR_TRANCHE, "старший", OTHER_TRANCHE, "иной"
            ),
            _classifier(ISSUER_KIND, "Тип эмитента", "issuer", BANK, "банк", OTHERS, "прочие"),
            _classifier(
                NCC, "Принимается НКЦ в обеспечение", "instrument", NCC_YES, "да", NCC_NO, "нет"
            ),
        ],
        "instruments": [
            {
                "id": str(p["id"]),
                "secid": p["secid"],
                "short_name": p["secid"],
                "kind": p["kind"],
                "iss_type": p["iss_type"],
                "issuer_id": str(p["issuer"]),
                "coupon_kind": p["coupon_kind"],
                "currency": "RUB",
                "nominal_indexation": "none",
                "classes": {str(k): str(v) for k, v in p["classes"].items()},
                # У ИЦБ — рейтинг выпуска; у остальных облигаций своего рейтинга нет.
                "ratings": _rated([("expert_ra", "AAA")])
                if p["secid"].startswith("ICB")
                else _rated(None, unrated=p["kind"] == "bond"),
            }
            for p in PAPERS
        ],
        "issuers": [
            {
                "id": str(issuer),
                "name": name,
                "classes": {str(k): str(v) for k, v in issuer_classes[issuer].items()},
                "ratings": _rated(issuer_ratings.get(issuer), unrated=issuer not in blank),
            }
            for issuer, name in ISSUER_NAMES.items()
        ],
    }


def _classifier(cid: UUID, name: str, target: str, *values: Any) -> dict[str, Any]:
    pairs = list(zip(values[::2], values[1::2], strict=True))
    return {
        "id": str(cid),
        "name": name,
        "target": target,
        "description": None,
        "created_by": "risk",
        "created_at": "2026-11-02T09:00:00+03:00",
        "values": [{"id": str(v), "name": n} for v, n in pairs],
    }


def scale() -> dict[str, Any]:
    return {
        "agencies": [
            {"code": code, "name": code, "ratings": GRADES}
            for code in ("acra", "expert_ra", "nkr", "nra")
        ],
        "grades": GRADES,
    }


# --- набор лимитов политики --------------------------------------------------------


def _in(attribute: str, *values: Any, negate: bool = False) -> dict[str, Any]:
    return {"attribute": attribute, "values": [str(v) for v in values], "negate": negate}


def _cls(classifier: UUID, *values: UUID, negate: bool = False) -> dict[str, Any]:
    return _in(f"class:{classifier}", *values, negate=negate)


RUB = _in("currency", "RUB")
# Классы политики — корпоративные облигации: не ОФЗ и не паи (раздел 2 политики).
CORPORATE = [_in("kind", "bond"), _in("iss_type", "ofz_bond", negate=True)]
CORPORATE_SENIOR = [*CORPORATE, _cls(SENIORITY, SENIOR), _cls(SECURITIZATION, NOT_SECURITIZED)]
SENIOR_ICB = [
    *CORPORATE,
    _cls(SECURITIZATION, ICB),
    _cls(TRANCHE, SENIOR_TRANCHE),
    _in("rating_group", "AAA"),
]
BANK_SUB = [
    *CORPORATE,
    _cls(SENIORITY, SUBORDINATED),
    _cls(ISSUER_KIND, BANK),
    _in("rating_group", "AAA", "AA+"),
]


def _share(
    label: str, conditions: list[dict[str, Any]], group: str, maximum: str, **extra: Any
) -> dict[str, Any]:
    return {
        "type": "share",
        "label": label,
        "filter": conditions,
        "group": group,
        "max": maximum,
        **extra,
    }


def model_set() -> dict[str, Any]:
    """Набор лимитов политики «Доход 2» — как его раскладывает на типы методика."""
    return {
        "document": {
            "name": "Инвестиционная политика ИПИФ «Доход 2»",
            "version": "1.0",
            "approved_on": "2026-10-23",
        },
        "settings": {
            "ratings": {
                "agencies": ["acra", "expert_ra", "nkr", "nra"],
                "pick": "highest",
                "groups": {"AAA": "AAA", "AA+": "AA+", "AA": "AA", "AA-": "AA"},
                # П. 6.2: субординированная — рейтинг эмитента-банка, ИЦБ — выпуска,
                # остальные — выпуска, а без него — эмитента.
                "rules": [
                    {"filter": [_cls(SENIORITY, SUBORDINATED)], "source": "issuer"},
                    {"filter": [_cls(SECURITIZATION, ICB)], "source": "instrument"},
                    {"filter": [], "source": "instrument_or_issuer"},
                ],
            },
            "warn_at": "90",
        },
        "limits": [
            {
                "code": "Л1",
                "name": "Плечо",
                "clause": "п. 5.1",
                "checks": [{"type": "metric", "metric": "leverage", "max": "4.0"}],
            },
            {
                "code": "Л2",
                "name": "Модифицированная дюрация портфеля",
                "clause": "п. 5.1",
                "checks": [{"type": "metric", "metric": "duration", "max": "2.5"}],
            },
            {
                "code": "7.3",
                "name": "Хеджирование не уводит дюрацию ниже нуля",
                "clause": "п. 7.3",
                "checks": [{"type": "metric", "metric": "duration", "min": "0"}],
            },
            {
                "code": "Л3",
                "name": "Корпоративные облигации AAA и AA+",
                "clause": "п. 6.1",
                "checks": [
                    _share(
                        "на класс",
                        [*CORPORATE_SENIOR, _in("rating_group", "AAA", "AA+")],
                        "all",
                        "100",
                    ),
                    _share(
                        "на эмитента",
                        [*CORPORATE_SENIOR, _in("rating_group", "AAA", "AA+", "AA")],
                        "issuer",
                        "10",
                        exclude_issuers=[str(VEB)],
                    ),
                ],
            },
            {
                "code": "Л4",
                "name": "Корпоративные облигации AA и AA−",
                "clause": "п. 6.1",
                "checks": [
                    _share("на класс", [*CORPORATE_SENIOR, _in("rating_group", "AA")], "all", "50"),
                    _share(
                        "на эмитента",
                        [*CORPORATE_SENIOR, _in("rating_group", "AA")],
                        "issuer",
                        "2.5",
                        exclude_issuers=[str(VEB)],
                    ),
                ],
            },
            {
                "code": "Л5",
                "name": "ИЦБ и прочие секьюритизации, старший транш",
                "clause": "п. 6.1",
                "checks": [
                    _share("на класс", SENIOR_ICB, "all", "20"),
                    _share("на выпуск", SENIOR_ICB, "instrument", "2.5"),
                ],
            },
            {
                "code": "Л6",
                "name": "Субординированные облигации банков",
                "clause": "п. 6.1",
                "checks": [
                    _share("на класс", BANK_SUB, "all", "10"),
                    _share("на эмитента", BANK_SUB, "issuer", "2.5"),
                ],
            },
            {
                "code": "Л7",
                "name": "Все облигации одного банка",
                "clause": "п. 6.3",
                "checks": [
                    _share(
                        "на эмитента",
                        [_in("kind", "bond"), _cls(ISSUER_KIND, BANK)],
                        "issuer",
                        "10",
                    )
                ],
            },
            {
                "code": "6.1",
                "name": "Корпоративные облигации ниже AA− или без рейтинга",
                "clause": "п. 6.1",
                "checks": [
                    _share(
                        "на класс",
                        [
                            _in("kind", "bond"),
                            _in("iss_type", "ofz_bond", negate=True),
                            _in("rating_group", "none"),
                        ],
                        "all",
                        "0",
                    )
                ],
            },
            {
                "code": "4",
                "name": "Допустимые инструменты",
                "clause": "разд. 4, пп. 4.2, 7.4",
                "checks": [
                    {
                        "type": "eligible",
                        "rules": [
                            {
                                "name": "паи БПИФ",
                                "filter": [
                                    _in("instrument", PAPER["LQDT"]["id"], PAPER["SBMM"]["id"]),
                                    RUB,
                                ],
                            },
                            {
                                "name": "ОФЗ",
                                "filter": [_in("iss_type", "ofz_bond"), RUB],
                                "shorts": True,
                            },
                            {
                                "name": "корпоративные, старший долг",
                                "filter": [
                                    *CORPORATE_SENIOR,
                                    _in("rating_group", "AAA", "AA+", "AA"),
                                    RUB,
                                ],
                            },
                            {"name": "ИЦБ", "filter": [*SENIOR_ICB, RUB]},
                            {"name": "субординированные", "filter": [*BANK_SUB, RUB]},
                        ],
                    }
                ],
            },
            {
                "code": "4.3",
                "name": "Покупка — только бумаг, которые НКЦ принимает в обеспечение",
                "clause": "п. 4.3",
                "checks": [
                    {
                        "type": "purchase",
                        "filter": [_in("kind", "bond")],
                        "condition": [_cls(NCC, NCC_YES)],
                    }
                ],
            },
        ],
    }


# --- подменные соседи --------------------------------------------------------------

PORTFOLIO_URL = "http://portfolio.test"
BOOKING_URL = "http://booking.test"
REFDATA_URL = "http://refdata.test"
# Вторник 17.11.2026, вечер: неделя примера уже прошла.
NOW = datetime(2026, 11, 17, 19, 0, tzinfo=MOSCOW)


class World:
    """Всё, куда сервис ходит по сети: Keycloak (ключи), портфели, сделки, справочник."""

    def __init__(self, tokens: TokenFactory) -> None:
        self.tokens = tokens
        self.down: set[str] = set()
        self.failing: set[str] = set()  # отвечают ошибкой
        # Позиции: стоимость, млн ₽, и MD; по дням — изменения к таблице 10.11.
        self.changes: dict[date, dict[str, tuple[str, str] | None]] = {}
        self.cash: dict[date, str] = {}
        self.deals: dict[date, list[dict[str, Any]]] = {}
        self.not_calculated: set[date] = set()
        self.blank: dict[date, frozenset[UUID]] = {}  # рейтинги эмитентов ещё не введены
        self.now = NOW  # часы сервиса: журнал проверяет прошедшие дни
        self.keycloak_down = False
        # Портфели показывают все фонды, а сервис сделок прячет по hidden: так проверяем
        # отказ в доступе у соседа.
        self.visible_funds = False
        self.tokens_issued = 0  # сколько раз служебная учётная запись брала токен
        # Трейдер второй фонд не видит.
        self.hidden = {"trading": {str(OTHER_FUND)}}

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def holdings(self, day: date) -> dict[str, tuple[str, str]]:
        found = dict(HOLDINGS)
        for secid, change in self.changes.get(day, {}).items():
            if change is None:
                found.pop(secid, None)
            else:
                found[secid] = change
        return found

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        url, path = str(request.url), request.url.path
        if path.endswith("/protocol/openid-connect/certs"):
            return httpx2.Response(200, json=self.tokens.jwks())
        if path.endswith("/protocol/openid-connect/token"):
            # Служебная учётная запись am-limits: роль fund_reader.
            if self.keycloak_down:
                raise httpx2.ConnectError("keycloak is down", request=request)
            self.tokens_issued += 1
            token = self.tokens.issue(
                subject="svc-limits", username="service-account-am-limits", roles=["fund_reader"]
            )
            return httpx2.Response(200, json={"access_token": token, "expires_in": 300})
        for name, base in (
            ("portfolio", PORTFOLIO_URL),
            ("booking", BOOKING_URL),
            ("refdata", REFDATA_URL),
        ):
            if url.startswith(base):
                if name in self.down:
                    raise httpx2.ConnectError(f"{name} is down", request=request)
                if name in self.failing:
                    return httpx2.Response(500, json={"detail": "boom"})
                handler = getattr(self, f"_{name}")
                response: httpx2.Response = handler(request, path)
                return response
        raise AssertionError(f"unexpected request {url}")

    @staticmethod
    def _roles(request: httpx2.Request) -> list[str]:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        found: list[str] = jwt.decode(token, options={"verify_signature": False})["realm_access"][
            "roles"
        ]
        return found

    def _portfolio(self, request: httpx2.Request, path: str) -> httpx2.Response:
        assert path == "/portfolios"
        roles = self._roles(request)
        hidden = {h for role in roles for h in self.hidden.get(role, set())}
        if self.visible_funds:
            hidden = set()
        items = [
            {"id": str(FUND), "code": "DOHOD2", "name": "Доход 2", "is_fund": True},
            {"id": str(OTHER_FUND), "code": "OPIF1", "name": "ОПИФ 1", "is_fund": True},
            {"id": str(BONDS), "code": "DOHOD2-BONDS", "name": "Облигации", "is_fund": False},
        ]
        for item in items:
            item["fund_id"] = None if item["is_fund"] else str(FUND)
        return httpx2.Response(200, json=[i for i in items if i["id"] not in hidden])

    def _booking(self, request: httpx2.Request, path: str) -> httpx2.Response:
        params = request.url.params
        # Сервис сделок сам проверяет доступ: чужой фонд — «не найден».
        roles = self._roles(request)
        asked = params.get("fund_id") or params.get("portfolio_id")
        hidden = {h for role in roles for h in self.hidden.get(role, set())}
        if "fund_reader" not in roles and asked in hidden:
            return httpx2.Response(404, json={"detail": "not found"})
        if path == "/nav":
            day = date.fromisoformat(params["from"])
            days = [] if day in self.not_calculated else [self._nav(day)]
            return httpx2.Response(200, json={"fund_id": params["fund_id"], "days": days})
        if path == "/positions":
            day = date.fromisoformat(params["date"])
            return httpx2.Response(200, json={"lines": self._lines(day)})
        assert path == "/trades"
        return httpx2.Response(200, json=self.deals.get(date.fromisoformat(params["date"]), []))

    def _lines(self, day: date) -> list[dict[str, Any]]:
        lines = []
        for secid, (value, duration) in self.holdings(day).items():
            paper = PAPER[secid]
            floater = paper["coupon_kind"] == "floater"
            lines.append(
                {
                    "instrument_id": str(paper["id"]),
                    "secid": secid,
                    "kind": paper["kind"],
                    "trade_quantity": int(Decimal(value) * 1000),
                    "clean": str(Decimal(value) * MLN),
                    "accrued_value": "0",
                    "coupon_due": "0",
                    "principal_due": "0",
                    # У флоатера сервис сделок отдаёт и спред-дюрацию: лимитам она не нужна.
                    "duration": None
                    if paper["kind"] != "bond"
                    else ("0.9" if floater else duration),
                    "reset_duration": duration if floater else None,
                }
            )
        return lines

    def _nav(self, day: date) -> dict[str, Any]:
        cash = Decimal(self.cash.get(day, CASH)) * MLN
        securities = sum((Decimal(v) * MLN for v, _ in self.holdings(day).values()), Decimal(0))
        fees, repo = Decimal(5 * MLN), Decimal(1795 * MLN)
        zero = "0"
        return {
            "date": day.isoformat(),
            "nav": str(securities + cash - fees - repo),
            "cash": str(cash),
            "receivable": zero,
            "payable": zero,
            "claims": zero,
            "obligations": zero,
            "expenses": zero,
            "fees_owed": str(fees),
            "repo_placed": zero,
            "repo_borrowed": str(repo),
        }

    def _refdata(self, request: httpx2.Request, path: str) -> httpx2.Response:
        params = request.url.params
        if path == "/ratings/scale":
            return httpx2.Response(200, json=scale())
        if path == "/calendar/add-trading-days":
            # Торговые дни — будни: в ноябре 2026 после 4.11 праздников нет.
            day = date.fromisoformat(params["date"])
            for _ in range(int(params["days"])):
                day += timedelta(days=1)
                while day.weekday() >= SATURDAY:
                    day += timedelta(days=1)
            return httpx2.Response(200, json={"date": day.isoformat()})
        assert path == "/attributes"
        day = date.fromisoformat(params["date"])
        return httpx2.Response(200, json=attributes(day, self.blank.get(day, frozenset())))


def deal(secid: str, side: str = "buy", amount: str = "15", **changes: Any) -> dict[str, Any]:
    """Сделка дня, как её отдаёт сервис сделок; сумма — млн ₽."""
    return {
        "instrument_id": str(PAPER[secid]["id"]),
        "secid": secid,
        "side": side,
        "amount": str(Decimal(amount) * MLN),
        "kind": "outright",
        "status": "entered",
        **changes,
    }


class Api:
    """Пользователь с ролью: запросы к сервису с его токеном."""

    def __init__(self, client: TestClient, tokens: TokenFactory, role: str) -> None:
        self._client = client
        token = tokens.issue(subject=f"u-{role}", username=role, roles=[role])
        self._headers = {"Authorization": f"Bearer {token}"}

    def call(self, method: str, path: str, body: Any = None) -> httpx2.Response:
        return self._client.request(method, path, json=body, headers=self._headers)

    def ok(self, method: str, path: str, body: Any = None) -> Any:
        response = self.call(method, path, body)
        assert response.is_success, response.text
        return response.json()

    def refused(self, method: str, path: str, body: Any = None) -> tuple[int, str]:
        response = self.call(method, path, body)
        assert response.is_error, response.text
        detail = response.json()["detail"]
        return response.status_code, detail["code"] if isinstance(detail, dict) else "invalid"


@pytest.fixture
def risk(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "risk")


@pytest.fixture
def trader(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "trading")


@pytest.fixture(scope="session")
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def world(tokens: TokenFactory) -> World:
    return World(tokens)


@pytest.fixture
def model() -> ModuleType:
    """Данные примера: ``model.FUND``, ``model.model_set()``, ``model.deal(...)``."""
    return sys.modules[__name__]


@pytest.fixture
def bare_settings(postgres_url: str, redis_url: str, schema: str) -> LimitsSettings:
    return LimitsSettings(
        auth_issuer=TEST_ISSUER,
        auth_jwks_url=TEST_JWKS_URL,
        auth_audience=TEST_AUDIENCE,
        database_url=SecretStr(postgres_url),
        database_schema=schema,
        redis_url=SecretStr(redis_url),
        portfolio_url=PORTFOLIO_URL,
        booking_url=BOOKING_URL,
        refdata_url=REFDATA_URL,
        keycloak_url="http://keycloak.test/auth",
        keycloak_client_secret=SecretStr("limits-test-secret"),
    )


@pytest.fixture
async def settings(bare_settings: LimitsSettings) -> LimitsSettings:
    await asyncio.to_thread(upgrade, bare_settings, "limits:migrations")
    return bare_settings


@pytest.fixture
def client(settings: LimitsSettings, world: World) -> Iterator[TestClient]:
    app = build_app(settings, http_transport=world.transport(), clock=lambda: world.now)
    with TestClient(app) as test_client:
        yield test_client
