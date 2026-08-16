"""Actionable errors raised while parsing and validating Document IR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One validation problem at a JSON-style path."""

    path: str
    message: str
    code: str = "invalid"

    def __str__(self) -> str:
        return f"{self.path}: {self.message} [{self.code}]"


class DocumentIRValidationError(ValueError):
    """Raised when Document IR violates its structural or semantic contract."""

    def __init__(self, issues: Iterable[ValidationIssue]) -> None:
        collected = tuple(issues)
        if not collected:
            raise ValueError("DocumentIRValidationError requires at least one issue")

        self.issues = collected
        details = "\n".join(f"- {issue}" for issue in collected)
        super().__init__(f"Document IR validation failed:\n{details}")

    @classmethod
    def single(
        cls,
        path: str,
        message: str,
        code: str = "invalid",
    ) -> DocumentIRValidationError:
        """Build an exception for a single validation problem."""

        return cls((ValidationIssue(path=path, message=message, code=code),))
