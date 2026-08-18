"""Contracts for the fixed Japanese-only OCR language-profile experiment."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .ocr_experiment import OcrExperimentComparisonResult, OcrExperimentDecision


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _json_object(value: Any, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a JSON object")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must contain only finite JSON values") from exc
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):  # pragma: no cover - Mapping is guarded above
        raise TypeError(f"{field_name} must be a JSON object")
    return MappingProxyType(decoded)


def _strings(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise TypeError(f"{field_name} must be a sequence of strings")
    result = tuple(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise ValueError(f"{field_name} entries must be non-empty strings")
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrLanguageSmokeRun:
    """One real candidate-profile run over the immutable mixed-language fixture."""

    source_sha256: str
    observed_text: str
    invocation_evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.source_sha256) is None
        ):
            raise ValueError("source_sha256 must be 64 lower-case hex digits")
        if not isinstance(self.observed_text, str):
            raise TypeError("observed_text must be a string")
        object.__setattr__(
            self,
            "invocation_evidence",
            _json_object(self.invocation_evidence, "invocation_evidence"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrProtectedLiteralRecovery:
    """Control-to-candidate recovery state for one reviewed ASCII literal."""

    literal: str
    control_recovered: bool
    candidate_recovered: bool

    def __post_init__(self) -> None:
        if not isinstance(self.literal, str) or not self.literal:
            raise ValueError("literal must be a non-empty string")
        for field_name in ("control_recovered", "candidate_recovered"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be a boolean")

    @property
    def lost(self) -> bool:
        return self.control_recovered and not self.candidate_recovered

    def to_dict(self) -> dict[str, object]:
        return {
            "literal": self.literal,
            "control_recovered": self.control_recovered,
            "candidate_recovered": self.candidate_recovered,
            "lost": self.lost,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrLanguageProfileComparisonResult:
    """Generic comparison plus language-specific no-regression evidence."""

    comparison: OcrExperimentComparisonResult
    protected_literals: tuple[OcrProtectedLiteralRecovery, ...]
    multilingual_smoke: Mapping[str, object]
    decision: OcrExperimentDecision
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.comparison, OcrExperimentComparisonResult):
            raise TypeError("comparison must be an OcrExperimentComparisonResult")
        values = tuple(self.protected_literals)
        if any(not isinstance(item, OcrProtectedLiteralRecovery) for item in values):
            raise TypeError(
                "protected_literals must contain OcrProtectedLiteralRecovery values"
            )
        literals = tuple(item.literal for item in values)
        if len(literals) != len(set(literals)):
            raise ValueError("protected_literals must not contain duplicates")
        object.__setattr__(self, "protected_literals", values)
        object.__setattr__(
            self,
            "multilingual_smoke",
            _json_object(self.multilingual_smoke, "multilingual_smoke"),
        )
        object.__setattr__(self, "decision", OcrExperimentDecision(self.decision))
        reasons = _strings(self.reasons, "reasons")
        if not reasons:
            raise ValueError("reasons must not be empty")
        object.__setattr__(self, "reasons", reasons)

    @property
    def text_character_accuracy(self):
        return self.comparison.text_character_accuracy

    @property
    def logical_block_coverage(self):
        return self.comparison.logical_block_coverage

    @property
    def essential_anchor_recall(self):
        return self.comparison.essential_anchor_recall

    @property
    def anchors(self):
        return self.comparison.anchors

    @property
    def blocks(self):
        return self.comparison.blocks

    @property
    def checks(self):
        return self.comparison.checks

    def to_dict(self) -> dict[str, object]:
        report = self.comparison.to_dict()
        report["adoption_policy"]["protected_literals_must_not_regress"] = True
        report["adoption_policy"]["multilingual_smoke_must_pass"] = True
        report["recovery"]["protected_literals"] = {
            "items": [item.to_dict() for item in self.protected_literals],
            "lost": [item.literal for item in self.protected_literals if item.lost],
        }
        report["multilingual_smoke"] = dict(self.multilingual_smoke)
        report["decision"] = self.decision.value
        report["reasons"] = list(self.reasons)
        return report

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )


__all__ = [
    "OcrLanguageProfileComparisonResult",
    "OcrLanguageSmokeRun",
    "OcrProtectedLiteralRecovery",
]
