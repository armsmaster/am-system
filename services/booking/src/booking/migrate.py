"""Применение миграций схемы сделок и позиций: ``python -m booking.migrate``."""

from __future__ import annotations

from am_common.migrations import upgrade
from booking.settings import BookingSettings


def main() -> None:
    upgrade(BookingSettings(), "booking:migrations")


if __name__ == "__main__":
    main()
