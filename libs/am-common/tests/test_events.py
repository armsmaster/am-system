from __future__ import annotations

from datetime import UTC

from am_common.auth import Principal, Role
from am_common.events import Actor, Event, stream_name


def test_actor_comes_from_the_authenticated_user() -> None:
    principal = Principal(subject="u-1", username="ivanov", roles=frozenset({Role.ADMIN}))

    assert Actor.from_principal(principal) == Actor(subject="u-1", username="ivanov")


def test_event_gets_unique_id_and_utc_time() -> None:
    def make() -> Event:
        actor = Actor(subject="u-1", username="ivanov")
        return Event(
            service="portfolio",
            type="portfolio.created",
            actor=actor,
            entity_type="portfolio",
            entity_id="1",
        )

    first, second = make(), make()

    assert first.event_id != second.event_id
    assert first.occurred_at.tzinfo is UTC
    assert first.data == {}


def test_event_survives_json_round_trip() -> None:
    event = Event(
        service="portfolio",
        type="portfolio.renamed",
        actor=Actor(subject="u-1", username="ivanov"),
        entity_type="portfolio",
        entity_id="42",
        data={"before": {"name": "Старое"}, "after": {"name": "Новое"}},
        comment="опечатка",
    )

    assert Event.model_validate_json(event.model_dump_json()) == event


def test_each_service_publishes_to_its_own_stream() -> None:
    assert stream_name("portfolio") == "am.events.portfolio"
