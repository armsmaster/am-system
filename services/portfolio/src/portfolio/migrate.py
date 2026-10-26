"""Применение миграций схемы портфелей: ``python -m portfolio.migrate``."""

from __future__ import annotations

from am_common.migrations import upgrade
from portfolio.settings import PortfolioSettings


def main() -> None:
    upgrade(PortfolioSettings(), "portfolio:migrations")


if __name__ == "__main__":
    main()
