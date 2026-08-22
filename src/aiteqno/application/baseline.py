"""ID-independent scoring against reviewed source-image ground truth."""

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass
from typing import Final, Sequence

from aiteqno.domain import (
    DocumentIR,
    ElementType,
    TablePrimitiveRole,
    TextElement,
    read_page_table_topology,
    validate_document,
)
from aiteqno.ports.baseline import (
    BaselineComponentScore,
    LogicalBlockEvaluation,
    ManualCheckEvidence,
    ManualCheckStatus,
    RelationshipEvaluation,
    SourceBaselineObservation,
    SourceBaselineReference,
    SourceBaselineResult,
    SourceStructuralItem,
    StructuralItemEvaluation,
)
from aiteqno.ports.evaluation import (
    EvaluationState,
    HardGateResult,
    NormalizedBoundingBox,
    RelationshipKind,
)


SOURCE_BASELINE_EVALUATOR_NAME: Final = "aiteqno-source-baseline-evaluator"
SOURCE_BASELINE_EVALUATOR_VERSION: Final = "1.1"
DEFAULT_SOURCE_BASELINE_THRESHOLD: Final = 70.0
DEFAULT_LOGICAL_BLOCK_ACCURACY_THRESHOLD: Final = 60.0
DEFAULT_STRUCTURE_MATCH_THRESHOLD: Final = 50.0

_REDUNDANT_TOPOLOGY_STRUCTURE_ROLES: Final = frozenset(
    {
        TablePrimitiveRole.CELL_RECTANGLE,
        TablePrimitiveRole.DUPLICATED_SUPPORTING_PRIMITIVE,
    }
)

SOURCE_BASELINE_COMPONENT_WEIGHTS: Final = (
    ("text_accuracy", 0.45),
    ("logical_block_coverage", 0.20),
    ("structure_similarity", 0.20),
    ("geometry_similarity", 0.15),
)

DEFAULT_SOURCE_BASELINE_COMPONENT_MINIMA: Final = {
    "text_accuracy": 70.0,
    "logical_block_coverage": 60.0,
    "structure_similarity": 60.0,
    "geometry_similarity": 50.0,
}


def _score(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 100:
        raise ValueError(f"{field_name} must be finite and between 0 and 100")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceBaselineConfig:
    """Thresholds for the source-grounded baseline; weights stay contractual."""

    threshold: float = DEFAULT_SOURCE_BASELINE_THRESHOLD
    minimum_text_accuracy: float = DEFAULT_SOURCE_BASELINE_COMPONENT_MINIMA[
        "text_accuracy"
    ]
    minimum_logical_block_coverage: float = DEFAULT_SOURCE_BASELINE_COMPONENT_MINIMA[
        "logical_block_coverage"
    ]
    minimum_structure_similarity: float = DEFAULT_SOURCE_BASELINE_COMPONENT_MINIMA[
        "structure_similarity"
    ]
    minimum_geometry_similarity: float = DEFAULT_SOURCE_BASELINE_COMPONENT_MINIMA[
        "geometry_similarity"
    ]
    logical_block_accuracy_threshold: float = DEFAULT_LOGICAL_BLOCK_ACCURACY_THRESHOLD
    structure_match_threshold: float = DEFAULT_STRUCTURE_MATCH_THRESHOLD

    def __post_init__(self) -> None:
        for field_name in (
            "threshold",
            "minimum_text_accuracy",
            "minimum_logical_block_coverage",
            "minimum_structure_similarity",
            "minimum_geometry_similarity",
            "logical_block_accuracy_threshold",
            "structure_match_threshold",
        ):
            object.__setattr__(
                self,
                field_name,
                _score(getattr(self, field_name), f"source baseline {field_name}"),
            )

    @property
    def component_minima(self) -> dict[str, float]:
        return {
            "text_accuracy": self.minimum_text_accuracy,
            "logical_block_coverage": self.minimum_logical_block_coverage,
            "structure_similarity": self.minimum_structure_similarity,
            "geometry_similarity": self.minimum_geometry_similarity,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "threshold": self.threshold,
            "component_minima": self.component_minima,
            "logical_block_accuracy_threshold": (self.logical_block_accuracy_threshold),
            "structure_match_threshold": self.structure_match_threshold,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class _CandidateGeometry:
    id: str
    element_type: ElementType
    page_number: int
    bbox: NormalizedBoundingBox


@dataclass(frozen=True, slots=True)
class _LogicalRegionEvidence:
    reference_id: str
    candidate_element_ids: tuple[str, ...]
    reading_order_keys: tuple[tuple[int, int, str], ...]
    bbox: NormalizedBoundingBox | None


def normalize_source_text(value: str) -> str:
    """Apply NFKC and remove every Unicode whitespace character."""

    if not isinstance(value, str):
        raise TypeError("source evaluation text must be a string")
    return "".join(unicodedata.normalize("NFKC", value).split())


def source_character_accuracy(expected: str, observed: str) -> float:
    """Return normalized edit-distance character accuracy on a 0..100 scale."""

    normalized_expected = normalize_source_text(expected)
    normalized_observed = normalize_source_text(observed)
    if not normalized_expected and not normalized_observed:
        return 100.0
    denominator = max(len(normalized_expected), len(normalized_observed))
    if denominator == 0:
        return 0.0
    distance = _levenshtein_distance(normalized_expected, normalized_observed)
    return round(100 * max(0.0, 1.0 - distance / denominator), 6)


def evaluate_source_baseline(
    reference: SourceBaselineReference,
    observation: SourceBaselineObservation,
    *,
    config: SourceBaselineConfig = SourceBaselineConfig(),
) -> SourceBaselineResult:
    """Evaluate candidate IR and final DOCX evidence against reviewed source truth."""

    if not isinstance(reference, SourceBaselineReference):
        raise TypeError("reference must be a SourceBaselineReference")
    if not isinstance(observation, SourceBaselineObservation):
        raise TypeError("observation must be a SourceBaselineObservation")
    if not isinstance(config, SourceBaselineConfig):
        raise TypeError("config must be a SourceBaselineConfig")
    validate_document(observation.candidate_ir)

    selected_text = (
        observation.visible_rendered_text
        if observation.visible_rendered_text is not None
        else observation.final_docx_text
    )
    text_evidence = (
        "rendered_visible"
        if observation.visible_rendered_text is not None
        else "docx_readback"
    )
    expected_text = "\n".join(item.text for item in reference.text_regions)
    text_accuracy = source_character_accuracy(expected_text, selected_text)

    logical_blocks, text_geometry_scores, logical_region_evidence = (
        _evaluate_logical_blocks(
            reference,
            observation.candidate_ir,
            minimum_accuracy=config.logical_block_accuracy_threshold,
        )
    )
    logical_block_coverage = 100 * (
        sum(item.covered for item in logical_blocks) / len(logical_blocks)
    )

    structural_items, observed_structure_count = _evaluate_structures(
        reference.structural_items,
        observation.candidate_ir,
        minimum_similarity=config.structure_match_threshold,
    )
    structure_similarity = 100 * _f1(
        expected=len(reference.structural_items),
        observed=observed_structure_count,
        matched=sum(item.matched for item in structural_items),
    )

    geometry_scores = [
        *text_geometry_scores,
        *(item.similarity for item in structural_items),
    ]
    geometry_similarity = (
        sum(geometry_scores) / len(geometry_scores) if geometry_scores else 100.0
    )

    relationships = _evaluate_relationships(
        reference,
        logical_region_evidence=logical_region_evidence,
        structural_items=structural_items,
        document=observation.candidate_ir,
    )

    raw_scores = {
        "text_accuracy": text_accuracy,
        "logical_block_coverage": logical_block_coverage,
        "structure_similarity": structure_similarity,
        "geometry_similarity": geometry_similarity,
    }
    minima = config.component_minima
    components = tuple(
        BaselineComponentScore(
            name=name,
            score=round(raw_scores[name], 6),
            weight=weight,
            minimum=minima[name],
        )
        for name, weight in SOURCE_BASELINE_COMPONENT_WEIGHTS
    )
    overall_score = round(
        sum(component.weighted_score for component in components),
        2,
    )

    manual_checks = _resolve_manual_checks(reference, observation)
    hard_gates = _hard_gates(
        reference,
        observation,
        selected_text=selected_text,
        logical_blocks=logical_blocks,
        structural_items=structural_items,
        relationships=relationships,
        manual_checks=manual_checks,
    )

    reasons: list[str] = []
    if overall_score < config.threshold:
        reasons.append(f"score_below_threshold:{overall_score:g}<{config.threshold:g}")
    reasons.extend(
        f"component_below_minimum:{component.name}:"
        f"{component.score:g}<{component.minimum:g}"
        for component in components
        if not component.passed
    )
    reasons.extend(
        f"hard_gate_failed:{gate.name}" for gate in hard_gates if gate.passed is False
    )

    if reasons:
        state = EvaluationState.FAIL
    else:
        review_reasons = [
            f"hard_gate_unknown:{gate.name}"
            for gate in hard_gates
            if gate.passed is None
        ]
        if review_reasons:
            state = EvaluationState.REQUIRES_HUMAN_REVIEW
            reasons.extend(review_reasons)
        else:
            state = EvaluationState.PASS
            reasons.append("score_and_component_minima_met_and_all_hard_gates_pass")

    return SourceBaselineResult(
        evaluator_name=SOURCE_BASELINE_EVALUATOR_NAME,
        evaluator_version=SOURCE_BASELINE_EVALUATOR_VERSION,
        reference_id=reference.reference_id,
        source_sha256=reference.source_sha256,
        text_evidence=text_evidence,
        overall_score=overall_score,
        threshold=config.threshold,
        components=components,
        logical_blocks=logical_blocks,
        structural_items=structural_items,
        hard_gates=hard_gates,
        manual_checks=manual_checks,
        state=state,
        reasons=tuple(reasons),
        relationships=relationships,
    )


def _evaluate_logical_blocks(
    reference: SourceBaselineReference,
    document: DocumentIR,
    *,
    minimum_accuracy: float,
) -> tuple[
    tuple[LogicalBlockEvaluation, ...],
    tuple[float, ...],
    tuple[_LogicalRegionEvidence, ...],
]:
    results: list[LogicalBlockEvaluation] = []
    geometry_scores: list[float] = []
    evidence: list[_LogicalRegionEvidence] = []
    pages = {page.number: page for page in document.pages}
    for region in reference.text_regions:
        page = pages.get(region.page_number)
        candidates: list[tuple[int, str, str, NormalizedBoundingBox]] = []
        if page is not None:
            for element in page.elements:
                if not isinstance(element, TextElement):
                    continue
                bbox = _normalized_bbox(element.bbox, page.size.width, page.size.height)
                center = (bbox.x + bbox.width / 2, bbox.y + bbox.height / 2)
                if _contains(region.bbox, center):
                    candidates.append(
                        (element.reading_order, element.id, element.text, bbox)
                    )
        candidates.sort(key=lambda item: (item[0], item[1]))
        candidate_text = "\n".join(item[2] for item in candidates)
        accuracy = source_character_accuracy(region.text, candidate_text)
        covered = bool(candidates) and accuracy >= minimum_accuracy
        results.append(
            LogicalBlockEvaluation(
                reference_id=region.id,
                candidate_element_ids=tuple(item[1] for item in candidates),
                observed_text=candidate_text,
                character_accuracy=accuracy,
                covered=covered,
                essential=region.essential,
            )
        )
        candidate_bbox = _union_boxes(tuple(item[3] for item in candidates))
        evidence.append(
            _LogicalRegionEvidence(
                reference_id=region.id,
                candidate_element_ids=tuple(item[1] for item in candidates),
                reading_order_keys=tuple(
                    (region.page_number, item[0], item[1]) for item in candidates
                ),
                bbox=candidate_bbox,
            )
        )
        geometry_scores.append(
            0.0
            if candidate_bbox is None
            else 100 * _box_similarity(region.bbox, candidate_bbox)
        )
    return tuple(results), tuple(geometry_scores), tuple(evidence)


def _evaluate_structures(
    expected: Sequence[SourceStructuralItem],
    document: DocumentIR,
    *,
    minimum_similarity: float,
) -> tuple[tuple[StructuralItemEvaluation, ...], int]:
    candidates = tuple(_candidate_structures(document))
    pairs: list[tuple[float, str, str]] = []
    expected_by_id = {item.id: item for item in expected}
    candidate_by_id = {item.id: item for item in candidates}
    for item in expected:
        for candidate in candidates:
            if (
                item.element_type is candidate.element_type
                and item.page_number == candidate.page_number
            ):
                pairs.append(
                    (_box_similarity(item.bbox, candidate.bbox), item.id, candidate.id)
                )
    pairs.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_expected: set[str] = set()
    used_candidates: set[str] = set()
    matches: dict[str, tuple[str, float]] = {}
    for similarity, expected_id, candidate_id in pairs:
        if 100 * similarity < minimum_similarity:
            continue
        if expected_id in used_expected or candidate_id in used_candidates:
            continue
        used_expected.add(expected_id)
        used_candidates.add(candidate_id)
        matches[expected_id] = (candidate_id, 100 * similarity)

    results = tuple(
        StructuralItemEvaluation(
            reference_id=item.id,
            candidate_element_id=(matches[item.id][0] if item.id in matches else None),
            similarity=(round(matches[item.id][1], 6) if item.id in matches else 0.0),
            matched=item.id in matches,
            essential=item.essential,
        )
        for item in expected
    )
    if len(expected_by_id) != len(expected) or len(candidate_by_id) != len(candidates):
        raise AssertionError("Document IR and source reference IDs must be unique")
    return results, len(candidates)


def _candidate_structures(document: DocumentIR) -> Sequence[_CandidateGeometry]:
    candidates: list[_CandidateGeometry] = []
    for page in document.pages:
        topology = read_page_table_topology(page)
        redundant_ids = (
            {
                assignment.element_id
                for assignment in topology.primitive_roles
                if assignment.role in _REDUNDANT_TOPOLOGY_STRUCTURE_ROLES
            }
            if topology is not None
            else set()
        )
        for element in page.elements:
            if element.type is ElementType.TEXT or element.id in redundant_ids:
                continue
            candidates.append(
                _CandidateGeometry(
                    id=element.id,
                    element_type=element.type,
                    page_number=page.number,
                    bbox=_normalized_bbox(
                        element.bbox,
                        page.size.width,
                        page.size.height,
                    ),
                )
            )
    return tuple(candidates)


def _evaluate_relationships(
    reference: SourceBaselineReference,
    *,
    logical_region_evidence: Sequence[_LogicalRegionEvidence],
    structural_items: Sequence[StructuralItemEvaluation],
    document: DocumentIR,
) -> tuple[RelationshipEvaluation, ...]:
    logical_by_id = {item.reference_id: item for item in logical_region_evidence}
    source_text_by_id = {item.id: item for item in reference.text_regions}
    structural_match_by_id = {item.reference_id: item for item in structural_items}
    candidate_structure_by_id = {
        item.id: item for item in _candidate_structures(document)
    }
    results: list[RelationshipEvaluation] = []
    for relationship in reference.relationships:
        passed: bool
        reason: str
        if relationship.kind is RelationshipKind.READING_ORDER:
            source = logical_by_id[relationship.source]
            target = logical_by_id[relationship.target]
            if not source.reading_order_keys or not target.reading_order_keys:
                passed = False
                reason = "candidate reading-order evidence is missing for an endpoint"
            elif set(source.candidate_element_ids).intersection(
                target.candidate_element_ids
            ):
                passed = False
                reason = "the same candidate text token occupies both logical regions"
            else:
                passed = max(source.reading_order_keys) < min(target.reading_order_keys)
                reason = (
                    "candidate text order preserves source-before-target"
                    if passed
                    else "candidate text order does not preserve source-before-target"
                )
        elif relationship.kind is RelationshipKind.CONTAINMENT:
            source_match = structural_match_by_id[relationship.source]
            target = logical_by_id[relationship.target]
            matched_structure = (
                candidate_structure_by_id.get(source_match.candidate_element_id)
                if source_match.candidate_element_id is not None
                else None
            )
            if matched_structure is None or target.bbox is None:
                passed = False
                reason = "candidate containment geometry is missing for an endpoint"
            else:
                passed = _contains_box(matched_structure.bbox, target.bbox)
                reason = (
                    "matched candidate structure contains the target text geometry"
                    if passed
                    else "matched candidate structure does not contain target text geometry"
                )
        else:
            source = logical_by_id[relationship.source]
            target = logical_by_id[relationship.target]
            if source.bbox is None or target.bbox is None:
                passed = False
                reason = "candidate adjacency geometry is missing for an endpoint"
            else:
                source_reference = source_text_by_id[relationship.source]
                target_reference = source_text_by_id[relationship.target]
                passed, reason = _adjacency_matches(
                    source_reference.bbox,
                    target_reference.bbox,
                    source.bbox,
                    target.bbox,
                )
        results.append(
            RelationshipEvaluation(
                kind=relationship.kind,
                source=relationship.source,
                target=relationship.target,
                passed=passed,
                essential=relationship.essential,
                reason=reason,
            )
        )
    return tuple(results)


def _normalized_bbox(
    bbox: object, page_width: float, page_height: float
) -> NormalizedBoundingBox:
    x = float(getattr(bbox, "x")) / page_width
    y = float(getattr(bbox, "y")) / page_height
    width = float(getattr(bbox, "width")) / page_width
    height = float(getattr(bbox, "height")) / page_height
    width = min(1.0, max(width, min(1 / page_width, 1.0)))
    height = min(1.0, max(height, min(1 / page_height, 1.0)))
    x = min(max(x, 0.0), 1 - width)
    y = min(max(y, 0.0), 1 - height)
    return NormalizedBoundingBox(x=x, y=y, width=width, height=height)


def _contains(bbox: NormalizedBoundingBox, point: tuple[float, float]) -> bool:
    return bbox.x <= point[0] <= bbox.right and bbox.y <= point[1] <= bbox.bottom


def _contains_box(
    outer: NormalizedBoundingBox,
    inner: NormalizedBoundingBox,
    *,
    tolerance: float = 1e-9,
) -> bool:
    return (
        outer.x - tolerance <= inner.x
        and outer.y - tolerance <= inner.y
        and outer.right + tolerance >= inner.right
        and outer.bottom + tolerance >= inner.bottom
    )


def _adjacency_matches(
    expected_source: NormalizedBoundingBox,
    expected_target: NormalizedBoundingBox,
    observed_source: NormalizedBoundingBox,
    observed_target: NormalizedBoundingBox,
) -> tuple[bool, str]:
    expected_axis, expected_sign = _dominant_direction(
        expected_source,
        expected_target,
    )
    observed_axis, observed_sign = _dominant_direction(
        observed_source,
        observed_target,
    )
    if expected_axis is None or observed_axis is None:
        return (
            False,
            "adjacency direction cannot be established from coincident centers",
        )
    if (expected_axis, expected_sign) != (observed_axis, observed_sign):
        return False, "candidate adjacency direction differs from the source reference"

    expected_gap = _directed_gap(
        expected_source,
        expected_target,
        axis=expected_axis,
        sign=expected_sign,
    )
    observed_gap = _directed_gap(
        observed_source,
        observed_target,
        axis=observed_axis,
        sign=observed_sign,
    )
    allowed_gap = expected_gap + max(0.05, expected_gap)
    if observed_gap > allowed_gap:
        return (
            False,
            "candidate regions preserve direction but are not comparably adjacent "
            f"(source gap {expected_gap:.6f}, candidate gap {observed_gap:.6f})",
        )
    return (
        True,
        "candidate regions preserve source-relative direction and proximity "
        f"(source gap {expected_gap:.6f}, candidate gap {observed_gap:.6f})",
    )


def _dominant_direction(
    source: NormalizedBoundingBox,
    target: NormalizedBoundingBox,
) -> tuple[str | None, int]:
    source_center = (source.x + source.width / 2, source.y + source.height / 2)
    target_center = (target.x + target.width / 2, target.y + target.height / 2)
    dx = target_center[0] - source_center[0]
    dy = target_center[1] - source_center[1]
    if abs(dx) <= 1e-12 and abs(dy) <= 1e-12:
        return None, 0
    if abs(dx) >= abs(dy):
        return "horizontal", 1 if dx > 0 else -1
    return "vertical", 1 if dy > 0 else -1


def _directed_gap(
    source: NormalizedBoundingBox,
    target: NormalizedBoundingBox,
    *,
    axis: str,
    sign: int,
) -> float:
    if axis == "horizontal":
        return max(
            0.0,
            target.x - source.right if sign > 0 else source.x - target.right,
        )
    return max(
        0.0,
        target.y - source.bottom if sign > 0 else source.y - target.bottom,
    )


def _union_boxes(
    boxes: Sequence[NormalizedBoundingBox],
) -> NormalizedBoundingBox | None:
    if not boxes:
        return None
    left = min(item.x for item in boxes)
    top = min(item.y for item in boxes)
    right = max(item.right for item in boxes)
    bottom = max(item.bottom for item in boxes)
    return NormalizedBoundingBox(
        x=left,
        y=top,
        width=right - left,
        height=bottom - top,
    )


def _box_similarity(
    expected: NormalizedBoundingBox,
    observed: NormalizedBoundingBox,
) -> float:
    intersection_width = max(
        0.0,
        min(expected.right, observed.right) - max(expected.x, observed.x),
    )
    intersection_height = max(
        0.0,
        min(expected.bottom, observed.bottom) - max(expected.y, observed.y),
    )
    intersection = intersection_width * intersection_height
    union = (
        expected.width * expected.height
        + observed.width * observed.height
        - intersection
    )
    iou = intersection / union if union else 0.0
    expected_center = (
        expected.x + expected.width / 2,
        expected.y + expected.height / 2,
    )
    observed_center = (
        observed.x + observed.width / 2,
        observed.y + observed.height / 2,
    )
    center_distance = math.dist(expected_center, observed_center)
    center_similarity = max(0.0, 1.0 - center_distance / math.sqrt(2))
    return 0.70 * iou + 0.30 * center_similarity


def _resolve_manual_checks(
    reference: SourceBaselineReference,
    observation: SourceBaselineObservation,
) -> tuple[ManualCheckEvidence, ...]:
    supplied = {item.name: item for item in observation.manual_checks}
    resolved: list[ManualCheckEvidence] = []
    for name in reference.required_manual_checks:
        resolved.append(
            supplied.pop(
                name,
                ManualCheckEvidence(
                    name=name,
                    status=ManualCheckStatus.PENDING,
                    note="required check has no supplied evidence",
                ),
            )
        )
    resolved.extend(supplied[name] for name in sorted(supplied))
    return tuple(resolved)


def _hard_gates(
    reference: SourceBaselineReference,
    observation: SourceBaselineObservation,
    *,
    selected_text: str,
    logical_blocks: Sequence[LogicalBlockEvaluation],
    structural_items: Sequence[StructuralItemEvaluation],
    relationships: Sequence[RelationshipEvaluation],
    manual_checks: Sequence[ManualCheckEvidence],
) -> tuple[HardGateResult, ...]:
    digest_matches = reference.source_sha256 == observation.source_sha256
    candidate_page_count_matches = (
        len(observation.candidate_ir.pages) == reference.expected_page_count
    )
    if observation.rendered_page_count is None:
        rendered_page_count_passed: bool | None = None
        rendered_page_count_reason = "rendered DOCX page count evidence is unavailable"
    else:
        rendered_page_count_passed = (
            observation.rendered_page_count == reference.expected_page_count
        )
        rendered_page_count_reason = (
            "rendered DOCX page count matches reviewed source"
            if rendered_page_count_passed
            else "rendered DOCX page count does not match reviewed source: "
            f"expected {reference.expected_page_count}, "
            f"observed {observation.rendered_page_count}"
        )

    normalized_output = normalize_source_text(selected_text)
    missing_anchors = tuple(
        anchor
        for anchor in reference.essential_text_anchors
        if normalize_source_text(anchor) not in normalized_output
    )
    missing_blocks = tuple(
        item.reference_id
        for item in logical_blocks
        if item.essential and not item.covered
    )
    missing_structures = tuple(
        item.reference_id
        for item in structural_items
        if item.essential and not item.matched
    )
    failed_relationships = tuple(
        item for item in relationships if item.essential and not item.passed
    )

    failed_manual_checks = tuple(
        item.name for item in manual_checks if item.status is ManualCheckStatus.FAILED
    )
    pending_manual_checks = tuple(
        item.name for item in manual_checks if item.status is ManualCheckStatus.PENDING
    )
    if failed_manual_checks:
        manual_passed: bool | None = False
        manual_reason = "failed manual checks: " + ", ".join(failed_manual_checks)
    elif pending_manual_checks:
        manual_passed = None
        manual_reason = "pending manual checks: " + ", ".join(pending_manual_checks)
    else:
        manual_passed = True
        manual_reason = "all required manual checks passed"

    return (
        HardGateResult(
            name="source_digest_matches",
            passed=digest_matches,
            reason=(
                "observation and reviewed reference use the same source digest"
                if digest_matches
                else "observation source digest differs from reviewed reference"
            ),
        ),
        HardGateResult(
            name="reference_reviewed",
            passed=True if reference.reviewed else None,
            reason=(
                "source reference is marked human-reviewed"
                if reference.reviewed
                else "source reference still requires human review"
            ),
        ),
        HardGateResult(
            name="candidate_ir_page_count",
            passed=candidate_page_count_matches,
            reason=(
                "candidate IR page count matches reviewed source"
                if candidate_page_count_matches
                else "candidate IR page count does not match reviewed source"
            ),
        ),
        HardGateResult(
            name="rendered_docx_page_count",
            passed=rendered_page_count_passed,
            reason=rendered_page_count_reason,
        ),
        HardGateResult(
            name="essential_text_anchors",
            passed=not missing_anchors,
            reason=(
                "all essential anchors are exact normalized substrings"
                if not missing_anchors
                else "missing essential anchors: " + ", ".join(missing_anchors)
            ),
        ),
        HardGateResult(
            name="essential_logical_blocks",
            passed=not missing_blocks,
            reason=(
                "all essential logical blocks meet the character-accuracy floor"
                if not missing_blocks
                else "missing essential logical blocks: " + ", ".join(missing_blocks)
            ),
        ),
        HardGateResult(
            name="essential_structures",
            passed=not missing_structures,
            reason=(
                "all essential structures have geometry-based matches"
                if not missing_structures
                else "missing essential structures: " + ", ".join(missing_structures)
            ),
        ),
        HardGateResult(
            name="essential_relationships",
            passed=not failed_relationships,
            reason=(
                "all essential source relationships are supported by candidate evidence"
                if not failed_relationships
                else "unsupported essential relationships: "
                + ", ".join(
                    f"{item.kind.value}:{item.source}->{item.target}"
                    for item in failed_relationships
                )
            ),
        ),
        HardGateResult(
            name="manual_checks",
            passed=manual_passed,
            reason=manual_reason,
        ),
    )


def _f1(*, expected: int, observed: int, matched: int) -> float:
    if expected == 0 and observed == 0:
        return 1.0
    if expected == 0 or observed == 0 or matched == 0:
        return 0.0
    precision = matched / observed
    recall = matched / expected
    return 2 * precision * recall / (precision + recall)


def _levenshtein_distance(expected: str, observed: str) -> int:
    if len(expected) < len(observed):
        expected, observed = observed, expected
    previous = list(range(len(observed) + 1))
    for expected_index, expected_character in enumerate(expected, start=1):
        current = [expected_index]
        for observed_index, observed_character in enumerate(observed, start=1):
            insertion = current[observed_index - 1] + 1
            deletion = previous[observed_index] + 1
            substitution = previous[observed_index - 1] + (
                expected_character != observed_character
            )
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


__all__ = [
    "DEFAULT_LOGICAL_BLOCK_ACCURACY_THRESHOLD",
    "DEFAULT_SOURCE_BASELINE_COMPONENT_MINIMA",
    "DEFAULT_SOURCE_BASELINE_THRESHOLD",
    "DEFAULT_STRUCTURE_MATCH_THRESHOLD",
    "SOURCE_BASELINE_COMPONENT_WEIGHTS",
    "SOURCE_BASELINE_EVALUATOR_NAME",
    "SOURCE_BASELINE_EVALUATOR_VERSION",
    "SourceBaselineConfig",
    "evaluate_source_baseline",
    "normalize_source_text",
    "source_character_accuracy",
]
