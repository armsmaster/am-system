"""Применение миграций схемы сервиса входа: ``python -m identity.migrate``."""

from __future__ import annotations

from am_common.migrations import upgrade
from identity.settings import IdentitySettings


def main() -> None:
    upgrade(IdentitySettings(), "identity:migrations")


if __name__ == "__main__":
    main()
