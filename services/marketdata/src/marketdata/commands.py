"""Изменения рыночных данных: ручная цена, правило оценки, загрузка из ISS.

Ручная цена и правило попадают в журнал действий: изменение и событие
записываются одной транзакцией (outbox). Запрос к ISS — до транзакции:
держать открытой транзакцию во время сетевого запроса нельзя.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.clock import moscow_now
from am_common.db import Database
from am_common.events import Actor, Event
from am_common.outbox import Outbox
from marketdata.db import accrued_interest, face_value, outbox
from marketdata.iss import IssClient
from marketdata.prices import (
    DEFAULT_RULE,
    ISS,
    PRICE_LABELS,
    ManualPrice,
    Rule,
    RuleViolationError,
    moscow_date,
)
from marketdata.refdata import RefdataClient
from marketdata.repository import HISTORY, SETTLEMENT, InstrumentRecord, Repository, RuleRecord

SERVICE = "marketdata"


class Commands:
    def __init__(
        self,
        session: AsyncSession,
        actor: Actor,
        *,
        clock: Callable[[], datetime] = moscow_now,
    ) -> None:
        self._session = session
        self._actor = actor
        self._clock = clock
        self._repo = Repository(session)
        self._outbox = Outbox(outbox)

    async def set_manual_price(
        self, instrument_id: UUID, value: Decimal, comment: str
    ) -> ManualPrice:
        """Ручная цена трейдинга до конца торгового дня (решения 67 и 70)."""
        async with self._session.begin():
            record = await self._repo.get(instrument_id)
            price = ManualPrice(
                value=value, entered_at=self._clock(), author=self._actor.username, comment=comment
            )
            await self._repo.add_manual(instrument_id, price)
            await self._emit(
                "manual_price.set",
                record,
                {
                    "after": {
                        "value": str(value),
                        "valid_through": moscow_date(price.entered_at).isoformat(),
                    }
                },
                comment,
            )
        return price

    async def set_rule(self, instrument_id: UUID, rule: Rule, comment: str) -> RuleRecord:
        """Новое правило оценки действует с момента сохранения (решение 70)."""
        rule.check()
        async with self._session.begin():
            record = await self._repo.get(instrument_id)
            history = (await self._repo.rules([instrument_id])).get(instrument_id, [])
            before = history[-1].rule if history else DEFAULT_RULE
            if rule == before:
                raise RuleViolationError("no_changes", "Правило не изменилось.")
            saved = RuleRecord(
                valid_from=self._clock(), rule=rule, author=self._actor.username, comment=comment
            )
            await self._repo.add_rule(instrument_id, saved)
            await self._emit(
                "price_rule.changed",
                record,
                {"before": rule_view(before), "after": rule_view(rule)},
                comment,
            )
        return saved

    async def _emit(
        self, kind: str, record: InstrumentRecord, data: dict[str, Any], comment: str
    ) -> None:
        await self._outbox.add(
            self._session,
            Event(
                type=kind,
                service=SERVICE,
                actor=self._actor,
                entity_type="instrument",
                entity_id=str(record.id),
                data={"instrument": {"secid": record.secid, "isin": record.isin}, **data},
                comment=comment,
            ),
        )


def rule_view(rule: Rule) -> dict[str, Any]:
    """Правило для журнала: типы цен словами, параметры DEMA, порог разброса."""
    return {
        "price_types": ", ".join(PRICE_LABELS[t] for t in rule.types),
        "dema_timeframe": rule.dema_timeframe,
        "dema_half_life": None if rule.dema_half_life is None else str(rule.dema_half_life),
        "spread_threshold_bp": rule.spread_threshold_bp,
    }


async def load_history(
    database: Database, iss: IssClient, record: InstrumentRecord, since: date, till: date
) -> int:
    """Итоги дней ISS: цены, НКД и номинал на дату торгов. Возвращает число дней."""
    days = await iss.history(record.secid, record.kind, record.board, since, till)
    now = moscow_now()
    async with database.session() as session, session.begin():
        repo = Repository(session)
        await repo.add_quotes(
            [
                {
                    "instrument_id": record.id,
                    "source": ISS,
                    "price_type": kind.value,
                    "observed_at": day.time,
                    "value": value,
                    "received_at": now,
                }
                for day in days
                for kind, value in day.prices.items()
            ]
        )
        for table, attribute in ((accrued_interest, "accrued"), (face_value, "face_value")):
            await repo.upsert_points(
                table,
                [
                    {
                        "instrument_id": record.id,
                        "as_of": day.day,
                        "source": HISTORY,
                        "value": getattr(day, attribute),
                    }
                    for day in days
                    if getattr(day, attribute) is not None
                ],
            )
        await repo.finish_history(record.id, till)
    return len(days)


async def poll_intraday(
    database: Database, iss: IssClient, records: Sequence[InstrumentRecord]
) -> int:
    """Данные дня ISS по всем бумагам: цены и НКД с номиналом на дату расчётов."""
    by_secid = {r.secid: r for r in records}
    received = 0
    for kind in ("bond", "fund_share"):
        wanted = [(r.secid, r.board) for r in records if r.kind == kind]
        if not wanted:
            continue
        snapshots = await iss.snapshots(kind, wanted)
        now = moscow_now()
        async with database.session() as session, session.begin():
            repo = Repository(session)
            await repo.add_quotes(
                [
                    {
                        "instrument_id": by_secid[s.secid].id,
                        "source": ISS,
                        "price_type": price_type.value,
                        "observed_at": s.time,
                        "value": value,
                        "received_at": now,
                    }
                    for s in snapshots
                    if s.time is not None
                    for price_type, value in s.prices.items()
                ]
            )
            for table, attribute in ((accrued_interest, "accrued"), (face_value, "face_value")):
                await repo.upsert_points(
                    table,
                    [
                        {
                            "instrument_id": by_secid[s.secid].id,
                            "as_of": s.settle_date,
                            "source": SETTLEMENT,
                            "value": getattr(s, attribute),
                        }
                        for s in snapshots
                        if s.settle_date is not None and getattr(s, attribute) is not None
                    ],
                )
        received += len(snapshots)
    return received


async def refresh_instruments(database: Database, refdata: RefdataClient) -> int:
    """Обновляет копию списка бумаг из справочника и узнаёт режим торгов у новых бумаг."""
    items = await refdata.instruments()
    async with database.session() as session, session.begin():
        await Repository(session).sync_instruments(items)
    async with database.session() as session:
        missing = [r.id for r in await Repository(session).instruments() if r.board is None]
    for instrument_id in missing:
        board = await refdata.board(instrument_id)
        async with database.session() as session, session.begin():
            await Repository(session).set_board(instrument_id, board)
    return len(items)
