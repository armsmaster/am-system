"""Применение миграций схемы справочника: ``python -m refdata.migrate``."""

from __future__ import annotations

from am_common.migrations import upgrade
from refdata.settings import RefdataSettings


def main() -> None:
    upgrade(RefdataSettings(), "refdata:migrations")


if __name__ == "__main__":
    main()
