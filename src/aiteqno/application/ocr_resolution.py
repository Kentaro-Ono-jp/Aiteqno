"""Pure same-runtime A/B decision for the 300 DPI OCR-input experiment."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from aiteqno.ports import (
    OcrExperimentCheck,
    OcrExperimentContract,
    OcrExperimentRun,
    OcrResolutionCheck,
    OcrResolutionComparisonResult,
    OcrResolutionDecision,
    OcrResolutionMetricDelta,
    OcrResolutionRecoveryDelta,
    OcrResolutionRun,
)

from .ocr_experiment import compare_ocr_experiment


OCR_RESOLUTION_EVALUATOR_NAME = "aiteqno-ocr-resolution-comparison"
OCR_RESOLUTION_EVALUATOR_VERSION = "1.0.0"
DEFAULT_MINIMUM_TEXT_ACCURACY_DELTA = 1.0
_EXPECTED_TRANSFORM_VERSION = "tesseract-raster-transform-v1"
_EXPECTED_TARGET_DPI = 300
_EXPECTED_MAX_WORKING_PIXELS = 40_000_000
_EXPECTED_INVERSE_MAPPING_POLICY = (
    "clip-working-bbox; source-left-top=floor(edge*source/working); "
    "source-right-bottom=ceil(edge*source/working); clamp-source-crop; "
    "add-source-offset"
)
_RESOLUTION_EXPERIMENT_CONTRACT = OcrExperimentContract(
    experiment_id="tesseract_ocr_input_resolution",
    control_label="source_resolution",
    candidate_label="300_dpi_working_raster",
    evaluator_name=OCR_RESOLUTION_EVALUATOR_NAME,
    evaluator_version=OCR_RESOLUTION_EVALUATOR_VERSION,
    required_hypothesis_checks=("transform_integrity",),
    allowed_runtime_differences=("effective_ocr_dpi",),
    supported_reason="all_ocr_resolution_adoption_conditions_pass",
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
    transform_check = _transform_check(control, candidate)
    common = compare_ocr_experiment(
        OcrExperimentRun(
            quality=control.quality,
            document=control.document,
            evidence=control.transform,
        ),
        OcrExperimentRun(
            quality=candidate.quality,
            document=candidate.document,
            evidence=candidate.transform,
        ),
        contract=_RESOLUTION_EXPERIMENT_CONTRACT,
        hypothesis_checks=(
            OcrExperimentCheck(
                name=transform_check.name,
                passed=transform_check.passed,
                reasons=transform_check.reasons,
                details=transform_check.details,
            ),
        ),
        minimum_text_accuracy_delta=minimum_text_accuracy_delta,
    )

    text_delta = OcrResolutionMetricDelta(
        control=common.text_character_accuracy.control,
        candidate=common.text_character_accuracy.candidate,
        delta=common.text_character_accuracy.delta,
    )
    block_delta = OcrResolutionMetricDelta(
        control=common.logical_block_coverage.control,
        candidate=common.logical_block_coverage.candidate,
        delta=common.logical_block_coverage.delta,
    )
    anchor_delta = OcrResolutionMetricDelta(
        control=common.essential_anchor_recall.control,
        candidate=common.essential_anchor_recall.candidate,
        delta=common.essential_anchor_recall.delta,
    )
    anchor_recovery = OcrResolutionRecoveryDelta(
        control_recovered=common.anchors.control_recovered,
        candidate_recovered=common.anchors.candidate_recovered,
        gained=common.anchors.gained,
        lost=common.anchors.lost,
    )
    block_recovery = OcrResolutionRecoveryDelta(
        control_recovered=common.blocks.control_recovered,
        candidate_recovered=common.blocks.candidate_recovered,
        gained=common.blocks.gained,
        lost=common.blocks.lost,
    )
    checks = _resolution_compatibility_checks(common.checks)

    return OcrResolutionComparisonResult(
        evaluator_name=OCR_RESOLUTION_EVALUATOR_NAME,
        evaluator_version=OCR_RESOLUTION_EVALUATOR_VERSION,
        minimum_text_accuracy_delta=common.minimum_text_accuracy_delta,
        control_quality_state=control.quality.state.value,
        candidate_quality_state=candidate.quality.state.value,
        control_effective_ocr_dpi=control.quality.runtime.effective_ocr_dpi,
        candidate_effective_ocr_dpi=candidate.quality.runtime.effective_ocr_dpi,
        control_transform_sha256=common.control_evidence_sha256,
        candidate_transform_sha256=common.candidate_evidence_sha256,
        text_character_accuracy=text_delta,
        logical_block_coverage=block_delta,
        essential_anchor_recall=anchor_delta,
        anchors=anchor_recovery,
        blocks=block_recovery,
        control_unrecovered_essential_blocks=(
            common.control_unrecovered_essential_blocks
        ),
        candidate_unrecovered_essential_blocks=(
            common.candidate_unrecovered_essential_blocks
        ),
        checks=checks,
        decision=OcrResolutionDecision(common.decision.value),
        reasons=common.reasons,
    )


def _resolution_compatibility_checks(
    checks: Sequence[OcrExperimentCheck],
) -> tuple[OcrResolutionCheck, ...]:
    """Retain Issue #47's check details while using the common decision engine."""

    result: list[OcrResolutionCheck] = []
    for check in checks:
        reasons = check.reasons
        details = dict(check.details)
        if check.name == "runtime_equivalence":
            if check.passed:
                reasons = (
                    "runtime is identical except for the allowed effective OCR DPI",
                )
            compared = details.get("compared_fields")
            details = {
                "allowed_differences": [
                    "runtime.configuration.effective_ocr_dpi",
                    "ocr_input_transform",
                ],
                "control_effective_ocr_dpi": details.get("control_effective_ocr_dpi"),
                "candidate_effective_ocr_dpi": details.get(
                    "candidate_effective_ocr_dpi"
                ),
                "compared_fields": [
                    name
                    for name in compared
                    if isinstance(name, str) and name != "effective_ocr_dpi"
                ]
                if isinstance(compared, Sequence)
                else [],
            }
        elif check.name == "source_geometry_and_provenance_integrity":
            reasons = tuple(
                reason.replace(":evidence_crop:", ":transform_crop:").replace(
                    "candidate:parameters_digest_does_not_identify_hypothesis",
                    "candidate:parameters_digest_does_not_identify_transform",
                )
                for reason in reasons
            )
            details["parent_region_refs_come_from_structure_crops"] = details.pop(
                "parent_region_refs_come_from_evidence_crops"
            )
            details["transform_conditions_must_change_parameters_digest"] = details.pop(
                "experiment_conditions_must_change_parameters_digest"
            )
        result.append(
            OcrResolutionCheck(
                name=check.name,
                passed=check.passed,
                reasons=reasons,
                details=details,
            )
        )
    return tuple(result)


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


def _json_sha256(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
