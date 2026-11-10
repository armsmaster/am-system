"""Тарифы брокера на счёт (решение 151): ставки комиссии для загрузки из отчёта QUIK.

Купля-продажа — % от оборота без НКД, РЕПО — % годовых от суммы первой части.
Тариф задаёт администратор с датой начала; прежние остаются в истории. Тариф
на день — последний с датой начала не позже дня. Уже загруженные сделки новый
тариф не меняет: комиссия записана в сделку при загрузке.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from am_common.auth import Principal
from am_common.events import Actor, Event
from am_common.outbox import Outbox
from booking.clients import PortfolioClient
from booking.db import outbox
from booking.repository import Repository
from booking.trades import SERVICE, RuleViolationError


class TariffDesk:
    def __init__(
        self,
        session: AsyncSession,
        principal: Principal,
        authorization: str,
        portfolios: PortfolioClient,
        clock: Callable[[], datetime],
    ) -> None:
        self._session = session
        self._actor = Actor.from_principal(principal)
        self._authorization = authorization
        self._portfolios = portfolios
        self._clock = clock
        self._repo = Repository(session)
        self._outbox = Outbox(outbox)

    async def history(self, account_id: UUID) -> list[Row[Any]]:
        """Тарифы счёта по дате начала — вся история."""
        await self._account(account_id)
        async with self._session.begin():
            return await self._repo.tariffs(account_id)

    async def set(
        self,
        account_id: UUID,
        *,
        outright_rate: Decimal,
        repo_rate: Decimal,
        valid_from: date,
        comment: str | None,
    ) -> list[Row[Any]]:
        """Новый тариф с даты начала."""
        portfolios = await self._account(account_id)
        async with self._session.begin():
            await self._repo.add_tariff(
                {
                    "account_id": account_id,
                    "outright_rate": outright_rate,
                    "repo_rate": repo_rate,
                    "valid_from": valid_from,
                    "comment": comment,
                    "author": self._actor.username,
                    "created_at": self._clock(),
                }
            )
            await self._outbox.add(
                self._session,
                Event(
                    service=SERVICE,
                    type="broker_tariff.set",
                    actor=self._actor,
                    entity_type="account",
                    entity_id=str(account_id),
                    data={
                        "portfolios": portfolios,
                        # Не repo_rate: так в журнале называется ставка самой сделки РЕПО.
                        "after": {
                            "outright_fee": str(outright_rate),
                            "repo_fee": str(repo_rate),
                            "valid_from": valid_from.isoformat(),
                        },
                    },
                    comment=comment,
                ),
            )
            return await self._repo.tariffs(account_id)

    async def _account(self, account_id: UUID) -> list[str]:
        """Счёт виден, если виден хотя бы один его портфель; коды этих портфелей."""
        visible = await self._portfolios.visible(self._authorization)
        codes = sorted(p.code for p in visible.values() if p.account_id == account_id)
        if not codes:
            raise RuleViolationError(
                "account_not_found", "Счёта нет или к его портфелям нет доступа.", 404
            )
        return codes
