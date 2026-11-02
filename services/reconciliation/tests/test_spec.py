"""Спецификация приёмника выписок (docs/api/statement-feed): схема и пример — как в коде."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from am_common.testing import TokenFactory

SPEC = Path(__file__).parents[3] / "docs" / "api" / "statement-feed"
PATH = "/feed/v1/statements"


def load(name: str) -> Any:
    return json.loads((SPEC / name).read_text(encoding="utf-8"))


def validator() -> Draft202012Validator:
    schema = load("statements.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def test_example_matches_the_schema() -> None:
    assert list(validator().iter_errors(load("statements.example.json"))) == []


@pytest.mark.parametrize(
    "broken",
    [
        {"kind": "settlement", "amount": -1},
        {"kind": "coupon", "amount": 1},
        {"kind": "transfer", "trade": "1", "amount": 1},
        {"kind": "transfer", "amount": 0},
    ],
)
def test_schema_checks_movement_keys(broken: dict[str, Any]) -> None:
    example = copy.deepcopy(load("statements.example.json"))
    example["statements"][0]["movements"] = [broken]
    assert list(validator().iter_errors(example)) != []


def test_schema_checks_repo_fields() -> None:
    example = copy.deepcopy(load("statements.example.json"))
    trade = example["statements"][1]["trades"][0]
    trade["kind"] = "repo"
    assert list(validator().iter_errors(example)) != []
    trade |= {"repo_rate": 14.5, "second_date": "2026-11-30", "second_amount": 28033871.92}
    assert list(validator().iter_errors(example)) == []
    del trade["kind"]
    assert list(validator().iter_errors(example)) != []


@pytest.mark.integration
def test_example_goes_through_the_api(client: TestClient, tokens: TokenFactory) -> None:
    token = tokens.issue(username="service-account-am-statement-feed", roles=["statement_feed"])
    headers = {"Authorization": f"Bearer {token}"}

    found = client.post(PATH, json=load("statements.example.json"), headers=headers).json()

    assert (found["created"], found["rejected"]) == (6, [])
