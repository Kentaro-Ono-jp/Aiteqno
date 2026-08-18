"""Pure common decision engine for one fixed same-runtime OCR experiment."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence

from aiteqno.domain import (
    DocumentIR,
    DocumentIRValidationError,
    ProvenanceStage,
    TextElement,
    validate_document,
)
from aiteqno.ports import OcrQualityResult
from aiteqno.ports.ocr_experiment import (
    OCR_EXPERIMENT_RUNTIME_FIELDS,
    OcrExperimentCheck,
    OcrExperimentComparisonResult,
    OcrExperimentContract,
    OcrExperimentDecision,
    OcrExperimentMetricDelta,
    OcrExperimentRecoveryDelta,
    OcrExperimentRun,
)


DEFAULT_MINIMUM_TEXT_ACCURACY_DELTA = 1.0
_REQUIRED_INTEGRITY_GATES = (
    "source_digest_matches_reference",
    "reference_reviewed",
    "candidate_page_count",
)
_NORMALIZATION_CONTRACT = "NFKC then remove every Unicode whitespace character"
_COMMON_CHECK_NAMES = (
    "source_reference_threshold_normalization",
    "runtime_equivalence",
    "ocr_quality_integrity_hard_gates",
    "source_geometry_and_provenance_integrity",
    "non_text_document_ir_integrity",
)


def compare_ocr_experiment(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
    *,
    contract: OcrExperimentContract,
    hypothesis_checks: Sequence[OcrExperimentCheck],
    minimum_text_accuracy_delta: float = DEFAULT_MINIMUM_TEXT_ACCURACY_DELTA,
) -> OcrExperimentComparisonResult:
    """Compare already-scored OCR runs under an immutable experiment contract."""

    if not isinstance(control, OcrExperimentRun):
        raise TypeError("control must be an OcrExperimentRun")
    if not isinstance(candidate, OcrExperimentRun):
        raise TypeError("candidate must be an OcrExperimentRun")
    if not isinstance(contract, OcrExperimentContract):
        raise TypeError("contract must be an OcrExperimentContract")
    minimum_delta = _finite_non_negative(
        minimum_text_accuracy_delta,
        "minimum_text_accuracy_delta",
    )
    ordered_hypothesis_checks = _validated_hypothesis_checks(
        contract,
        hypothesis_checks,
    )

    context_check = _context_check(control.quality, candidate.quality)
    runtime_check = _runtime_check(control.quality, candidate.quality, contract)
    hard_gate_check = _hard_gate_check(control.quality, candidate.quality)
    geometry_check = _geometry_check(control, candidate, contract)
    nontext_check = _nontext_check(control, candidate)
    checks = (
        context_check,
        runtime_check,
        hard_gate_check,
        *ordered_hypothesis_checks,
        geometry_check,
        nontext_check,
    )

    text_delta = _metric_delta(
        control.quality.text_character_accuracy.score,
        candidate.quality.text_character_accuracy.score,
    )
    block_delta = _metric_delta(
        control.quality.logical_block_coverage.score,
        candidate.quality.logical_block_coverage.score,
    )
    anchor_delta = _metric_delta(
        control.quality.essential_anchor_recall.score,
        candidate.quality.essential_anchor_recall.score,
    )
    anchor_recovery = _anchor_recovery(control.quality, candidate.quality)
    block_recovery = _block_recovery(control.quality, candidate.quality)
    control_missing_essential = _unrecovered_essential_blocks(control.quality)
    candidate_missing_essential = _unrecovered_essential_blocks(candidate.quality)

    invalid_reasons = tuple(
        f"comparison_invalid:{check.name}" for check in checks if not check.passed
    )
    if invalid_reasons:
        decision = OcrExperimentDecision.INVALID
        reasons = invalid_reasons
    else:
        regression_reasons: list[str] = []
        if text_delta.delta < 0:
            regression_reasons.append("regression:text_character_accuracy")
        if block_delta.delta < 0:
            regression_reasons.append("regression:logical_block_coverage")
        if anchor_delta.delta < 0:
            regression_reasons.append("regression:essential_anchor_recall")
        regression_reasons.extend(
            f"regression:lost_anchor:{value}" for value in anchor_recovery.lost
        )
        regression_reasons.extend(
            f"regression:lost_logical_block:{value}" for value in block_recovery.lost
        )
        if len(candidate_missing_essential) > len(control_missing_essential):
            regression_reasons.append(
                "regression:unrecovered_essential_blocks_increased"
            )

        if regression_reasons:
            decision = OcrExperimentDecision.REGRESSED
            reasons = tuple(regression_reasons)
        elif text_delta.delta < minimum_delta:
            decision = OcrExperimentDecision.INCONCLUSIVE
            reasons = (
                "text_accuracy_delta_below_minimum:"
                f"{text_delta.delta:g}<{minimum_delta:g}",
            )
        else:
            decision = OcrExperimentDecision.SUPPORTED
            reasons = (contract.supported_reason,)

    return OcrExperimentComparisonResult(
        contract=contract,
        minimum_text_accuracy_delta=minimum_delta,
        control_quality_state=control.quality.state.value,
        candidate_quality_state=candidate.quality.state.value,
        control_effective_ocr_dpi=control.quality.runtime.effective_ocr_dpi,
        candidate_effective_ocr_dpi=candidate.quality.runtime.effective_ocr_dpi,
        control_evidence_sha256=_json_sha256(control.evidence),
        candidate_evidence_sha256=_json_sha256(candidate.evidence),
        text_character_accuracy=text_delta,
        logical_block_coverage=block_delta,
        essential_anchor_recall=anchor_delta,
        anchors=anchor_recovery,
        blocks=block_recovery,
        control_unrecovered_essential_blocks=control_missing_essential,
        candidate_unrecovered_essential_blocks=candidate_missing_essential,
        checks=checks,
        decision=decision,
        reasons=reasons,
    )


def _validated_hypothesis_checks(
    contract: OcrExperimentContract,
    checks: Sequence[OcrExperimentCheck],
) -> tuple[OcrExperimentCheck, ...]:
    if isinstance(checks, (str, bytes, bytearray)) or not isinstance(checks, Sequence):
        raise TypeError("hypothesis_checks must be a sequence")
    values = tuple(checks)
    if any(not isinstance(check, OcrExperimentCheck) for check in values):
        raise TypeError("hypothesis_checks must contain only OcrExperimentCheck values")
    names = tuple(check.name for check in values)
    if len(names) != len(set(names)):
        raise ValueError("hypothesis check names must be unique")
    overlap = sorted(set(names) & set(_COMMON_CHECK_NAMES))
    if overlap:
        raise ValueError(
            "hypothesis check names conflict with common checks: " + ", ".join(overlap)
        )
    if set(names) != set(contract.required_hypothesis_checks):
        raise ValueError(
            "hypothesis checks must match required_hypothesis_checks exactly"
        )
    by_name = {check.name: check for check in values}
    return tuple(by_name[name] for name in contract.required_hypothesis_checks)


def _context_check(
    control: OcrQualityResult,
    candidate: OcrQualityResult,
) -> OcrExperimentCheck:
    mismatches: list[str] = []
    fixed_values = (
        ("evaluator_name", control.evaluator_name, candidate.evaluator_name),
        ("evaluator_version", control.evaluator_version, candidate.evaluator_version),
        ("reference_id", control.reference_id, candidate.reference_id),
        (
            "reference_source_sha256",
            control.reference_source_sha256,
            candidate.reference_source_sha256,
        ),
        (
            "observed_source_sha256",
            control.observed_source_sha256,
            candidate.observed_source_sha256,
        ),
        ("expected_text", control.expected_text, candidate.expected_text),
        (
            "text_accuracy_minimum",
            control.text_character_accuracy.minimum,
            candidate.text_character_accuracy.minimum,
        ),
        (
            "block_coverage_minimum",
            control.logical_block_coverage.minimum,
            candidate.logical_block_coverage.minimum,
        ),
        (
            "anchor_recall_minimum",
            control.essential_anchor_recall.minimum,
            candidate.essential_anchor_recall.minimum,
        ),
        (
            "block_recovery_accuracy_minimum",
            control.block_recovery_accuracy_minimum,
            candidate.block_recovery_accuracy_minimum,
        ),
        (
            "low_confidence_threshold",
            control.low_confidence_threshold,
            candidate.low_confidence_threshold,
        ),
    )
    for name, control_value, candidate_value in fixed_values:
        if control_value != candidate_value:
            mismatches.append(f"mismatch:{name}")

    control_anchor_ids = tuple(item.anchor for item in control.anchors)
    candidate_anchor_ids = tuple(item.anchor for item in candidate.anchors)
    if control_anchor_ids != candidate_anchor_ids:
        mismatches.append("mismatch:essential_anchor_definitions")
    control_blocks = tuple(
        (item.reference_id, item.expected_text, item.essential)
        for item in control.blocks
    )
    candidate_blocks = tuple(
        (item.reference_id, item.expected_text, item.essential)
        for item in candidate.blocks
    )
    if control_blocks != candidate_blocks:
        mismatches.append("mismatch:logical_block_definitions")

    reasons = tuple(mismatches) or (
        "source, reference, evaluator, thresholds, and normalization are identical",
    )
    return OcrExperimentCheck(
        name="source_reference_threshold_normalization",
        passed=not mismatches,
        reasons=reasons,
        details={
            "normalization": _NORMALIZATION_CONTRACT,
            "reference_id": {
                "control": control.reference_id,
                "candidate": candidate.reference_id,
            },
            "source_sha256": {
                "control_reference": control.reference_source_sha256,
                "control_observed": control.observed_source_sha256,
                "candidate_reference": candidate.reference_source_sha256,
                "candidate_observed": candidate.observed_source_sha256,
            },
        },
    )


def _runtime_check(
    control: OcrQualityResult,
    candidate: OcrQualityResult,
    contract: OcrExperimentContract,
) -> OcrExperimentCheck:
    left = control.runtime
    right = candidate.runtime
    values = {
        "provider": (left.provider, right.provider),
        "provider_version": (left.provider_version, right.provider_version),
        "executable": (left.executable, right.executable),
        "languages": (left.languages, right.languages),
        "page_segmentation_mode": (
            left.page_segmentation_mode,
            right.page_segmentation_mode,
        ),
        "engine_mode": (left.engine_mode, right.engine_mode),
        "source_dpi_x": (left.source_dpi_x, right.source_dpi_x),
        "source_dpi_y": (left.source_dpi_y, right.source_dpi_y),
        "effective_ocr_dpi": (
            left.effective_ocr_dpi,
            right.effective_ocr_dpi,
        ),
        "traineddata": (left.traineddata, right.traineddata),
        "operating_system": (left.operating_system, right.operating_system),
        "python_version": (left.python_version, right.python_version),
    }
    if tuple(values) != OCR_EXPERIMENT_RUNTIME_FIELDS:
        raise RuntimeError("OCR experiment runtime field contract drifted")
    allowed = set(contract.allowed_runtime_differences)
    mismatches = [
        f"mismatch:{name}"
        for name, (one, two) in values.items()
        if name not in allowed and one != two
    ]
    reasons = tuple(mismatches) or (
        "runtime is identical except for explicitly allowed experiment fields",
    )
    return OcrExperimentCheck(
        name="runtime_equivalence",
        passed=not mismatches,
        reasons=reasons,
        details={
            "allowed_differences": list(contract.allowed_runtime_differences),
            "control_effective_ocr_dpi": left.effective_ocr_dpi,
            "candidate_effective_ocr_dpi": right.effective_ocr_dpi,
            "compared_fields": list(values),
        },
    )


def _hard_gate_check(
    control: OcrQualityResult,
    candidate: OcrQualityResult,
) -> OcrExperimentCheck:
    failures: list[str] = []
    details: dict[str, object] = {}
    for side, result in (("control", control), ("candidate", candidate)):
        names = [gate.name for gate in result.hard_gates]
        if len(names) != len(set(names)):
            failures.append(f"{side}:duplicate_hard_gate")
        by_name = {gate.name: gate for gate in result.hard_gates}
        statuses: dict[str, str] = {}
        for name in _REQUIRED_INTEGRITY_GATES:
            gate = by_name.get(name)
            if gate is None:
                statuses[name] = "missing"
                failures.append(f"{side}:missing:{name}")
            else:
                statuses[name] = (
                    "unknown"
                    if gate.passed is None
                    else ("pass" if gate.passed else "fail")
                )
                if gate.passed is not True:
                    failures.append(f"{side}:not_passed:{name}")
        details[side] = statuses
    reasons = tuple(failures) or (
        "source digest, reviewed reference, and page count gates pass on both runs",
    )
    return OcrExperimentCheck(
        name="ocr_quality_integrity_hard_gates",
        passed=not failures,
        reasons=reasons,
        details=details,
    )


def _geometry_check(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
    contract: OcrExperimentContract,
) -> OcrExperimentCheck:
    failures: list[str] = []
    control_allowed_regions, control_require_region_ref = _evidence_regions(
        control.evidence
    )
    if "region_plan" in contract.allowed_geometry_differences:
        candidate_allowed_regions, candidate_require_region_ref = _evidence_regions(
            candidate.evidence
        )
    else:
        candidate_allowed_regions = control_allowed_regions
        candidate_require_region_ref = control_require_region_ref
    control_allowed_region_refs = set(control_allowed_regions)
    candidate_allowed_region_refs = set(candidate_allowed_regions)
    _evidence_crops_inside_page("control", control.evidence, control.document, failures)
    _evidence_crops_inside_page(
        "candidate", candidate.evidence, candidate.document, failures
    )
    control_region_refs, control_parameter_digests = _geometry_issues(
        "control",
        control.document,
        runtime_provider=control.quality.runtime.provider,
        runtime_provider_version=control.quality.runtime.provider_version,
        allowed_region_refs=control_allowed_region_refs,
        allowed_regions=control_allowed_regions,
        require_region_ref=control_require_region_ref,
        failures=failures,
    )
    candidate_region_refs, candidate_parameter_digests = _geometry_issues(
        "candidate",
        candidate.document,
        runtime_provider=candidate.quality.runtime.provider,
        runtime_provider_version=candidate.quality.runtime.provider_version,
        allowed_region_refs=candidate_allowed_region_refs,
        allowed_regions=candidate_allowed_regions,
        require_region_ref=candidate_require_region_ref,
        failures=failures,
    )
    if control_parameter_digests & candidate_parameter_digests:
        failures.append("candidate:parameters_digest_does_not_identify_hypothesis")
    reasons = tuple(failures) or (
        "OCR provenance bboxes match source-pixel text geometry and remain in page bounds",
    )
    return OcrExperimentCheck(
        name="source_geometry_and_provenance_integrity",
        passed=not failures,
        reasons=reasons,
        details={
            "inverse_coordinate_space": "original_png_source_pixels",
            "allowed_geometry_differences": list(contract.allowed_geometry_differences),
            "control_allowed_region_refs": sorted(control_allowed_region_refs),
            "candidate_allowed_region_refs": sorted(candidate_allowed_region_refs),
            "control_region_refs": sorted(control_region_refs),
            "candidate_region_refs": sorted(candidate_region_refs),
            "parent_region_refs_come_from_evidence_crops": True,
            "control_parameters_digests": sorted(control_parameter_digests),
            "candidate_parameters_digests": sorted(candidate_parameter_digests),
            "experiment_conditions_must_change_parameters_digest": True,
        },
    )


def _geometry_issues(
    side: str,
    document: DocumentIR,
    *,
    runtime_provider: str,
    runtime_provider_version: str,
    allowed_region_refs: set[str] | None,
    allowed_regions: Mapping[str, tuple[int, int, int, int]],
    require_region_ref: bool,
    failures: list[str],
) -> tuple[set[str], set[str]]:
    try:
        validate_document(document)
    except DocumentIRValidationError:
        failures.append(f"{side}:document_ir_invalid")
    collected_refs: set[str] = set()
    parameter_digests: set[str] = set()
    text_count = 0
    for page in document.pages:
        if page.source is None:
            failures.append(f"{side}:page:{page.number}:source_missing")
            continue
        for element in page.elements:
            if not isinstance(element, TextElement):
                continue
            text_count += 1
            ocr_records = tuple(
                record
                for record in element.provenance
                if record.stage is ProvenanceStage.OCR
            )
            if not ocr_records:
                failures.append(f"{side}:text:{element.id}:ocr_provenance_missing")
                continue
            matching_bbox = False
            for record in ocr_records:
                if record.provider != runtime_provider:
                    failures.append(f"{side}:text:{element.id}:provider_mismatch")
                if record.provider_version != runtime_provider_version:
                    failures.append(
                        f"{side}:text:{element.id}:provider_version_mismatch"
                    )
                if record.parameters_digest is None:
                    failures.append(
                        f"{side}:text:{element.id}:parameters_digest_missing"
                    )
                else:
                    parameter_digests.add(record.parameters_digest)
                source_bbox = record.source_bbox_px
                if source_bbox is None:
                    failures.append(f"{side}:text:{element.id}:source_bbox_missing")
                    continue
                if (
                    source_bbox.x + source_bbox.width > page.source.pixel_width
                    or source_bbox.y + source_bbox.height > page.source.pixel_height
                ):
                    failures.append(
                        f"{side}:text:{element.id}:source_bbox_outside_page"
                    )
                left = round(source_bbox.x * 72 / page.source.dpi_x, 6)
                top = round(source_bbox.y * 72 / page.source.dpi_y, 6)
                right = round(
                    (source_bbox.x + source_bbox.width) * 72 / page.source.dpi_x,
                    6,
                )
                bottom = round(
                    (source_bbox.y + source_bbox.height) * 72 / page.source.dpi_y,
                    6,
                )
                expected = (
                    left,
                    top,
                    round(right - left, 6),
                    round(bottom - top, 6),
                )
                actual = (
                    element.bbox.x,
                    element.bbox.y,
                    element.bbox.width,
                    element.bbox.height,
                )
                if all(
                    math.isclose(one, two, abs_tol=1e-6)
                    for one, two in zip(expected, actual)
                ):
                    matching_bbox = True
                if len(record.source_refs) > 1:
                    failures.append(f"{side}:text:{element.id}:multiple_parent_regions")
                collected_refs.update(record.source_refs)
                if allowed_region_refs is not None and any(
                    ref not in allowed_region_refs for ref in record.source_refs
                ):
                    failures.append(f"{side}:text:{element.id}:unknown_parent_region")
                if require_region_ref and not record.source_refs:
                    failures.append(f"{side}:text:{element.id}:parent_region_missing")
                if len(record.source_refs) == 1:
                    region_bbox = allowed_regions.get(record.source_refs[0])
                    if region_bbox is not None and not _pixel_bbox_inside(
                        source_bbox.x,
                        source_bbox.y,
                        source_bbox.width,
                        source_bbox.height,
                        region_bbox,
                    ):
                        failures.append(
                            f"{side}:text:{element.id}:outside_parent_region"
                        )
            if not matching_bbox:
                failures.append(f"{side}:text:{element.id}:bbox_provenance_mismatch")
    if text_count == 0:
        failures.append(f"{side}:ocr_text_elements_missing")
    return collected_refs, parameter_digests


def _evidence_regions(
    evidence: Mapping[str, object],
) -> tuple[dict[str, tuple[int, int, int, int]], bool]:
    crops = evidence.get("crops")
    if not isinstance(crops, Sequence) or isinstance(crops, (str, bytes, bytearray)):
        return {}, False
    regions: dict[str, tuple[int, int, int, int]] = {}
    refs: list[object] = []
    for crop in crops:
        if not isinstance(crop, Mapping):
            continue
        region_ref = crop.get("region_ref")
        refs.append(region_ref)
        bbox = crop.get("source_bbox")
        if isinstance(region_ref, str) and region_ref and isinstance(bbox, Mapping):
            values = (
                bbox.get("x"),
                bbox.get("y"),
                bbox.get("width"),
                bbox.get("height"),
            )
            if all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in values
            ):
                regions[region_ref] = values
    return regions, bool(refs) and len(regions) == len(refs)


def _evidence_crops_inside_page(
    side: str,
    evidence: Mapping[str, object],
    document: DocumentIR,
    failures: list[str],
) -> None:
    if len(document.pages) != 1 or document.pages[0].source is None:
        return
    source = document.pages[0].source
    crops = evidence.get("crops")
    if not isinstance(crops, Sequence) or isinstance(crops, (str, bytes, bytearray)):
        return
    for index, crop in enumerate(crops):
        if not isinstance(crop, Mapping):
            continue
        bbox = crop.get("source_bbox")
        if not isinstance(bbox, Mapping):
            continue
        values = (
            bbox.get("x"),
            bbox.get("y"),
            bbox.get("width"),
            bbox.get("height"),
        )
        if not all(
            isinstance(value, int) and not isinstance(value, bool) for value in values
        ):
            continue
        if not _pixel_bbox_inside(
            *values,
            (0, 0, source.pixel_width, source.pixel_height),
        ):
            failures.append(f"{side}:evidence_crop:{index}:outside_page")
        if crop.get("region_ref") is None and values != (
            0,
            0,
            source.pixel_width,
            source.pixel_height,
        ):
            failures.append(f"{side}:evidence_crop:{index}:full_page_bbox")


def _pixel_bbox_inside(
    x: int,
    y: int,
    width: int,
    height: int,
    container: tuple[int, int, int, int],
) -> bool:
    container_x, container_y, container_width, container_height = container
    return (
        x >= container_x
        and y >= container_y
        and x + width <= container_x + container_width
        and y + height <= container_y + container_height
    )


def _nontext_check(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
) -> OcrExperimentCheck:
    failures: list[str] = []
    control_document = control.document
    candidate_document = candidate.document
    if control_document.ir_version != candidate_document.ir_version:
        failures.append("mismatch:ir_version")
    if control_document.document_id != candidate_document.document_id:
        failures.append("mismatch:document_id")
    if control_document.generator != candidate_document.generator:
        failures.append("mismatch:generator")
    if control_document.metadata != candidate_document.metadata:
        failures.append("mismatch:document_metadata")
    if control_document.extensions != candidate_document.extensions:
        failures.append("mismatch:document_extensions")
    if control_document.assets != candidate_document.assets:
        failures.append("mismatch:assets")
    control_source_digest = _document_source_digest(control_document)
    candidate_source_digest = _document_source_digest(candidate_document)
    if control_source_digest != control.quality.observed_source_sha256:
        failures.append("control:document_source_digest")
    if candidate_source_digest != candidate.quality.observed_source_sha256:
        failures.append("candidate:document_source_digest")
    if len(control_document.pages) != len(candidate_document.pages):
        failures.append("mismatch:page_count")
    for index, (left, right) in enumerate(
        zip(control_document.pages, candidate_document.pages)
    ):
        if left.id != right.id or left.number != right.number:
            failures.append(f"mismatch:page_identity:{index}")
        if left.size != right.size:
            failures.append(f"mismatch:page_size:{index}")
        if left.source != right.source:
            failures.append(f"mismatch:page_source:{index}")
        if left.extensions != right.extensions:
            failures.append(f"mismatch:page_extensions:{index}")
        left_nontext = tuple(
            element for element in left.elements if not isinstance(element, TextElement)
        )
        right_nontext = tuple(
            element
            for element in right.elements
            if not isinstance(element, TextElement)
        )
        if left_nontext != right_nontext:
            failures.append(f"mismatch:line_rectangle_image_elements:{index}")
    reasons = tuple(failures) or (
        "document identity, PageSource, PageSize, non-text elements, and assets are identical",
    )
    return OcrExperimentCheck(
        name="non_text_document_ir_integrity",
        passed=not failures,
        reasons=reasons,
        details={
            "compared": [
                "ir_version",
                "document_id",
                "generator",
                "source_sha256",
                "document_metadata_and_extensions",
                "PageSource",
                "PageSize",
                "page_extensions",
                "line",
                "rectangle",
                "image",
                "asset",
            ]
        },
    )


def _document_source_digest(document: DocumentIR) -> object:
    extension = document.extensions.get("jp.reactorfront.aiteqno.extract")
    if not isinstance(extension, Mapping):
        return None
    return extension.get("source_sha256")


def _metric_delta(control: float, candidate: float) -> OcrExperimentMetricDelta:
    return OcrExperimentMetricDelta(
        control=control,
        candidate=candidate,
        delta=round(candidate - control, 6),
    )


def _anchor_recovery(
    control: OcrQualityResult,
    candidate: OcrQualityResult,
) -> OcrExperimentRecoveryDelta:
    order = tuple(item.anchor for item in control.anchors)
    control_recovered = tuple(item.anchor for item in control.anchors if item.recovered)
    candidate_set = {item.anchor for item in candidate.anchors if item.recovered}
    candidate_recovered = tuple(value for value in order if value in candidate_set)
    control_set = set(control_recovered)
    return OcrExperimentRecoveryDelta(
        control_recovered=control_recovered,
        candidate_recovered=candidate_recovered,
        gained=tuple(value for value in order if value in candidate_set - control_set),
        lost=tuple(value for value in order if value in control_set - candidate_set),
    )


def _block_recovery(
    control: OcrQualityResult,
    candidate: OcrQualityResult,
) -> OcrExperimentRecoveryDelta:
    order = tuple(item.reference_id for item in control.blocks)
    control_recovered = tuple(
        item.reference_id for item in control.blocks if item.recovered
    )
    candidate_set = {item.reference_id for item in candidate.blocks if item.recovered}
    candidate_recovered = tuple(value for value in order if value in candidate_set)
    control_set = set(control_recovered)
    return OcrExperimentRecoveryDelta(
        control_recovered=control_recovered,
        candidate_recovered=candidate_recovered,
        gained=tuple(value for value in order if value in candidate_set - control_set),
        lost=tuple(value for value in order if value in control_set - candidate_set),
    )


def _unrecovered_essential_blocks(result: OcrQualityResult) -> tuple[str, ...]:
    return tuple(
        block.reference_id
        for block in result.blocks
        if block.essential and not block.recovered
    )


def _json_sha256(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _finite_non_negative(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return result


__all__ = [
    "DEFAULT_MINIMUM_TEXT_ACCURACY_DELTA",
    "compare_ocr_experiment",
]
