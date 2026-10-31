"""Спецификация для скрипта (docs/api/price-feed): примеры проходят схемы, схемы ловят ошибки.

Что примеры принимает сам сервис, проверяет test_service.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

SPEC = Path(__file__).parents[3] / "docs" / "api" / "price-feed"


def load(name: str) -> Any:
    return json.loads((SPEC / name).read_text(encoding="utf-8"))


def validator(kind: str) -> Draft202012Validator:
    schema = load(f"{kind}.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


@pytest.mark.parametrize("kind", ["quotes", "bars", "rates"])
def test_examples_match_their_schemas(kind: str) -> None:
    assert list(validator(kind).iter_errors(load(f"{kind}.example.json"))) == []


@pytest.mark.parametrize(
    ("kind", "batch"),
    [
        (
            "quotes",
            {
                "source": "QUIK",
                "quotes": [
                    {"code": "SU26238RMFS4", "time": "2026-10-27T11:05:00+03:00", "vwap": 51.8}
                ],
            },
        ),
        (
            "quotes",
            {
                "source": "quik",
                "quotes": [{"code": "SU26238RMFS4", "time": "2026-10-27T11:05:00+03:00"}],
            },
        ),
        (
            "quotes",
            {
                "source": "quik",
                "quotes": [{"code": "SU26238RMFS4", "time": "2026-10-27T11:05:00", "vwap": 51.8}],
            },
        ),
        (
            "quotes",
            {
                "source": "quik",
                "quotes": [
                    {"code": "SU26238RMFS4", "time": "2026-10-27T11:05:00+03:00", "vwap": 0}
                ],
            },
        ),
        (
            "bars",
            {
                "source": "quik",
                "bars": [
                    {
                        "code": "RU000A10F504",
                        "timeframe": "5h",
                        "start": "2026-10-27T10:00:00+03:00",
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                    }
                ],
            },
        ),
        (
            "bars",
            {
                "source": "quik",
                "bars": [
                    {
                        "code": "RU000A10F504",
                        "timeframe": "10m",
                        "start": "2026-10-27T10:00:00+03:00",
                        "open": 1,
                        "high": 1,
                        "low": 1,
                    }
                ],
            },
        ),
        (
            "rates",
            {"source": "cbr", "rates": [{"rate": "libor", "date": "2026-10-26", "value": 5}]},
        ),
        (
            "rates",
            {"source": "cbr", "rates": [{"rate": "ruonia", "date": "26.10.2026", "value": 13.9}]},
        ),
        (
            "rates",
            {
                "source": "cbr",
                "rates": [{"rate": "ruonia", "date": "2026-10-26", "value": 0.139e3}],
            },
        ),
    ],
)
def test_schemas_catch_broken_batches(kind: str, batch: dict[str, Any]) -> None:
    assert list(validator(kind).iter_errors(batch)) != []
