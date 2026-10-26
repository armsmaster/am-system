"""События об изменениях — общий формат для outbox, шины и журнала.

Событие описывает один факт: кто, когда и что изменил. В ``data`` обычно
лежат состояния «было» и «стало» — ровно то, что попадёт в журнал.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from am_common.auth import Principal


class Actor(BaseModel):
    """Кто совершил действие: пользователь или сама система."""

    model_config = ConfigDict(frozen=True)

    subject: str
    username: str

    @classmethod
    def from_principal(cls, principal: Principal) -> Actor:
        return cls(subject=principal.subject, username=principal.username)


SYSTEM_ACTOR = Actor(subject="system", username="system")


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    service: str
    # Тип в виде «сущность.действие», например portfolio.created.
    type: str
    actor: Actor
    entity_type: str
    entity_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = None


def stream_name(service: str) -> str:
    """Поток Redis, в который сервис публикует свои события."""
    return f"am.events.{service}"
