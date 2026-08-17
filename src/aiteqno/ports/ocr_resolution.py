"""Contracts for one same-runtime OCR input-resolution A/B comparison."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from aiteqno.domain import DocumentIR
from aiteqno.ports.ocr_quality import OcrQualityResult


class OcrResolutionDecision(str, Enum):
    """Outcome of the deliberately narrow 300 DPI OCR-input hypothesis."""

    SUPPORTED = "supported"
    INCONCLUSIVE = "inconclusive"
    REGRESSED = "regressed"
    INVALID = "invalid"


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
    if not isinstance(decoded, dict):  # pragma: no cover - guarded by Mapping above
        raise TypeError(f"{field_name} must be a JSON object")
    return MappingProxyType(decoded)


def _strings(
    values: Sequence[str],
    field_name: str,
    *,
    unique: bool = True,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise TypeError(f"{field_name} must be a sequence of strings")
    result = tuple(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise ValueError(f"{field_name} entries must be non-empty strings")
    if unique and len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrResolutionRun:
    """One completed side of an A/B run and backend-owned transform evidence."""

    quality: OcrQualityResult
    document: DocumentIR
    transform: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.quality, OcrQualityResult):
            raise TypeError("quality must be an OcrQualityResult")
        if not isinstance(self.document, DocumentIR):
            raise TypeError("document must be a DocumentIR")
        object.__setattr__(
            self,
            "transform",
            _json_object(self.transform, "transform"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrResolutionMetricDelta:
    """One percentage-point comparison; confidence and token counts never use it."""

    control: float
    candidate: float
    delta: float

    def __post_init__(self) -> None:
        for field_name in ("control", "candidate", "delta"):
            object.__setattr__(
                self,
                field_name,
                _finite(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, float]:
        return {
            "control": self.control,
            "candidate": self.candidate,
            "delta_percentage_points": self.delta,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrResolutionRecoveryDelta:
    """Stable recovered/gained/lost identities for anchors or logical blocks."""

    control_recovered: tuple[str, ...]
    candidate_recovered: tuple[str, ...]
    gained: tuple[str, ...]
    lost: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "control_recovered",
            "candidate_recovered",
            "gained",
            "lost",
        ):
            object.__setattr__(
                self,
                field_name,
                _strings(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "control_recovered": list(self.control_recovered),
            "candidate_recovered": list(self.candidate_recovered),
            "gained": list(self.gained),
            "lost": list(self.lost),
            "candidate_is_superset": not self.lost,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrResolutionCheck:
    """One automatic comparability or integrity check."""

    name: str
    passed: bool
    reasons: tuple[str, ...]
    details: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("check name must be a non-empty string")
        if not isinstance(self.passed, bool):
            raise TypeError("check passed must be a boolean")
        object.__setattr__(self, "reasons", _strings(self.reasons, "check reasons"))
        object.__setattr__(
            self,
            "details",
            _json_object(self.details, "check details"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "pass" if self.passed else "fail",
            "reasons": list(self.reasons),
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrResolutionComparisonResult:
    """Deterministic evidence and decision for Issue #47's sole hypothesis."""

    evaluator_name: str
    evaluator_version: str
    minimum_text_accuracy_delta: float
    control_quality_state: str
    candidate_quality_state: str
    control_effective_ocr_dpi: int
    candidate_effective_ocr_dpi: int
    control_transform_sha256: str
    candidate_transform_sha256: str
    text_character_accuracy: OcrResolutionMetricDelta
    logical_block_coverage: OcrResolutionMetricDelta
    essential_anchor_recall: OcrResolutionMetricDelta
    anchors: OcrResolutionRecoveryDelta
    blocks: OcrResolutionRecoveryDelta
    control_unrecovered_essential_blocks: tuple[str, ...]
    candidate_unrecovered_essential_blocks: tuple[str, ...]
    checks: tuple[OcrResolutionCheck, ...]
    decision: OcrResolutionDecision
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in ("evaluator_name", "evaluator_version"):
            if not isinstance(getattr(self, field_name), str) or not getattr(
                self, field_name
            ):
                raise ValueError(f"{field_name} must be a non-empty string")
        object.__setattr__(
            self,
            "minimum_text_accuracy_delta",
            _finite(
                self.minimum_text_accuracy_delta,
                "minimum_text_accuracy_delta",
            ),
        )
        if self.minimum_text_accuracy_delta < 0:
            raise ValueError("minimum_text_accuracy_delta must not be negative")
        for field_name in ("control_quality_state", "candidate_quality_state"):
            if getattr(self, field_name) not in {"pass", "fail"}:
                raise ValueError(f"{field_name} must be pass or fail")
        for field_name in (
            "control_effective_ocr_dpi",
            "candidate_effective_ocr_dpi",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        for field_name in (
            "control_transform_sha256",
            "candidate_transform_sha256",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{field_name} must be a lower-case SHA-256 digest")
        for field_name in (
            "text_character_accuracy",
            "logical_block_coverage",
            "essential_anchor_recall",
        ):
            if not isinstance(getattr(self, field_name), OcrResolutionMetricDelta):
                raise TypeError(f"{field_name} must be an OcrResolutionMetricDelta")
        for field_name in ("anchors", "blocks"):
            if not isinstance(getattr(self, field_name), OcrResolutionRecoveryDelta):
                raise TypeError(f"{field_name} must be an OcrResolutionRecoveryDelta")
        for field_name in (
            "control_unrecovered_essential_blocks",
            "candidate_unrecovered_essential_blocks",
            "reasons",
        ):
            object.__setattr__(
                self,
                field_name,
                _strings(getattr(self, field_name), field_name),
            )
        checks = tuple(self.checks)
        if any(not isinstance(check, OcrResolutionCheck) for check in checks):
            raise TypeError("checks must contain only OcrResolutionCheck values")
        if len({check.name for check in checks}) != len(checks):
            raise ValueError("check names must be unique")
        object.__setattr__(self, "checks", checks)
        object.__setattr__(self, "decision", OcrResolutionDecision(self.decision))

    def to_dict(self) -> dict[str, object]:
        checks = {check.name: check.to_dict() for check in self.checks}
        return {
            "schema_version": "1.0",
            "scope": {
                "experiment": "tesseract_ocr_input_resolution",
                "control": "source_resolution",
                "candidate": "300_dpi_working_raster",
                "ends_before": [
                    "docx",
                    "preview",
                    "libreoffice",
                    "poppler",
                    "rendered_page_ocr",
                ],
            },
            "evaluator": {
                "name": self.evaluator_name,
                "version": self.evaluator_version,
            },
            "adoption_policy": {
                "minimum_text_accuracy_delta_percentage_points": (
                    self.minimum_text_accuracy_delta
                ),
                "logical_block_coverage_must_not_decrease": True,
                "essential_anchor_recall_must_not_decrease": True,
                "control_recovered_anchors_and_blocks_must_be_retained": True,
                "unrecovered_essential_blocks_must_not_increase": True,
                "confidence_is_scoring_input": False,
                "token_count_is_scoring_input": False,
            },
            "runs": {
                "control": {
                    "ocr_quality_state": self.control_quality_state,
                    "effective_ocr_dpi": self.control_effective_ocr_dpi,
                    "transform_sha256": self.control_transform_sha256,
                },
                "candidate": {
                    "ocr_quality_state": self.candidate_quality_state,
                    "effective_ocr_dpi": self.candidate_effective_ocr_dpi,
                    "transform_sha256": self.candidate_transform_sha256,
                },
            },
            "metrics": {
                "text_character_accuracy": self.text_character_accuracy.to_dict(),
                "logical_block_coverage": self.logical_block_coverage.to_dict(),
                "essential_anchor_recall": self.essential_anchor_recall.to_dict(),
            },
            "recovery": {
                "anchors": self.anchors.to_dict(),
                "logical_blocks": self.blocks.to_dict(),
                "essential_blocks": {
                    "control_unrecovered": list(
                        self.control_unrecovered_essential_blocks
                    ),
                    "candidate_unrecovered": list(
                        self.candidate_unrecovered_essential_blocks
                    ),
                    "unrecovered_count_delta": (
                        len(self.candidate_unrecovered_essential_blocks)
                        - len(self.control_unrecovered_essential_blocks)
                    ),
                },
            },
            "checks": checks,
            "decision": self.decision.value,
            "reasons": list(self.reasons),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )


__all__ = [
    "OcrResolutionCheck",
    "OcrResolutionComparisonResult",
    "OcrResolutionDecision",
    "OcrResolutionMetricDelta",
    "OcrResolutionRecoveryDelta",
    "OcrResolutionRun",
]
