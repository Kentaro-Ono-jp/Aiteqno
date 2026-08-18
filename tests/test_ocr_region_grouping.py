from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from aiteqno.application import compare_ocr_region_grouping, plan_ocr_regions
from aiteqno.domain import DocumentIR
from aiteqno.ports import (
    OcrBlockEvaluation,
    OcrExperimentDecision,
    OcrExperimentRun,
    OcrMetricEvaluation,
    OcrRegionGroupingConfig,
)
from tests.test_ocr_grouping import region
from tests.test_ocr_language import invocation_evidence, smoke_run
from tests.test_ocr_resolution import document, quality


def grouping_plan(*, enabled: bool):
    return plan_ocr_regions(
        (
            ("region-title-1", region(50, 40, 300, 60)),
            ("region-title-2", region(355, 40, 30, 60)),
            ("region-phone", region(40, 190, 60, 40)),
        ),
        (),
        config=OcrRegionGroupingConfig(enabled=enabled),
    )


def candidate_document(*, enabled: bool, digest: str) -> DocumentIR:
    data = document(split_text=True, parameters_digest=digest).to_dict()
    texts = [value for value in data["pages"][0]["elements"] if value["type"] == "text"]
    first, phone = texts
    first["provenance"][0]["source_refs"] = [
        "p001-text-line-group-0000" if enabled else "region-title-1"
    ]
    first["provenance"][0]["parameters_digest"] = digest
    phone_bbox = {"x": 50, "y": 200, "width": 30, "height": 20}
    phone["bbox"] = {"x": 37.5, "y": 150.0, "width": 22.5, "height": 15.0}
    phone["provenance"][0]["source_refs"] = ["region-phone"]
    phone["provenance"][0]["source_bbox_px"] = phone_bbox
    phone["provenance"][0]["parameters_digest"] = digest
    return DocumentIR.from_dict(data)


def _crop(record: dict[str, object], index: int) -> dict[str, object]:
    bbox = copy.deepcopy(record["bbox"])
    width = bbox["width"]
    height = bbox["height"]
    return {
        "region_ref": record["region_ref"],
        "source_bbox": bbox,
        "source_dimensions": {"width": width, "height": height},
        "pre_padding_dimensions": {"width": width, "height": height},
        "working_dimensions": {"width": width + 4, "height": height + 4},
        "padding_pixels": 2,
        "applied": True,
        "working_raster_sha256": f"{index + 1:x}" * 64,
    }


def grouping_evidence(*, enabled: bool, digest: str) -> dict[str, object]:
    plan = grouping_plan(enabled=enabled)
    evidence = invocation_evidence(
        languages=("jpn",),
        parameters_digest=digest,
    )
    plan_dict = plan.evidence.to_dict()
    crops = [
        _crop(record, index)
        for index, record in enumerate(plan_dict["planned_regions"])
    ]
    transform_crops = [
        {
            "region_ref": crop["region_ref"],
            "source_bbox": copy.deepcopy(crop["source_bbox"]),
            "source_dimensions": copy.deepcopy(crop["source_dimensions"]),
            "working_dimensions": copy.deepcopy(crop["source_dimensions"]),
            "actual_scale": {"x": 1.0, "y": 1.0},
            "resized": False,
            "working_raster_sha256": f"{index + 8:x}" * 64,
        }
        for index, crop in enumerate(crops)
    ]
    evidence["raster_transform"]["crops"] = transform_crops
    evidence["crop_padding"]["crops"] = copy.deepcopy(crops)
    evidence["crops"] = copy.deepcopy(crops)
    evidence["region_grouping"] = plan_dict
    return evidence


def grouping_quality(*, candidate: bool, title_recovered: bool = True, score=72.0):
    result = quality(
        dpi=96,
        text_score=score if candidate else 70.0,
        block_score=50.0 if candidate and title_recovered else 0.0,
        anchor_score=0.0,
        recovered_blocks=(),
        recovered_anchors=(),
    )
    blocks = (
        OcrBlockEvaluation(
            reference_id="title",
            expected_text="文書解析評価シート",
            observed_text="文書解析評価シート" if candidate and title_recovered else "",
            candidate_element_ids=("p001-text-0000",)
            if candidate and title_recovered
            else (),
            character_accuracy=100.0 if candidate and title_recovered else 0.0,
            recovered=bool(candidate and title_recovered),
            essential=True,
        ),
        OcrBlockEvaluation(
            reference_id="content-structure",
            expected_text="表箇条書き段組み入力欄なし",
            observed_text="",
            candidate_element_ids=(),
            character_accuracy=0.0,
            recovered=False,
            essential=True,
        ),
    )
    runtime = replace(
        result.runtime,
        languages=("jpn",),
        traineddata=result.runtime.traineddata[:1],
    )
    return replace(
        result,
        runtime=runtime,
        observed_text="PNG PDF DOCX JSON 30 90 70",
        logical_block_coverage=OcrMetricEvaluation(
            score=50.0 if candidate and title_recovered else 0.0,
            minimum=60.0,
        ),
        blocks=blocks,
        unrecovered_blocks=("content-structure",)
        if candidate and title_recovered
        else ("title", "content-structure"),
    )


def experiment_run(*, candidate: bool, title_recovered: bool = True, score=72.0):
    digest = ("6" if candidate else "3") * 64
    return OcrExperimentRun(
        quality=grouping_quality(
            candidate=candidate,
            title_recovered=title_recovered,
            score=score,
        ),
        document=candidate_document(enabled=candidate, digest=digest),
        evidence=grouping_evidence(enabled=candidate, digest=digest),
    )


class OcrRegionGroupingComparisonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.control = experiment_run(candidate=False)
        self.candidate = experiment_run(candidate=True)

    def compare(self, *, candidate=None, smoke=None):
        return compare_ocr_region_grouping(
            self.control,
            candidate or self.candidate,
            multilingual_smoke=smoke or smoke_run(),
        )

    def test_supported_result_requires_fixed_plan_target_and_singletons(self) -> None:
        result = self.compare()

        self.assertEqual(result.decision, OcrExperimentDecision.SUPPORTED)
        self.assertTrue(all(value.passed for value in result.checks))
        self.assertEqual(result.target_recovery["newly_recovered"], ["title"])
        self.assertEqual(result.singleton_observations["status"], "pass")
        report = result.to_dict()
        self.assertEqual(
            report["adoption_policy"]["allowed_geometry_differences"],
            ["region_plan"],
        )
        self.assertEqual(report["multilingual_smoke"]["status"], "pass")
        self.assertEqual(result.to_json(), result.to_json())

    def test_missing_target_recovery_is_inconclusive(self) -> None:
        candidate = experiment_run(candidate=True, title_recovered=False)

        result = self.compare(candidate=candidate)

        self.assertEqual(result.decision, OcrExperimentDecision.INCONCLUSIVE)
        self.assertIn("required_grouping_target_not_newly_recovered", result.reasons)

    def test_singleton_observation_or_plan_drift_is_invalid(self) -> None:
        data = self.candidate.document.to_dict()
        texts = [
            value for value in data["pages"][0]["elements"] if value["type"] == "text"
        ]
        texts[1]["text"] = "changed-phone"
        changed_singleton = replace(
            self.candidate,
            document=DocumentIR.from_dict(data),
        )
        evidence = copy.deepcopy(dict(self.candidate.evidence))
        evidence["region_grouping"]["configuration"][
            "minimum_vertical_overlap_ratio"
        ] = 0.5
        changed_plan = replace(self.candidate, evidence=evidence)

        self.assertEqual(
            self.compare(candidate=changed_singleton).decision,
            OcrExperimentDecision.INVALID,
        )
        self.assertEqual(
            self.compare(candidate=changed_plan).decision,
            OcrExperimentDecision.INVALID,
        )

    def test_protected_literal_or_smoke_loss_is_regressed(self) -> None:
        protected_quality = replace(
            self.candidate.quality,
            observed_text="PNG PDF DOCX JSON 30 90",
        )
        protected = self.compare(
            candidate=replace(self.candidate, quality=protected_quality)
        )
        smoke = self.compare(smoke=smoke_run(observed_text="患者番号"))

        self.assertEqual(protected.decision, OcrExperimentDecision.REGRESSED)
        self.assertIn("regression:protected_literal:70", protected.reasons)
        self.assertEqual(smoke.decision, OcrExperimentDecision.REGRESSED)
        self.assertIn(
            "regression:multilingual_smoke:literal:AITEQNO",
            smoke.reasons,
        )


if __name__ == "__main__":
    unittest.main()
