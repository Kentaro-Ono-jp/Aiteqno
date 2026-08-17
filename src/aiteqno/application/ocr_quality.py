"""ID- and token-segmentation-independent OCR-only quality evaluation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Sequence

from aiteqno.application.baseline import (
    normalize_source_text,
    source_character_accuracy,
)
from aiteqno.domain import DocumentIR, TextElement, validate_document
from aiteqno.ports.baseline import SourceBaselineReference
from aiteqno.ports.evaluation import (
    EvaluationState,
    HardGateResult,
    NormalizedBoundingBox,
)
from aiteqno.ports.ocr_quality import (
    LowConfidenceTokenDiagnostic,
    OcrAnchorEvaluation,
    OcrBlockEvaluation,
    OcrConfidenceDistribution,
    OcrMetricEvaluation,
    OcrQualityObservation,
    OcrQualityResult,
)


OCR_QUALITY_EVALUATOR_NAME: Final = "aiteqno-ocr-quality-evaluator"
OCR_QUALITY_EVALUATOR_VERSION: Final = "1.0"
DEFAULT_MINIMUM_TEXT_ACCURACY: Final = 70.0
DEFAULT_MINIMUM_LOGICAL_BLOCK_COVERAGE: Final = 60.0
DEFAULT_REQUIRED_ANCHOR_RECALL: Final = 100.0
DEFAULT_LOGICAL_BLOCK_ACCURACY_THRESHOLD: Final = 60.0
DEFAULT_LOW_CONFIDENCE_THRESHOLD: Final = 0.5


def _percentage(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 100:
        raise ValueError(f"{field_name} must be finite and between 0 and 100")
    return result


def _unit_interval(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrQualityConfig:
    """Independent minima; no weighted overall score can hide text failure."""

    minimum_text_accuracy: float = DEFAULT_MINIMUM_TEXT_ACCURACY
    minimum_logical_block_coverage: float = DEFAULT_MINIMUM_LOGICAL_BLOCK_COVERAGE
    required_anchor_recall: float = DEFAULT_REQUIRED_ANCHOR_RECALL
    logical_block_accuracy_threshold: float = DEFAULT_LOGICAL_BLOCK_ACCURACY_THRESHOLD
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD

    def __post_init__(self) -> None:
        for field_name in (
            "minimum_text_accuracy",
            "minimum_logical_block_coverage",
            "required_anchor_recall",
            "logical_block_accuracy_threshold",
        ):
            object.__setattr__(
                self,
                field_name,
                _percentage(getattr(self, field_name), f"OCR quality {field_name}"),
            )
        object.__setattr__(
            self,
            "low_confidence_threshold",
            _unit_interval(
                self.low_confidence_threshold,
                "OCR quality low_confidence_threshold",
            ),
        )


@dataclass(frozen=True, slots=True)
class _CandidateToken:
    element: TextElement
    page_number: int
    bbox: NormalizedBoundingBox

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.page_number, self.element.reading_order)


def normalize_ocr_text(value: str) -> str:
    """Apply NFKC and remove every Unicode whitespace character."""

    if not isinstance(value, str):
        raise TypeError("OCR quality text must be a string")
    return normalize_source_text(value)


def ocr_character_accuracy(expected: str, observed: str) -> float:
    """Return normalized edit-distance accuracy on a 0..100 scale."""

    return source_character_accuracy(expected, observed)


def evaluate_ocr_quality(
    reference: SourceBaselineReference,
    observation: OcrQualityObservation,
    *,
    config: OcrQualityConfig = OcrQualityConfig(),
) -> OcrQualityResult:
    """Evaluate source PNG to candidate IR text, and nothing downstream."""

    if not isinstance(reference, SourceBaselineReference):
        raise TypeError("reference must be a SourceBaselineReference")
    if not isinstance(observation, OcrQualityObservation):
        raise TypeError("observation must be an OcrQualityObservation")
    if not isinstance(config, OcrQualityConfig):
        raise TypeError("config must be an OcrQualityConfig")
    validate_document(observation.candidate_ir)

    candidates = _candidate_tokens(observation.candidate_ir)
    expected_text = normalize_ocr_text(
        "".join(region.text for region in reference.text_regions)
    )
    observed_text = normalize_ocr_text(
        "".join(candidate.element.text for candidate in candidates)
    )
    text_accuracy = OcrMetricEvaluation(
        score=ocr_character_accuracy(expected_text, observed_text),
        minimum=config.minimum_text_accuracy,
    )

    blocks = _evaluate_blocks(reference, candidates, config=config)
    block_coverage = OcrMetricEvaluation(
        score=round(
            100 * sum(block.recovered for block in blocks) / len(blocks),
            6,
        ),
        minimum=config.minimum_logical_block_coverage,
    )
    anchors = tuple(
        OcrAnchorEvaluation(
            anchor=anchor,
            recovered=normalize_ocr_text(anchor) in observed_text,
        )
        for anchor in reference.essential_text_anchors
    )
    anchor_recall = OcrMetricEvaluation(
        score=(
            round(100 * sum(anchor.recovered for anchor in anchors) / len(anchors), 6)
            if anchors
            else 100.0
        ),
        minimum=config.required_anchor_recall,
    )

    missing_strings, extra_strings = _text_differences(expected_text, observed_text)
    unrecovered_blocks = tuple(
        block.reference_id for block in blocks if not block.recovered
    )
    confidence_distribution, low_confidence_tokens = _confidence_diagnostics(
        candidates,
        threshold=config.low_confidence_threshold,
    )

    missing_essential_blocks = tuple(
        block.reference_id
        for block in blocks
        if block.essential and not block.recovered
    )
    missing_anchors = tuple(anchor.anchor for anchor in anchors if not anchor.recovered)
    digest_matches = observation.source_sha256 == reference.source_sha256
    page_count_matches = (
        len(observation.candidate_ir.pages) == reference.expected_page_count
    )
    hard_gates = (
        HardGateResult(
            name="source_digest_matches_reference",
            passed=digest_matches,
            reason=(
                "candidate IR was produced from the reviewed source digest"
                if digest_matches
                else "candidate source digest differs from the reviewed reference"
            ),
        ),
        HardGateResult(
            name="reference_reviewed",
            passed=reference.reviewed,
            reason=(
                "source reference is marked human-reviewed"
                if reference.reviewed
                else "source reference is not marked human-reviewed"
            ),
        ),
        HardGateResult(
            name="candidate_page_count",
            passed=page_count_matches,
            reason=(
                "candidate page count matches the reviewed source"
                if page_count_matches
                else "candidate page count differs from the reviewed source"
            ),
        ),
        HardGateResult(
            name="essential_anchor_recall",
            passed=not missing_anchors,
            reason=(
                "all required essential anchors are exact normalized substrings"
                if not missing_anchors
                else "missing essential anchors: " + ", ".join(missing_anchors)
            ),
        ),
        HardGateResult(
            name="essential_logical_blocks",
            passed=not missing_essential_blocks,
            reason=(
                "all essential logical blocks meet the recovery threshold"
                if not missing_essential_blocks
                else "unrecovered essential logical blocks: "
                + ", ".join(missing_essential_blocks)
            ),
        ),
    )

    metrics = (
        ("text_character_accuracy", text_accuracy),
        ("logical_block_coverage", block_coverage),
        ("essential_anchor_recall", anchor_recall),
    )
    reasons = [
        f"metric_below_minimum:{name}:{metric.score:g}<{metric.minimum:g}"
        for name, metric in metrics
        if not metric.passed
    ]
    reasons.extend(
        f"hard_gate_failed:{gate.name}" for gate in hard_gates if gate.passed is False
    )
    if reasons:
        state = EvaluationState.FAIL
    else:
        state = EvaluationState.PASS
        reasons.append("all_ocr_metric_minima_and_hard_gates_pass")

    return OcrQualityResult(
        evaluator_name=OCR_QUALITY_EVALUATOR_NAME,
        evaluator_version=OCR_QUALITY_EVALUATOR_VERSION,
        reference_id=reference.reference_id,
        reference_source_sha256=reference.source_sha256,
        observed_source_sha256=observation.source_sha256,
        runtime=observation.runtime,
        expected_text=expected_text,
        observed_text=observed_text,
        text_character_accuracy=text_accuracy,
        logical_block_coverage=block_coverage,
        essential_anchor_recall=anchor_recall,
        block_recovery_accuracy_minimum=(config.logical_block_accuracy_threshold),
        low_confidence_threshold=config.low_confidence_threshold,
        blocks=blocks,
        anchors=anchors,
        missing_strings=missing_strings,
        extra_strings=extra_strings,
        unrecovered_blocks=unrecovered_blocks,
        confidence_distribution=confidence_distribution,
        low_confidence_tokens=low_confidence_tokens,
        hard_gates=hard_gates,
        state=state,
        reasons=tuple(reasons),
    )


def _candidate_tokens(document: DocumentIR) -> tuple[_CandidateToken, ...]:
    candidates = [
        _CandidateToken(
            element=element,
            page_number=page.number,
            bbox=_normalized_bbox(
                element.bbox.x,
                element.bbox.y,
                element.bbox.width,
                element.bbox.height,
                page.size.width,
                page.size.height,
            ),
        )
        for page in document.pages
        for element in page.elements
        if isinstance(element, TextElement)
    ]
    candidates.sort(key=lambda item: item.sort_key)
    return tuple(candidates)


def _evaluate_blocks(
    reference: SourceBaselineReference,
    candidates: Sequence[_CandidateToken],
    *,
    config: OcrQualityConfig,
) -> tuple[OcrBlockEvaluation, ...]:
    regions = tuple(reference.text_regions)
    region_indexes = {region.id: index for index, region in enumerate(regions)}
    assigned: dict[str, list[_CandidateToken]] = {region.id: [] for region in regions}
    for candidate in candidates:
        center = (
            candidate.bbox.x + candidate.bbox.width / 2,
            candidate.bbox.y + candidate.bbox.height / 2,
        )
        containing = [
            region
            for region in regions
            if region.page_number == candidate.page_number
            and _contains(region.bbox, center)
        ]
        if not containing:
            continue
        # A token belongs to one logical region only.  The smallest containing
        # source region wins; reviewed order and ID make overlap ties stable.
        owner = min(
            containing,
            key=lambda region: (
                region.bbox.width * region.bbox.height,
                region_indexes[region.id],
            ),
        )
        assigned[owner.id].append(candidate)

    results: list[OcrBlockEvaluation] = []
    for region in regions:
        tokens = sorted(assigned[region.id], key=lambda item: item.sort_key)
        expected_text = normalize_ocr_text(region.text)
        observed_text = normalize_ocr_text(
            "".join(token.element.text for token in tokens)
        )
        accuracy = ocr_character_accuracy(expected_text, observed_text)
        results.append(
            OcrBlockEvaluation(
                reference_id=region.id,
                expected_text=expected_text,
                observed_text=observed_text,
                candidate_element_ids=tuple(token.element.id for token in tokens),
                character_accuracy=accuracy,
                recovered=bool(observed_text)
                and accuracy >= config.logical_block_accuracy_threshold,
                essential=region.essential,
            )
        )
    return tuple(results)


def _confidence_diagnostics(
    candidates: Sequence[_CandidateToken],
    *,
    threshold: float,
) -> tuple[
    OcrConfidenceDistribution,
    tuple[LowConfidenceTokenDiagnostic, ...],
]:
    values: list[float] = []
    low_tokens: list[LowConfidenceTokenDiagnostic] = []
    for candidate in candidates:
        confidence = candidate.element.confidence
        if confidence is None or confidence.recognition is None:
            continue
        value = confidence.recognition
        values.append(value)
        if value < threshold:
            low_tokens.append(
                LowConfidenceTokenDiagnostic(
                    candidate_element_id=candidate.element.id,
                    page_number=candidate.page_number,
                    reading_order=candidate.element.reading_order,
                    text=candidate.element.text,
                    confidence=value,
                    confidence_source="recognition",
                )
            )

    sorted_values = sorted(values)
    if sorted_values:
        statistics: tuple[float | None, ...] = (
            round(sorted_values[0], 6),
            _percentile(sorted_values, 0.10),
            _percentile(sorted_values, 0.50),
            round(sum(sorted_values) / len(sorted_values), 6),
            _percentile(sorted_values, 0.90),
            round(sorted_values[-1], 6),
        )
    else:
        statistics = (None, None, None, None, None, None)
    distribution = OcrConfidenceDistribution(
        token_count=len(candidates),
        available_count=len(sorted_values),
        missing_count=len(candidates) - len(sorted_values),
        minimum=statistics[0],
        p10=statistics[1],
        median=statistics[2],
        mean=statistics[3],
        p90=statistics[4],
        maximum=statistics[5],
    )
    return distribution, tuple(low_tokens)


def _text_differences(
    expected: str,
    observed: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return deterministic edit runs from the same Levenshtein model as scoring."""

    missing: list[str] = []
    extra: list[str] = []
    rows = [[0] * (len(observed) + 1) for _ in range(len(expected) + 1)]
    for index in range(len(expected) + 1):
        rows[index][0] = index
    for index in range(len(observed) + 1):
        rows[0][index] = index
    for expected_index, expected_character in enumerate(expected, start=1):
        for observed_index, observed_character in enumerate(observed, start=1):
            rows[expected_index][observed_index] = min(
                rows[expected_index - 1][observed_index] + 1,
                rows[expected_index][observed_index - 1] + 1,
                rows[expected_index - 1][observed_index - 1]
                + (expected_character != observed_character),
            )

    edits: list[tuple[str, str, str]] = []
    expected_index = len(expected)
    observed_index = len(observed)
    while expected_index or observed_index:
        if (
            expected_index
            and observed_index
            and expected[expected_index - 1] == observed[observed_index - 1]
            and rows[expected_index][observed_index]
            == rows[expected_index - 1][observed_index - 1]
        ):
            edits.append(
                (
                    "equal",
                    expected[expected_index - 1],
                    observed[observed_index - 1],
                )
            )
            expected_index -= 1
            observed_index -= 1
        elif (
            expected_index
            and observed_index
            and rows[expected_index][observed_index]
            == rows[expected_index - 1][observed_index - 1] + 1
        ):
            edits.append(
                ("edit", expected[expected_index - 1], observed[observed_index - 1])
            )
            expected_index -= 1
            observed_index -= 1
        elif (
            expected_index
            and rows[expected_index][observed_index]
            == rows[expected_index - 1][observed_index] + 1
        ):
            edits.append(("edit", expected[expected_index - 1], ""))
            expected_index -= 1
        else:
            edits.append(("edit", "", observed[observed_index - 1]))
            observed_index -= 1

    missing_run: list[str] = []
    extra_run: list[str] = []

    def flush() -> None:
        if missing_run:
            missing.append("".join(missing_run))
            missing_run.clear()
        if extra_run:
            extra.append("".join(extra_run))
            extra_run.clear()

    for operation, expected_character, observed_character in reversed(edits):
        if operation == "equal":
            flush()
        else:
            if expected_character:
                missing_run.append(expected_character)
            if observed_character:
                extra_run.append(observed_character)
    flush()
    return tuple(missing), tuple(extra)


def _normalized_bbox(
    x: float,
    y: float,
    width: float,
    height: float,
    page_width: float,
    page_height: float,
) -> NormalizedBoundingBox:
    normalized_width = min(
        1.0,
        max(width / page_width, min(1 / page_width, 1.0)),
    )
    normalized_height = min(
        1.0,
        max(height / page_height, min(1 / page_height, 1.0)),
    )
    normalized_x = min(max(x / page_width, 0.0), 1 - normalized_width)
    normalized_y = min(max(y / page_height, 0.0), 1 - normalized_height)
    return NormalizedBoundingBox(
        x=normalized_x,
        y=normalized_y,
        width=normalized_width,
        height=normalized_height,
    )


def _contains(box: NormalizedBoundingBox, point: tuple[float, float]) -> bool:
    return box.x <= point[0] <= box.right and box.y <= point[1] <= box.bottom


def _percentile(values: Sequence[float], quantile: float) -> float:
    if len(values) == 1:
        return round(values[0], 6)
    position = (len(values) - 1) * quantile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return round(values[lower_index], 6)
    lower_weight = upper_index - position
    result = values[lower_index] * lower_weight + values[upper_index] * (
        1 - lower_weight
    )
    return round(result, 6)


__all__ = [
    "DEFAULT_LOGICAL_BLOCK_ACCURACY_THRESHOLD",
    "DEFAULT_LOW_CONFIDENCE_THRESHOLD",
    "DEFAULT_MINIMUM_LOGICAL_BLOCK_COVERAGE",
    "DEFAULT_MINIMUM_TEXT_ACCURACY",
    "DEFAULT_REQUIRED_ANCHOR_RECALL",
    "OCR_QUALITY_EVALUATOR_NAME",
    "OCR_QUALITY_EVALUATOR_VERSION",
    "OcrQualityConfig",
    "evaluate_ocr_quality",
    "normalize_ocr_text",
    "ocr_character_accuracy",
]
