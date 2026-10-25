"""Подключение к PostgreSQL.

У каждого сервиса своя схема и своя роль в БД. ``search_path``
выставляется на схему сервиса при каждом соединении, поэтому в SQL и
моделях схема не указывается.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class Database:
    def __init__(self, url: str, schema: str, *, connect_timeout: float = 5.0) -> None:
        connect_args: dict[str, Any] = {
            "timeout": connect_timeout,
            "server_settings": {"search_path": schema},
        }
        self.engine = create_async_engine(url, pool_pre_ping=True, connect_args=connect_args)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session:
            yield session

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()
