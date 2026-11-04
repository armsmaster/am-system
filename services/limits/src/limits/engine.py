"""Использование лимитов фонда за день — без ввода-вывода (design/09).

Активы — по составу СЧА: бумаги с НКД и купонами к получению, деньги, к получению
по сделкам, требования к пайщикам, размещённое в обратное РЕПО (решение 134).
Плечо — активы / СЧА. Дюрация — Σ MDᵢ × Vᵢ / активы: у фикса MD по главной
доходности, у флоатера — до ближайшего купона (решение 135).

Фильтр отвечает «да», «нет» или «нет данных»: у бумаги не заполнен признак, который
нужен условию. «Нет данных» — не нарушение, но и не «соблюдён» (решение 141).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from limits.model import (
    Condition,
    EligibleCheck,
    Limit,
    MetricCheck,
    PurchaseCheck,
    RatingSettings,
    Rule,
    SetBody,
    ShareCheck,
)

ZERO = Decimal(0)
HUNDRED = Decimal(100)
NO_GROUP = "none"  # рейтинговой группы нет: рейтинг ниже групп или рейтинга нет


# --- входы ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Position:
    """Позиция фонда на конец дня: стоимость с НКД и купонами к получению."""

    instrument_id: UUID
    secid: str
    kind: str  # bond | fund_share
    quantity: int  # < 0 — короткая позиция
    value: Decimal
    duration: Decimal | None  # MD для лимитов; у облигации без расчёта — нет


@dataclass(frozen=True, slots=True)
class Figures:
    """Состав СЧА фонда за день."""

    cash: Decimal
    receivable: Decimal
    payable: Decimal
    claims: Decimal
    obligations: Decimal
    expenses: Decimal
    fees_owed: Decimal
    repo_placed: Decimal
    repo_borrowed: Decimal
    nav: Decimal


@dataclass(frozen=True, slots=True)
class Deal:
    """Сделка купли-продажи за день; РЕПО — не покупка бумаги."""

    instrument_id: UUID
    secid: str
    side: str  # buy | sell


@dataclass(frozen=True, slots=True)
class Rated:
    status: str  # rated | unrated | unknown — рейтинг ещё не вводили
    grades: dict[str, str] = field(default_factory=dict)  # агентство → ступень общей шкалы


@dataclass(frozen=True, slots=True)
class Paper:
    id: UUID
    secid: str
    kind: str
    iss_type: str | None
    issuer_id: UUID | None
    coupon_kind: str | None
    currency: str
    classes: dict[UUID, UUID]  # классификатор → значение
    ratings: Rated


@dataclass(frozen=True, slots=True)
class Issuer:
    id: UUID
    name: str
    classes: dict[UUID, UUID]
    ratings: Rated


@dataclass(frozen=True, slots=True)
class Classifier:
    id: UUID
    name: str
    target: str  # instrument | issuer
    values: dict[UUID, str]


@dataclass(frozen=True, slots=True)
class Attributes:
    """Признаки бумаг и эмитентов на дату — из справочника."""

    papers: dict[UUID, Paper]
    issuers: dict[UUID, Issuer]
    classifiers: dict[UUID, Classifier]
    grades: tuple[str, ...]  # общая шкала — от высшей ступени к низшей


# --- выходы ---------------------------------------------------------------------


class Status(StrEnum):
    OK = "ok"
    WARNING = "warning"  # использовано не меньше порога предупреждения
    BREACH = "breach"
    NO_DATA = "no_data"  # у бумаг не заполнены нужные признаки


@dataclass(frozen=True, slots=True)
class Group:
    key: str
    name: str
    value: Decimal  # % активов
    status: Status


@dataclass(frozen=True, slots=True)
class Problem:
    secid: str
    reason: str


@dataclass(frozen=True, slots=True)
class CheckResult:
    code: str
    name: str
    clause: str | None
    label: str | None
    type: str
    status: Status
    value: Decimal | None  # показатель; доля — наибольшая по группам, %; правила — нарушений
    maximum: Decimal | None
    minimum: Decimal | None
    usage: Decimal | None  # % лимита
    groups: list[Group] = field(default_factory=list)
    violations: list[Problem] = field(default_factory=list)
    missing: list[Problem] = field(default_factory=list)
    metric: str | None = None  # у показателя фонда: leverage | duration


@dataclass(frozen=True, slots=True)
class Totals:
    assets: Decimal
    liabilities: Decimal
    nav: Decimal
    leverage: Decimal | None
    duration: Decimal | None


@dataclass(frozen=True, slots=True)
class Usage:
    totals: Totals
    checks: list[CheckResult]


# --- расчёт ---------------------------------------------------------------------


def totals(figures: Figures, positions: Sequence[Position]) -> Totals:
    """Активы и обязательства по составу СЧА; короткая позиция — обязательство."""
    longs = sum((p.value for p in positions if p.value > 0), ZERO)
    shorts = sum((-p.value for p in positions if p.value < 0), ZERO)
    assets = (
        longs + max(figures.cash, ZERO) + figures.receivable + figures.claims + figures.repo_placed
    )
    liabilities = (
        shorts
        + max(-figures.cash, ZERO)
        + figures.payable
        + figures.obligations
        + figures.expenses
        + figures.fees_owed
        + figures.repo_borrowed
    )
    leverage = assets / figures.nav if figures.nav > 0 else None
    weighted = sum(
        (p.duration * p.value for p in positions if p.kind == "bond" and p.duration is not None),
        ZERO,
    )
    return Totals(
        assets=assets,
        liabilities=liabilities,
        nav=figures.nav,
        leverage=leverage,
        duration=weighted / assets if assets > 0 else None,
    )


def evaluate(
    body: SetBody,
    figures: Figures,
    positions: Sequence[Position],
    deals: Sequence[Deal],
    attributes: Attributes,
) -> Usage:
    found = totals(figures, positions)
    facts = _Facts(attributes, body.settings.ratings)
    held = [p for p in positions if p.quantity]
    warn_at = body.settings.warn_at
    checks: list[CheckResult] = []
    for limit in body.limits:
        for check in limit.checks:
            if isinstance(check, MetricCheck):
                result = _metric(limit, check, found, held)
            elif isinstance(check, ShareCheck):
                result = _share(limit, check, found, held, facts)
            elif isinstance(check, EligibleCheck):
                result = _eligible(limit, check, held, deals, facts)
            else:
                result = _purchase(limit, check, deals, facts)
            checks.append(_with_status(result, warn_at))
    return Usage(found, checks)


def _metric(
    limit: Limit, check: MetricCheck, found: Totals, held: Sequence[Position]
) -> CheckResult:
    if check.metric == "leverage":
        value = found.leverage
        missing = [] if value is not None else [Problem("—", "СЧА не больше нуля")]
    else:
        value = found.duration
        missing = [
            Problem(p.secid, "нет дюрации: нет цены или условий выпуска")
            for p in held
            if p.kind == "bond" and p.duration is None
        ]
        if value is None:
            missing.append(Problem("—", "активов нет"))
    breach = value is not None and (
        (check.max is not None and value > check.max)
        or (check.min is not None and value < check.min)
    )
    return _result(
        limit,
        check,
        status=Status.BREACH if breach else Status.OK,
        value=value,
        maximum=check.max,
        minimum=check.min,
        usage=None if value is None or not check.max else value / check.max * HUNDRED,
        missing=missing,
    )


def _share(
    limit: Limit,
    check: ShareCheck,
    found: Totals,
    held: Sequence[Position],
    facts: _Facts,
) -> CheckResult:
    sums: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    missing: list[Problem] = []
    excluded = set(check.exclude_issuers)
    for position in held:
        paper = facts.paper(position)
        if paper is None:
            missing.append(Problem(position.secid, "бумаги нет в справочнике"))
            continue
        if paper.issuer_id in excluded:
            continue
        hit, reasons = facts.match(check.filter, paper)
        if hit is None:
            missing += [Problem(position.secid, r) for r in reasons]
            continue
        if not hit:
            continue
        if check.group == "all":
            key, name = "all", "все бумаги проверки"
        elif check.group == "instrument":
            key, name = str(paper.id), paper.secid
        elif paper.issuer_id is None:
            missing.append(Problem(position.secid, _NO_ISSUER))
            continue
        else:
            key, name = str(paper.issuer_id), facts.issuer_name(paper.issuer_id)
        sums[key] = sums.get(key, ZERO) + position.value
        names[key] = name
    groups = [
        Group(
            key,
            names[key],
            share,
            Status.BREACH if share > check.max else Status.OK,
        )
        for key, share in (
            (key, total / found.assets * HUNDRED if found.assets > 0 else ZERO)
            for key, total in sums.items()
        )
    ]
    groups.sort(key=lambda g: (-g.value, g.name))
    value = groups[0].value if groups else ZERO
    return _result(
        limit,
        check,
        status=Status.BREACH if value > check.max else Status.OK,
        value=value,
        maximum=check.max,
        minimum=None,
        usage=value / check.max * HUNDRED if check.max else None,
        groups=groups,
        missing=missing,
    )


def _eligible(
    limit: Limit,
    check: EligibleCheck,
    held: Sequence[Position],
    deals: Sequence[Deal],
    facts: _Facts,
) -> CheckResult:
    violations: list[Problem] = []
    missing: list[Problem] = []
    for position in held:
        rule, reasons = facts.rule(check, position.instrument_id)
        if rule is None:
            _sort(
                position.secid,
                reasons,
                violations,
                missing,
                "позиция не подходит ни под одно правило",
            )
        elif position.quantity < 0 and not rule.shorts:
            violations.append(
                Problem(position.secid, f"короткая позиция: правило «{rule.name}» её не разрешает")
            )
    for deal in deals:
        rule, reasons = facts.rule(check, deal.instrument_id)
        if rule is None:
            _sort(deal.secid, reasons, violations, missing, "сделка с бумагой не по правилам")
    return _result(
        limit,
        check,
        status=Status.BREACH if violations else Status.OK,
        value=Decimal(len(violations)),
        maximum=None,
        minimum=None,
        usage=None,
        violations=_unique(violations),
        missing=_unique(missing),
    )


def _purchase(
    limit: Limit, check: PurchaseCheck, deals: Sequence[Deal], facts: _Facts
) -> CheckResult:
    violations: list[Problem] = []
    missing: list[Problem] = []
    for deal in deals:
        if deal.side != "buy":
            continue
        paper = facts.paper_by_id(deal.instrument_id)
        if paper is None:
            missing.append(Problem(deal.secid, "бумаги нет в справочнике"))
            continue
        hit, reasons = facts.match(check.filter, paper)
        if hit:
            hit, reasons = facts.match(check.condition, paper)
            if hit is False:
                violations.append(
                    Problem(deal.secid, f"покупка: не выполнено {facts.describe(check.condition)}")
                )
        if hit is None:
            missing += [Problem(deal.secid, r) for r in reasons]
    return _result(
        limit,
        check,
        status=Status.BREACH if violations else Status.OK,
        value=Decimal(len(violations)),
        maximum=None,
        minimum=None,
        usage=None,
        violations=_unique(violations),
        missing=_unique(missing),
    )


def _with_status(result: CheckResult, warn_at: Decimal) -> CheckResult:
    """Нарушение важнее «нет данных», «нет данных» — важнее предупреждения."""
    if result.status is Status.BREACH:
        return result
    if result.missing:
        status = Status.NO_DATA
    elif result.usage is not None and result.usage >= warn_at:
        status = Status.WARNING
    else:
        return result
    return replace(result, status=status)


def _result(
    limit: Limit,
    check: MetricCheck | ShareCheck | EligibleCheck | PurchaseCheck,
    *,
    status: Status,
    value: Decimal | None,
    maximum: Decimal | None,
    minimum: Decimal | None,
    usage: Decimal | None,
    groups: list[Group] | None = None,
    violations: list[Problem] | None = None,
    missing: list[Problem] | None = None,
) -> CheckResult:
    return CheckResult(
        code=limit.code,
        name=limit.name,
        clause=limit.clause,
        label=check.label,
        type=check.type,
        status=status,
        value=value,
        maximum=maximum,
        minimum=minimum,
        usage=usage,
        groups=groups or [],
        violations=violations or [],
        missing=missing or [],
        metric=check.metric if isinstance(check, MetricCheck) else None,
    )


def _sort(
    secid: str,
    reasons: list[str],
    violations: list[Problem],
    missing: list[Problem],
    violation: str,
) -> None:
    """Не подошла ни под одно правило: из-за пустых признаков — «нет данных», иначе — нарушение."""
    if reasons:
        missing += [Problem(secid, r) for r in reasons]
    else:
        violations.append(Problem(secid, violation))


def _unique(problems: Iterable[Problem]) -> list[Problem]:
    return list(dict.fromkeys(problems))


_NO_ISSUER = "эмитент неизвестен — сверьте бумагу с MOEX ISS"


class _Facts:
    """Признаки бумаг для фильтров и рейтинговая группа по правилу набора."""

    def __init__(self, attributes: Attributes, ratings: RatingSettings) -> None:
        self._a = attributes
        self._ratings = ratings
        self._rank = {grade: index for index, grade in enumerate(attributes.grades)}

    def paper(self, position: Position) -> Paper | None:
        return self.paper_by_id(position.instrument_id)

    def paper_by_id(self, instrument_id: UUID) -> Paper | None:
        return self._a.papers.get(instrument_id)

    def issuer_name(self, issuer_id: UUID) -> str:
        found = self._a.issuers.get(issuer_id)
        return found.name if found is not None else str(issuer_id)

    def rule(self, check: EligibleCheck, instrument_id: UUID) -> tuple[Rule | None, list[str]]:
        """Первое правило, под которое подходит бумага; нет — почему: пустые признаки."""
        paper = self.paper_by_id(instrument_id)
        if paper is None:
            return None, ["бумаги нет в справочнике"]
        reasons: list[str] = []
        for rule in check.rules:
            hit, why = self.match(rule.filter, paper)
            if hit:
                return rule, []
            if hit is None:
                reasons += why
        return None, list(dict.fromkeys(reasons))

    def match(self, conditions: Sequence[Condition], paper: Paper) -> tuple[bool | None, list[str]]:
        """Все условия вместе: есть «нет» — нет; иначе есть пустой признак — нет данных."""
        unknown: list[str] = []
        for condition in conditions:
            found, why = self._fact(condition.attribute, paper)
            if found is None:
                unknown += why
                continue
            if bool(found & set(condition.values)) == condition.negate:
                return False, []
        if unknown:
            return None, list(dict.fromkeys(unknown))
        return True, []

    def describe(self, conditions: Sequence[Condition]) -> str:
        """Условия словами: «Принимается НКЦ в обеспечение: да»."""
        parts = []
        for c in conditions:
            name, values = (
                self._attribute_name(c.attribute),
                [self._value_name(c, v) for v in c.values],
            )
            parts.append(f"{name}: {'не ' if c.negate else ''}{', '.join(values)}")
        return "; ".join(parts)

    def _attribute_name(self, attribute: str) -> str:
        if attribute.startswith("class:"):
            found = self._a.classifiers.get(UUID(attribute[6:]))
            return f"«{found.name}»" if found else attribute
        return _ATTRIBUTE_NAMES.get(attribute, attribute)

    def _value_name(self, condition: Condition, value: str) -> str:
        if condition.attribute.startswith("class:"):
            found = self._a.classifiers.get(UUID(condition.attribute[6:]))
            if found is not None:
                return found.values.get(UUID(value), value)
        return value

    def _fact(self, attribute: str, paper: Paper) -> tuple[set[str] | None, list[str]]:
        """Значение признака бумаги; None — признак не заполнен, и почему."""
        if attribute.startswith("class:"):
            return self._class(UUID(attribute[6:]), paper)
        if attribute == "rating_group":
            group, why = self._group(paper)
            return (None, why) if group is None else ({group}, [])
        # Пустой тип ISS и эмитент — у бумаг, которые ещё не сверяли с ISS.
        plain = {
            "kind": (paper.kind, ""),
            "iss_type": (paper.iss_type, "тип бумаги ISS неизвестен — сверьте бумагу с MOEX ISS"),
            "coupon_kind": (paper.coupon_kind or NO_GROUP, ""),
            "currency": (paper.currency, ""),
            "instrument": (str(paper.id), ""),
            "issuer": (None if paper.issuer_id is None else str(paper.issuer_id), _NO_ISSUER),
        }
        value, reason = plain[attribute]
        return (None, [reason]) if value is None else ({value}, [])

    def _class(self, classifier_id: UUID, paper: Paper) -> tuple[set[str] | None, list[str]]:
        found = self._a.classifiers.get(classifier_id)
        if found is None:
            return None, ["классификатора набора нет в справочнике"]
        if found.target == "instrument":
            value = paper.classes.get(classifier_id)
            return (None, [f"не задано «{found.name}»"]) if value is None else ({str(value)}, [])
        issuer = None if paper.issuer_id is None else self._a.issuers.get(paper.issuer_id)
        if issuer is None:
            return None, [_NO_ISSUER]
        value = issuer.classes.get(classifier_id)
        if value is None:
            return None, [f"у эмитента не задано «{found.name}»"]
        return {str(value)}, []

    def _group(self, paper: Paper) -> tuple[str | None, list[str]]:
        """Рейтинговая группа по правилу п. 6.2: чей рейтинг, какие агентства, какой из них."""
        for rule in self._ratings.rules:
            hit, why = self.match(rule.filter, paper)
            if hit is None:
                return None, why
            if hit:
                break
        else:
            return NO_GROUP, []
        issuer = None if paper.issuer_id is None else self._a.issuers.get(paper.issuer_id)
        if rule.source == "instrument":
            rated, whose = paper.ratings, "выпуска"
        elif rule.source == "issuer" or paper.ratings.status == "unrated":
            if issuer is None:
                return None, [_NO_ISSUER]
            rated, whose = issuer.ratings, "эмитента"
        else:
            rated, whose = paper.ratings, "выпуска"
        if rated.status == "unknown":
            return None, [f"рейтинг {whose} не вводили"]
        accepted = set(self._ratings.agencies)
        grades = [g for agency, g in rated.grades.items() if agency in accepted and g in self._rank]
        if not grades:
            return NO_GROUP, []
        ordered = sorted(grades, key=self._rank.__getitem__)
        grade = ordered[0] if self._ratings.pick == "highest" else ordered[-1]
        return self._ratings.groups.get(grade, NO_GROUP), []


_ATTRIBUTE_NAMES = {
    "kind": "вид бумаги",
    "iss_type": "тип ISS",
    "coupon_kind": "тип купона",
    "currency": "валюта номинала",
    "instrument": "бумага",
    "issuer": "эмитент",
    "rating_group": "рейтинговая группа",
}
