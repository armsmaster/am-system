"""Применение миграций схемы рыночных данных: ``python -m marketdata.migrate``."""

from __future__ import annotations

from am_common.migrations import upgrade
from marketdata.settings import MarketdataSettings


def main() -> None:
    upgrade(MarketdataSettings(), "marketdata:migrations")


if __name__ == "__main__":
    main()
