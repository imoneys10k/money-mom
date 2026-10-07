"""Errors carry a stable machine-readable `code` so agents can branch on them."""

from __future__ import annotations

from typing import Any


class LedgerError(Exception):
    code = "ledger_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        where: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.where = where
        self.details = details or {}

    def __str__(self) -> str:
        where = f" ({self.where})" if self.where else ""
        return f"[{self.code}] {self.message}{where}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.where:
            out["where"] = self.where
        if self.details:
            out["details"] = self.details
        return out


class ValidationError(LedgerError):
    """An event is malformed: wrong shape, type, or an unknown field."""

    code = "invalid_event"


class RuleError(LedgerError):
    """A well-formed event breaks a ledger rule (unbalanced, locked, ...)."""

    code = "rule_violation"
