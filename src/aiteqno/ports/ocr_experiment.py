"""Contracts for one fixed, same-runtime OCR hypothesis comparison."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from aiteqno.domain import DocumentIR
from aiteqno.ports.ocr_quality import OcrQualityResult


OCR_EXPERIMENT_RUNTIME_FIELDS = (
    "provider",
    "provider_version",
    "executable",
    "languages",
    "page_segmentation_mode",
    "engine_mode",
    "source_dpi_x",
    "source_dpi_y",
    "effective_ocr_dpi",
    "traineddata",
    "operating_system",
    "python_version",
)
OCR_EXPERIMENT_ALLOWED_RUNTIME_DIFFERENCES = (
    "languages",
    "page_segmentation_mode",
    "engine_mode",
    "effective_ocr_dpi",
    "traineddata",
)
OCR_EXPERIMENT_ALLOWED_GEOMETRY_DIFFERENCES = ("region_plan",)


class OcrExperimentDecision(str, Enum):
    """Decision produced by a fixed OCR experiment contract."""

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
    if not isinstance(decoded, dict):  # pragma: no cover - Mapping is guarded above
        raise TypeError(f"{field_name} must be a JSON object")
    return MappingProxyType(decoded)


def _strings(
    values: Sequence[str],
    field_name: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise TypeError(f"{field_name} must be a sequence of strings")
    result = tuple(values)
    if not allow_empty and not result:
        raise ValueError(f"{field_name} must not be empty")
    if any(not isinstance(value, str) or not value for value in result):
        raise ValueError(f"{field_name} entries must be non-empty strings")
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


def _non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrExperimentContract:
    """Immutable scope and allowed runtime drift for one OCR hypothesis."""

    experiment_id: str
    control_label: str
    candidate_label: str
    evaluator_name: str
    evaluator_version: str
    required_hypothesis_checks: tuple[str, ...]
    allowed_runtime_differences: tuple[str, ...] = ()
    allowed_geometry_differences: tuple[str, ...] = ()
    supported_reason: str = "all_ocr_experiment_adoption_conditions_pass"
    ends_before: tuple[str, ...] = (
        "docx",
        "preview",
        "libreoffice",
        "poppler",
        "rendered_page_ocr",
    )

    def __post_init__(self) -> None:
        for field_name in (
            "experiment_id",
            "control_label",
            "candidate_label",
            "evaluator_name",
            "evaluator_version",
            "supported_reason",
        ):
            object.__setattr__(
                self,
                field_name,
                _non_empty_string(getattr(self, field_name), field_name),
            )
        if self.control_label == self.candidate_label:
            raise ValueError("control_label and candidate_label must differ")
        required = _strings(
            self.required_hypothesis_checks,
            "required_hypothesis_checks",
            allow_empty=False,
        )
        allowed = _strings(
            self.allowed_runtime_differences,
            "allowed_runtime_differences",
        )
        unknown = tuple(
            value
            for value in allowed
            if value not in OCR_EXPERIMENT_ALLOWED_RUNTIME_DIFFERENCES
        )
        if unknown:
            raise ValueError(
                "allowed_runtime_differences contains unknown fields: "
                + ", ".join(unknown)
            )
        object.__setattr__(self, "required_hypothesis_checks", required)
        object.__setattr__(self, "allowed_runtime_differences", allowed)
        allowed_geometry = _strings(
            self.allowed_geometry_differences,
            "allowed_geometry_differences",
        )
        unknown_geometry = tuple(
            value
            for value in allowed_geometry
            if value not in OCR_EXPERIMENT_ALLOWED_GEOMETRY_DIFFERENCES
        )
        if unknown_geometry:
            raise ValueError(
                "allowed_geometry_differences contains unknown fields: "
                + ", ".join(unknown_geometry)
            )
        object.__setattr__(
            self,
            "allowed_geometry_differences",
            allowed_geometry,
        )
        object.__setattr__(
            self,
            "ends_before",
            _strings(self.ends_before, "ends_before", allow_empty=False),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrExperimentRun:
    """One completed side of a comparison plus backend-owned evidence."""

    quality: OcrQualityResult
    document: DocumentIR
    evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.quality, OcrQualityResult):
            raise TypeError("quality must be an OcrQualityResult")
        if not isinstance(self.document, DocumentIR):
            raise TypeError("document must be a DocumentIR")
        object.__setattr__(self, "evidence", _json_object(self.evidence, "evidence"))


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrExperimentMetricDelta:
    """One percentage-point comparison; diagnostics never use it."""

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
class OcrExperimentRecoveryDelta:
    """Stable recovered, gained, and lost anchor or block identities."""

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
class OcrExperimentCheck:
    """One automatic hypothesis, comparability, or integrity check."""

    name: str
    passed: bool
    reasons: tuple[str, ...]
    details: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _non_empty_string(self.name, "check name"))
        if not isinstance(self.passed, bool):
            raise TypeError("check passed must be a boolean")
        object.__setattr__(
            self,
            "reasons",
            _strings(self.reasons, "check reasons", allow_empty=False),
        )
        object.__setattr__(self, "details", _json_object(self.details, "check details"))

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "pass" if self.passed else "fail",
            "reasons": list(self.reasons),
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrExperimentComparisonResult:
    """Deterministic evidence and decision for one fixed OCR hypothesis."""

    contract: OcrExperimentContract
    minimum_text_accuracy_delta: float
    control_quality_state: str
    candidate_quality_state: str
    control_effective_ocr_dpi: int
    candidate_effective_ocr_dpi: int
    control_evidence_sha256: str
    candidate_evidence_sha256: str
    text_character_accuracy: OcrExperimentMetricDelta
    logical_block_coverage: OcrExperimentMetricDelta
    essential_anchor_recall: OcrExperimentMetricDelta
    anchors: OcrExperimentRecoveryDelta
    blocks: OcrExperimentRecoveryDelta
    control_unrecovered_essential_blocks: tuple[str, ...]
    candidate_unrecovered_essential_blocks: tuple[str, ...]
    checks: tuple[OcrExperimentCheck, ...]
    decision: OcrExperimentDecision
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.contract, OcrExperimentContract):
            raise TypeError("contract must be an OcrExperimentContract")
        object.__setattr__(
            self,
            "minimum_text_accuracy_delta",
            _finite(self.minimum_text_accuracy_delta, "minimum_text_accuracy_delta"),
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
            "control_evidence_sha256",
            "candidate_evidence_sha256",
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
            if not isinstance(getattr(self, field_name), OcrExperimentMetricDelta):
                raise TypeError(f"{field_name} must be an OcrExperimentMetricDelta")
        for field_name in ("anchors", "blocks"):
            if not isinstance(getattr(self, field_name), OcrExperimentRecoveryDelta):
                raise TypeError(f"{field_name} must be an OcrExperimentRecoveryDelta")
        for field_name in (
            "control_unrecovered_essential_blocks",
            "candidate_unrecovered_essential_blocks",
        ):
            object.__setattr__(
                self,
                field_name,
                _strings(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "reasons",
            _strings(self.reasons, "reasons", allow_empty=False),
        )
        checks = tuple(self.checks)
        if any(not isinstance(check, OcrExperimentCheck) for check in checks):
            raise TypeError("checks must contain only OcrExperimentCheck values")
        if len({check.name for check in checks}) != len(checks):
            raise ValueError("check names must be unique")
        object.__setattr__(self, "checks", checks)
        object.__setattr__(self, "decision", OcrExperimentDecision(self.decision))

    def to_dict(self) -> dict[str, object]:
        checks = {check.name: check.to_dict() for check in self.checks}
        return {
            "schema_version": "1.0",
            "scope": {
                "experiment": self.contract.experiment_id,
                "control": self.contract.control_label,
                "candidate": self.contract.candidate_label,
                "ends_before": list(self.contract.ends_before),
            },
            "evaluator": {
                "name": self.contract.evaluator_name,
                "version": self.contract.evaluator_version,
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
                "allowed_runtime_differences": list(
                    self.contract.allowed_runtime_differences
                ),
                "allowed_geometry_differences": list(
                    self.contract.allowed_geometry_differences
                ),
                "required_hypothesis_checks": list(
                    self.contract.required_hypothesis_checks
                ),
            },
            "runs": {
                "control": {
                    "ocr_quality_state": self.control_quality_state,
                    "effective_ocr_dpi": self.control_effective_ocr_dpi,
                    "evidence_sha256": self.control_evidence_sha256,
                },
                "candidate": {
                    "ocr_quality_state": self.candidate_quality_state,
                    "effective_ocr_dpi": self.candidate_effective_ocr_dpi,
                    "evidence_sha256": self.candidate_evidence_sha256,
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
    "OCR_EXPERIMENT_ALLOWED_GEOMETRY_DIFFERENCES",
    "OCR_EXPERIMENT_ALLOWED_RUNTIME_DIFFERENCES",
    "OCR_EXPERIMENT_RUNTIME_FIELDS",
    "OcrExperimentCheck",
    "OcrExperimentComparisonResult",
    "OcrExperimentContract",
    "OcrExperimentDecision",
    "OcrExperimentMetricDelta",
    "OcrExperimentRecoveryDelta",
    "OcrExperimentRun",
]
