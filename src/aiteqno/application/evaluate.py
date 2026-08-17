"""Deterministic restoration scoring and readability hard gates."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from os import PathLike
from typing import Final

from aiteqno.domain import (
    DocumentIR,
    ElementType,
    ImageElement,
    TextElement,
    read_page_table_topology,
)
from aiteqno.domain import validate_document
from aiteqno.ports import DocxRenderReport
from aiteqno.ports.evaluation import (
    ComponentScore,
    DocxObserver,
    ElementMatch,
    EvaluationReference,
    EvaluationState,
    HardGateResult,
    NormalizedBoundingBox,
    ObservedElement,
    ReferenceElement,
    RelationshipKind,
    RestorationEvaluationInput,
    RestorationEvaluationResult,
    SnapshotObservation,
    StructuralRelationship,
)


EVALUATOR_NAME: Final = "aiteqno-restoration-evaluator"
EVALUATOR_VERSION: Final = "1.0"
DEFAULT_RESTORATION_THRESHOLD: Final = 70.0
MIN_TEXT_ELEMENT_SIMILARITY: Final = 0.60
GEOMETRY_IOU_WEIGHT: Final = 0.70
GEOMETRY_CENTER_WEIGHT: Final = 0.30

COMPONENT_WEIGHTS: Final = (
    ("text_similarity", 0.45),
    ("element_coverage", 0.20),
    ("structure_similarity", 0.20),
    ("geometry_similarity", 0.15),
)

_FATAL_ASSET_WARNING_CODES: Final = frozenset(
    {
        "asset_missing",
        "asset_unreadable",
        "asset_digest_mismatch",
        "asset_identity_mismatch",
        "asset_path_escape",
        "asset_media_type_mismatch",
        "asset_dimension_mismatch",
        "asset_resolver_unavailable",
    }
)
_SOURCE_BACKGROUND_WARNING_CODE: Final = "source_page_background_rejected"


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationConfig:
    """The configurable V1 pass threshold; metric weights remain contractual."""

    threshold: float = DEFAULT_RESTORATION_THRESHOLD

    def __post_init__(self) -> None:
        if isinstance(self.threshold, bool) or not isinstance(
            self.threshold, (int, float)
        ):
            raise TypeError("evaluation threshold must be a number")
        threshold = float(self.threshold)
        if not math.isfinite(threshold) or not 0 <= threshold <= 100:
            raise ValueError(
                "evaluation threshold must be finite and between 0 and 100"
            )
        object.__setattr__(self, "threshold", threshold)


def normalize_evaluation_text(value: str) -> str:
    """Normalize text exactly as required by the V1 quality contract."""

    if not isinstance(value, str):
        raise TypeError("evaluation text must be a string")
    return " ".join(unicodedata.normalize("NFKC", value).split())


def build_evaluation_reference(
    document: DocumentIR,
    *,
    reference_id: str,
    reviewed: bool,
    essential_element_ids: Iterable[str] = (),
    essential_text_anchors: Sequence[str] = (),
    relationships: Sequence[StructuralRelationship] = (),
    required_human_checks: Sequence[str] = (),
) -> EvaluationReference:
    """Create normalized reviewed expectations from a validated Document IR."""

    if not isinstance(document, DocumentIR):
        raise TypeError("document must be a DocumentIR")
    validate_document(document)
    essential_ids = frozenset(essential_element_ids)
    known_ids = {element.id for page in document.pages for element in page.elements}
    unknown_ids = sorted(essential_ids - known_ids)
    if unknown_ids:
        raise ValueError(
            "essential element IDs are absent from Document IR: "
            + ", ".join(unknown_ids)
        )
    assets = {asset.id: asset for asset in document.assets}
    expected: list[ReferenceElement] = []
    for page in document.pages:
        for element in page.elements:
            bbox = None
            if element.bbox.width > 0 and element.bbox.height > 0:
                bbox = NormalizedBoundingBox(
                    x=element.bbox.x / page.size.width,
                    y=element.bbox.y / page.size.height,
                    width=element.bbox.width / page.size.width,
                    height=element.bbox.height / page.size.height,
                )
            expected.append(
                ReferenceElement(
                    id=element.id,
                    element_type=element.type,
                    page_number=page.number,
                    text=element.text if isinstance(element, TextElement) else None,
                    bbox=bbox,
                    reading_order=(
                        element.reading_order
                        if isinstance(element, TextElement)
                        else None
                    ),
                    content_sha256=(
                        assets[element.asset_id].sha256
                        if isinstance(element, ImageElement)
                        else None
                    ),
                    essential=element.id in essential_ids,
                )
            )
    return EvaluationReference(
        reference_id=reference_id,
        ir_version=document.ir_version,
        reviewed=reviewed,
        elements=tuple(expected),
        relationships=tuple(relationships),
        essential_text_anchors=tuple(essential_text_anchors),
        required_human_checks=tuple(required_human_checks),
    )


def build_docx_structure_relationships(
    document: DocumentIR,
) -> tuple[StructuralRelationship, ...]:
    """Describe the source-addressable structure emitted by the DOCX adapter.

    The score formula remains unchanged. This helper supplies the evaluator with
    the table, cell, and text relationships that are explicitly encoded in the
    generated OOXML, instead of treating an empty relationship set as the
    expected structure.
    """

    if not isinstance(document, DocumentIR):
        raise TypeError("document must be a DocumentIR")
    validate_document(document)
    relationships: list[StructuralRelationship] = []
    ordered_text_ids: list[str] = []

    for page in document.pages:
        ordered_text_ids.extend(
            element.id
            for element in sorted(
                (
                    element
                    for element in page.elements
                    if isinstance(element, TextElement)
                ),
                key=lambda element: element.reading_order,
            )
        )
        topology = read_page_table_topology(page)
        if topology is None:
            continue
        for table in topology.tables:
            for cell in table.cells:
                relationships.append(
                    StructuralRelationship(
                        kind=RelationshipKind.CONTAINMENT,
                        source=table.id,
                        target=cell.id,
                    )
                )
                relationships.extend(
                    StructuralRelationship(
                        kind=RelationshipKind.CONTAINMENT,
                        source=cell.id,
                        target=text_element_id,
                    )
                    for text_element_id in cell.text_element_ids
                )
            for row_index in range(table.logical_rows):
                row_cells = tuple(
                    cell for cell in table.cells if cell.row_index == row_index
                )
                relationships.extend(
                    StructuralRelationship(
                        kind=RelationshipKind.ADJACENCY,
                        source=left.id,
                        target=right.id,
                    )
                    for left, right in zip(row_cells, row_cells[1:], strict=False)
                )

    relationships.extend(
        StructuralRelationship(
            kind=RelationshipKind.READING_ORDER,
            source=source,
            target=target,
        )
        for source, target in zip(
            ordered_text_ids,
            ordered_text_ids[1:],
            strict=False,
        )
    )
    return tuple(relationships)


def evaluate_restoration(
    document: DocumentIR,
    reference: EvaluationReference,
    docx_path: str | PathLike[str],
    render_report: DocxRenderReport,
    *,
    observer: DocxObserver,
    snapshot: SnapshotObservation | None = None,
    completed_human_checks: Sequence[str] = (),
    config: EvaluationConfig = EvaluationConfig(),
) -> RestorationEvaluationResult:
    """Observe a generated DOCX and evaluate it without reading the source image."""

    if not isinstance(document, DocumentIR):
        raise TypeError("document must be a DocumentIR")
    validate_document(document)
    observation = observer.observe(docx_path)
    evaluation_input = RestorationEvaluationInput(
        ir_version=document.ir_version,
        ir_schema_valid=True,
        reference=reference,
        observation=observation,
        render_report=render_report,
        snapshot=snapshot,
        completed_human_checks=tuple(completed_human_checks),
    )
    return evaluate_restoration_input(evaluation_input, config=config)


def evaluate_restoration_input(
    evaluation_input: RestorationEvaluationInput,
    *,
    config: EvaluationConfig = EvaluationConfig(),
) -> RestorationEvaluationResult:
    """Evaluate already normalized evidence; useful for fixtures and E2E adapters."""

    if not isinstance(evaluation_input, RestorationEvaluationInput):
        raise TypeError("evaluation_input must be a RestorationEvaluationInput")
    if not isinstance(config, EvaluationConfig):
        raise TypeError("config must be an EvaluationConfig")

    reference = evaluation_input.reference
    observation = evaluation_input.observation
    matches = _match_elements(evaluation_input)
    matched_reference_ids = {item.reference_id for item in matches}
    matched_observed_ids = {item.observed_id for item in matches}
    missing_ids = tuple(
        sorted(
            element.id
            for element in reference.elements
            if element.id not in matched_reference_ids
        )
    )
    unexpected_ids = tuple(
        sorted(
            element.id
            for element in observation.elements
            if element.id not in matched_observed_ids
        )
    )

    text_score = 100 * _text_similarity(reference.elements, observation.elements)
    element_score = 100 * _f1(
        expected=len(reference.elements),
        observed=len(observation.elements),
        matched=len(matches),
    )
    mapped_relationships = _mapped_observed_relationships(
        observation.relationships,
        matches,
    )
    expected_relationships = {
        relationship.identity for relationship in reference.relationships
    }
    structure_score = 100 * _set_f1(
        expected_relationships,
        mapped_relationships,
    )
    geometry_score = 100 * _geometry_similarity(
        reference.elements,
        evaluation_input.snapshot,
        matches,
    )

    raw_scores = {
        "text_similarity": text_score,
        "element_coverage": element_score,
        "structure_similarity": structure_score,
        "geometry_similarity": geometry_score,
    }
    components = tuple(
        ComponentScore(
            name=name,
            score=round(raw_scores[name], 6),
            weight=weight,
        )
        for name, weight in COMPONENT_WEIGHTS
    )
    overall_score = round(
        sum(component.weighted_score for component in components),
        2,
    )

    hard_gates = _hard_gates(
        evaluation_input,
        matches,
        mapped_relationships,
    )
    failed_gates = tuple(gate for gate in hard_gates if gate.passed is False)
    unknown_gates = tuple(gate for gate in hard_gates if gate.passed is None)
    completed_checks = set(evaluation_input.completed_human_checks)
    pending_checks = tuple(
        check
        for check in reference.required_human_checks
        if check not in completed_checks
    )

    reasons: list[str] = []
    if overall_score < config.threshold:
        reasons.append(f"score_below_threshold:{overall_score:g}<{config.threshold:g}")
    reasons.extend(f"hard_gate_failed:{gate.name}" for gate in failed_gates)

    if reasons:
        state = EvaluationState.FAIL
    else:
        review_reasons: list[str] = []
        if not reference.reviewed:
            review_reasons.append("reference_not_reviewed")
        review_reasons.extend(
            f"hard_gate_unknown:{gate.name}" for gate in unknown_gates
        )
        review_reasons.extend(
            f"human_check_required:{check}" for check in pending_checks
        )
        if observation.errors:
            review_reasons.append("docx_observation_has_errors")
        if evaluation_input.snapshot is not None and evaluation_input.snapshot.errors:
            review_reasons.append("snapshot_observation_has_errors")
        if review_reasons:
            state = EvaluationState.REQUIRES_HUMAN_REVIEW
            reasons.extend(review_reasons)
        else:
            state = EvaluationState.PASS
            reasons.append("score_meets_threshold_and_all_hard_gates_pass")

    return RestorationEvaluationResult(
        evaluator_name=EVALUATOR_NAME,
        evaluator_version=EVALUATOR_VERSION,
        ir_version=evaluation_input.ir_version,
        reference_id=reference.reference_id,
        overall_score=overall_score,
        threshold=config.threshold,
        components=components,
        matches=matches,
        missing_element_ids=missing_ids,
        unexpected_element_ids=unexpected_ids,
        hard_gates=hard_gates,
        state=state,
        reasons=tuple(reasons),
        required_human_checks=pending_checks,
    )


def _match_elements(
    evaluation_input: RestorationEvaluationInput,
) -> tuple[ElementMatch, ...]:
    report = evaluation_input.render_report
    rendered_ids = set(report.rendered_element_ids) - set(report.omitted_element_ids)
    candidates: list[tuple[float, str, str]] = []
    for expected in evaluation_input.reference.elements:
        if expected.id not in rendered_ids:
            continue
        for observed in evaluation_input.observation.elements:
            similarity = _element_pair_similarity(expected, observed)
            if similarity is not None:
                candidates.append((similarity, expected.id, observed.id))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_reference: set[str] = set()
    used_observed: set[str] = set()
    matches: list[ElementMatch] = []
    for similarity, reference_id, observed_id in candidates:
        if reference_id in used_reference or observed_id in used_observed:
            continue
        used_reference.add(reference_id)
        used_observed.add(observed_id)
        matches.append(
            ElementMatch(
                reference_id=reference_id,
                observed_id=observed_id,
                similarity=round(similarity, 6),
            )
        )
    return tuple(sorted(matches, key=lambda item: item.reference_id))


def _element_pair_similarity(
    expected: ReferenceElement,
    observed: ObservedElement,
) -> float | None:
    if (
        expected.element_type is not observed.element_type
        or expected.page_number != observed.page_number
    ):
        return None
    geometry = (
        _box_similarity(expected.bbox, observed.bbox)
        if expected.bbox is not None and observed.bbox is not None
        else 0.5
    )
    source_matches = observed.source_element_id == expected.id
    if expected.element_type is ElementType.TEXT:
        text_similarity = _string_similarity(expected.text or "", observed.text or "")
        if text_similarity < MIN_TEXT_ELEMENT_SIMILARITY:
            return None
        return min(
            1.0,
            0.80 * text_similarity + 0.20 * geometry + (0.05 if source_matches else 0),
        )
    if (
        expected.element_type is ElementType.IMAGE
        and expected.content_sha256 is not None
        and expected.content_sha256 != observed.content_sha256
    ):
        return None
    content_bonus = (
        0.10
        if expected.content_sha256 is not None
        and expected.content_sha256 == observed.content_sha256
        else 0
    )
    return min(
        1.0, 0.50 + 0.40 * geometry + content_bonus + (0.10 if source_matches else 0)
    )


def _text_similarity(
    expected: Sequence[ReferenceElement],
    observed: Sequence[ObservedElement],
) -> float:
    expected_text = _combined_text(expected)
    observed_text = _combined_text(observed)
    return _string_similarity(expected_text, observed_text)


def _combined_text(
    elements: Sequence[ReferenceElement] | Sequence[ObservedElement],
) -> str:
    texts = sorted(
        (element for element in elements if element.element_type is ElementType.TEXT),
        key=lambda element: (
            element.page_number,
            element.reading_order if element.reading_order is not None else 2**31 - 1,
            element.id,
        ),
    )
    return normalize_evaluation_text("\n".join(element.text or "" for element in texts))


def _string_similarity(expected: str, observed: str) -> float:
    normalized_expected = normalize_evaluation_text(expected)
    normalized_observed = normalize_evaluation_text(observed)
    if not normalized_expected and not normalized_observed:
        return 1.0
    if not normalized_expected or not normalized_observed:
        return 0.0
    return SequenceMatcher(
        None,
        normalized_expected,
        normalized_observed,
        autojunk=False,
    ).ratio()


def _mapped_observed_relationships(
    relationships: Sequence[StructuralRelationship],
    matches: Sequence[ElementMatch],
) -> set[tuple[str, str, str]]:
    observed_to_reference = {match.observed_id: match.reference_id for match in matches}
    return {
        (
            relationship.kind.value,
            observed_to_reference.get(relationship.source, relationship.source),
            observed_to_reference.get(relationship.target, relationship.target),
        )
        for relationship in relationships
    }


def _geometry_similarity(
    expected: Sequence[ReferenceElement],
    snapshot: SnapshotObservation | None,
    matches: Sequence[ElementMatch],
) -> float:
    expected_with_geometry = tuple(
        element for element in expected if element.bbox is not None
    )
    if not expected_with_geometry:
        return 1.0
    if snapshot is None or not snapshot.available:
        return 0.0
    observed_ids = {match.reference_id: match.observed_id for match in matches}
    scores: list[float] = []
    for element in expected_with_geometry:
        regions = [
            region
            for region in snapshot.regions
            if region.element_type is element.element_type
            and (
                region.source_element_id == element.id
                or region.observed_element_id == observed_ids.get(element.id)
            )
        ]
        if not regions:
            scores.append(0.0)
            continue
        region = min(regions, key=lambda item: item.id)
        scores.append(_box_similarity(element.bbox, region.bbox))
    return sum(scores) / len(scores)


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
    return GEOMETRY_IOU_WEIGHT * iou + GEOMETRY_CENTER_WEIGHT * center_similarity


def _hard_gates(
    evaluation_input: RestorationEvaluationInput,
    matches: Sequence[ElementMatch],
    mapped_relationships: set[tuple[str, str, str]],
) -> tuple[HardGateResult, ...]:
    reference = evaluation_input.reference
    observation = evaluation_input.observation
    report = evaluation_input.render_report
    snapshot = evaluation_input.snapshot
    versions_match = (
        evaluation_input.ir_schema_valid
        and evaluation_input.ir_version == reference.ir_version
        and evaluation_input.ir_version == report.ir_version
    )
    ir_reason = (
        "IR schema and evaluator/report versions agree"
        if versions_match
        else "IR schema validity or evaluator/report version agreement failed"
    )

    report_matches_docx = report.output_sha256 == observation.source_sha256
    if snapshot is None or not snapshot.available:
        libreoffice_passed: bool | None = None
        libreoffice_reason = "rendered DOCX snapshot evidence is unavailable"
    elif snapshot.opened_without_repair is None:
        libreoffice_passed = None
        libreoffice_reason = "snapshot exists but open-without-repair was not observed"
    else:
        libreoffice_passed = snapshot.opened_without_repair and not snapshot.errors
        libreoffice_reason = (
            "DOCX snapshot renderer opened the document without repair"
            if libreoffice_passed
            else "DOCX snapshot renderer did not establish repair-free opening"
        )

    candidate_text = _combined_text(observation.elements)
    missing_anchors = tuple(
        anchor
        for anchor in reference.essential_text_anchors
        if normalize_evaluation_text(anchor) not in candidate_text
    )
    essential_relationships = {
        relationship.identity
        for relationship in reference.relationships
        if relationship.essential
    }
    missing_relationships = essential_relationships - mapped_relationships
    matched_ids = {match.reference_id for match in matches}
    essential_ids = {element.id for element in reference.elements if element.essential}
    missing_essential_ids = essential_ids - matched_ids

    background_warnings = tuple(
        warning
        for warning in report.warnings
        if warning.code == _SOURCE_BACKGROUND_WARNING_CODE
    )
    fatal_warnings = tuple(
        warning
        for warning in report.warnings
        if warning.code in _FATAL_ASSET_WARNING_CODES
        and (warning.element_id is None or warning.element_id in essential_ids)
    )
    omitted_essential = essential_ids.intersection(report.omitted_element_ids)
    fatal_render_issue = bool(report.errors or fatal_warnings or omitted_essential)
    docx_content_safe = not observation.external_relationships

    return (
        HardGateResult(
            name="ir_contract",
            passed=versions_match,
            reason=ir_reason,
        ),
        HardGateResult(
            name="docx_package_readable",
            passed=observation.package_readable,
            reason=(
                "DOCX OPC package is readable"
                if observation.package_readable
                else "DOCX OPC package is unreadable"
            ),
        ),
        HardGateResult(
            name="python_docx_reopenable",
            passed=observation.python_docx_reopenable,
            reason=(
                "python-docx reopened the generated document"
                if observation.python_docx_reopenable
                else "python-docx could not reopen the generated document"
            ),
        ),
        HardGateResult(
            name="render_report_integrity",
            passed=report_matches_docx,
            reason=(
                "render report digest matches the observed DOCX"
                if report_matches_docx
                else "render report digest does not match the observed DOCX"
            ),
        ),
        HardGateResult(
            name="libreoffice_open_without_repair",
            passed=libreoffice_passed,
            reason=libreoffice_reason,
        ),
        HardGateResult(
            name="essential_text_readable",
            passed=not missing_anchors,
            reason=(
                "all essential text anchors are present after DOCX read-back"
                if not missing_anchors
                else "missing essential text anchors: " + ", ".join(missing_anchors)
            ),
        ),
        HardGateResult(
            name="essential_structure_preserved",
            passed=not missing_relationships,
            reason=(
                "all essential structural relationships are preserved"
                if not missing_relationships
                else "missing essential relationships: "
                + ", ".join("/".join(item) for item in sorted(missing_relationships))
            ),
        ),
        HardGateResult(
            name="essential_elements_present",
            passed=not missing_essential_ids,
            reason=(
                "all essential elements are represented in DOCX and render report"
                if not missing_essential_ids
                else "missing essential elements: "
                + ", ".join(sorted(missing_essential_ids))
            ),
        ),
        HardGateResult(
            name="source_page_background_prohibited",
            passed=not background_warnings,
            reason=(
                "no whole-source-page background was used"
                if not background_warnings
                else "render report records a prohibited source-page background"
            ),
        ),
        HardGateResult(
            name="fatal_render_issues_absent",
            passed=not fatal_render_issue,
            reason=(
                "no fatal render error, essential omission, or required-asset failure"
                if not fatal_render_issue
                else "render report contains a fatal error, essential omission, or required-asset failure"
            ),
        ),
        HardGateResult(
            name="external_relationships_absent",
            passed=docx_content_safe,
            reason=(
                "DOCX contains no external relationships"
                if docx_content_safe
                else "DOCX contains prohibited external relationships"
            ),
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


def _set_f1(
    expected: set[tuple[str, str, str]],
    observed: set[tuple[str, str, str]],
) -> float:
    return _f1(
        expected=len(expected),
        observed=len(observed),
        matched=len(expected.intersection(observed)),
    )


__all__ = [
    "COMPONENT_WEIGHTS",
    "DEFAULT_RESTORATION_THRESHOLD",
    "EVALUATOR_NAME",
    "EVALUATOR_VERSION",
    "EvaluationConfig",
    "GEOMETRY_CENTER_WEIGHT",
    "GEOMETRY_IOU_WEIGHT",
    "MIN_TEXT_ELEMENT_SIMILARITY",
    "build_evaluation_reference",
    "build_docx_structure_relationships",
    "evaluate_restoration",
    "evaluate_restoration_input",
    "normalize_evaluation_text",
]
