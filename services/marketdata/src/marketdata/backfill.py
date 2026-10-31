"""Цены за прошлые дни: событие, по которому сервис сделок пересчитывает оценку
позиций с этого дня (design/06, «Пересчёт»).

Цены за сегодня событий не дают: сегодняшнюю оценку сервис сделок берёт
по текущей цене при чтении, а в начале следующего дня пересчитывает вчера.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.events import Actor, Event
from am_common.outbox import Outbox
from marketdata.db import outbox

SERVICE = "marketdata"
PRICES_BACKFILLED = "prices.backfilled"


def past_days(rows: Iterable[tuple[UUID, date]], today: date) -> dict[UUID, date]:
    """Самая ранняя прошлая дата по каждой бумаге; сегодняшние и будущие не в счёт."""
    earliest: dict[UUID, date] = {}
    for instrument_id, day in rows:
        if day < today and (instrument_id not in earliest or day < earliest[instrument_id]):
            earliest[instrument_id] = day
    return earliest


async def announce(
    session: AsyncSession,
    earliest: Mapping[UUID, date],
    secids: Mapping[UUID, str],
    actor: Actor,
) -> None:
    """Одно событие на пакет или загрузку: какие бумаги и с какой даты."""
    if not earliest:
        return
    since = min(earliest.values())
    await Outbox(outbox).add(
        session,
        Event(
            service=SERVICE,
            type=PRICES_BACKFILLED,
            actor=actor,
            entity_type="prices",
            entity_id=since.isoformat(),
            data={
                "since": since.isoformat(),
                "instruments": [
                    {"id": str(i), "secid": secids[i], "since": d.isoformat()}
                    for i, d in sorted(earliest.items(), key=lambda item: secids[item[0]])
                ],
            },
        ),
    )
