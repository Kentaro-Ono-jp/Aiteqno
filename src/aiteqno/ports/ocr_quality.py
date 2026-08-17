"""Contracts for source-grounded OCR-only quality evaluation.

The report ends at candidate Document IR.  It deliberately contains no DOCX,
LibreOffice, Poppler, preview, or rendered-page evidence.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Sequence

from aiteqno.domain import DocumentIR
from aiteqno.ports.evaluation import EvaluationState, HardGateResult


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _non_empty(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be 64 lower-case hex digits")
    return value


def _integer(
    value: Any,
    field_name: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field_name} must be an integer of at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be at most {maximum}")
    return value


def _number(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    if (exclusive_minimum and result <= minimum) or (
        not exclusive_minimum and result < minimum
    ):
        comparator = "greater than" if exclusive_minimum else "at least"
        raise ValueError(f"{field_name} must be {comparator} {minimum:g}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{field_name} must be at most {maximum:g}")
    return result


def _strings(
    values: Sequence[str],
    field_name: str,
    *,
    non_empty: bool = True,
    unique: bool = False,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise TypeError(f"{field_name} must be a sequence of strings")
    result = tuple(values)
    for value in result:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} entries must be strings")
        if non_empty:
            _non_empty(value, f"{field_name} entry")
    if unique and len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrTrainedDataEvidence:
    """Digest evidence for one language model used by the OCR runtime."""

    language: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        _non_empty(self.language, "traineddata language")
        _integer(self.size_bytes, "traineddata size_bytes", minimum=1)
        object.__setattr__(
            self,
            "sha256",
            _sha256(self.sha256, "traineddata sha256"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "language": self.language,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrRuntimeEvidence:
    """Runtime evidence embedded directly in every OCR quality report."""

    provider: str
    provider_version: str
    executable: str
    languages: tuple[str, ...]
    page_segmentation_mode: int
    engine_mode: int
    effective_ocr_dpi: int
    source_dpi_x: float
    source_dpi_y: float
    traineddata: tuple[OcrTrainedDataEvidence, ...]
    operating_system: str
    python_version: str

    def __post_init__(self) -> None:
        _non_empty(self.provider, "OCR runtime provider")
        _non_empty(self.provider_version, "OCR runtime provider_version")
        _non_empty(self.executable, "OCR runtime executable")
        languages = _strings(
            self.languages,
            "OCR runtime languages",
            unique=True,
        )
        if not languages:
            raise ValueError("OCR runtime languages must not be empty")
        object.__setattr__(self, "languages", languages)
        _integer(
            self.page_segmentation_mode,
            "OCR runtime page_segmentation_mode",
            minimum=0,
            maximum=13,
        )
        _integer(
            self.engine_mode,
            "OCR runtime engine_mode",
            minimum=0,
            maximum=3,
        )
        _integer(
            self.effective_ocr_dpi,
            "OCR runtime effective_ocr_dpi",
            minimum=1,
        )
        for field_name in ("source_dpi_x", "source_dpi_y"):
            object.__setattr__(
                self,
                field_name,
                _number(
                    getattr(self, field_name),
                    f"OCR runtime {field_name}",
                    minimum=0,
                    exclusive_minimum=True,
                ),
            )
        traineddata = tuple(self.traineddata)
        if any(not isinstance(item, OcrTrainedDataEvidence) for item in traineddata):
            raise TypeError("OCR runtime traineddata contains an invalid value")
        if tuple(item.language for item in traineddata) != languages:
            raise ValueError(
                "OCR runtime traineddata must match languages in the same order"
            )
        object.__setattr__(self, "traineddata", traineddata)
        _non_empty(self.operating_system, "OCR runtime operating_system")
        _non_empty(self.python_version, "OCR runtime python_version")

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "provider_version": self.provider_version,
            "configuration": {
                "languages": list(self.languages),
                "page_segmentation_mode": self.page_segmentation_mode,
                "engine_mode": self.engine_mode,
                "effective_ocr_dpi": self.effective_ocr_dpi,
                "source_metadata_dpi": {
                    "x": self.source_dpi_x,
                    "y": self.source_dpi_y,
                },
            },
            "traineddata": [item.to_dict() for item in self.traineddata],
            "platform": {
                "operating_system": self.operating_system,
                "python_version": self.python_version,
            },
            # Executable paths vary between hosts and are evidence only, never a
            # quality input.  Keeping them under diagnostics makes that explicit.
            "diagnostics": {"executable": self.executable},
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrQualityObservation:
    """Candidate IR plus the exact source/runtime evidence that produced it."""

    source_sha256: str
    candidate_ir: DocumentIR
    runtime: OcrRuntimeEvidence

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_sha256",
            _sha256(self.source_sha256, "OCR observation source_sha256"),
        )
        if not isinstance(self.candidate_ir, DocumentIR):
            raise TypeError("OCR observation candidate_ir must be a DocumentIR")
        if not isinstance(self.runtime, OcrRuntimeEvidence):
            raise TypeError("OCR observation runtime must be OcrRuntimeEvidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrMetricEvaluation:
    """One independently enforced OCR quality metric."""

    score: float
    minimum: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "score",
            _number(self.score, "OCR metric score", minimum=0, maximum=100),
        )
        object.__setattr__(
            self,
            "minimum",
            _number(self.minimum, "OCR metric minimum", minimum=0, maximum=100),
        )

    @property
    def passed(self) -> bool:
        return self.score >= self.minimum

    def to_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "minimum": self.minimum,
            "status": "pass" if self.passed else "fail",
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrBlockEvaluation:
    """Text reconstructed inside one reviewed source region."""

    reference_id: str
    expected_text: str
    observed_text: str
    candidate_element_ids: tuple[str, ...]
    character_accuracy: float
    recovered: bool
    essential: bool

    def __post_init__(self) -> None:
        _non_empty(self.reference_id, "OCR block reference_id")
        _non_empty(self.expected_text, "OCR block expected_text")
        if not isinstance(self.observed_text, str):
            raise TypeError("OCR block observed_text must be a string")
        object.__setattr__(
            self,
            "candidate_element_ids",
            _strings(
                self.candidate_element_ids,
                "OCR block candidate_element_ids",
                unique=True,
            ),
        )
        object.__setattr__(
            self,
            "character_accuracy",
            _number(
                self.character_accuracy,
                "OCR block character_accuracy",
                minimum=0,
                maximum=100,
            ),
        )
        if not isinstance(self.recovered, bool):
            raise TypeError("OCR block recovered must be a boolean")
        if not isinstance(self.essential, bool):
            raise TypeError("OCR block essential must be a boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_id": self.reference_id,
            "expected_text": self.expected_text,
            "observed_text": self.observed_text,
            "candidate_element_ids": list(self.candidate_element_ids),
            "character_accuracy": self.character_accuracy,
            "recovered": self.recovered,
            "essential": self.essential,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrAnchorEvaluation:
    """Exact normalized-substring evidence for one essential phrase."""

    anchor: str
    recovered: bool

    def __post_init__(self) -> None:
        _non_empty(self.anchor, "OCR anchor")
        if not isinstance(self.recovered, bool):
            raise TypeError("OCR anchor recovered must be a boolean")

    def to_dict(self) -> dict[str, object]:
        return {"anchor": self.anchor, "recovered": self.recovered}


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrConfidenceDistribution:
    """Non-scoring summary of confidence values attached to candidate tokens."""

    token_count: int
    available_count: int
    missing_count: int
    minimum: float | None
    p10: float | None
    median: float | None
    mean: float | None
    p90: float | None
    maximum: float | None

    def __post_init__(self) -> None:
        for field_name in ("token_count", "available_count", "missing_count"):
            _integer(getattr(self, field_name), field_name, minimum=0)
        if self.available_count + self.missing_count != self.token_count:
            raise ValueError("confidence counts must add up to token_count")
        statistics = (
            self.minimum,
            self.p10,
            self.median,
            self.mean,
            self.p90,
            self.maximum,
        )
        if self.available_count == 0 and any(value is not None for value in statistics):
            raise ValueError("confidence statistics require available values")
        if self.available_count > 0 and any(value is None for value in statistics):
            raise ValueError("confidence statistics are required for available values")
        for field_name in ("minimum", "p10", "median", "mean", "p90", "maximum"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _number(
                        value,
                        f"confidence {field_name}",
                        minimum=0,
                        maximum=1,
                    ),
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "role": "diagnostic_only",
            "token_count": self.token_count,
            "available_count": self.available_count,
            "missing_count": self.missing_count,
            "minimum": self.minimum,
            "p10": self.p10,
            "median": self.median,
            "mean": self.mean,
            "p90": self.p90,
            "maximum": self.maximum,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class LowConfidenceTokenDiagnostic:
    """A candidate token selected only for troubleshooting, never scoring."""

    candidate_element_id: str
    page_number: int
    reading_order: int
    text: str
    confidence: float
    confidence_source: str

    def __post_init__(self) -> None:
        _non_empty(self.candidate_element_id, "low-confidence candidate_element_id")
        _integer(self.page_number, "low-confidence page_number", minimum=1)
        _integer(self.reading_order, "low-confidence reading_order", minimum=0)
        if not isinstance(self.text, str):
            raise TypeError("low-confidence text must be a string")
        object.__setattr__(
            self,
            "confidence",
            _number(
                self.confidence,
                "low-confidence confidence",
                minimum=0,
                maximum=1,
            ),
        )
        if self.confidence_source != "recognition":
            raise ValueError("confidence_source must be recognition")

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_element_id": self.candidate_element_id,
            "page_number": self.page_number,
            "reading_order": self.reading_order,
            "text": self.text,
            "confidence": self.confidence,
            "confidence_source": self.confidence_source,
            "role": "diagnostic_only",
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrQualityResult:
    """Deterministic, OCR-only scores, gates, runtime, and diagnostics."""

    evaluator_name: str
    evaluator_version: str
    reference_id: str
    reference_source_sha256: str
    observed_source_sha256: str
    runtime: OcrRuntimeEvidence
    expected_text: str
    observed_text: str
    text_character_accuracy: OcrMetricEvaluation
    logical_block_coverage: OcrMetricEvaluation
    essential_anchor_recall: OcrMetricEvaluation
    block_recovery_accuracy_minimum: float
    low_confidence_threshold: float
    blocks: tuple[OcrBlockEvaluation, ...]
    anchors: tuple[OcrAnchorEvaluation, ...]
    missing_strings: tuple[str, ...]
    extra_strings: tuple[str, ...]
    unrecovered_blocks: tuple[str, ...]
    confidence_distribution: OcrConfidenceDistribution
    low_confidence_tokens: tuple[LowConfidenceTokenDiagnostic, ...]
    hard_gates: tuple[HardGateResult, ...]
    state: EvaluationState
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _non_empty(self.evaluator_name, "OCR result evaluator_name")
        _non_empty(self.evaluator_version, "OCR result evaluator_version")
        _non_empty(self.reference_id, "OCR result reference_id")
        for field_name in ("reference_source_sha256", "observed_source_sha256"):
            object.__setattr__(
                self,
                field_name,
                _sha256(getattr(self, field_name), f"OCR result {field_name}"),
            )
        if not isinstance(self.runtime, OcrRuntimeEvidence):
            raise TypeError("OCR result runtime must be OcrRuntimeEvidence")
        for field_name in ("expected_text", "observed_text"):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"OCR result {field_name} must be a string")
        for field_name in (
            "text_character_accuracy",
            "logical_block_coverage",
            "essential_anchor_recall",
        ):
            if not isinstance(getattr(self, field_name), OcrMetricEvaluation):
                raise TypeError(f"OCR result {field_name} must be OcrMetricEvaluation")
        for field_name in (
            "block_recovery_accuracy_minimum",
            "low_confidence_threshold",
        ):
            object.__setattr__(
                self,
                field_name,
                _number(
                    getattr(self, field_name),
                    f"OCR result {field_name}",
                    minimum=0,
                    maximum=100 if field_name.startswith("block_") else 1,
                ),
            )
        typed_sequences = (
            ("blocks", OcrBlockEvaluation),
            ("anchors", OcrAnchorEvaluation),
            ("low_confidence_tokens", LowConfidenceTokenDiagnostic),
            ("hard_gates", HardGateResult),
        )
        for field_name, item_type in typed_sequences:
            values = tuple(getattr(self, field_name))
            if any(not isinstance(item, item_type) for item in values):
                raise TypeError(f"OCR result {field_name} contains an invalid value")
            object.__setattr__(self, field_name, values)
        for field_name, unique in (
            ("missing_strings", False),
            ("extra_strings", False),
            ("unrecovered_blocks", True),
            ("reasons", True),
        ):
            object.__setattr__(
                self,
                field_name,
                _strings(getattr(self, field_name), field_name, unique=unique),
            )
        if not isinstance(self.confidence_distribution, OcrConfidenceDistribution):
            raise TypeError(
                "OCR result confidence_distribution must be OcrConfidenceDistribution"
            )
        object.__setattr__(self, "state", EvaluationState(self.state))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "scope": {
                "text_source": "candidate_ir",
                "normalization": (
                    "NFKC then remove every Unicode whitespace character"
                ),
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
            "reference_id": self.reference_id,
            "source_digest": {
                "reference": self.reference_source_sha256,
                "observed": self.observed_source_sha256,
            },
            "runtime": self.runtime.to_dict(),
            "thresholds": {
                "text_character_accuracy": self.text_character_accuracy.minimum,
                "logical_block_coverage": self.logical_block_coverage.minimum,
                "essential_anchor_recall": self.essential_anchor_recall.minimum,
                "block_recovery_accuracy": self.block_recovery_accuracy_minimum,
                "low_confidence_diagnostic": self.low_confidence_threshold,
            },
            "scores": {
                "text_character_accuracy": self.text_character_accuracy.to_dict(),
                "logical_block_coverage": self.logical_block_coverage.to_dict(),
                "essential_anchor_recall": self.essential_anchor_recall.to_dict(),
            },
            "full_text": {
                "expected": self.expected_text,
                "observed": self.observed_text,
            },
            "blocks": [item.to_dict() for item in self.blocks],
            "essential_anchors": [item.to_dict() for item in self.anchors],
            "diagnostics": {
                "missing_strings": list(self.missing_strings),
                "extra_strings": list(self.extra_strings),
                "unrecovered_blocks": list(self.unrecovered_blocks),
                "confidence_distribution": self.confidence_distribution.to_dict(),
                "low_confidence_tokens": [
                    item.to_dict() for item in self.low_confidence_tokens
                ],
                "confidence_is_scoring_input": False,
            },
            "hard_gates": [item.to_dict() for item in self.hard_gates],
            "state": self.state.value,
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
    "LowConfidenceTokenDiagnostic",
    "OcrAnchorEvaluation",
    "OcrBlockEvaluation",
    "OcrConfidenceDistribution",
    "OcrMetricEvaluation",
    "OcrQualityObservation",
    "OcrQualityResult",
    "OcrRuntimeEvidence",
    "OcrTrainedDataEvidence",
]
