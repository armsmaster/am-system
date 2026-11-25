"""Таблицы сервиса: приглашения, время последнего входа, outbox.

Пользователей и роли не храним — они в Keycloak.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, MetaData, Table, Text, Uuid, func

from am_common.outbox import outbox_table

metadata = MetaData()

# Ссылка-приглашение: задать пароль и войти. Сам секрет не храним — только SHA-256.
invitation = Table(
    "invitation",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Text, nullable=False),
    Column("username", Text, nullable=False),
    Column("token_hash", Text, nullable=False, unique=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("used_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
)
Index("invitation_user", invitation.c.user_id, invitation.c.created_at)

# Последний вход по паролю или по ссылке (обновление токена — не вход).
last_login = Table(
    "last_login",
    metadata,
    Column("user_id", Text, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False),
)

outbox = outbox_table(metadata)
