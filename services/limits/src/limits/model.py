"""Набор лимитов фонда — данные, а не код (решение 130, design/09).

В коде — четыре типа проверок и формулы показателей. Что проверять, с какими
фильтрами и порогами, — набор, который ведёт риск-менеджмент:

- **metric** — показатель фонда не больше или не меньше порога: плечо, дюрация к активам;
- **share** — доля в активах позиций по фильтру: целиком, по эмитенту или по выпуску;
- **eligible** — каждая позиция и сделка дня подходит под одно из правил;
- **purchase** — купленная бумага выполняет условие на дату сделки.

Фильтр — условия «признак входит в список» или «не входит», все вместе.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Percent = Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)]

# Признаки бумаги для фильтров. class:<id> — значение классификатора бумаги или её
# эмитента; к чему относится классификатор, знает справочник.
ATTRIBUTE = (
    r"^(kind|iss_type|coupon_kind|currency|instrument|issuer|rating_group|class:[0-9a-f-]{36})$"
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Condition(_Strict):
    attribute: Annotated[str, StringConstraints(pattern=ATTRIBUTE)]
    values: Annotated[list[Text], Field(min_length=1)]
    negate: bool = False  # «не входит в список»


Filter = list[Condition]


class MetricCheck(_Strict):
    type: Literal["metric"]
    label: Text | None = None
    metric: Literal["leverage", "duration"]
    max: Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=4)] | None = None
    min: Annotated[Decimal, Field(max_digits=10, decimal_places=4)] | None = None

    @model_validator(mode="after")
    def _bound(self) -> MetricCheck:
        if self.max is None and self.min is None:
            raise ValueError("нужен порог: не больше или не меньше")
        return self


class ShareCheck(_Strict):
    type: Literal["share"]
    label: Text | None = None
    filter: Filter = []
    group: Literal["all", "issuer", "instrument"] = "all"
    # Эмитенты, к которым проверка не применяется: ВЭБ.РФ для Л3 и Л4 (п. 6.5).
    exclude_issuers: list[UUID] = []
    max: Percent


class Rule(_Strict):
    name: Text
    filter: Filter = []
    shorts: bool = False  # разрешены ли короткие позиции


class EligibleCheck(_Strict):
    type: Literal["eligible"]
    label: Text | None = None
    rules: Annotated[list[Rule], Field(min_length=1)]


class PurchaseCheck(_Strict):
    type: Literal["purchase"]
    label: Text | None = None
    filter: Filter = []
    condition: Annotated[Filter, Field(min_length=1)]


Check = Annotated[
    MetricCheck | ShareCheck | EligibleCheck | PurchaseCheck, Field(discriminator="type")
]


class Limit(_Strict):
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
    name: Text
    clause: Text | None = None  # пункт документа
    checks: Annotated[list[Check], Field(min_length=1)]


class RatingRule(_Strict):
    """Чей рейтинг брать (п. 6.2 политики): первое правило, под которое подходит бумага."""

    filter: Filter = []
    source: Literal["instrument", "issuer", "instrument_or_issuer"]


class RatingSettings(_Strict):
    agencies: Annotated[list[Text], Field(min_length=1)]  # принимаемые агентства
    pick: Literal["highest", "lowest"] = "highest"  # из рейтингов нескольких агентств
    # Ступень общей шкалы → рейтинговая группа; ступени без группы — «группы нет».
    groups: dict[str, Text]
    rules: Annotated[list[RatingRule], Field(min_length=1)]


class Deadlines(_Strict):
    """Сроки устранения нарушения в рабочих днях и предельное продление (п. 8.4, 8.5)."""

    active: Annotated[int, Field(ge=0, le=100)] = 1
    passive_metric: Annotated[int, Field(ge=0, le=100)] = 2
    passive_other: Annotated[int, Field(ge=0, le=100)] = 20
    extension_months: Annotated[int, Field(ge=0, le=24)] = 3


class Settings(_Strict):
    ratings: RatingSettings
    warn_at: Percent = Decimal(90)  # предупреждение — с этой доли лимита, %
    deadlines: Deadlines = Deadlines()


class Document(_Strict):
    name: Text
    version: Text | None = None
    approved_on: date | None = None


class SetBody(_Strict):
    document: Document
    settings: Settings
    limits: Annotated[list[Limit], Field(min_length=1)]
