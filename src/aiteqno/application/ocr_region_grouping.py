"""Fixed same-runtime comparison for geometry-only OCR region grouping."""

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
    OcrLanguageSmokeRun,
    OcrProtectedLiteralRecovery,
    OcrRegionGroupingComparisonResult,
)

from .ocr_experiment import compare_ocr_experiment
from .ocr_grouping import (
    OCR_REGION_GROUPING_ALGORITHM,
    OCR_REGION_GROUPING_ALGORITHM_VERSION,
)
from .ocr_language import (
    OCR_LANGUAGE_CANDIDATE_LANGUAGES,
    OCR_LANGUAGE_PROTECTED_LITERALS,
    OCR_LANGUAGE_SMOKE_REQUIRED_ANY,
    OCR_LANGUAGE_SMOKE_REQUIRED_LITERALS,
    OCR_LANGUAGE_SMOKE_SOURCE_SHA256,
)
from .table_topology import infer_table_topology


OCR_REGION_GROUPING_EVALUATOR_NAME = "aiteqno-ocr-region-grouping-comparison"
OCR_REGION_GROUPING_EVALUATOR_VERSION = "1.0.0"
OCR_REGION_GROUPING_TARGET_BLOCKS = ("title", "content-structure")
_EXPECTED_PADDING_PIXELS = 2
_EXPECTED_MAX_WORKING_PIXELS = 40_000_000
_EXPECTED_INVOCATION_VERSION = "tesseract-invocation-evidence-v1"
_EXPECTED_PADDING_VERSION = "tesseract-crop-padding-v1"
_EXPECTED_PLAN_FIELDS = {
    "schema_version",
    "algorithm",
    "algorithm_version",
    "configuration",
    "configuration_digest",
    "source_regions",
    "vertical_separators",
    "planned_regions",
    "groups",
    "adjacency_decisions",
    "singleton_region_refs",
    "counts",
    "plan_digest",
}
_EXPECTED_INVOCATION_FIELDS = {
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
    "region_grouping",
}
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
_GROUPING_EXPERIMENT_CONTRACT = OcrExperimentContract(
    experiment_id="tesseract_ocr_region_grouping",
    control_label="two_pixel_padding_jpn_single_regions",
    candidate_label="two_pixel_padding_jpn_geometry_line_groups",
    evaluator_name=OCR_REGION_GROUPING_EVALUATOR_NAME,
    evaluator_version=OCR_REGION_GROUPING_EVALUATOR_VERSION,
    required_hypothesis_checks=(
        "region_grouping_integrity",
        "singleton_observation_integrity",
        "normalized_table_topology_integrity",
        "multilingual_smoke_comparability",
    ),
    allowed_runtime_differences=(),
    allowed_geometry_differences=("region_plan",),
    supported_reason="all_geometry_only_region_grouping_adoption_conditions_pass",
)


def compare_ocr_region_grouping(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
    *,
    multilingual_smoke: OcrLanguageSmokeRun,
    minimum_text_accuracy_delta: float = 1.0,
) -> OcrRegionGroupingComparisonResult:
    """Compare fixed disabled/enabled grouping plans on the adopted 2px+jpn profile."""

    if not isinstance(control, OcrExperimentRun):
        raise TypeError("control must be an OcrExperimentRun")
    if not isinstance(candidate, OcrExperimentRun):
        raise TypeError("candidate must be an OcrExperimentRun")
    if not isinstance(multilingual_smoke, OcrLanguageSmokeRun):
        raise TypeError("multilingual_smoke must be an OcrLanguageSmokeRun")

    singleton_check, singleton_report = _singleton_observation_check(
        control,
        candidate,
    )
    base = compare_ocr_experiment(
        control,
        candidate,
        contract=_GROUPING_EXPERIMENT_CONTRACT,
        hypothesis_checks=(
            _region_grouping_integrity_check(control, candidate),
            singleton_check,
            _table_topology_integrity_check(control, candidate),
            _multilingual_smoke_comparability_check(
                candidate,
                multilingual_smoke,
            ),
        ),
        minimum_text_accuracy_delta=minimum_text_accuracy_delta,
    )
    protected = _protected_literal_recovery(control, candidate)
    smoke_report = _multilingual_smoke_report(multilingual_smoke)
    target_report = _target_recovery(control, candidate)

    regression_reasons = [
        f"regression:protected_literal:{value.literal}"
        for value in protected
        if value.lost
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
    elif (
        decision is OcrExperimentDecision.SUPPORTED
        and not target_report["newly_recovered"]
    ):
        decision = OcrExperimentDecision.INCONCLUSIVE
        reasons = ["required_grouping_target_not_newly_recovered"]
    reasons = list(dict.fromkeys(reasons))
    return OcrRegionGroupingComparisonResult(
        comparison=base,
        protected_literals=protected,
        multilingual_smoke=smoke_report,
        target_recovery=target_report,
        singleton_observations=singleton_report,
        decision=decision,
        reasons=tuple(reasons),
    )


def _region_grouping_integrity_check(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
) -> OcrExperimentCheck:
    failures: list[str] = []
    control_plan = _plan(control.evidence)
    candidate_plan = _plan(candidate.evidence)
    _validate_invocation("control", control, failures)
    _validate_invocation("candidate", candidate, failures)
    _validate_plan("control", control_plan, expected_enabled=False, failures=failures)
    _validate_plan(
        "candidate",
        candidate_plan,
        expected_enabled=True,
        failures=failures,
    )

    if control_plan and candidate_plan:
        for field_name in (
            "schema_version",
            "algorithm",
            "algorithm_version",
            "source_regions",
            "vertical_separators",
        ):
            if control_plan.get(field_name) != candidate_plan.get(field_name):
                failures.append(f"mismatch:region_grouping:{field_name}")
        left_config = _mapping(control_plan.get("configuration")) or {}
        right_config = _mapping(candidate_plan.get("configuration")) or {}
        for field_name in sorted(set(left_config) | set(right_config)):
            if field_name != "enabled" and left_config.get(
                field_name
            ) != right_config.get(field_name):
                failures.append(f"mismatch:region_grouping:configuration:{field_name}")
        if control_plan.get("configuration_digest") == candidate_plan.get(
            "configuration_digest"
        ):
            failures.append("candidate:configuration_digest_unchanged")
        if control_plan.get("plan_digest") == candidate_plan.get("plan_digest"):
            failures.append("candidate:plan_digest_unchanged")
        control_count = len(_mapping_sequence(control_plan.get("planned_regions")))
        candidate_count = len(_mapping_sequence(candidate_plan.get("planned_regions")))
        if candidate_count >= control_count:
            failures.append("candidate:planned_region_count_not_reduced")

    left_invariant = _invocation_invariant(control.evidence)
    right_invariant = _invocation_invariant(candidate.evidence)
    if left_invariant != right_invariant:
        failures.append("mismatch:invocation_invariant")
    if control.evidence.get("parameters_digest") == candidate.evidence.get(
        "parameters_digest"
    ):
        failures.append("candidate:parameters_digest_unchanged")
    _validate_crops_match_plan("control", control.evidence, control_plan, failures)
    _validate_crops_match_plan(
        "candidate",
        candidate.evidence,
        candidate_plan,
        failures,
    )

    reasons = tuple(dict.fromkeys(failures)) or (
        "only the deterministic source-geometry OCR region plan differs",
    )
    return OcrExperimentCheck(
        name="region_grouping_integrity",
        passed=not failures,
        reasons=reasons,
        details={
            "algorithm": OCR_REGION_GROUPING_ALGORITHM,
            "algorithm_version": OCR_REGION_GROUPING_ALGORITHM_VERSION,
            "control_plan_digest": control_plan.get("plan_digest"),
            "candidate_plan_digest": candidate_plan.get("plan_digest"),
            "control_parameters_digest": control.evidence.get("parameters_digest"),
            "candidate_parameters_digest": candidate.evidence.get("parameters_digest"),
            "control_planned_regions": len(
                _mapping_sequence(control_plan.get("planned_regions"))
            ),
            "candidate_planned_regions": len(
                _mapping_sequence(candidate_plan.get("planned_regions"))
            ),
            "candidate_groups": len(_mapping_sequence(candidate_plan.get("groups"))),
            "uses_ocr_text": False,
            "uses_ocr_confidence": False,
            "allowed_differences": [
                "region_grouping.configuration.enabled",
                "region_grouping.configuration_digest",
                "region_grouping.planned_regions",
                "region_grouping.groups",
                "region_grouping.adjacency_decisions",
                "region_grouping.singleton_region_refs",
                "region_grouping.plan_digest",
                "raster_transform.crops",
                "crop_padding.crops",
                "crops",
                "parameters_digest",
                "recognized_text_and_tokenization",
            ],
        },
    )


def _validate_invocation(
    side: str,
    run: OcrExperimentRun,
    failures: list[str],
) -> None:
    evidence = run.evidence
    if set(evidence) != _EXPECTED_INVOCATION_FIELDS:
        failures.append(f"{side}:evidence_fields")
    expected = (
        ("schema_version", "1.0"),
        ("invocation_version", _EXPECTED_INVOCATION_VERSION),
        ("provider", "tesseract"),
    )
    for field_name, value in expected:
        if evidence.get(field_name) != value:
            failures.append(f"{side}:{field_name}")
    if not _digest(evidence.get("parameters_digest")):
        failures.append(f"{side}:parameters_digest")
    configuration = _mapping(evidence.get("configuration"))
    if configuration is None:
        failures.append(f"{side}:configuration")
    else:
        if set(configuration) != _EXPECTED_CONFIGURATION_FIELDS:
            failures.append(f"{side}:configuration_fields")
        expected_configuration = (
            ("languages", list(OCR_LANGUAGE_CANDIDATE_LANGUAGES)),
            ("page_segmentation_mode", 6),
            ("engine_mode", 3),
            ("target_dpi", None),
            ("region_padding_px", _EXPECTED_PADDING_PIXELS),
            ("max_working_pixels", _EXPECTED_MAX_WORKING_PIXELS),
        )
        for field_name, value in expected_configuration:
            if configuration.get(field_name) != value:
                failures.append(f"{side}:configuration:{field_name}")
        source_dpi = _mapping(configuration.get("source_metadata_dpi"))
        if source_dpi is None or not _source_dpi_is_96(source_dpi):
            failures.append(f"{side}:configuration:source_metadata_dpi")
        runtime = run.quality.runtime
        runtime_pairs = (
            ("provider", runtime.provider, evidence.get("provider")),
            (
                "provider_version",
                runtime.provider_version,
                evidence.get("provider_version"),
            ),
            ("executable", runtime.executable, evidence.get("executable")),
            (
                "languages",
                list(runtime.languages),
                configuration.get("languages"),
            ),
            (
                "page_segmentation_mode",
                runtime.page_segmentation_mode,
                configuration.get("page_segmentation_mode"),
            ),
            (
                "engine_mode",
                runtime.engine_mode,
                configuration.get("engine_mode"),
            ),
            (
                "effective_ocr_dpi",
                runtime.effective_ocr_dpi,
                configuration.get("effective_ocr_dpi"),
            ),
            (
                "source_dpi_x",
                runtime.source_dpi_x,
                source_dpi.get("x") if source_dpi else None,
            ),
            (
                "source_dpi_y",
                runtime.source_dpi_y,
                source_dpi.get("y") if source_dpi else None,
            ),
        )
        for field_name, observed, expected_value in runtime_pairs:
            if observed != expected_value:
                failures.append(f"{side}:runtime:{field_name}")
    invocation_traineddata = tuple(
        {
            "language": value.get("language"),
            "size_bytes": value.get("size_bytes"),
            "sha256": value.get("sha256"),
        }
        for value in _mapping_sequence(evidence.get("traineddata"))
    )
    runtime_traineddata = tuple(
        {
            "language": value.language,
            "size_bytes": value.size_bytes,
            "sha256": value.sha256,
        }
        for value in run.quality.runtime.traineddata
    )
    if invocation_traineddata != runtime_traineddata:
        failures.append(f"{side}:runtime:traineddata")
    transform = _mapping(evidence.get("raster_transform"))
    if (
        transform is None
        or transform.get("enabled") is not False
        or transform.get("target_dpi") is not None
    ):
        failures.append(f"{side}:no_upscale")
    elif any(
        value.get("resized") is not False
        for value in _mapping_sequence(transform.get("crops"))
    ):
        failures.append(f"{side}:resized_crop")
    padding = _mapping(evidence.get("crop_padding"))
    if (
        padding is None
        or padding.get("padding_version") != _EXPECTED_PADDING_VERSION
        or padding.get("enabled") is not True
        or padding.get("configured_padding_pixels") != _EXPECTED_PADDING_PIXELS
        or padding.get("target_dpi") is not None
    ):
        failures.append(f"{side}:two_pixel_padding")
    crops = _mapping_sequence(evidence.get("crops"))
    if padding is not None and padding.get("crops") != list(crops):
        failures.append(f"{side}:padding_crops")
    if not crops or any(
        crop.get("region_ref") is None
        or crop.get("padding_pixels") != _EXPECTED_PADDING_PIXELS
        or crop.get("applied") is not True
        for crop in crops
    ):
        failures.append(f"{side}:region_crops")
    document_digests = {
        record.parameters_digest
        for page in run.document.pages
        for element in page.elements
        if isinstance(element, TextElement)
        for record in element.provenance
        if record.stage is ProvenanceStage.OCR
    }
    if document_digests != {evidence.get("parameters_digest")}:
        failures.append(f"{side}:document_parameters_digest")


def _validate_plan(
    side: str,
    plan: Mapping[str, object],
    *,
    expected_enabled: bool,
    failures: list[str],
) -> None:
    if set(plan) != _EXPECTED_PLAN_FIELDS:
        failures.append(f"{side}:region_grouping:fields")
    if plan.get("schema_version") != "1.0":
        failures.append(f"{side}:region_grouping:schema_version")
    if plan.get("algorithm") != OCR_REGION_GROUPING_ALGORITHM:
        failures.append(f"{side}:region_grouping:algorithm")
    if plan.get("algorithm_version") != OCR_REGION_GROUPING_ALGORITHM_VERSION:
        failures.append(f"{side}:region_grouping:algorithm_version")
    configuration = _mapping(plan.get("configuration"))
    if configuration is None:
        failures.append(f"{side}:region_grouping:configuration")
        configuration = {}
    expected_configuration = {
        "enabled": expected_enabled,
        "minimum_vertical_overlap_ratio": 0.45,
        "maximum_horizontal_gap_height_ratio": 1.0,
        "block_vertical_separators": True,
        "uses_ocr_text": False,
        "uses_ocr_confidence": False,
    }
    if dict(configuration) != expected_configuration:
        failures.append(f"{side}:region_grouping:fixed_configuration")
    expected_configuration_digest = _json_sha256(
        {
            "algorithm": OCR_REGION_GROUPING_ALGORITHM,
            "algorithm_version": OCR_REGION_GROUPING_ALGORITHM_VERSION,
            "configuration": expected_configuration,
        }
    )
    if plan.get("configuration_digest") != expected_configuration_digest:
        failures.append(f"{side}:region_grouping:configuration_digest")

    source = _mapping_sequence(plan.get("source_regions"))
    planned = _mapping_sequence(plan.get("planned_regions"))
    groups = _mapping_sequence(plan.get("groups"))
    decisions = _mapping_sequence(plan.get("adjacency_decisions"))
    source_by_ref = _records_by_ref(source, "region_ref")
    planned_by_ref = _records_by_ref(planned, "region_ref")
    if len(source_by_ref) != len(source):
        failures.append(f"{side}:region_grouping:duplicate_source_ref")
    if len(planned_by_ref) != len(planned):
        failures.append(f"{side}:region_grouping:duplicate_planned_ref")
    members: list[str] = []
    for record in planned:
        member_refs = _string_sequence(record.get("member_refs"))
        members.extend(member_refs)
        if not member_refs:
            failures.append(f"{side}:region_grouping:empty_members")
            continue
        member_bboxes = [
            _mapping(source_by_ref.get(value, {}).get("bbox")) for value in member_refs
        ]
        if any(value is None for value in member_bboxes):
            failures.append(f"{side}:region_grouping:unknown_member")
            continue
        expected_bbox = _union_bbox(
            tuple(value for value in member_bboxes if value is not None)
        )
        if record.get("bbox") != expected_bbox:
            failures.append(f"{side}:region_grouping:union_bbox")
        expected_kind = "singleton" if len(member_refs) == 1 else "group"
        if record.get("kind") != expected_kind:
            failures.append(f"{side}:region_grouping:planned_kind")
        if expected_kind == "singleton" and record.get("region_ref") != member_refs[0]:
            failures.append(f"{side}:region_grouping:singleton_ref_changed")
    if sorted(members) != sorted(source_by_ref):
        failures.append(f"{side}:region_grouping:source_partition")

    singleton_refs = _string_sequence(plan.get("singleton_region_refs"))
    expected_singletons = tuple(
        record.get("region_ref")
        for record in planned
        if record.get("kind") == "singleton"
    )
    if singleton_refs != expected_singletons:
        failures.append(f"{side}:region_grouping:singleton_refs")
    group_by_ref = _records_by_ref(groups, "region_ref")
    expected_group_refs = {
        str(record.get("region_ref"))
        for record in planned
        if record.get("kind") == "group"
    }
    if set(group_by_ref) != expected_group_refs:
        failures.append(f"{side}:region_grouping:group_records")
    for group_ref, group in group_by_ref.items():
        planned_group = planned_by_ref.get(group_ref, {})
        if group.get("member_refs") != planned_group.get("member_refs"):
            failures.append(f"{side}:region_grouping:group_member_order")
        if group.get("union_bbox") != planned_group.get("bbox"):
            failures.append(f"{side}:region_grouping:group_union")
        links = _mapping_sequence(group.get("adjacency_links"))
        member_refs = _string_sequence(group.get("member_refs"))
        if len(links) != len(member_refs) - 1:
            failures.append(f"{side}:region_grouping:group_link_count")
        for link in links:
            if (
                link.get("grouped") is not True
                or link.get("blocking_separator_refs") != []
                or not _at_least(
                    link.get("vertical_overlap_ratio"),
                    expected_configuration["minimum_vertical_overlap_ratio"],
                )
                or not _at_most(
                    link.get("horizontal_gap_px"),
                    link.get("maximum_horizontal_gap_px"),
                )
            ):
                failures.append(f"{side}:region_grouping:invalid_group_link")
    for decision in decisions:
        if decision.get("grouped") is True and decision not in tuple(
            link
            for group in groups
            for link in _mapping_sequence(group.get("adjacency_links"))
        ):
            failures.append(f"{side}:region_grouping:orphan_group_link")

    if expected_enabled and not groups:
        failures.append(f"{side}:region_grouping:groups_missing")
    if not expected_enabled and (groups or decisions or len(planned) != len(source)):
        failures.append(f"{side}:region_grouping:control_not_identity")
    expected_counts = {
        "source_regions": len(source),
        "planned_regions": len(planned),
        "groups": len(groups),
        "singletons": len(singleton_refs),
    }
    if plan.get("counts") != expected_counts:
        failures.append(f"{side}:region_grouping:counts")
    digest_payload = {
        key: plan.get(key)
        for key in (
            "schema_version",
            "algorithm",
            "algorithm_version",
            "configuration",
            "configuration_digest",
            "source_regions",
            "vertical_separators",
            "planned_regions",
            "groups",
            "adjacency_decisions",
            "singleton_region_refs",
        )
    }
    if plan.get("plan_digest") != _json_sha256(digest_payload):
        failures.append(f"{side}:region_grouping:plan_digest")


def _validate_crops_match_plan(
    side: str,
    evidence: Mapping[str, object],
    plan: Mapping[str, object],
    failures: list[str],
) -> None:
    planned = _mapping_sequence(plan.get("planned_regions"))
    crops = _mapping_sequence(evidence.get("crops"))
    expected = tuple((value.get("region_ref"), value.get("bbox")) for value in planned)
    observed = tuple(
        (value.get("region_ref"), value.get("source_bbox")) for value in crops
    )
    if observed != expected:
        failures.append(f"{side}:crops_do_not_match_region_plan")


def _singleton_observation_check(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
) -> tuple[OcrExperimentCheck, dict[str, object]]:
    candidate_plan = _plan(candidate.evidence)
    refs = _string_sequence(candidate_plan.get("singleton_region_refs"))
    control_observations = _singleton_observations(control, refs)
    candidate_observations = _singleton_observations(candidate, refs)
    changed = tuple(
        region_ref
        for region_ref in refs
        if control_observations.get(region_ref)
        != candidate_observations.get(region_ref)
    )
    report = {
        "compared_region_refs": list(refs),
        "changed_region_refs": list(changed),
        "parameters_digest_excluded_as_declared_hypothesis_identity": True,
        "control_sha256": _json_sha256(control_observations),
        "candidate_sha256": _json_sha256(candidate_observations),
        "status": "pass" if not changed else "fail",
    }
    reasons = (
        tuple(f"singleton_observation_changed:{value}" for value in changed)
        if changed
        else ("every non-grouped source region retained its OCR observation",)
    )
    return (
        OcrExperimentCheck(
            name="singleton_observation_integrity",
            passed=not changed,
            reasons=reasons,
            details=report,
        ),
        report,
    )


def _singleton_observations(
    run: OcrExperimentRun,
    refs: Sequence[str],
) -> dict[str, object]:
    observations: dict[str, list[dict[str, object]]] = {value: [] for value in refs}
    ref_set = set(refs)
    for page in run.document.pages:
        for element in page.elements:
            if not isinstance(element, TextElement):
                continue
            ocr_records = tuple(
                value
                for value in element.provenance
                if value.stage is ProvenanceStage.OCR
            )
            for record in ocr_records:
                if len(record.source_refs) != 1 or record.source_refs[0] not in ref_set:
                    continue
                source_bbox = record.source_bbox_px
                extension = element.extensions.get("jp.reactorfront.aiteqno.ocr", {})
                structure = [
                    {
                        "provider": value.provider,
                        "provider_version": value.provider_version,
                        "source_bbox": _pixel_bbox(value.source_bbox_px),
                        "notes": value.notes,
                    }
                    for value in element.provenance
                    if value.stage is ProvenanceStage.STRUCTURE
                ]
                observations[record.source_refs[0]].append(
                    {
                        "text": element.text,
                        "source_bbox": _pixel_bbox(source_bbox),
                        "point_bbox": {
                            "x": element.bbox.x,
                            "y": element.bbox.y,
                            "width": element.bbox.width,
                            "height": element.bbox.height,
                        },
                        "confidence": (
                            {
                                "overall": element.confidence.overall,
                                "detection": element.confidence.detection,
                                "recognition": element.confidence.recognition,
                            }
                            if element.confidence is not None
                            else None
                        ),
                        "ocr": dict(extension)
                        if isinstance(extension, Mapping)
                        else {},
                        "ocr_provenance": {
                            "provider": record.provider,
                            "provider_version": record.provider_version,
                            "source_refs": list(record.source_refs),
                            "notes": record.notes,
                        },
                        "structure_provenance": structure,
                    }
                )
    for values in observations.values():
        values.sort(
            key=lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
        )
    return observations


def _table_topology_integrity_check(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
) -> OcrExperimentCheck:
    left = _normalized_table_topology(control)
    right = _normalized_table_topology(candidate)
    matched = left == right
    return OcrExperimentCheck(
        name="normalized_table_topology_integrity",
        passed=matched,
        reasons=(
            (
                "table and cell topology is byte-equivalent after OCR token "
                "identity removal"
            )
            if matched
            else "mismatch:normalized_table_topology",
        ),
        details={
            "control_sha256": _json_sha256(left),
            "candidate_sha256": _json_sha256(right),
            "byte_equivalent": matched,
        },
    )


def _normalized_table_topology(run: OcrExperimentRun) -> dict[str, object]:
    document = infer_table_topology(run.document)
    return {
        "pages": [
            {
                "page_id": page.id,
                "page_number": page.number,
                "topology": _without_ocr_token_identity(
                    page.extensions.get("jp.reactorfront.aiteqno.table_topology")
                ),
            }
            for page in document.pages
        ]
    }


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
    evidence = smoke.invocation_evidence
    configuration = _mapping(evidence.get("configuration"))
    candidate_configuration = _mapping(candidate.evidence.get("configuration"))
    if configuration is None or candidate_configuration is None:
        failures.append("smoke:configuration")
    else:
        for field_name in (
            "languages",
            "page_segmentation_mode",
            "engine_mode",
            "target_dpi",
            "region_padding_px",
            "max_working_pixels",
        ):
            if configuration.get(field_name) != candidate_configuration.get(field_name):
                failures.append(f"smoke:mismatch:configuration:{field_name}")
    for field_name in ("provider", "provider_version", "executable", "traineddata"):
        if evidence.get(field_name) != candidate.evidence.get(field_name):
            failures.append(f"smoke:mismatch:{field_name}")
    smoke_crops = _mapping_sequence(evidence.get("crops"))
    if (
        len(smoke_crops) != 1
        or smoke_crops[0].get("region_ref") is not None
        or smoke_crops[0].get("padding_pixels") != 0
        or smoke_crops[0].get("applied") is not False
    ):
        failures.append("smoke:full_page_crop")
    smoke_report = _multilingual_smoke_report(smoke)
    reasons = tuple(failures) or (
        "the immutable mixed-language fixture used the same jpn-only runtime profile",
    )
    return OcrExperimentCheck(
        name="multilingual_smoke_comparability",
        passed=not failures,
        reasons=reasons,
        details={
            "source_sha256": smoke.source_sha256,
            "status": smoke_report["status"],
            "candidate_languages": list(OCR_LANGUAGE_CANDIDATE_LANGUAGES),
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
    any_results: list[dict[str, object]] = []
    missing_any_groups: list[int] = []
    for index, alternatives in enumerate(OCR_LANGUAGE_SMOKE_REQUIRED_ANY):
        matched = [value for value in alternatives if value in normalized]
        if not matched:
            missing_any_groups.append(index)
        any_results.append(
            {
                "index": index,
                "alternatives": list(alternatives),
                "matched": matched,
                "passed": bool(matched),
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


def _target_recovery(
    control: OcrExperimentRun,
    candidate: OcrExperimentRun,
) -> dict[str, object]:
    left = {value.reference_id: value for value in control.quality.blocks}
    right = {value.reference_id: value for value in candidate.quality.blocks}
    items: list[dict[str, object]] = []
    newly_recovered: list[str] = []
    for reference_id in OCR_REGION_GROUPING_TARGET_BLOCKS:
        control_block = left.get(reference_id)
        candidate_block = right.get(reference_id)
        gained = bool(
            control_block is not None
            and candidate_block is not None
            and not control_block.recovered
            and candidate_block.recovered
        )
        if gained:
            newly_recovered.append(reference_id)
        items.append(
            {
                "reference_id": reference_id,
                "control": _block_summary(control_block),
                "candidate": _block_summary(candidate_block),
                "newly_recovered": gained,
            }
        )
    return {
        "required_any": list(OCR_REGION_GROUPING_TARGET_BLOCKS),
        "items": items,
        "newly_recovered": newly_recovered,
        "status": "pass" if newly_recovered else "fail",
    }


def _block_summary(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "observed_text": value.observed_text,
        "character_accuracy": value.character_accuracy,
        "recovered": value.recovered,
    }


def _invocation_invariant(evidence: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in evidence.items()
        if key
        not in {
            "region_grouping",
            "parameters_digest",
            "raster_transform",
            "crop_padding",
            "crops",
        }
    }


def _plan(evidence: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(evidence.get("region_grouping")) or {}


def _records_by_ref(
    records: Sequence[Mapping[str, object]],
    field_name: str,
) -> dict[str, Mapping[str, object]]:
    return {
        value: record
        for record in records
        if isinstance((value := record.get(field_name)), str) and value
    }


def _union_bbox(values: Sequence[Mapping[str, object]]) -> dict[str, int] | None:
    parsed: list[tuple[int, int, int, int]] = []
    for value in values:
        numbers = (
            value.get("x"),
            value.get("y"),
            value.get("width"),
            value.get("height"),
        )
        if not all(
            isinstance(number, int) and not isinstance(number, bool)
            for number in numbers
        ):
            return None
        parsed.append(numbers)
    if not parsed:
        return None
    left = min(value[0] for value in parsed)
    top = min(value[1] for value in parsed)
    right = max(value[0] + value[2] for value in parsed)
    bottom = max(value[1] + value[3] for value in parsed)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _pixel_bbox(value: object) -> dict[str, int] | None:
    if value is None:
        return None
    return {
        "x": value.x,
        "y": value.y,
        "width": value.width,
        "height": value.height,
    }


def _at_least(value: object, minimum: object) -> bool:
    return (
        _finite_number(value) is not None
        and _finite_number(minimum) is not None
        and float(value) >= float(minimum)
    )


def _at_most(value: object, maximum: object) -> bool:
    return (
        _finite_number(value) is not None
        and _finite_number(maximum) is not None
        and float(value) <= float(maximum)
    )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _source_dpi_is_96(value: Mapping[str, object]) -> bool:
    x = _finite_number(value.get("x"))
    y = _finite_number(value.get("y"))
    return (
        x is not None
        and y is not None
        and math.isclose(
            (x + y) / 2.0,
            96.0,
            abs_tol=0.1,
        )
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
    "OCR_REGION_GROUPING_EVALUATOR_NAME",
    "OCR_REGION_GROUPING_EVALUATOR_VERSION",
    "OCR_REGION_GROUPING_TARGET_BLOCKS",
    "compare_ocr_region_grouping",
]
