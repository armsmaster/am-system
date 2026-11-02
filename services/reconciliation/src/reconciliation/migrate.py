"""Применение миграций схемы сверки: ``python -m reconciliation.migrate``."""

from __future__ import annotations

from am_common.migrations import upgrade
from reconciliation.settings import ReconciliationSettings


def main() -> None:
    upgrade(ReconciliationSettings(), "reconciliation:migrations")


if __name__ == "__main__":
    main()
