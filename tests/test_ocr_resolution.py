from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from aiteqno.application import compare_ocr_resolution
from aiteqno.domain import DocumentIR
from aiteqno.ports import (
    EvaluationState,
    HardGateResult,
    OcrAnchorEvaluation,
    OcrBlockEvaluation,
    OcrConfidenceDistribution,
    OcrMetricEvaluation,
    OcrQualityResult,
    OcrResolutionDecision,
    OcrResolutionRun,
    OcrRuntimeEvidence,
    OcrTrainedDataEvidence,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "document_ir"
    / "canonical.document.ir.json"
)
SOURCE_SHA256 = "a" * 64


def runtime(
    *,
    dpi: int,
    version: str = "5.5.0-test",
    source_dpi: float = 96,
) -> OcrRuntimeEvidence:
    return OcrRuntimeEvidence(
        provider="tesseract",
        provider_version=version,
        executable="/same/bin/tesseract",
        languages=("jpn", "eng"),
        page_segmentation_mode=6,
        engine_mode=3,
        effective_ocr_dpi=dpi,
        source_dpi_x=source_dpi,
        source_dpi_y=source_dpi,
        traineddata=(
            OcrTrainedDataEvidence(
                language="jpn",
                size_bytes=100,
                sha256="1" * 64,
            ),
            OcrTrainedDataEvidence(
                language="eng",
                size_bytes=200,
                sha256="2" * 64,
            ),
        ),
        operating_system="SyntheticOS 1.0",
        python_version="3.14.0-test",
    )


def quality(
    *,
    dpi: int,
    text_score: float,
    block_score: float,
    anchor_score: float,
    recovered_blocks: tuple[str, ...],
    recovered_anchors: tuple[str, ...],
    runtime_version: str = "5.5.0-test",
    token_count: int = 10,
    source_dpi: float = 96,
) -> OcrQualityResult:
    blocks = tuple(
        OcrBlockEvaluation(
            reference_id=identifier,
            expected_text=expected,
            observed_text=expected if identifier in recovered_blocks else "",
            candidate_element_ids=(f"token-{index}",)
            if identifier in recovered_blocks
            else (),
            character_accuracy=100 if identifier in recovered_blocks else 0,
            recovered=identifier in recovered_blocks,
            essential=True,
        )
        for index, (identifier, expected) in enumerate(
            (("heading", "文書解析"), ("details", "対象形式"))
        )
    )
    anchors = tuple(
        OcrAnchorEvaluation(
            anchor=value,
            recovered=value in recovered_anchors,
        )
        for value in ("文書解析", "対象形式")
    )
    missing_blocks = tuple(
        block.reference_id for block in blocks if not block.recovered
    )
    gates = (
        HardGateResult(
            name="source_digest_matches_reference",
            passed=True,
            reason="same reviewed source",
        ),
        HardGateResult(
            name="reference_reviewed",
            passed=True,
            reason="reviewed",
        ),
        HardGateResult(
            name="candidate_page_count",
            passed=True,
            reason="same page count",
        ),
        HardGateResult(
            name="essential_anchor_recall",
            passed=len(recovered_anchors) == 2,
            reason="quality gate is independent of comparison adoption",
        ),
        HardGateResult(
            name="essential_logical_blocks",
            passed=len(recovered_blocks) == 2,
            reason="quality gate is independent of comparison adoption",
        ),
    )
    return OcrQualityResult(
        evaluator_name="aiteqno-source-to-candidate-ir-ocr",
        evaluator_version="1.0.0",
        reference_id="synthetic-source-v1",
        reference_source_sha256=SOURCE_SHA256,
        observed_source_sha256=SOURCE_SHA256,
        runtime=runtime(
            dpi=dpi,
            version=runtime_version,
            source_dpi=source_dpi,
        ),
        expected_text="文書解析対象形式",
        observed_text="synthetic",
        text_character_accuracy=OcrMetricEvaluation(score=text_score, minimum=70),
        logical_block_coverage=OcrMetricEvaluation(score=block_score, minimum=60),
        essential_anchor_recall=OcrMetricEvaluation(
            score=anchor_score,
            minimum=100,
        ),
        block_recovery_accuracy_minimum=70,
        low_confidence_threshold=0.5,
        blocks=blocks,
        anchors=anchors,
        missing_strings=("synthetic-missing",),
        extra_strings=("synthetic-extra",),
        unrecovered_blocks=missing_blocks,
        confidence_distribution=OcrConfidenceDistribution(
            token_count=token_count,
            available_count=0,
            missing_count=token_count,
            minimum=None,
            p10=None,
            median=None,
            mean=None,
            p90=None,
            maximum=None,
        ),
        low_confidence_tokens=(),
        hard_gates=gates,
        state=EvaluationState.FAIL,
        reasons=("synthetic_quality_below_contract",),
    )


def document(
    *,
    bbox_x: float = 48,
    nontext_color: str | None = None,
    split_text: bool = False,
    parameters_digest: str = "3" * 64,
    source_dpi: float = 96,
) -> DocumentIR:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    page = data["pages"][0]
    text = next(element for element in page["elements"] if element["type"] == "text")
    data["extensions"] = {
        "jp.reactorfront.aiteqno.extract": {
            "pipeline_provider": "aiteqno",
            "pipeline_version": "synthetic",
            "source_sha256": SOURCE_SHA256,
        }
    }
    text["bbox"]["x"] = bbox_x
    text["provenance"] = [
        {
            "stage": "ocr",
            "provider": "tesseract",
            "provider_version": "5.5.0-test",
            "source_refs": ["region-heading"],
            "source_bbox_px": {"x": 64, "y": 56, "width": 280, "height": 32},
            "parameters_digest": parameters_digest,
        }
    ]
    if split_text:
        second = copy.deepcopy(text)
        second["id"] = "p001-text-0001-split"
        second["reading_order"] = 1
        second["bbox"] = {"x": 300, "y": 42, "width": 24, "height": 24}
        second["provenance"][0]["source_bbox_px"] = {
            "x": 400,
            "y": 56,
            "width": 32,
            "height": 32,
        }
        second["provenance"][0]["source_refs"] = ["region-details"]
        insert_at = page["elements"].index(text) + 1
        page["elements"].insert(insert_at, second)
    page["source"]["dpi_x"] = source_dpi
    page["source"]["dpi_y"] = source_dpi
    for element in page["elements"]:
        if element["type"] != "text":
            continue
        source_bbox = element["provenance"][0]["source_bbox_px"]
        left = round(source_bbox["x"] * 72 / source_dpi, 6)
        top = round(source_bbox["y"] * 72 / source_dpi, 6)
        right = round(
            (source_bbox["x"] + source_bbox["width"]) * 72 / source_dpi,
            6,
        )
        bottom = round(
            (source_bbox["y"] + source_bbox["height"]) * 72 / source_dpi,
            6,
        )
        element["bbox"] = {
            "x": left,
            "y": top,
            "width": round(right - left, 6),
            "height": round(bottom - top, 6),
        }
    if bbox_x != 48:
        text["bbox"]["x"] = bbox_x
    if nontext_color is not None:
        line = next(
            element for element in page["elements"] if element["type"] == "line"
        )
        line["style"]["color"] = nontext_color
    return DocumentIR.from_dict(data)


def transform(*, candidate: bool, source_dpi: float = 96) -> dict[str, object]:
    def crop(
        *,
        region_ref: str,
        x: int,
        y: int,
        source_width: int,
        source_height: int,
        digest_character: str,
    ) -> dict[str, object]:
        scale = 300 / source_dpi if candidate and source_dpi < 300 else 1
        working_width = int(source_width * scale + 0.5)
        working_height = int(source_height * scale + 0.5)
        return {
            "region_ref": region_ref,
            "source_bbox": {
                "x": x,
                "y": y,
                "width": source_width,
                "height": source_height,
            },
            "source_dimensions": {
                "width": source_width,
                "height": source_height,
            },
            "working_dimensions": {
                "width": working_width,
                "height": working_height,
            },
            "actual_scale": {
                "x": round(working_width / source_width, 12),
                "y": round(working_height / source_height, 12),
            },
            "resized": candidate,
            "working_raster_sha256": digest_character * 64,
        }

    return {
        "schema_version": "1.0",
        "transform_version": "tesseract-raster-transform-v1",
        "enabled": candidate,
        "target_dpi": 300 if candidate else None,
        "source_effective_dpi": float(source_dpi),
        "effective_ocr_dpi": 300 if candidate else 96,
        "max_working_pixels": 40_000_000,
        "pixel_mode": "RGB",
        "resampling": "LANCZOS" if candidate else "none",
        "imaging_library": {"name": "Pillow", "version": "12.3.0"},
        "inverse_mapping_policy": (
            "clip-working-bbox; source-left-top=floor(edge*source/working); "
            "source-right-bottom=ceil(edge*source/working); clamp-source-crop; "
            "add-source-offset"
        ),
        "crops": [
            crop(
                region_ref="region-heading",
                x=0,
                y=0,
                source_width=400,
                source_height=100,
                digest_character="5" if candidate else "4",
            ),
            crop(
                region_ref="region-details",
                x=350,
                y=40,
                source_width=100,
                source_height=80,
                digest_character="7" if candidate else "8",
            ),
        ],
    }


def run(
    *,
    candidate: bool,
    text_score: float,
    block_score: float,
    anchor_score: float,
    recovered_blocks: tuple[str, ...],
    recovered_anchors: tuple[str, ...],
    quality_override: OcrQualityResult | None = None,
    document_override: DocumentIR | None = None,
    source_dpi: float = 96,
) -> OcrResolutionRun:
    return OcrResolutionRun(
        quality=quality_override
        or quality(
            dpi=300 if candidate else 96,
            text_score=text_score,
            block_score=block_score,
            anchor_score=anchor_score,
            recovered_blocks=recovered_blocks,
            recovered_anchors=recovered_anchors,
            source_dpi=source_dpi,
        ),
        document=document_override
        or document(
            split_text=candidate,
            parameters_digest=("6" if candidate else "3") * 64,
            source_dpi=source_dpi,
        ),
        transform=transform(candidate=candidate, source_dpi=source_dpi),
    )


class OcrResolutionComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.control = run(
            candidate=False,
            text_score=40.0,
            block_score=50.0,
            anchor_score=50.0,
            recovered_blocks=("heading",),
            recovered_anchors=("文書解析",),
        )

    def candidate(
        self,
        *,
        text_score: float = 42.0,
        block_score: float = 100.0,
        anchor_score: float = 100.0,
        recovered_blocks: tuple[str, ...] = ("heading", "details"),
        recovered_anchors: tuple[str, ...] = ("文書解析", "対象形式"),
        quality_override: OcrQualityResult | None = None,
        document_override: DocumentIR | None = None,
    ) -> OcrResolutionRun:
        return run(
            candidate=True,
            text_score=text_score,
            block_score=block_score,
            anchor_score=anchor_score,
            recovered_blocks=recovered_blocks,
            recovered_anchors=recovered_anchors,
            quality_override=quality_override,
            document_override=document_override,
        )

    def test_supported_when_all_adoption_conditions_pass(self) -> None:
        result = compare_ocr_resolution(self.control, self.candidate())

        self.assertEqual(result.decision, OcrResolutionDecision.SUPPORTED)
        self.assertEqual(result.text_character_accuracy.delta, 2.0)
        self.assertEqual(result.anchors.gained, ("対象形式",))
        self.assertEqual(result.anchors.lost, ())
        self.assertEqual(result.blocks.gained, ("details",))
        self.assertTrue(all(check.passed for check in result.checks))
        geometry = next(
            check
            for check in result.checks
            if check.name == "source_geometry_and_provenance_integrity"
        )
        self.assertEqual(geometry.details["control_region_refs"], ["region-heading"])
        self.assertEqual(
            geometry.details["candidate_region_refs"],
            ["region-details", "region-heading"],
        )
        report = result.to_dict()
        self.assertEqual(report["decision"], "supported")
        self.assertFalse(report["adoption_policy"]["confidence_is_scoring_input"])
        self.assertFalse(report["adoption_policy"]["token_count_is_scoring_input"])
        self.assertEqual(result.to_json(), result.to_json())

    def test_compatibility_wrapper_preserves_resolution_artifact_scope(self) -> None:
        result = compare_ocr_resolution(self.control, self.candidate())
        report = result.to_dict()

        self.assertEqual(
            report["scope"],
            {
                "experiment": "tesseract_ocr_input_resolution",
                "control": "source_resolution",
                "candidate": "300_dpi_working_raster",
                "ends_before": [
                    "docx",
                    "preview",
                    "libreoffice",
                    "poppler",
                    "rendered_page_ocr",
                ],
            },
        )
        self.assertEqual(
            report["checks"]["runtime_equivalence"]["details"]["allowed_differences"],
            ["runtime.configuration.effective_ocr_dpi", "ocr_input_transform"],
        )
        self.assertIn("transform_sha256", report["runs"]["control"])
        self.assertNotIn("evidence_sha256", report["runs"]["control"])

    def test_inconclusive_when_text_gain_is_below_one_point(self) -> None:
        result = compare_ocr_resolution(
            self.control,
            self.candidate(text_score=40.999999),
        )

        self.assertEqual(result.decision, OcrResolutionDecision.INCONCLUSIVE)
        self.assertEqual(
            result.reasons,
            ("text_accuracy_delta_below_minimum:0.999999<1",),
        )

    def test_regressed_when_control_recoveries_are_lost(self) -> None:
        result = compare_ocr_resolution(
            self.control,
            self.candidate(
                text_score=42,
                block_score=0,
                anchor_score=0,
                recovered_blocks=(),
                recovered_anchors=(),
            ),
        )

        self.assertEqual(result.decision, OcrResolutionDecision.REGRESSED)
        self.assertEqual(result.anchors.lost, ("文書解析",))
        self.assertEqual(result.blocks.lost, ("heading",))
        self.assertIn("regression:logical_block_coverage", result.reasons)
        self.assertIn("regression:essential_anchor_recall", result.reasons)
        self.assertIn(
            "regression:unrecovered_essential_blocks_increased",
            result.reasons,
        )

    def test_regressed_when_text_accuracy_decreases(self) -> None:
        result = compare_ocr_resolution(
            self.control,
            self.candidate(text_score=39),
        )

        self.assertEqual(result.decision, OcrResolutionDecision.REGRESSED)
        self.assertIn("regression:text_character_accuracy", result.reasons)

    def test_invalid_when_runtime_differs_beyond_effective_dpi(self) -> None:
        changed = quality(
            dpi=300,
            text_score=42,
            block_score=100,
            anchor_score=100,
            recovered_blocks=("heading", "details"),
            recovered_anchors=("文書解析", "対象形式"),
            runtime_version="5.6.0-different",
        )
        result = compare_ocr_resolution(
            self.control,
            self.candidate(quality_override=changed),
        )

        self.assertEqual(result.decision, OcrResolutionDecision.INVALID)
        runtime_check = next(
            check for check in result.checks if check.name == "runtime_equivalence"
        )
        self.assertFalse(runtime_check.passed)
        self.assertEqual(runtime_check.reasons, ("mismatch:provider_version",))

    def test_invalid_when_threshold_changes(self) -> None:
        candidate_quality = self.candidate().quality
        candidate_quality = replace(
            candidate_quality,
            text_character_accuracy=OcrMetricEvaluation(score=42, minimum=69),
        )
        result = compare_ocr_resolution(
            self.control,
            self.candidate(quality_override=candidate_quality),
        )

        self.assertEqual(result.decision, OcrResolutionDecision.INVALID)
        context = next(
            check
            for check in result.checks
            if check.name == "source_reference_threshold_normalization"
        )
        self.assertIn("mismatch:text_accuracy_minimum", context.reasons)

    def test_invalid_when_candidate_geometry_disagrees_with_provenance(self) -> None:
        result = compare_ocr_resolution(
            self.control,
            self.candidate(
                document_override=document(bbox_x=49, parameters_digest="6" * 64)
            ),
        )

        self.assertEqual(result.decision, OcrResolutionDecision.INVALID)
        geometry = next(
            check
            for check in result.checks
            if check.name == "source_geometry_and_provenance_integrity"
        )
        self.assertFalse(geometry.passed)
        self.assertIn(
            "candidate:text:p001-text-0000:bbox_provenance_mismatch",
            geometry.reasons,
        )

    def test_invalid_when_nontext_ir_changes(self) -> None:
        result = compare_ocr_resolution(
            self.control,
            self.candidate(
                document_override=document(
                    nontext_color="#ff0000",
                    parameters_digest="6" * 64,
                )
            ),
        )

        self.assertEqual(result.decision, OcrResolutionDecision.INVALID)
        integrity = next(
            check
            for check in result.checks
            if check.name == "non_text_document_ir_integrity"
        )
        self.assertEqual(
            integrity.reasons,
            ("mismatch:line_rectangle_image_elements:0",),
        )

    def test_confidence_and_token_count_do_not_change_decision(self) -> None:
        first = self.candidate().quality
        changed = replace(
            first,
            confidence_distribution=OcrConfidenceDistribution(
                token_count=999,
                available_count=0,
                missing_count=999,
                minimum=None,
                p10=None,
                median=None,
                mean=None,
                p90=None,
                maximum=None,
            ),
        )

        normal = compare_ocr_resolution(self.control, self.candidate())
        diagnostic_change = compare_ocr_resolution(
            self.control,
            self.candidate(quality_override=changed),
        )

        self.assertEqual(normal.decision, OcrResolutionDecision.SUPPORTED)
        self.assertEqual(diagnostic_change.decision, normal.decision)
        self.assertEqual(diagnostic_change.reasons, normal.reasons)

    def test_transform_evidence_must_describe_exact_fixed_hypothesis(self) -> None:
        candidate = self.candidate()
        changed_transform = copy.deepcopy(dict(candidate.transform))
        changed_transform["resampling"] = "BICUBIC"
        candidate = OcrResolutionRun(
            quality=candidate.quality,
            document=candidate.document,
            transform=changed_transform,
        )

        result = compare_ocr_resolution(self.control, candidate)

        self.assertEqual(result.decision, OcrResolutionDecision.INVALID)
        transform_check = next(
            check for check in result.checks if check.name == "transform_integrity"
        )
        self.assertIn("candidate:resampling", transform_check.reasons)

    def test_candidate_transform_cannot_claim_300_dpi_without_resizing(self) -> None:
        candidate = self.candidate()
        changed_transform = copy.deepcopy(dict(candidate.transform))
        for crop in changed_transform["crops"]:
            crop["working_dimensions"] = copy.deepcopy(crop["source_dimensions"])
            crop["actual_scale"] = {"x": 1.0, "y": 1.0}
            crop["resized"] = False
        candidate = OcrResolutionRun(
            quality=candidate.quality,
            document=candidate.document,
            transform=changed_transform,
        )

        result = compare_ocr_resolution(self.control, candidate)

        self.assertEqual(result.decision, OcrResolutionDecision.INVALID)
        transform_check = next(
            check for check in result.checks if check.name == "transform_integrity"
        )
        self.assertIn(
            "candidate:crop:0:deterministic_working_dimensions",
            transform_check.reasons,
        )

    def test_empty_or_false_policy_transform_evidence_is_invalid(self) -> None:
        for field_name, value, reason in (
            ("crops", [], "candidate:crops_empty"),
            (
                "inverse_mapping_policy",
                "wrong",
                "candidate:inverse_mapping_policy",
            ),
            (
                "source_effective_dpi",
                95.0,
                "candidate:source_effective_dpi_runtime_mismatch",
            ),
        ):
            with self.subTest(field_name=field_name):
                candidate = self.candidate()
                changed_transform = copy.deepcopy(dict(candidate.transform))
                changed_transform[field_name] = value
                candidate = OcrResolutionRun(
                    quality=candidate.quality,
                    document=candidate.document,
                    transform=changed_transform,
                )

                result = compare_ocr_resolution(self.control, candidate)

                self.assertEqual(result.decision, OcrResolutionDecision.INVALID)
                transform_check = next(
                    check
                    for check in result.checks
                    if check.name == "transform_integrity"
                )
                self.assertIn(reason, transform_check.reasons)

    def test_transform_evidence_requires_actual_pillow_version(self) -> None:
        candidate = self.candidate()
        changed_transform = copy.deepcopy(dict(candidate.transform))
        changed_transform["imaging_library"]["version"] = ""
        candidate = OcrResolutionRun(
            quality=candidate.quality,
            document=candidate.document,
            transform=changed_transform,
        )

        result = compare_ocr_resolution(self.control, candidate)

        self.assertEqual(result.decision, OcrResolutionDecision.INVALID)
        transform_check = next(
            check for check in result.checks if check.name == "transform_integrity"
        )
        self.assertIn("candidate:imaging_library", transform_check.reasons)

    def test_region_crop_requires_candidate_parent_region_provenance(self) -> None:
        candidate_document = self.candidate().document.to_dict()
        for element in candidate_document["pages"][0]["elements"]:
            if element["type"] == "text":
                for provenance in element["provenance"]:
                    if provenance["stage"] == "ocr":
                        provenance["source_refs"] = []
        candidate = self.candidate(
            document_override=DocumentIR.from_dict(candidate_document)
        )

        result = compare_ocr_resolution(self.control, candidate)

        self.assertEqual(result.decision, OcrResolutionDecision.INVALID)
        geometry = next(
            check
            for check in result.checks
            if check.name == "source_geometry_and_provenance_integrity"
        )
        self.assertIn(
            "candidate:text:p001-text-0000:parent_region_missing",
            geometry.reasons,
        )

    def test_candidate_bbox_must_be_inside_its_named_parent_region(self) -> None:
        candidate_document = self.candidate().document.to_dict()
        second = next(
            element
            for element in candidate_document["pages"][0]["elements"]
            if element["id"] == "p001-text-0001-split"
        )
        second["provenance"][0]["source_refs"] = ["region-heading"]
        candidate = self.candidate(
            document_override=DocumentIR.from_dict(candidate_document)
        )

        result = compare_ocr_resolution(self.control, candidate)

        self.assertEqual(result.decision, OcrResolutionDecision.INVALID)
        geometry = next(
            check
            for check in result.checks
            if check.name == "source_geometry_and_provenance_integrity"
        )
        self.assertIn(
            "candidate:text:p001-text-0001-split:outside_parent_region",
            geometry.reasons,
        )

    def test_real_png_dpi_uses_edge_rounded_point_geometry(self) -> None:
        control = run(
            candidate=False,
            text_score=40,
            block_score=50,
            anchor_score=50,
            recovered_blocks=("heading",),
            recovered_anchors=("文書解析",),
            source_dpi=96.012,
        )
        candidate = run(
            candidate=True,
            text_score=42,
            block_score=100,
            anchor_score=100,
            recovered_blocks=("heading", "details"),
            recovered_anchors=("文書解析", "対象形式"),
            source_dpi=96.012,
        )

        result = compare_ocr_resolution(control, candidate)

        self.assertEqual(result.decision, OcrResolutionDecision.SUPPORTED)
        geometry = next(
            check
            for check in result.checks
            if check.name == "source_geometry_and_provenance_integrity"
        )
        self.assertTrue(geometry.passed, geometry.reasons)


if __name__ == "__main__":
    unittest.main()
