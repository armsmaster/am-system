from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from am_common.logs import JsonFormatter, configure_logging

_UVICORN = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_root = (root.handlers[:], root.level)
    saved_uvicorn = {
        name: (logging.getLogger(name).handlers[:], logging.getLogger(name).propagate)
        for name in _UVICORN
    }
    yield
    root.handlers[:], root.level = saved_root
    for name, (handlers, propagate) in saved_uvicorn.items():
        logging.getLogger(name).handlers[:] = handlers
        logging.getLogger(name).propagate = propagate


def make_record(exc_info: Any = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="booking.roll",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="пересчитан портфель %s",
        args=("ОФЗ-1",),
        exc_info=exc_info,
    )


def test_record_becomes_one_json_line() -> None:
    line = JsonFormatter("booking").format(make_record())

    payload = json.loads(line)
    assert payload["service"] == "booking"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "booking.roll"
    assert payload["message"] == "пересчитан портфель ОФЗ-1"
    assert payload["ts"].endswith("+00:00")
    assert "exception" not in payload


def test_exception_is_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = make_record(exc_info=sys.exc_info())

    payload = json.loads(JsonFormatter("booking").format(record))

    assert "ValueError: boom" in payload["exception"]


@pytest.mark.usefixtures("restore_logging")
def test_uvicorn_logs_go_through_the_json_handler(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("portfolio", "debug")
    logging.getLogger("uvicorn.access").info("GET /health 200")

    last_line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(last_line)
    assert payload["logger"] == "uvicorn.access"
    assert payload["service"] == "portfolio"
    assert logging.getLogger().level == logging.DEBUG
