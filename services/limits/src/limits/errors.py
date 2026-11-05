"""Ошибки сервиса лимитов: нарушено правило (422) и не найдено (404)."""

from __future__ import annotations


class RuleViolationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotFoundError(Exception):
    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what
