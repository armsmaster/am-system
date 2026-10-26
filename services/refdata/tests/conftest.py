from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from am_common.migrations import upgrade
from am_common.testing import TEST_AUDIENCE, TEST_ISSUER, TEST_JWKS_URL, TokenFactory
from refdata.main import build_app
from refdata.settings import RefdataSettings

FIXTURES = Path(__file__).parent / "fixtures" / "iss"
ISS_URL = "http://iss.test/iss"


class FakeIss:
    """Подменный MOEX ISS: отдаёт записанные ответы, их можно подправить в тесте."""

    def __init__(self, tokens: TokenFactory) -> None:
        self.tokens = tokens
        self.down = False
        self.requests: list[str] = []
        # Правки ответов: (вид, код) → функция, меняющая JSON.
        self.patches: dict[tuple[str, str], Callable[[dict[str, Any]], None]] = {}

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def describe(self, code: str, field: str, value: Any) -> None:
        """Подменить поле описания бумаги."""

        def patch(payload: dict[str, Any]) -> None:
            for row in payload["description"]["data"]:
                if row[0] == field:
                    row[2] = value

        self.patches[("desc", code)] = patch

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/protocol/openid-connect/certs"):
            return httpx2.Response(200, json=self.tokens.jwks())
        self.requests.append(path)
        if self.down:
            raise httpx2.ConnectError("ISS is down", request=request)
        if path == "/iss/securities.json":
            return self._search(request.url.params["q"])
        if match := re.fullmatch(r"/iss/securities/([^/]+)\.json", path):
            return self._fixture("desc", match[1])
        if match := re.fullmatch(
            r"/iss/statistics/engines/stock/markets/bonds/bondization/([^/]+)\.json", path
        ):
            return self._fixture("bond", match[1])
        raise AssertionError(f"unexpected ISS request {path}")

    def _search(self, query: str) -> httpx2.Response:
        """Как поиск ISS: строки всех записанных поисков, где код или ISIN содержат запрос."""
        wanted = query.upper()
        found: dict[str, list[Any]] = {}
        columns: list[str] = []
        for file in sorted(FIXTURES.glob("search_*.json")):
            table = json.loads(file.read_text(encoding="utf-8"))["securities"]
            columns = table["columns"]
            for row in table["data"]:
                record = dict(zip(columns, row, strict=True))
                if wanted in record["secid"].upper() or wanted in (record["isin"] or "").upper():
                    found[record["secid"]] = row
        return httpx2.Response(
            200, json={"securities": {"columns": columns, "data": list(found.values())}}
        )

    def _fixture(self, kind: str, code: str) -> httpx2.Response:
        file = FIXTURES / f"{kind}_{code}.json"
        if not file.exists():
            return httpx2.Response(404)
        payload = json.loads(file.read_text(encoding="utf-8"))
        if patch := self.patches.get((kind, code)):
            patch(payload)
        return httpx2.Response(200, json=payload)


@pytest.fixture(scope="session")
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def iss(tokens: TokenFactory) -> FakeIss:
    return FakeIss(tokens)


@pytest.fixture
def bare_settings(postgres_url: str, redis_url: str, schema: str) -> RefdataSettings:
    """Настройки сервиса с отдельной пустой схемой — для проверки миграций."""
    return RefdataSettings(
        auth_issuer=TEST_ISSUER,
        auth_jwks_url=TEST_JWKS_URL,
        auth_audience=TEST_AUDIENCE,
        database_url=SecretStr(postgres_url),
        database_schema=schema,
        redis_url=SecretStr(redis_url),
        iss_url=ISS_URL,
        # Ежедневная синхронизация в тестах API не мешает: проверка раз в час.
        sync_check_seconds=3600,
    )


@pytest.fixture
async def settings(bare_settings: RefdataSettings) -> RefdataSettings:
    """Настройки сервиса с отдельной, уже мигрированной схемой."""
    await asyncio.to_thread(upgrade, bare_settings, "refdata:migrations")
    return bare_settings


@pytest.fixture
def client(settings: RefdataSettings, iss: FakeIss) -> Iterator[TestClient]:
    with TestClient(build_app(settings, http_transport=iss.transport())) as test_client:
        yield test_client
