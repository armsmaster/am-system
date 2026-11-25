from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from identity.domain import check_invite, check_password, new_token, token_hash, user_status
from identity.keycloak import IdentityError

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("password", "username", "message"),
    [
        ("short", "ivan", "не короче 10"),
        ("Ivan.Petrov", "ivan.petrov", "совпадать с логином"),
    ],
)
def test_password_rules(password: str, username: str, message: str) -> None:
    with pytest.raises(IdentityError) as error:
        check_password(password, username)

    assert error.value.code == "weak_password"
    assert message in error.value.message


def test_good_password_passes() -> None:
    check_password("long-enough-1", "ivan")


def test_token_is_random_and_stored_as_hash() -> None:
    token = new_token()

    assert len(token) >= 43
    assert token != new_token()
    assert token_hash(token) == token_hash(token)
    assert len(token_hash(token)) == 64
    assert token not in token_hash(token)


@pytest.mark.parametrize(
    ("used", "revoked", "expires", "error"),
    [
        (NOW, None, NOW + timedelta(days=1), "invite_used"),
        (None, NOW, NOW + timedelta(days=1), "invite_revoked"),
        (None, None, NOW, "invite_expired"),
    ],
)
def test_closed_invites(
    used: datetime | None, revoked: datetime | None, expires: datetime, error: str
) -> None:
    with pytest.raises(IdentityError) as raised:
        check_invite(expires_at=expires, used_at=used, revoked_at=revoked, now=NOW)

    assert raised.value.status == 410
    assert raised.value.code == error


def test_open_invite_passes() -> None:
    check_invite(expires_at=NOW + timedelta(seconds=1), used_at=None, revoked_at=None, now=NOW)


@pytest.mark.parametrize(
    ("enabled", "expires", "logged_in", "status"),
    [
        (False, NOW + timedelta(days=1), False, "blocked"),
        (True, NOW + timedelta(days=1), False, "pending"),
        (True, NOW - timedelta(days=1), False, "expired"),
        (True, NOW + timedelta(days=1), True, "active"),
        (True, None, False, "active"),
    ],
)
def test_user_status(enabled: bool, expires: datetime | None, logged_in: bool, status: str) -> None:
    assert (
        user_status(
            enabled=enabled, open_invite_expires_at=expires, has_logged_in=logged_in, now=NOW
        )
        == status
    )
