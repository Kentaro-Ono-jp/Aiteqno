"""Pure same-runtime A/B decision for the 300 DPI OCR-input experiment."""

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
from aiteqno.ports import (
    OcrQualityResult,
    OcrResolutionCheck,
    OcrResolutionComparisonResult,
    OcrResolutionDecision,
    OcrResolutionMetricDelta,
    OcrResolutionRecoveryDelta,
    OcrResolutionRun,
)


OCR_RESOLUTION_EVALUATOR_NAME = "aiteqno-ocr-resolution-comparison"
OCR_RESOLUTION_EVALUATOR_VERSION = "1.0.0"
DEFAULT_MINIMUM_TEXT_ACCURACY_DELTA = 1.0
_REQUIRED_INTEGRITY_GATES = (
    "source_digest_matches_reference",
    "reference_reviewed",
    "candidate_page_count",
)
_NORMALIZATION_CONTRACT = "NFKC then remove every Unicode whitespace character"
_EXPECTED_TRANSFORM_VERSION = "tesseract-raster-transform-v1"
_EXPECTED_TARGET_DPI = 300
_EXPECTED_MAX_WORKING_PIXELS = 40_000_000
_EXPECTED_INVERSE_MAPPING_POLICY = (
    "clip-working-bbox; source-left-top=floor(edge*source/working); "
    "source-right-bottom=ceil(edge*source/working); clamp-source-crop; "
    "add-source-offset"
)


def compare_ocr_resolution(
    control: OcrResolutionRun,
    candidate: OcrResolutionRun,
    *,
    minimum_text_accuracy_delta: float = DEFAULT_MINIMUM_TEXT_ACCURACY_DELTA,
) -> OcrResolutionComparisonResult:
    """Compare two completed OCR-only runs without rescoring either report.

    The quality evaluator, its reference, thresholds, and normalization remain
    immutable inputs.  This function only establishes A/B comparability,
    verifies source-coordinate/non-text integrity, and applies Issue #47's
    adoption rules to the already-produced OCR-quality results.
    """

    if not isinstance(control, OcrResolutionRun):
        raise TypeError("control must be an OcrResolutionRun")
    if not isinstance(candidate, OcrResolutionRun):
        raise TypeError("candidate must be an OcrResolutionRun")
    minimum_delta = _finite_non_negative(
        minimum_text_accuracy_delta,
        "minimum_text_accuracy_delta",
    )

    context_check = _context_check(control.quality, candidate.quality)
    runtime_check = _runtime_check(control.quality, candidate.quality)
    hard_gate_check = _hard_gate_check(control.quality, candidate.quality)
    transform_check = _transform_check(control, candidate)
    geometry_check = _geometry_check(control, candidate)
    nontext_check = _nontext_check(control, candidate)
    checks = (
        context_check,
        runtime_check,
        hard_gate_check,
        transform_check,
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
        decision = OcrResolutionDecision.INVALID
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
            decision = OcrResolutionDecision.REGRESSED
            reasons = tuple(regression_reasons)
        elif text_delta.delta < minimum_delta:
            decision = OcrResolutionDecision.INCONCLUSIVE
            reasons = (
                "text_accuracy_delta_below_minimum:"
                f"{text_delta.delta:g}<{minimum_delta:g}",
            )
        else:
            decision = OcrResolutionDecision.SUPPORTED
            reasons = ("all_ocr_resolution_adoption_conditions_pass",)

    return OcrResolutionComparisonResult(
        evaluator_name=OCR_RESOLUTION_EVALUATOR_NAME,
        evaluator_version=OCR_RESOLUTION_EVALUATOR_VERSION,
        minimum_text_accuracy_delta=minimum_delta,
        control_quality_state=control.quality.state.value,
        candidate_quality_state=candidate.quality.state.value,
        control_effective_ocr_dpi=control.quality.runtime.effective_ocr_dpi,
        candidate_effective_ocr_dpi=candidate.quality.runtime.effective_ocr_dpi,
        control_transform_sha256=_json_sha256(control.transform),
        candidate_transform_sha256=_json_sha256(candidate.transform),
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


def _context_check(
    control: OcrQualityResult,
    candidate: OcrQualityResult,
) -> OcrResolutionCheck:
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
    return OcrResolutionCheck(
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
) -> OcrResolutionCheck:
    left = control.runtime
    right = candidate.runtime
    values = (
        ("provider", left.provider, right.provider),
        ("provider_version", left.provider_version, right.provider_version),
        ("executable", left.executable, right.executable),
        ("languages", left.languages, right.languages),
        (
            "page_segmentation_mode",
            left.page_segmentation_mode,
            right.page_segmentation_mode,
        ),
        ("engine_mode", left.engine_mode, right.engine_mode),
        ("source_dpi_x", left.source_dpi_x, right.source_dpi_x),
        ("source_dpi_y", left.source_dpi_y, right.source_dpi_y),
        ("traineddata", left.traineddata, right.traineddata),
        ("operating_system", left.operating_system, right.operating_system),
        ("python_version", left.python_version, right.python_version),
    )
    mismatches = [f"mismatch:{name}" for name, one, two in values if one != two]
    reasons = tuple(mismatches) or (
        "runtime is identical except for the allowed effective OCR DPI",
    )
    return OcrResolutionCheck(
        name="runtime_equivalence",
        passed=not mismatches,
        reasons=reasons,
        details={
            "allowed_differences": [
                "runtime.configuration.effective_ocr_dpi",
                "ocr_input_transform",
            ],
            "control_effective_ocr_dpi": left.effective_ocr_dpi,
            "candidate_effective_ocr_dpi": right.effective_ocr_dpi,
            "compared_fields": [name for name, _, _ in values],
        },
    )


def _hard_gate_check(
    control: OcrQualityResult,
    candidate: OcrQualityResult,
) -> OcrResolutionCheck:
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
    return OcrResolutionCheck(
        name="ocr_quality_integrity_hard_gates",
        passed=not failures,
        reasons=reasons,
        details=details,
    )


def _transform_check(
    control: OcrResolutionRun,
    candidate: OcrResolutionRun,
) -> OcrResolutionCheck:
    left = control.transform
    right = candidate.transform
    failures: list[str] = []

    for side, evidence in (("control", left), ("candidate", right)):
        if evidence.get("schema_version") != "1.0":
            failures.append(f"{side}:schema_version")
        if evidence.get("transform_version") != _EXPECTED_TRANSFORM_VERSION:
            failures.append(f"{side}:transform_version")
        if evidence.get("pixel_mode") != "RGB":
            failures.append(f"{side}:pixel_mode")
        if evidence.get("max_working_pixels") != _EXPECTED_MAX_WORKING_PIXELS:
            failures.append(f"{side}:max_working_pixels")
        if evidence.get("inverse_mapping_policy") != _EXPECTED_INVERSE_MAPPING_POLICY:
            failures.append(f"{side}:inverse_mapping_policy")
        imaging = evidence.get("imaging_library")
        if (
            not isinstance(imaging, Mapping)
            or imaging.get("name") != "Pillow"
            or not isinstance(imaging.get("version"), str)
            or not imaging.get("version")
        ):
            failures.append(f"{side}:imaging_library")
        if not _positive_number(evidence.get("source_effective_dpi")):
            failures.append(f"{side}:source_effective_dpi")
        _validate_crops(side, evidence, failures)

    expected_control_source_dpi = round(
        (control.quality.runtime.source_dpi_x + control.quality.runtime.source_dpi_y)
        / 2.0,
        6,
    )
    expected_candidate_source_dpi = round(
        (
            candidate.quality.runtime.source_dpi_x
            + candidate.quality.runtime.source_dpi_y
        )
        / 2.0,
        6,
    )
    if left.get("source_effective_dpi") != expected_control_source_dpi:
        failures.append("control:source_effective_dpi_runtime_mismatch")
    if right.get("source_effective_dpi") != expected_candidate_source_dpi:
        failures.append("candidate:source_effective_dpi_runtime_mismatch")

    if left.get("enabled") is not False:
        failures.append("control:enabled")
    if left.get("target_dpi") is not None:
        failures.append("control:target_dpi")
    if left.get("resampling") != "none":
        failures.append("control:resampling")
    expected_control_effective_dpi = max(1, int(round(expected_control_source_dpi)))
    if left.get("effective_ocr_dpi") != expected_control_effective_dpi:
        failures.append("control:effective_ocr_dpi")
    if left.get("effective_ocr_dpi") != control.quality.runtime.effective_ocr_dpi:
        failures.append("control:effective_ocr_dpi_runtime_mismatch")

    if right.get("enabled") is not True:
        failures.append("candidate:enabled")
    if right.get("target_dpi") != _EXPECTED_TARGET_DPI:
        failures.append("candidate:target_dpi")
    expected_candidate_effective_dpi = (
        _EXPECTED_TARGET_DPI
        if expected_candidate_source_dpi < _EXPECTED_TARGET_DPI
        else max(1, int(round(expected_candidate_source_dpi)))
    )
    if right.get("effective_ocr_dpi") != expected_candidate_effective_dpi:
        failures.append("candidate:effective_ocr_dpi")
    if right.get("resampling") != "LANCZOS":
        failures.append("candidate:resampling")
    if right.get("effective_ocr_dpi") != candidate.quality.runtime.effective_ocr_dpi:
        failures.append("candidate:effective_ocr_dpi_runtime_mismatch")

    for field_name in (
        "transform_version",
        "source_effective_dpi",
        "max_working_pixels",
        "pixel_mode",
        "imaging_library",
        "inverse_mapping_policy",
    ):
        if left.get(field_name) != right.get(field_name):
            failures.append(f"mismatch:{field_name}")
    _compare_crop_sources(left.get("crops"), right.get("crops"), failures)

    reasons = tuple(dict.fromkeys(failures)) or (
        "control is source-resolution and candidate is the fixed 300 DPI LANCZOS transform",
    )
    return OcrResolutionCheck(
        name="transform_integrity",
        passed=not failures,
        reasons=reasons,
        details={
            "control_sha256": _json_sha256(left),
            "candidate_sha256": _json_sha256(right),
            "control_effective_ocr_dpi": left.get("effective_ocr_dpi"),
            "candidate_effective_ocr_dpi": right.get("effective_ocr_dpi"),
            "candidate_target_dpi": right.get("target_dpi"),
        },
    )


def _validate_crops(
    side: str,
    evidence: Mapping[str, object],
    failures: list[str],
) -> None:
    crops = evidence.get("crops")
    if not isinstance(crops, Sequence) or isinstance(crops, (str, bytes, bytearray)):
        failures.append(f"{side}:crops")
        return
    if not crops:
        failures.append(f"{side}:crops_empty")
        return
    max_pixels = evidence.get("max_working_pixels")
    enabled = evidence.get("enabled")
    target_dpi = evidence.get("target_dpi")
    source_effective_dpi = evidence.get("source_effective_dpi")
    region_refs: list[str | None] = []
    for index, crop in enumerate(crops):
        prefix = f"{side}:crop:{index}"
        if not isinstance(crop, Mapping):
            failures.append(prefix)
            continue
        region_ref = crop.get("region_ref")
        if region_ref is not None and (
            not isinstance(region_ref, str) or not region_ref
        ):
            failures.append(f"{prefix}:region_ref")
        else:
            region_refs.append(region_ref)
        source_dimensions = crop.get("source_dimensions")
        working_dimensions = crop.get("working_dimensions")
        actual_scale = crop.get("actual_scale")
        source_bbox = crop.get("source_bbox")
        if not all(
            isinstance(value, Mapping)
            for value in (
                source_dimensions,
                working_dimensions,
                actual_scale,
                source_bbox,
            )
        ):
            failures.append(f"{prefix}:shape")
            continue
        source_width = _positive_int(source_dimensions.get("width"))
        source_height = _positive_int(source_dimensions.get("height"))
        working_width = _positive_int(working_dimensions.get("width"))
        working_height = _positive_int(working_dimensions.get("height"))
        if None in (source_width, source_height, working_width, working_height):
            failures.append(f"{prefix}:dimensions")
            continue
        bbox_values = (
            _non_negative_int(source_bbox.get("x")),
            _non_negative_int(source_bbox.get("y")),
            _positive_int(source_bbox.get("width")),
            _positive_int(source_bbox.get("height")),
        )
        if None in bbox_values:
            failures.append(f"{prefix}:source_bbox")
        if (
            source_bbox.get("width") != source_width
            or source_bbox.get("height") != source_height
        ):
            failures.append(f"{prefix}:source_bbox_dimensions")
        if isinstance(max_pixels, int) and working_width * working_height > max_pixels:
            failures.append(f"{prefix}:working_pixel_limit")
        scale = (
            target_dpi / source_effective_dpi
            if enabled is True
            and isinstance(target_dpi, int)
            and _positive_number(source_effective_dpi)
            and source_effective_dpi < target_dpi
            else 1.0
        )
        expected_working_width = max(1, math.floor(source_width * scale + 0.5))
        expected_working_height = max(1, math.floor(source_height * scale + 0.5))
        if (
            working_width != expected_working_width
            or working_height != expected_working_height
        ):
            failures.append(f"{prefix}:deterministic_working_dimensions")
        expected_scale_x = working_width / source_width
        expected_scale_y = working_height / source_height
        if not _same_number(actual_scale.get("x"), expected_scale_x):
            failures.append(f"{prefix}:actual_scale_x")
        if not _same_number(actual_scale.get("y"), expected_scale_y):
            failures.append(f"{prefix}:actual_scale_y")
        resized = crop.get("resized")
        expected_resized = (
            source_width != working_width or source_height != working_height
        )
        if resized is not expected_resized:
            failures.append(f"{prefix}:resized")
        digest = crop.get("working_raster_sha256")
        if not _is_sha256(digest):
            failures.append(f"{prefix}:working_raster_sha256")
    if any(ref is None for ref in region_refs) and any(
        isinstance(ref, str) for ref in region_refs
    ):
        failures.append(f"{side}:mixed_full_page_and_region_crops")
    concrete_refs = [ref for ref in region_refs if isinstance(ref, str)]
    if len(concrete_refs) != len(set(concrete_refs)):
        failures.append(f"{side}:duplicate_region_refs")


def _compare_crop_sources(
    control_crops: object,
    candidate_crops: object,
    failures: list[str],
) -> None:
    if not isinstance(control_crops, Sequence) or isinstance(
        control_crops, (str, bytes, bytearray)
    ):
        return
    if not isinstance(candidate_crops, Sequence) or isinstance(
        candidate_crops, (str, bytes, bytearray)
    ):
        return
    if len(control_crops) != len(candidate_crops):
        failures.append("mismatch:crop_count")
        return
    for index, (left, right) in enumerate(zip(control_crops, candidate_crops)):
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            continue
        for name in ("region_ref", "source_bbox", "source_dimensions"):
            if left.get(name) != right.get(name):
                failures.append(f"mismatch:crop:{index}:{name}")


def _geometry_check(
    control: OcrResolutionRun,
    candidate: OcrResolutionRun,
) -> OcrResolutionCheck:
    failures: list[str] = []
    allowed_regions, require_region_ref = _transform_regions(control.transform)
    allowed_region_refs = set(allowed_regions)
    _transform_crops_inside_page(
        "control", control.transform, control.document, failures
    )
    _transform_crops_inside_page(
        "candidate", candidate.transform, candidate.document, failures
    )
    control_region_refs, control_parameter_digests = _geometry_issues(
        "control",
        control.document,
        runtime_provider=control.quality.runtime.provider,
        runtime_provider_version=control.quality.runtime.provider_version,
        allowed_region_refs=allowed_region_refs,
        allowed_regions=allowed_regions,
        require_region_ref=require_region_ref,
        failures=failures,
    )
    candidate_region_refs, candidate_parameter_digests = _geometry_issues(
        "candidate",
        candidate.document,
        runtime_provider=candidate.quality.runtime.provider,
        runtime_provider_version=candidate.quality.runtime.provider_version,
        allowed_region_refs=allowed_region_refs,
        allowed_regions=allowed_regions,
        require_region_ref=require_region_ref,
        failures=failures,
    )
    if control_parameter_digests & candidate_parameter_digests:
        failures.append("candidate:parameters_digest_does_not_identify_transform")
    reasons = tuple(failures) or (
        "OCR provenance bboxes match source-pixel text geometry and remain in page bounds",
    )
    return OcrResolutionCheck(
        name="source_geometry_and_provenance_integrity",
        passed=not failures,
        reasons=reasons,
        details={
            "inverse_coordinate_space": "original_png_source_pixels",
            "allowed_region_refs": sorted(allowed_region_refs),
            "control_region_refs": sorted(control_region_refs),
            "candidate_region_refs": sorted(candidate_region_refs),
            "parent_region_refs_come_from_structure_crops": True,
            "control_parameters_digests": sorted(control_parameter_digests),
            "candidate_parameters_digests": sorted(candidate_parameter_digests),
            "transform_conditions_must_change_parameters_digest": True,
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
    observed_ref_presence: list[bool] = []
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
                # Extraction converts both pixel edges independently before it
                # subtracts them.  Reproduce that canonical conversion exactly;
                # converting width/height directly differs by one micro-point
                # for real PNG DPI values such as 96.012.
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
                observed_ref_presence.append(bool(record.source_refs))
                collected_refs.update(record.source_refs)
                if allowed_region_refs is not None and any(
                    ref not in allowed_region_refs for ref in record.source_refs
                ):
                    failures.append(f"{side}:text:{element.id}:unknown_parent_region")
                if require_region_ref and not record.source_refs:
                    failures.append(f"{side}:text:{element.id}:parent_region_missing")
                if len(record.source_refs) == 1 and source_bbox is not None:
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


def _transform_regions(
    transform: Mapping[str, object],
) -> tuple[dict[str, tuple[int, int, int, int]], bool]:
    crops = transform.get("crops")
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


def _transform_crops_inside_page(
    side: str,
    transform: Mapping[str, object],
    document: DocumentIR,
    failures: list[str],
) -> None:
    if len(document.pages) != 1 or document.pages[0].source is None:
        return
    source = document.pages[0].source
    crops = transform.get("crops")
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
            failures.append(f"{side}:transform_crop:{index}:outside_page")
        if crop.get("region_ref") is None and values != (
            0,
            0,
            source.pixel_width,
            source.pixel_height,
        ):
            failures.append(f"{side}:transform_crop:{index}:full_page_bbox")


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
    control: OcrResolutionRun,
    candidate: OcrResolutionRun,
) -> OcrResolutionCheck:
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
    return OcrResolutionCheck(
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


def _metric_delta(control: float, candidate: float) -> OcrResolutionMetricDelta:
    return OcrResolutionMetricDelta(
        control=control,
        candidate=candidate,
        delta=round(candidate - control, 6),
    )


def _anchor_recovery(
    control: OcrQualityResult,
    candidate: OcrQualityResult,
) -> OcrResolutionRecoveryDelta:
    order = tuple(item.anchor for item in control.anchors)
    control_recovered = tuple(item.anchor for item in control.anchors if item.recovered)
    candidate_set = {item.anchor for item in candidate.anchors if item.recovered}
    candidate_recovered = tuple(value for value in order if value in candidate_set)
    control_set = set(control_recovered)
    return OcrResolutionRecoveryDelta(
        control_recovered=control_recovered,
        candidate_recovered=candidate_recovered,
        gained=tuple(value for value in order if value in candidate_set - control_set),
        lost=tuple(value for value in order if value in control_set - candidate_set),
    )


def _block_recovery(
    control: OcrQualityResult,
    candidate: OcrQualityResult,
) -> OcrResolutionRecoveryDelta:
    order = tuple(item.reference_id for item in control.blocks)
    control_recovered = tuple(
        item.reference_id for item in control.blocks if item.recovered
    )
    candidate_set = {item.reference_id for item in candidate.blocks if item.recovered}
    candidate_recovered = tuple(value for value in order if value in candidate_set)
    control_set = set(control_recovered)
    return OcrResolutionRecoveryDelta(
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


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _same_number(value: object, expected: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and math.isclose(float(value), expected, rel_tol=1e-9, abs_tol=1e-12)
    )


def _positive_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = [
    "DEFAULT_MINIMUM_TEXT_ACCURACY_DELTA",
    "OCR_RESOLUTION_EVALUATOR_NAME",
    "OCR_RESOLUTION_EVALUATOR_VERSION",
    "compare_ocr_resolution",
]
