"""Pure same-runtime decision for the two-pixel OCR crop-padding experiment."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from aiteqno.ports import (
    OcrExperimentCheck,
    OcrExperimentComparisonResult,
    OcrExperimentContract,
    OcrExperimentRun,
)

from .ocr_experiment import compare_ocr_experiment


OCR_PADDING_EVALUATOR_NAME = "aiteqno-ocr-crop-padding-comparison"
OCR_PADDING_EVALUATOR_VERSION = "1.0.0"
DEFAULT_MINIMUM_TEXT_ACCURACY_DELTA = 1.0
_EXPECTED_PADDING_VERSION = "tesseract-crop-padding-v1"
_EXPECTED_SCOPE = "region-crops-only"
_EXPECTED_PADDING_PIXELS = 2
_EXPECTED_MAX_WORKING_PIXELS = 40_000_000
_EXPECTED_BORDER_COLOR = [255, 255, 255]
_EXPECTED_OPERATION_ORDER = [
    "crop-source-region",
    "apply-raster-resolution-transform",
    "add-artificial-white-border",
    "invoke-tesseract",
    "subtract-artificial-border-from-result",
    "restore-original-source-pixel-coordinates",
]
_EXPECTED_MAPPING_POLICY = (
    "clip-ocr-bbox; subtract-artificial-border; clamp-pre-padding-raster; "
    "apply-raster-transform-inverse; add-source-offset"
)
_PADDING_EXPERIMENT_CONTRACT = OcrExperimentContract(
    experiment_id="tesseract_ocr_crop_padding",
    control_label="no_artificial_padding",
    candidate_label="two_source_pixel_white_padding",
    evaluator_name=OCR_PADDING_EVALUATOR_NAME,
    evaluator_version=OCR_PADDING_EVALUATOR_VERSION,
    required_hypothesis_checks=("crop_padding_integrity",),
    allowed_runtime_differences=(),
    supported_reason="all_ocr_crop_padding_adoption_conditions_pass",
)


def compare_ocr_padding(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
    *,
    minimum_text_accuracy_delta: float = DEFAULT_MINIMUM_TEXT_ACCURACY_DELTA,
) -> OcrExperimentComparisonResult:
    """Compare no-padding and exact two-source-pixel white-padding OCR runs."""

    if not isinstance(control, OcrExperimentRun):
        raise TypeError("control must be an OcrExperimentRun")
    if not isinstance(candidate, OcrExperimentRun):
        raise TypeError("candidate must be an OcrExperimentRun")
    return compare_ocr_experiment(
        control,
        candidate,
        contract=_PADDING_EXPERIMENT_CONTRACT,
        hypothesis_checks=(_padding_integrity_check(control, candidate),),
        minimum_text_accuracy_delta=minimum_text_accuracy_delta,
    )


def _padding_integrity_check(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
) -> OcrExperimentCheck:
    failures: list[str] = []
    for side, run in (("control", control), ("candidate", candidate)):
        _validate_common_evidence(side, run, failures)

    left = control.evidence
    right = candidate.evidence
    if left.get("enabled") is not False:
        failures.append("control:enabled")
    if left.get("configured_padding_pixels") != 0:
        failures.append("control:configured_padding_pixels")
    if right.get("enabled") is not True:
        failures.append("candidate:enabled")
    if right.get("configured_padding_pixels") != _EXPECTED_PADDING_PIXELS:
        failures.append("candidate:configured_padding_pixels")

    control_crops = _crops(left)
    candidate_crops = _crops(right)
    if len(control_crops) != len(candidate_crops):
        failures.append("mismatch:crop_count")
    for index, (control_crop, candidate_crop) in enumerate(
        zip(control_crops, candidate_crops)
    ):
        _validate_crop_pair(index, control_crop, candidate_crop, failures)
    for side, crops in (("control", control_crops), ("candidate", candidate_crops)):
        refs = tuple(crop.get("region_ref") for crop in crops)
        if len(refs) != len(set(refs)):
            failures.append(f"{side}:duplicate_region_ref")

    reasons = tuple(failures) or (
        "candidate adds exactly two white pixels around each region crop and "
        "restores original source-pixel coordinates",
    )
    return OcrExperimentCheck(
        name="crop_padding_integrity",
        passed=not failures,
        reasons=reasons,
        details={
            "padding_version": _EXPECTED_PADDING_VERSION,
            "scope": _EXPECTED_SCOPE,
            "control_padding_pixels": 0,
            "candidate_padding_pixels": _EXPECTED_PADDING_PIXELS,
            "border_color": _EXPECTED_BORDER_COLOR,
            "source_crop_bbox_expansion": False,
            "working_dimension_delta_per_axis": 2 * _EXPECTED_PADDING_PIXELS,
            "runtime_differences_allowed": [],
            "inverse_mapping_policy": _EXPECTED_MAPPING_POLICY,
            "crop_count": {
                "control": len(control_crops),
                "candidate": len(candidate_crops),
            },
        },
    )


def _validate_common_evidence(
    side: str,
    run: OcrExperimentRun,
    failures: list[str],
) -> None:
    evidence = run.evidence
    expected_source_dpi = round(
        (run.quality.runtime.source_dpi_x + run.quality.runtime.source_dpi_y) / 2.0,
        6,
    )
    expected_effective_dpi = max(1, int(round(expected_source_dpi)))
    expected_values = (
        ("schema_version", "1.0"),
        ("padding_version", _EXPECTED_PADDING_VERSION),
        ("scope", _EXPECTED_SCOPE),
        ("pixel_mode", "RGB"),
        ("border_color", _EXPECTED_BORDER_COLOR),
        ("operation_order", _EXPECTED_OPERATION_ORDER),
        ("inverse_mapping_policy", _EXPECTED_MAPPING_POLICY),
        ("max_working_pixels", _EXPECTED_MAX_WORKING_PIXELS),
        ("target_dpi", None),
        ("source_effective_dpi", expected_source_dpi),
        ("effective_ocr_dpi", expected_effective_dpi),
    )
    for name, expected in expected_values:
        if evidence.get(name) != expected:
            failures.append(f"{side}:{name}")
    if run.quality.runtime.effective_ocr_dpi != expected_effective_dpi:
        failures.append(f"{side}:runtime_effective_ocr_dpi")
    imaging = evidence.get("imaging_library")
    if (
        not isinstance(imaging, Mapping)
        or imaging.get("name") != "Pillow"
        or not isinstance(imaging.get("version"), str)
        or not imaging.get("version")
    ):
        failures.append(f"{side}:imaging_library")
    crops = evidence.get("crops")
    if (
        not isinstance(crops, Sequence)
        or isinstance(crops, (str, bytes, bytearray))
        or not crops
    ):
        failures.append(f"{side}:crops")
    elif any(not isinstance(crop, Mapping) for crop in crops):
        failures.append(f"{side}:crop_type")


def _crops(evidence: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw = evidence.get("crops")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    return tuple(crop for crop in raw if isinstance(crop, Mapping))


def _validate_crop_pair(
    index: int,
    control: Mapping[str, object],
    candidate: Mapping[str, object],
    failures: list[str],
) -> None:
    prefix = f"crop:{index}"
    for field_name in (
        "region_ref",
        "source_bbox",
        "source_dimensions",
        "pre_padding_dimensions",
    ):
        if control.get(field_name) != candidate.get(field_name):
            failures.append(f"{prefix}:mismatch:{field_name}")
    region_ref = control.get("region_ref")
    if not isinstance(region_ref, str) or not region_ref:
        failures.append(f"{prefix}:control:region_ref")

    source = _dimensions(control.get("source_dimensions"))
    source_bbox = _bbox(control.get("source_bbox"))
    control_pre = _dimensions(control.get("pre_padding_dimensions"))
    candidate_pre = _dimensions(candidate.get("pre_padding_dimensions"))
    control_working = _dimensions(control.get("working_dimensions"))
    candidate_working = _dimensions(candidate.get("working_dimensions"))
    if source is None:
        failures.append(f"{prefix}:source_dimensions")
        return
    if source_bbox is None or source_bbox[2:] != source:
        failures.append(f"{prefix}:source_bbox")
    if control_pre != source or candidate_pre != source:
        failures.append(f"{prefix}:pre_padding_dimensions")
    if control_working != source:
        failures.append(f"{prefix}:control:working_dimensions")
    expected_candidate = (
        source[0] + 2 * _EXPECTED_PADDING_PIXELS,
        source[1] + 2 * _EXPECTED_PADDING_PIXELS,
    )
    if candidate_working != expected_candidate:
        failures.append(f"{prefix}:candidate:working_dimensions")
    if control.get("padding_pixels") != 0 or control.get("applied") is not False:
        failures.append(f"{prefix}:control:padding")
    if (
        candidate.get("padding_pixels") != _EXPECTED_PADDING_PIXELS
        or candidate.get("applied") is not True
    ):
        failures.append(f"{prefix}:candidate:padding")
    control_digest = control.get("working_raster_sha256")
    candidate_digest = candidate.get("working_raster_sha256")
    if not _sha256_digest(control_digest):
        failures.append(f"{prefix}:control:working_raster_sha256")
    if not _sha256_digest(candidate_digest):
        failures.append(f"{prefix}:candidate:working_raster_sha256")
    if control_digest == candidate_digest:
        failures.append(f"{prefix}:working_raster_sha256_unchanged")


def _dimensions(value: object) -> tuple[int, int] | None:
    if not isinstance(value, Mapping):
        return None
    width = value.get("width")
    height = value.get("height")
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item < 1
        for item in (width, height)
    ):
        return None
    return width, height


def _bbox(value: object) -> tuple[int, int, int, int] | None:
    if not isinstance(value, Mapping):
        return None
    result = (
        value.get("x"),
        value.get("y"),
        value.get("width"),
        value.get("height"),
    )
    if any(isinstance(item, bool) or not isinstance(item, int) for item in result):
        return None
    x, y, width, height = result
    if x < 0 or y < 0 or width < 1 or height < 1:
        return None
    return x, y, width, height


def _sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = [
    "DEFAULT_MINIMUM_TEXT_ACCURACY_DELTA",
    "OCR_PADDING_EVALUATOR_NAME",
    "OCR_PADDING_EVALUATOR_VERSION",
    "compare_ocr_padding",
]
