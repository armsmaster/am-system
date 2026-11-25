"""Приглашения и последние входы в базе сервиса."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Row, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from identity.db import invitation, last_login
from identity.domain import new_token, token_hash


@dataclass(frozen=True, slots=True)
class Issued:
    token: str
    expires_at: datetime


async def issue_invite(
    session: AsyncSession,
    *,
    user_id: str,
    username: str,
    ttl_days: int,
    created_by: str,
    now: datetime,
) -> Issued:
    """Новая ссылка отменяет прежние: действует только последняя."""
    await session.execute(
        update(invitation)
        .where(
            invitation.c.user_id == user_id,
            invitation.c.used_at.is_(None),
            invitation.c.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    token = new_token()
    expires_at = now + timedelta(days=ttl_days)
    await session.execute(
        insert(invitation).values(
            id=uuid.uuid4(),
            user_id=user_id,
            username=username,
            token_hash=token_hash(token),
            expires_at=expires_at,
            created_by=created_by,
            created_at=now,
        )
    )
    return Issued(token, expires_at)


async def find_invite(
    session: AsyncSession, token: str, *, for_update: bool = False
) -> Row[tuple[object, ...]] | None:
    query = select(invitation).where(invitation.c.token_hash == token_hash(token))
    if for_update:
        query = query.with_for_update()
    return (await session.execute(query)).first()


async def mark_used(session: AsyncSession, invite_id: uuid.UUID, now: datetime) -> None:
    await session.execute(
        update(invitation).where(invitation.c.id == invite_id).values(used_at=now)
    )


async def latest_invites(session: AsyncSession) -> dict[str, Row[tuple[object, ...]]]:
    """Последняя ссылка каждого пользователя — для статуса в списке."""
    newest = (
        select(
            invitation,
            func.row_number()
            .over(partition_by=invitation.c.user_id, order_by=invitation.c.created_at.desc())
            .label("n"),
        )
    ).subquery()
    rows = (await session.execute(select(newest).where(newest.c.n == 1))).all()
    return {str(row.user_id): row for row in rows}


async def record_login(session: AsyncSession, user_id: str, now: datetime) -> None:
    statement = pg_insert(last_login).values(user_id=user_id, at=now)
    await session.execute(
        statement.on_conflict_do_update(index_elements=[last_login.c.user_id], set_={"at": now})
    )


async def last_logins(session: AsyncSession) -> dict[str, datetime]:
    rows = (await session.execute(select(last_login.c.user_id, last_login.c.at))).all()
    return {str(row.user_id): row.at for row in rows}
