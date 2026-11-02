"""Кредитные рейтинги по национальной шкале: агентства и общая шкала (design/09).

Агентства пишут одну и ту же ступень по-разному: AA-(RU) у АКРА, ruAA- у Эксперт РА,
AA-.ru у НКР, AA-|ru| у НРА. Справочник хранит запись агентства как есть и переводит
её в общую шкалу — по ней лимиты сравнивают рейтинги разных агентств.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from refdata.domain import RuleViolationError


@dataclass(frozen=True, slots=True)
class Agency:
    code: str
    name: str
    pattern: str  # запись ступени: {grade} — ступень общей шкалы


# Агентства из реестра Банка России с национальной шкалой.
AGENCIES: tuple[Agency, ...] = (
    Agency("acra", "АКРА", "{grade}(RU)"),
    Agency("expert_ra", "Эксперт РА", "ru{grade}"),
    Agency("nkr", "НКР", "{grade}.ru"),
    Agency("nra", "НРА", "{grade}|ru|"),
)
AGENCY_CODES = frozenset(a.code for a in AGENCIES)
_BY_CODE = {a.code: a for a in AGENCIES}

# Общая шкала — от высшей ступени к низшей.
GRADES: tuple[str, ...] = (
    "AAA",
    "AA+",
    "AA",
    "AA-",
    "A+",
    "A",
    "A-",
    "BBB+",
    "BBB",
    "BBB-",
    "BB+",
    "BB",
    "BB-",
    "B+",
    "B",
    "B-",
    "CCC",
    "CC",
    "C",
    "RD",
    "SD",
    "D",
)
_GRADE_SET = frozenset(GRADES)


def agency(code: str) -> Agency:
    found = _BY_CODE.get(code)
    if found is None:
        raise RuleViolationError("unknown_agency", f"Агентства «{code}» нет в списке.")
    return found


def notation(agency_code: str, grade: str) -> str:
    """Как агентство пишет ступень: AA- → AA-(RU) у АКРА."""
    return agency(agency_code).pattern.format(grade=grade)


def notations(agency_code: str) -> list[str]:
    """Все записи агентства — от высшей ступени к низшей: для выбора в форме."""
    return [notation(agency_code, grade) for grade in GRADES]


def grade_of(agency_code: str, rating: str) -> str:
    """Ступень общей шкалы по записи агентства; «−» и «-» — одно и то же."""
    found = agency(agency_code)
    text = rating.strip().replace("−", "-")
    prefix, suffix = found.pattern.split("{grade}")
    match = re.fullmatch(
        f"{re.escape(prefix)}(?P<grade>[A-Za-z+-]+){re.escape(suffix)}", text, re.IGNORECASE
    )
    if match is None or match["grade"].upper() not in _GRADE_SET:
        example = notation(found.code, "AA-")
        raise RuleViolationError(
            "unknown_rating",
            f"«{rating}» — не рейтинг {found.name} по национальной шкале. "
            f"Пример записи: {example}.",
        )
    return match["grade"].upper()
