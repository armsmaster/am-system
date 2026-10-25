from __future__ import annotations

import time
from typing import Any

import httpx2
import jwt
import pytest

from am_common.auth import (
    AuthError,
    JwksProvider,
    Principal,
    Role,
    TokenVerifier,
    principal_from_claims,
)
from am_common.testing import TEST_AUDIENCE, TEST_ISSUER, TEST_JWKS_URL, TokenFactory


@pytest.fixture
def verifier(tokens: TokenFactory) -> TokenVerifier:
    client = httpx2.AsyncClient(transport=tokens.transport())
    return TokenVerifier(
        JwksProvider(client, TEST_JWKS_URL), issuer=TEST_ISSUER, audience=TEST_AUDIENCE
    )


async def test_valid_token_gives_principal_with_known_roles(
    tokens: TokenFactory, verifier: TokenVerifier
) -> None:
    token = tokens.issue(
        subject="42", username="ivanov", roles=["trading", "offline_access", "uma_authorization"]
    )

    principal = await verifier.verify(token)

    assert principal == Principal(subject="42", username="ivanov", roles=frozenset({Role.TRADING}))


async def test_audience_may_be_a_list(tokens: TokenFactory, verifier: TokenVerifier) -> None:
    token = tokens.issue(aud=["account", TEST_AUDIENCE])

    assert (await verifier.verify(token)).subject == "user-1"


async def test_expired_token_is_rejected(tokens: TokenFactory, verifier: TokenVerifier) -> None:
    # Просрочен дольше допуска на расхождение часов (30 с).
    token = tokens.issue(expires_in=-31)

    with pytest.raises(AuthError, match="expired"):
        await verifier.verify(token)


async def test_slightly_expired_token_is_accepted_within_leeway(
    tokens: TokenFactory, verifier: TokenVerifier
) -> None:
    token = tokens.issue(expires_in=-10)

    assert (await verifier.verify(token)).subject == "user-1"


@pytest.mark.parametrize(
    "claims",
    [{"iss": "http://evil.test/realms/am"}, {"aud": "some-other-client"}],
    ids=["foreign issuer", "foreign audience"],
)
async def test_token_issued_for_someone_else_is_rejected(
    tokens: TokenFactory, verifier: TokenVerifier, claims: dict[str, Any]
) -> None:
    with pytest.raises(AuthError):
        await verifier.verify(tokens.issue(**claims))


async def test_token_signed_by_another_key_is_rejected(verifier: TokenVerifier) -> None:
    impostor = TokenFactory()  # тот же kid, другой ключ

    with pytest.raises(AuthError, match="Signature"):
        await verifier.verify(impostor.issue())


async def test_symmetric_algorithm_is_rejected(verifier: TokenVerifier) -> None:
    now = int(time.time())
    token = jwt.encode(
        {"iss": TEST_ISSUER, "aud": TEST_AUDIENCE, "sub": "x", "iat": now, "exp": now + 60},
        "a-shared-secret-that-is-long-enough-for-hs256",
        algorithm="HS256",
        headers={"kid": "test-key"},
    )

    with pytest.raises(AuthError):
        await verifier.verify(token)


async def test_token_without_subject_is_rejected(
    tokens: TokenFactory, verifier: TokenVerifier
) -> None:
    claims = jwt.decode(tokens.issue(), options={"verify_signature": False})
    del claims["sub"]

    with pytest.raises(AuthError, match="sub"):
        await verifier.verify(tokens.sign(claims))


async def test_garbage_is_rejected(verifier: TokenVerifier) -> None:
    with pytest.raises(AuthError, match="malformed"):
        await verifier.verify("not-a-jwt")


async def test_token_without_key_id_is_rejected(
    tokens: TokenFactory, verifier: TokenVerifier
) -> None:
    claims = jwt.decode(tokens.issue(), options={"verify_signature": False})

    with pytest.raises(AuthError, match="key id"):
        await verifier.verify(tokens.sign(claims, with_kid=False))


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "1"},
        {"sub": "1", "realm_access": "admin"},
        {"sub": "1", "realm_access": {"roles": "admin"}},
        {"sub": "1", "realm_access": {"roles": [1, None]}},
    ],
    ids=["no realm_access", "realm_access not a dict", "roles not a list", "roles not strings"],
)
def test_malformed_roles_give_no_roles(claims: dict[str, object]) -> None:
    assert principal_from_claims(claims).roles == frozenset()


def test_username_falls_back_to_subject() -> None:
    assert principal_from_claims({"sub": "abc"}).username == "abc"


@pytest.mark.parametrize(
    ("roles", "required", "allowed"),
    [
        ({Role.TRADING}, Role.TRADING, True),
        ({Role.TRADING}, Role.OPERATIONS, False),
        ({Role.ADMIN}, Role.TRADING, True),
        ({Role.ADMIN}, Role.OPERATIONS, True),
        (set(), Role.TRADING, False),
    ],
)
def test_admin_has_every_role(roles: set[Role], required: Role, allowed: bool) -> None:
    principal = Principal(subject="1", username="u", roles=frozenset(roles))

    assert principal.has_role(required) is allowed
