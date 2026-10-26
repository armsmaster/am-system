"""Применение миграций схемы журнала: ``python -m audit.migrate``."""

from __future__ import annotations

from am_common.migrations import upgrade
from audit.settings import AuditSettings


def main() -> None:
    upgrade(AuditSettings(), "audit:migrations")


if __name__ == "__main__":
    main()
