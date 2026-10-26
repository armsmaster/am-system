from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from am_common.settings import ServiceSettings
from am_common.testing import TEST_AUDIENCE, TEST_ISSUER, TEST_JWKS_URL, TokenFactory

type SettingsFactory = Callable[..., ServiceSettings]


@pytest.fixture(scope="session")
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def make_settings() -> SettingsFactory:
    def factory(**overrides: Any) -> ServiceSettings:
        return ServiceSettings(
            service_name="test",
            auth_issuer=TEST_ISSUER,
            auth_jwks_url=TEST_JWKS_URL,
            auth_audience=TEST_AUDIENCE,
            **overrides,
        )

    return factory
