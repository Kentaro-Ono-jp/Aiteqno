"""Fixed same-runtime comparison for the Japanese-only OCR profile."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, Sequence

from aiteqno.domain import ProvenanceStage, TextElement
from aiteqno.ports import (
    OcrExperimentCheck,
    OcrExperimentContract,
    OcrExperimentDecision,
    OcrExperimentRun,
    OcrLanguageProfileComparisonResult,
    OcrLanguageSmokeRun,
    OcrProtectedLiteralRecovery,
)

from .ocr_experiment import compare_ocr_experiment
from .table_topology import infer_table_topology


OCR_LANGUAGE_EVALUATOR_NAME = "aiteqno-ocr-language-profile-comparison"
OCR_LANGUAGE_EVALUATOR_VERSION = "1.0.0"
OCR_LANGUAGE_CONTROL_LANGUAGES = ("jpn", "eng")
OCR_LANGUAGE_CANDIDATE_LANGUAGES = ("jpn",)
OCR_LANGUAGE_PROTECTED_LITERALS = (
    "PNG",
    "PDF",
    "DOCX",
    "JSON",
    "30",
    "90",
    "70",
)
OCR_LANGUAGE_SMOKE_SOURCE_SHA256 = (
    "ccfa1bfedff409064924a6e57805e81fc826e754407e03827388df9da56a103f"
)
OCR_LANGUAGE_SMOKE_REQUIRED_LITERALS = ("AITEQNO", "2026")
OCR_LANGUAGE_SMOKE_REQUIRED_ANY = (("患者", "番号", "患者番号"),)
_EXPECTED_INVOCATION_VERSION = "tesseract-invocation-evidence-v1"
_EXPECTED_PADDING_VERSION = "tesseract-crop-padding-v1"
_EXPECTED_PADDING_PIXELS = 2
_EXPECTED_MAX_WORKING_PIXELS = 40_000_000
_EXPECTED_CONFIGURATION_FIELDS = {
    "languages",
    "page_segmentation_mode",
    "engine_mode",
    "timeout_seconds",
    "min_confidence",
    "preserve_interword_spaces",
    "source_metadata_dpi",
    "effective_ocr_dpi",
    "target_dpi",
    "region_padding_px",
    "max_working_pixels",
    "tessdata_configured",
    "tesseract_config",
}
_EXPECTED_EVIDENCE_FIELDS = {
    "schema_version",
    "invocation_version",
    "provider",
    "provider_version",
    "executable",
    "configuration",
    "traineddata",
    "parameters_digest",
    "raster_transform",
    "crop_padding",
    "crops",
}
_LANGUAGE_EXPERIMENT_CONTRACT = OcrExperimentContract(
    experiment_id="tesseract_ocr_language_profile",
    control_label="two_pixel_padding_jpn_eng",
    candidate_label="two_pixel_padding_jpn_only",
    evaluator_name=OCR_LANGUAGE_EVALUATOR_NAME,
    evaluator_version=OCR_LANGUAGE_EVALUATOR_VERSION,
    required_hypothesis_checks=(
        "language_profile_integrity",
        "normalized_table_topology_integrity",
        "multilingual_smoke_comparability",
    ),
    allowed_runtime_differences=("languages", "traineddata"),
    supported_reason="all_japanese_only_language_profile_adoption_conditions_pass",
)


def compare_ocr_language_profile(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
    *,
    multilingual_smoke: OcrLanguageSmokeRun,
    minimum_text_accuracy_delta: float = 1.0,
) -> OcrLanguageProfileComparisonResult:
    """Compare exact 2px ``jpn,eng`` and ``jpn`` runs plus the smoke gate."""

    if not isinstance(control, OcrExperimentRun):
        raise TypeError("control must be an OcrExperimentRun")
    if not isinstance(candidate, OcrExperimentRun):
        raise TypeError("candidate must be an OcrExperimentRun")
    if not isinstance(multilingual_smoke, OcrLanguageSmokeRun):
        raise TypeError("multilingual_smoke must be an OcrLanguageSmokeRun")

    base = compare_ocr_experiment(
        control,
        candidate,
        contract=_LANGUAGE_EXPERIMENT_CONTRACT,
        hypothesis_checks=(
            _language_profile_integrity_check(control, candidate),
            _table_topology_integrity_check(control, candidate),
            _multilingual_smoke_comparability_check(candidate, multilingual_smoke),
        ),
        minimum_text_accuracy_delta=minimum_text_accuracy_delta,
    )
    protected = _protected_literal_recovery(control, candidate)
    smoke_report = _multilingual_smoke_report(multilingual_smoke)
    regression_reasons = [
        f"regression:protected_literal:{item.literal}"
        for item in protected
        if item.lost
    ]
    regression_reasons.extend(
        f"regression:multilingual_smoke:literal:{literal}"
        for literal in smoke_report["missing_literals"]
    )
    regression_reasons.extend(
        f"regression:multilingual_smoke:any_group:{index}"
        for index in smoke_report["missing_any_groups"]
    )

    decision = base.decision
    reasons = list(base.reasons)
    if decision is not OcrExperimentDecision.INVALID and regression_reasons:
        decision = OcrExperimentDecision.REGRESSED
        reasons.extend(regression_reasons)
    reasons = list(dict.fromkeys(reasons))
    return OcrLanguageProfileComparisonResult(
        comparison=base,
        protected_literals=protected,
        multilingual_smoke=smoke_report,
        decision=decision,
        reasons=tuple(reasons),
    )


def _language_profile_integrity_check(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
) -> OcrExperimentCheck:
    failures: list[str] = []
    left = dict(control.evidence)
    right = dict(candidate.evidence)
    _validate_main_invocation(
        "control",
        control,
        expected_languages=OCR_LANGUAGE_CONTROL_LANGUAGES,
        failures=failures,
    )
    _validate_main_invocation(
        "candidate",
        candidate,
        expected_languages=OCR_LANGUAGE_CANDIDATE_LANGUAGES,
        failures=failures,
    )

    left_configuration = _mapping(left.get("configuration"))
    right_configuration = _mapping(right.get("configuration"))
    if left_configuration is not None and right_configuration is not None:
        for name in sorted(_EXPECTED_CONFIGURATION_FIELDS - {"languages"}):
            if left_configuration.get(name) != right_configuration.get(name):
                failures.append(f"mismatch:configuration:{name}")
    for name in (
        "schema_version",
        "invocation_version",
        "provider",
        "provider_version",
        "executable",
        "raster_transform",
        "crop_padding",
        "crops",
    ):
        if left.get(name) != right.get(name):
            failures.append(f"mismatch:{name}")

    left_traineddata = _traineddata_records(left)
    right_traineddata = _traineddata_records(right)
    if tuple(item.get("language") for item in left_traineddata) != (
        OCR_LANGUAGE_CONTROL_LANGUAGES
    ):
        failures.append("control:traineddata_languages")
    if tuple(item.get("language") for item in right_traineddata) != (
        OCR_LANGUAGE_CANDIDATE_LANGUAGES
    ):
        failures.append("candidate:traineddata_languages")
    if left_traineddata and right_traineddata and left_traineddata[0] != right_traineddata[0]:
        failures.append("mismatch:jpn_traineddata")
    if any(item.get("language") == "eng" for item in right_traineddata):
        failures.append("candidate:eng_traineddata_present")
    if left.get("parameters_digest") == right.get("parameters_digest"):
        failures.append("candidate:parameters_digest_unchanged")

    reasons = tuple(failures) or (
        "only ordered languages and the corresponding traineddata set differ",
    )
    return OcrExperimentCheck(
        name="language_profile_integrity",
        passed=not failures,
        reasons=reasons,
        details={
            "control_languages": list(OCR_LANGUAGE_CONTROL_LANGUAGES),
            "candidate_languages": list(OCR_LANGUAGE_CANDIDATE_LANGUAGES),
            "allowed_differences": [
                "configuration.languages",
                "traineddata",
                "parameters_digest",
                "recognized_text_and_tokenization",
            ],
            "common_jpn_traineddata_must_match": True,
            "candidate_eng_traineddata_forbidden": True,
            "padding_pixels": _EXPECTED_PADDING_PIXELS,
            "target_dpi": None,
            "page_segmentation_mode": 6,
            "engine_mode": 3,
            "control_parameters_digest": left.get("parameters_digest"),
            "candidate_parameters_digest": right.get("parameters_digest"),
        },
    )


def _validate_main_invocation(
    side: str,
    run: OcrExperimentRun,
    *,
    expected_languages: tuple[str, ...],
    failures: list[str],
) -> None:
    evidence = dict(run.evidence)
    if set(evidence) != _EXPECTED_EVIDENCE_FIELDS:
        failures.append(f"{side}:evidence_fields")
    if evidence.get("schema_version") != "1.0":
        failures.append(f"{side}:schema_version")
    if evidence.get("invocation_version") != _EXPECTED_INVOCATION_VERSION:
        failures.append(f"{side}:invocation_version")
    if evidence.get("provider") != "tesseract":
        failures.append(f"{side}:provider")
    if not _digest(evidence.get("parameters_digest")):
        failures.append(f"{side}:parameters_digest")

    configuration = _mapping(evidence.get("configuration"))
    if configuration is None:
        failures.append(f"{side}:configuration")
    else:
        if set(configuration) != _EXPECTED_CONFIGURATION_FIELDS:
            failures.append(f"{side}:configuration_fields")
        expected_values = (
            ("languages", list(expected_languages)),
            ("page_segmentation_mode", 6),
            ("engine_mode", 3),
            ("target_dpi", None),
            ("region_padding_px", _EXPECTED_PADDING_PIXELS),
            ("max_working_pixels", _EXPECTED_MAX_WORKING_PIXELS),
        )
        for name, expected in expected_values:
            if configuration.get(name) != expected:
                failures.append(f"{side}:configuration:{name}")
        dpi = _mapping(configuration.get("source_metadata_dpi"))
        if dpi is None or not _source_dpi_is_96(dpi):
            failures.append(f"{side}:configuration:source_metadata_dpi")

    _validate_runtime_matches_invocation(
        side,
        run,
        expected_languages=expected_languages,
        failures=failures,
    )
    _validate_two_pixel_no_upscale(side, evidence, require_regions=True, failures=failures)
    parameters_digest = evidence.get("parameters_digest")
    document_digests = _document_parameter_digests(run)
    if document_digests != {parameters_digest}:
        failures.append(f"{side}:document_parameters_digest")


def _validate_runtime_matches_invocation(
    side: str,
    run: OcrExperimentRun,
    *,
    expected_languages: tuple[str, ...],
    failures: list[str],
) -> None:
    evidence = dict(run.evidence)
    configuration = _mapping(evidence.get("configuration")) or {}
    runtime = run.quality.runtime
    comparisons = (
        ("provider", runtime.provider, evidence.get("provider")),
        ("provider_version", runtime.provider_version, evidence.get("provider_version")),
        ("executable", runtime.executable, evidence.get("executable")),
        ("languages", runtime.languages, expected_languages),
        (
            "page_segmentation_mode",
            runtime.page_segmentation_mode,
            configuration.get("page_segmentation_mode"),
        ),
        ("engine_mode", runtime.engine_mode, configuration.get("engine_mode")),
        (
            "effective_ocr_dpi",
            runtime.effective_ocr_dpi,
            configuration.get("effective_ocr_dpi"),
        ),
        ("source_dpi_x", runtime.source_dpi_x, _nested(configuration, "source_metadata_dpi", "x")),
        ("source_dpi_y", runtime.source_dpi_y, _nested(configuration, "source_metadata_dpi", "y")),
    )
    for name, observed, expected in comparisons:
        if observed != expected:
            failures.append(f"{side}:runtime:{name}")
    runtime_traineddata = tuple(
        {
            "language": item.language,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
        }
        for item in runtime.traineddata
    )
    invocation_traineddata = tuple(
        {
            "language": item.get("language"),
            "size_bytes": item.get("size_bytes"),
            "sha256": item.get("sha256"),
        }
        for item in _traineddata_records(evidence)
    )
    if runtime_traineddata != invocation_traineddata:
        failures.append(f"{side}:runtime:traineddata")


def _validate_two_pixel_no_upscale(
    side: str,
    evidence: Mapping[str, object],
    *,
    require_regions: bool,
    failures: list[str],
) -> None:
    transform = _mapping(evidence.get("raster_transform"))
    padding = _mapping(evidence.get("crop_padding"))
    if transform is None:
        failures.append(f"{side}:raster_transform")
    else:
        if transform.get("enabled") is not False or transform.get("target_dpi") is not None:
            failures.append(f"{side}:no_upscale")
        transform_crops = _mapping_sequence(transform.get("crops"))
        if not transform_crops or any(crop.get("resized") is not False for crop in transform_crops):
            failures.append(f"{side}:resized_crop")
    if padding is None:
        failures.append(f"{side}:crop_padding")
        return
    if padding.get("padding_version") != _EXPECTED_PADDING_VERSION:
        failures.append(f"{side}:padding_version")
    if padding.get("enabled") is not True:
        failures.append(f"{side}:padding_enabled")
    if padding.get("configured_padding_pixels") != _EXPECTED_PADDING_PIXELS:
        failures.append(f"{side}:configured_padding_pixels")
    if padding.get("target_dpi") is not None:
        failures.append(f"{side}:padding_target_dpi")
    crops = _mapping_sequence(padding.get("crops"))
    if not crops or list(crops) != evidence.get("crops"):
        failures.append(f"{side}:padding_crops")
        return
    for index, crop in enumerate(crops):
        prefix = f"{side}:crop:{index}"
        region_ref = crop.get("region_ref")
        if require_regions:
            if not isinstance(region_ref, str) or not region_ref:
                failures.append(f"{prefix}:region_ref")
            if crop.get("padding_pixels") != 2 or crop.get("applied") is not True:
                failures.append(f"{prefix}:padding")
        else:
            if region_ref is not None:
                failures.append(f"{prefix}:full_page_region_ref")
            if crop.get("padding_pixels") != 0 or crop.get("applied") is not False:
                failures.append(f"{prefix}:full_page_padding")


def _table_topology_integrity_check(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
) -> OcrExperimentCheck:
    failures: list[str] = []
    left = _normalized_table_topology(control)
    right = _normalized_table_topology(candidate)
    if left != right:
        failures.append("mismatch:normalized_table_topology")
    left_digest = _json_sha256(left)
    right_digest = _json_sha256(right)
    reasons = tuple(failures) or (
        "table and cell topology is byte-equivalent after OCR token identity removal",
    )
    return OcrExperimentCheck(
        name="normalized_table_topology_integrity",
        passed=not failures,
        reasons=reasons,
        details={
            "normalization": [
                "remove cell text_element_ids",
                "remove ambiguous_text_element_ids",
                "remove unassigned_text_element_ids",
            ],
            "control_sha256": left_digest,
            "candidate_sha256": right_digest,
            "byte_equivalent": left_digest == right_digest,
        },
    )


def _normalized_table_topology(run: OcrExperimentRun) -> dict[str, object]:
    document = infer_table_topology(run.document)
    pages: list[dict[str, object]] = []
    for page in document.pages:
        topology = page.extensions.get("jp.reactorfront.aiteqno.table_topology")
        pages.append(
            {
                "page_id": page.id,
                "page_number": page.number,
                "topology": _without_ocr_token_identity(topology),
            }
        )
    return {"pages": pages}


def _without_ocr_token_identity(value: object) -> object:
    ignored = {
        "text_element_ids",
        "ambiguous_text_element_ids",
        "unassigned_text_element_ids",
    }
    if isinstance(value, Mapping):
        return {
            key: _without_ocr_token_identity(item)
            for key, item in sorted(value.items())
            if key not in ignored
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_without_ocr_token_identity(item) for item in value]
    return value


def _multilingual_smoke_comparability_check(
    candidate: OcrExperimentRun,
    smoke: OcrLanguageSmokeRun,
) -> OcrExperimentCheck:
    failures: list[str] = []
    if smoke.source_sha256 != OCR_LANGUAGE_SMOKE_SOURCE_SHA256:
        failures.append("smoke:source_sha256")
    evidence = dict(smoke.invocation_evidence)
    if set(evidence) != _EXPECTED_EVIDENCE_FIELDS:
        failures.append("smoke:evidence_fields")
    if evidence.get("invocation_version") != _EXPECTED_INVOCATION_VERSION:
        failures.append("smoke:invocation_version")
    if not _digest(evidence.get("parameters_digest")):
        failures.append("smoke:parameters_digest")
    configuration = _mapping(evidence.get("configuration"))
    candidate_configuration = _mapping(candidate.evidence.get("configuration"))
    if configuration is None or candidate_configuration is None:
        failures.append("smoke:configuration")
    else:
        if set(configuration) != _EXPECTED_CONFIGURATION_FIELDS:
            failures.append("smoke:configuration_fields")
        if configuration.get("languages") != list(OCR_LANGUAGE_CANDIDATE_LANGUAGES):
            failures.append("smoke:languages")
        invariant_fields = _EXPECTED_CONFIGURATION_FIELDS - {
            "source_metadata_dpi",
            "effective_ocr_dpi",
            "tesseract_config",
        }
        for name in sorted(invariant_fields):
            if configuration.get(name) != candidate_configuration.get(name):
                failures.append(f"smoke:mismatch:configuration:{name}")
    for name in ("provider", "provider_version", "executable", "traineddata"):
        if evidence.get(name) != candidate.evidence.get(name):
            failures.append(f"smoke:mismatch:{name}")
    _validate_two_pixel_no_upscale(
        "smoke",
        evidence,
        require_regions=False,
        failures=failures,
    )
    reasons = tuple(failures) or (
        "the immutable mixed-language fixture used the same jpn-only runtime profile",
    )
    return OcrExperimentCheck(
        name="multilingual_smoke_comparability",
        passed=not failures,
        reasons=reasons,
        details={
            "source_sha256": smoke.source_sha256,
            "candidate_languages": list(OCR_LANGUAGE_CANDIDATE_LANGUAGES),
            "invocation_evidence_sha256": _json_sha256(evidence),
        },
    )


def _protected_literal_recovery(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
) -> tuple[OcrProtectedLiteralRecovery, ...]:
    left = _normalized_text(control.quality.observed_text)
    right = _normalized_text(candidate.quality.observed_text)
    return tuple(
        OcrProtectedLiteralRecovery(
            literal=literal,
            control_recovered=literal in left,
            candidate_recovered=literal in right,
        )
        for literal in OCR_LANGUAGE_PROTECTED_LITERALS
    )


def _multilingual_smoke_report(smoke: OcrLanguageSmokeRun) -> dict[str, object]:
    normalized = _normalized_text(smoke.observed_text)
    literal_results = {
        literal: literal in normalized
        for literal in OCR_LANGUAGE_SMOKE_REQUIRED_LITERALS
    }
    any_results = []
    missing_any_groups = []
    for index, alternatives in enumerate(OCR_LANGUAGE_SMOKE_REQUIRED_ANY):
        matched = [value for value in alternatives if value in normalized]
        passed = bool(matched)
        if not passed:
            missing_any_groups.append(index)
        any_results.append(
            {
                "index": index,
                "alternatives": list(alternatives),
                "matched": matched,
                "passed": passed,
            }
        )
    missing_literals = [
        literal for literal, recovered in literal_results.items() if not recovered
    ]
    return {
        "source_sha256": smoke.source_sha256,
        "normalization": "NFKC then remove every Unicode whitespace character",
        "candidate_languages": list(OCR_LANGUAGE_CANDIDATE_LANGUAGES),
        "observed_text": smoke.observed_text,
        "required_literals": literal_results,
        "required_any_groups": any_results,
        "missing_literals": missing_literals,
        "missing_any_groups": missing_any_groups,
        "status": "pass" if not missing_literals and not missing_any_groups else "fail",
        "invocation_evidence_sha256": _json_sha256(smoke.invocation_evidence),
    }


def _document_parameter_digests(run: OcrExperimentRun) -> set[object]:
    return {
        record.parameters_digest
        for page in run.document.pages
        for element in page.elements
        if isinstance(element, TextElement)
        for record in element.provenance
        if record.stage is ProvenanceStage.OCR
    }


def _traineddata_records(
    evidence: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    value = evidence.get("traineddata")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _nested(value: Mapping[str, object], outer: str, inner: str) -> object:
    child = _mapping(value.get(outer))
    return None if child is None else child.get(inner)


def _source_dpi_is_96(value: Mapping[str, object]) -> bool:
    x = value.get("x")
    y = value.get("y")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in (x, y)):
        return False
    assert isinstance(x, (int, float)) and isinstance(y, (int, float))
    return math.isclose((float(x) + float(y)) / 2.0, 96.0, abs_tol=0.1)


def _digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _normalized_text(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value)
        if not character.isspace()
    )


def _json_sha256(value: object) -> str:
    if isinstance(value, Mapping):
        value = dict(value)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "OCR_LANGUAGE_CANDIDATE_LANGUAGES",
    "OCR_LANGUAGE_CONTROL_LANGUAGES",
    "OCR_LANGUAGE_EVALUATOR_NAME",
    "OCR_LANGUAGE_EVALUATOR_VERSION",
    "OCR_LANGUAGE_PROTECTED_LITERALS",
    "OCR_LANGUAGE_SMOKE_REQUIRED_ANY",
    "OCR_LANGUAGE_SMOKE_REQUIRED_LITERALS",
    "OCR_LANGUAGE_SMOKE_SOURCE_SHA256",
    "compare_ocr_language_profile",
]
