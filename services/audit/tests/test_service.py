"""Сервис целиком: события из потока попадают в журнал и видны админу."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
import redis
from fastapi.testclient import TestClient

from am_common.events import Actor, Event
from am_common.testing import TEST_ISSUER, TEST_JWKS_URL, TokenFactory
from audit import main
from audit.settings import AuditSettings

pytestmark = pytest.mark.integration


@pytest.fixture
def client(settings: AuditSettings, tokens: TokenFactory) -> Iterator[TestClient]:
    with TestClient(main.build_app(settings, http_transport=tokens.transport())) as test_client:
        yield test_client


def admin(tokens: TokenFactory) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens.issue(roles=['admin'])}"}


def publish(settings: AuditSettings, event: Event) -> None:
    assert settings.redis_url is not None
    connection = redis.Redis.from_url(settings.redis_url.get_secret_value())
    connection.xadd(settings.streams[0], {"event": event.model_dump_json()})
    connection.close()


def wait_for_events(
    client: TestClient, tokens: TokenFactory, count: int
) -> list[dict[str, object]]:
    for _ in range(100):
        items: list[dict[str, object]] = client.get("/events", headers=admin(tokens)).json()[
            "items"
        ]
        if len(items) >= count:
            return items
        time.sleep(0.05)
    raise AssertionError("events did not reach the journal")


def make_event(n: int) -> Event:
    return Event(
        service="portfolio",
        type="portfolio.created",
        actor=Actor(subject="u-1", username="ivanov"),
        entity_type="portfolio",
        entity_id=str(n),
        data={"after": {"code": f"P{n}"}},
    )


def test_events_from_the_stream_reach_the_journal(
    client: TestClient, settings: AuditSettings, tokens: TokenFactory
) -> None:
    first, second = make_event(1), make_event(2)
    publish(settings, first)
    publish(settings, second)

    items = wait_for_events(client, tokens, 2)

    assert [item["entity_id"] for item in items] == ["2", "1"]
    assert items[0]["data"] == {"after": {"code": "P2"}}


def test_pages_and_filters(
    client: TestClient, settings: AuditSettings, tokens: TokenFactory
) -> None:
    for n in range(3):
        publish(settings, make_event(n))
    wait_for_events(client, tokens, 3)

    page = client.get("/events", params={"limit": 2}, headers=admin(tokens)).json()
    rest = client.get(
        "/events", params={"limit": 2, "before": page["next_before"]}, headers=admin(tokens)
    ).json()
    filtered = client.get(
        "/events", params={"entity_id": "1", "type": "portfolio.created"}, headers=admin(tokens)
    ).json()

    assert [item["entity_id"] for item in page["items"]] == ["2", "1"]
    assert [item["entity_id"] for item in rest["items"]] == ["0"]
    assert rest["next_before"] is None
    assert [item["entity_id"] for item in filtered["items"]] == ["1"]


@pytest.mark.parametrize("roles", [["trading"], ["operations"], []])
def test_only_admin_reads_the_journal(
    client: TestClient, tokens: TokenFactory, roles: list[str]
) -> None:
    headers = {"Authorization": f"Bearer {tokens.issue(roles=roles)}"}

    assert client.get("/events", headers=headers).status_code == 403


def test_serve_reads_environment_and_configures_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AM_AUTH_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AM_AUTH_JWKS_URL", TEST_JWKS_URL)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(main, "configure_logging", lambda *args: calls.append(args))

    app = main.serve()

    assert app.title == "Audit service"
    assert calls == [("audit", "INFO")]
