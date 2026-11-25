"""Правила: пароль, ссылка-приглашение, статус пользователя."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Literal

from identity.keycloak import IdentityError

# Роли людей; служебные (price_feed, close_day…) — у скриптов и сервисов, здесь их не дают.
HUMAN_ROLES = ("trading", "operations", "risk", "admin")
HumanRole = Literal["trading", "operations", "risk", "admin"]

# Срок ссылки-приглашения, дней.
InviteTtl = Literal[1, 3, 7]

MIN_PASSWORD_LENGTH = 10

UserStatus = Literal["active", "pending", "expired", "blocked"]


def check_password(password: str, username: str) -> None:
    """Те же правила, что в политике паролей Keycloak: не короче 10 символов, не логин."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise IdentityError(
            422, "weak_password", f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов."
        )
    if password.casefold() == username.casefold():
        raise IdentityError(422, "weak_password", "Пароль не должен совпадать с логином.")


def new_token() -> str:
    """Секрет ссылки: 32 случайных байта. В базе — только его хэш."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_invite(
    *,
    expires_at: datetime,
    used_at: datetime | None,
    revoked_at: datetime | None,
    now: datetime,
) -> None:
    """Ссылкой можно задать пароль один раз, пока она не истекла и не заменена новой."""
    if used_at is not None:
        raise IdentityError(
            410,
            "invite_used",
            "По этой ссылке пароль уже задан. Войдите с ним или попросите у администратора "
            "новую ссылку.",
        )
    if revoked_at is not None:
        raise IdentityError(
            410,
            "invite_revoked",
            "Ссылка заменена новой. Попросите у администратора последнюю ссылку.",
        )
    if expires_at <= now:
        raise IdentityError(
            410,
            "invite_expired",
            "Срок ссылки истёк. Попросите у администратора новую.",
        )


def user_status(
    *,
    enabled: bool,
    open_invite_expires_at: datetime | None,
    has_logged_in: bool,
    now: datetime,
) -> UserStatus:
    """Заблокирован; ждёт первого входа по ссылке; ссылка истекла; активен.

    ``open_invite_expires_at`` — срок последней ссылки, если она не использована
    и не заменена новой.
    """
    if not enabled:
        return "blocked"
    if has_logged_in or open_invite_expires_at is None:
        return "active"
    return "pending" if open_invite_expires_at > now else "expired"
