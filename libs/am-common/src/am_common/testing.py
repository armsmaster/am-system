"""Помощники для тестов сервисов: токены «как от Keycloak» без Keycloak.

Используется только в тестах. ``TokenFactory`` держит пару RSA-ключей,
отдаёт JWKS через подменный HTTP-транспорт и выпускает подписанные токены
с нужными ролями.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

TEST_ISSUER = "http://keycloak.test/auth/realms/am"
TEST_JWKS_URL = f"{TEST_ISSUER}/protocol/openid-connect/certs"
TEST_AUDIENCE = "am-api"


class TokenFactory:
    def __init__(
        self, *, issuer: str = TEST_ISSUER, audience: str = TEST_AUDIENCE, kid: str = "test-key"
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.kid = kid
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(self) -> dict[str, Any]:
        jwk = RSAAlgorithm.to_jwk(self._private_key.public_key(), as_dict=True)
        jwk.update(kid=self.kid, use="sig", alg="RS256")
        return {"keys": [jwk]}

    def issue(
        self,
        *,
        subject: str = "user-1",
        username: str = "user",
        roles: Iterable[str] = (),
        expires_in: int = 300,
        **claims: Any,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": subject,
            "preferred_username": username,
            "iat": now,
            "exp": now + expires_in,
            "realm_access": {"roles": list(roles)},
        }
        payload.update(claims)
        return self.sign(payload)

    def sign(self, payload: dict[str, Any], *, with_kid: bool = True) -> str:
        """Подписывает произвольный набор claims — для проверки кривых токенов."""
        headers = {"kid": self.kid} if with_kid else None
        return jwt.encode(payload, self._private_key, algorithm="RS256", headers=headers)

    def transport(self) -> httpx2.MockTransport:
        """HTTP-транспорт, который на любой запрос отдаёт JWKS этой фабрики."""
        return httpx2.MockTransport(lambda _request: httpx2.Response(200, json=self.jwks()))
