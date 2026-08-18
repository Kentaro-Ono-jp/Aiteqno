from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from aiteqno.application import compare_ocr_padding
from aiteqno.ports import OcrExperimentDecision, OcrExperimentRun
from tests.test_ocr_resolution import document, quality


def padding_evidence(*, candidate: bool) -> dict[str, object]:
    padding = 2 if candidate else 0

    def crop(
        *,
        region_ref: str,
        x: int,
        y: int,
        width: int,
        height: int,
        digest_character: str,
    ) -> dict[str, object]:
        return {
            "region_ref": region_ref,
            "source_bbox": {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            },
            "source_dimensions": {"width": width, "height": height},
            "pre_padding_dimensions": {"width": width, "height": height},
            "working_dimensions": {
                "width": width + 2 * padding,
                "height": height + 2 * padding,
            },
            "padding_pixels": padding,
            "applied": candidate,
            "working_raster_sha256": digest_character * 64,
        }

    return {
        "schema_version": "1.0",
        "padding_version": "tesseract-crop-padding-v1",
        "enabled": candidate,
        "configured_padding_pixels": padding,
        "source_effective_dpi": 96.0,
        "effective_ocr_dpi": 96,
        "target_dpi": None,
        "scope": "region-crops-only",
        "pixel_mode": "RGB",
        "border_color": [255, 255, 255],
        "operation_order": [
            "crop-source-region",
            "apply-raster-resolution-transform",
            "add-artificial-white-border",
            "invoke-tesseract",
            "subtract-artificial-border-from-result",
            "restore-original-source-pixel-coordinates",
        ],
        "inverse_mapping_policy": (
            "clip-ocr-bbox; subtract-artificial-border; "
            "clamp-pre-padding-raster; apply-raster-transform-inverse; "
            "add-source-offset"
        ),
        "max_working_pixels": 40_000_000,
        "imaging_library": {"name": "Pillow", "version": "12.3.0"},
        "crops": [
            crop(
                region_ref="region-heading",
                x=0,
                y=0,
                width=400,
                height=100,
                digest_character="5" if candidate else "4",
            ),
            crop(
                region_ref="region-details",
                x=350,
                y=40,
                width=100,
                height=80,
                digest_character="7" if candidate else "8",
            ),
        ],
    }


def experiment_run(
    *,
    candidate: bool,
    text_score: float,
    block_score: float,
    anchor_score: float,
    recovered_blocks: tuple[str, ...],
    recovered_anchors: tuple[str, ...],
) -> OcrExperimentRun:
    return OcrExperimentRun(
        quality=quality(
            dpi=96,
            text_score=text_score,
            block_score=block_score,
            anchor_score=anchor_score,
            recovered_blocks=recovered_blocks,
            recovered_anchors=recovered_anchors,
        ),
        document=document(
            split_text=candidate,
            parameters_digest=("6" if candidate else "3") * 64,
        ),
        evidence=padding_evidence(candidate=candidate),
    )


class OcrPaddingComparisonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.control = experiment_run(
            candidate=False,
            text_score=40.0,
            block_score=50.0,
            anchor_score=50.0,
            recovered_blocks=("heading",),
            recovered_anchors=("文書解析",),
        )
        self.candidate = experiment_run(
            candidate=True,
            text_score=42.0,
            block_score=100.0,
            anchor_score=100.0,
            recovered_blocks=("heading", "details"),
            recovered_anchors=("文書解析", "対象形式"),
        )

    def test_supported_report_fixes_exact_two_pixel_white_padding(self) -> None:
        result = compare_ocr_padding(self.control, self.candidate)

        self.assertEqual(result.decision, OcrExperimentDecision.SUPPORTED)
        self.assertEqual(result.text_character_accuracy.delta, 2.0)
        self.assertEqual(result.blocks.gained, ("details",))
        self.assertEqual(result.anchors.gained, ("対象形式",))
        self.assertTrue(all(check.passed for check in result.checks))
        report = result.to_dict()
        self.assertEqual(
            report["scope"],
            {
                "experiment": "tesseract_ocr_crop_padding",
                "control": "no_artificial_padding",
                "candidate": "two_source_pixel_white_padding",
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
            report["checks"]["crop_padding_integrity"]["details"][
                "candidate_padding_pixels"
            ],
            2,
        )
        self.assertEqual(result.to_json(), result.to_json())

    def test_padding_size_dimension_color_and_source_crop_drift_are_invalid(
        self,
    ) -> None:
        mutations = {
            "padding": lambda evidence: evidence.update(configured_padding_pixels=3),
            "dimension": lambda evidence: evidence["crops"][0][
                "working_dimensions"
            ].update(width=405),
            "color": lambda evidence: evidence.update(border_color=[0, 0, 0]),
            "source_bbox": lambda evidence: evidence["crops"][0]["source_bbox"].update(
                x=1
            ),
            "target_dpi": lambda evidence: evidence.update(target_dpi=300),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                evidence = copy.deepcopy(dict(self.candidate.evidence))
                mutate(evidence)
                candidate = replace(self.candidate, evidence=evidence)

                result = compare_ocr_padding(self.control, candidate)

                self.assertEqual(result.decision, OcrExperimentDecision.INVALID)
                self.assertIn(
                    "comparison_invalid:crop_padding_integrity",
                    result.reasons,
                )

    def test_empty_or_full_page_crop_evidence_is_invalid(self) -> None:
        for crops in (
            [],
            [copy.deepcopy(padding_evidence(candidate=True)["crops"][0])],
        ):
            with self.subTest(crops=crops):
                if crops:
                    crops[0]["region_ref"] = None
                evidence = copy.deepcopy(dict(self.candidate.evidence))
                evidence["crops"] = crops
                candidate = replace(self.candidate, evidence=evidence)

                result = compare_ocr_padding(self.control, candidate)

                self.assertEqual(result.decision, OcrExperimentDecision.INVALID)

    def test_runtime_drift_is_invalid_and_recovered_item_loss_is_regression(
        self,
    ) -> None:
        runtime_drift = replace(
            self.candidate.quality.runtime,
            provider_version="different",
        )
        drifted = replace(
            self.candidate,
            quality=replace(self.candidate.quality, runtime=runtime_drift),
        )
        lost = experiment_run(
            candidate=True,
            text_score=60.0,
            block_score=50.0,
            anchor_score=0.0,
            recovered_blocks=("details",),
            recovered_anchors=(),
        )

        self.assertEqual(
            compare_ocr_padding(self.control, drifted).decision,
            OcrExperimentDecision.INVALID,
        )
        regression = compare_ocr_padding(self.control, lost)
        self.assertEqual(regression.decision, OcrExperimentDecision.REGRESSED)
        self.assertEqual(regression.blocks.lost, ("heading",))
        self.assertEqual(regression.anchors.lost, ("文書解析",))


if __name__ == "__main__":
    unittest.main()
