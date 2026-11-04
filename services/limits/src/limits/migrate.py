"""Применение миграций схемы лимитов: ``python -m limits.migrate``."""

from __future__ import annotations

from am_common.migrations import upgrade
from limits.settings import LimitsSettings


def main() -> None:
    upgrade(LimitsSettings(), "limits:migrations")


if __name__ == "__main__":
    main()
