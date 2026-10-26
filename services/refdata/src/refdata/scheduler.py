"""Ежедневная синхронизация справочника с MOEX ISS.

Каждые несколько минут фоновая задача проверяет: наступило ли время
синхронизации и выполнялась ли она сегодня. Запись в таблице ``sync_run``
гарантирует, что при нескольких экземплярах сервиса синхронизацию
выполнит один из них. Зависший запуск (экземпляр упал посередине)
через час заберёт другой экземпляр. Запуск с ошибками (ISS не ответил
по части бумаг) через час повторится целиком: повторная сверка бумаги,
у которой ничего не изменилось, новой версии не создаёт.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from am_common.app import Resources, Worker
from am_common.events import SYSTEM_ACTOR
from refdata.clock import moscow_now
from refdata.commands import sync_all
from refdata.iss import IssClient
from refdata.repository import Repository
from refdata.settings import RefdataSettings

logger = logging.getLogger(__name__)

RETRY_AFTER = timedelta(hours=1)


async def run_daily_sync_if_due(
    resources: Resources, settings: RefdataSettings, now: datetime
) -> bool:
    """Выполняет сегодняшнюю синхронизацию, если пора и её ещё никто не выполнил."""
    if now.time() < settings.sync_at:
        return False
    database = resources.require_database()
    async with database.session() as session, session.begin():
        claimed = await Repository(session).claim_sync_run(now.date(), now, RETRY_AFTER)
    if not claimed:
        return False

    iss = IssClient(resources.http, settings.iss_url, timeout=settings.iss_timeout)
    stats = await sync_all(database, iss, SYSTEM_ACTOR, clock=lambda: now)
    async with database.session() as session, session.begin():
        await Repository(session).finish_sync_run(
            now.date(), moscow_now(), stats.checked, stats.changed, stats.failed
        )
    logger.info(
        "daily ISS sync: checked=%s changed=%s failed=%s",
        stats.checked,
        stats.changed,
        stats.failed,
    )
    return True


def daily_sync_worker(
    settings: RefdataSettings, clock: Callable[[], datetime] = moscow_now
) -> Worker:
    async def daily_iss_sync(resources: Resources) -> None:
        while True:
            try:
                await run_daily_sync_if_due(resources, settings, clock())
            except (SQLAlchemyError, OSError) as exc:
                logger.warning("daily ISS sync failed, will retry: %s", exc)
            await asyncio.sleep(settings.sync_check_seconds)

    return daily_iss_sync
