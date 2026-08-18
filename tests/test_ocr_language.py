from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from aiteqno.application import compare_ocr_language_profile
from aiteqno.ports import (
    OcrExperimentDecision,
    OcrExperimentRun,
    OcrLanguageSmokeRun,
    OcrTrainedDataEvidence,
)
from tests.test_ocr_resolution import document, quality, transform


SMOKE_SHA256 = "ccfa1bfedff409064924a6e57805e81fc826e754407e03827388df9da56a103f"


def _padding_crop(
    source: dict[str, object],
    *,
    region_ref: str | None,
    full_page: bool = False,
) -> dict[str, object]:
    source_dimensions = copy.deepcopy(source["source_dimensions"])
    width = source_dimensions["width"]
    height = source_dimensions["height"]
    padding = 0 if full_page else 2
    return {
        "region_ref": region_ref,
        "source_bbox": copy.deepcopy(source["source_bbox"]),
        "source_dimensions": source_dimensions,
        "pre_padding_dimensions": copy.deepcopy(source_dimensions),
        "working_dimensions": {
            "width": width + 2 * padding,
            "height": height + 2 * padding,
        },
        "padding_pixels": padding,
        "applied": bool(padding),
        "working_raster_sha256": "9" * 64,
    }


def invocation_evidence(
    *,
    languages: tuple[str, ...],
    parameters_digest: str,
    smoke: bool = False,
) -> dict[str, object]:
    base_transform = transform(candidate=False, source_dpi=300 if smoke else 96)
    if smoke:
        source_crop = {
            "region_ref": None,
            "source_bbox": {"x": 0, "y": 0, "width": 1200, "height": 340},
            "source_dimensions": {"width": 1200, "height": 340},
            "working_dimensions": {"width": 1200, "height": 340},
            "actual_scale": {"x": 1.0, "y": 1.0},
            "resized": False,
            "working_raster_sha256": "8" * 64,
        }
        base_transform["source_effective_dpi"] = 300.0
        base_transform["effective_ocr_dpi"] = 300
        base_transform["crops"] = [source_crop]
        padding_crops = [_padding_crop(source_crop, region_ref=None, full_page=True)]
        source_dpi = 300.0
        effective_dpi = 300
    else:
        padding_crops = [
            _padding_crop(crop, region_ref=crop["region_ref"])
            for crop in base_transform["crops"]
        ]
        source_dpi = 96.0
        effective_dpi = 96
    padding = {
        "schema_version": "1.0",
        "padding_version": "tesseract-crop-padding-v1",
        "enabled": True,
        "configured_padding_pixels": 2,
        "source_effective_dpi": source_dpi,
        "effective_ocr_dpi": effective_dpi,
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
        "inverse_mapping_policy": "mapping",
        "max_working_pixels": 40_000_000,
        "imaging_library": {"name": "Pillow", "version": "12.3.0"},
        "crops": padding_crops,
    }
    records = [
        {
            "language": "jpn",
            "path": "/same/tessdata/jpn.traineddata",
            "size_bytes": 100,
            "sha256": "1" * 64,
        }
    ]
    if languages == ("jpn", "eng"):
        records.append(
            {
                "language": "eng",
                "path": "/same/tessdata/eng.traineddata",
                "size_bytes": 200,
                "sha256": "2" * 64,
            }
        )
    return {
        "schema_version": "1.0",
        "invocation_version": "tesseract-invocation-evidence-v1",
        "provider": "tesseract",
        "provider_version": "5.5.0-test",
        "executable": "/same/bin/tesseract",
        "configuration": {
            "languages": list(languages),
            "page_segmentation_mode": 6,
            "engine_mode": 3,
            "timeout_seconds": 30.0,
            "min_confidence": 0.0,
            "preserve_interword_spaces": False,
            "source_metadata_dpi": {"x": source_dpi, "y": source_dpi},
            "effective_ocr_dpi": effective_dpi,
            "target_dpi": None,
            "region_padding_px": 2,
            "max_working_pixels": 40_000_000,
            "tessdata_configured": True,
            "tesseract_config": f"--oem 3 --psm 6 --dpi {effective_dpi}",
        },
        "traineddata": records,
        "parameters_digest": parameters_digest,
        "raster_transform": base_transform,
        "crop_padding": padding,
        "crops": copy.deepcopy(padding_crops),
    }


def experiment_run(
    *,
    candidate: bool,
    text_score: float = 72.0,
    observed_text: str = "PNG PDF DOCX JSON 30 90 70",
) -> OcrExperimentRun:
    languages = ("jpn",) if candidate else ("jpn", "eng")
    digest = ("6" if candidate else "3") * 64
    result = quality(
        dpi=96,
        text_score=text_score,
        block_score=100.0,
        anchor_score=100.0,
        recovered_blocks=("heading", "details"),
        recovered_anchors=("文書解析", "対象形式"),
    )
    traineddata = result.runtime.traineddata[:1] if candidate else result.runtime.traineddata
    result = replace(
        result,
        observed_text=observed_text,
        runtime=replace(
            result.runtime,
            languages=languages,
            traineddata=traineddata,
        ),
    )
    return OcrExperimentRun(
        quality=result,
        document=document(split_text=candidate, parameters_digest=digest),
        evidence=invocation_evidence(
            languages=languages,
            parameters_digest=digest,
        ),
    )


def smoke_run(*, observed_text: str = "AITEQNO 2026 患者番号") -> OcrLanguageSmokeRun:
    return OcrLanguageSmokeRun(
        source_sha256=SMOKE_SHA256,
        observed_text=observed_text,
        invocation_evidence=invocation_evidence(
            languages=("jpn",),
            parameters_digest="7" * 64,
            smoke=True,
        ),
    )


class OcrLanguageProfileComparisonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.control = experiment_run(candidate=False, text_score=70.0)
        self.candidate = experiment_run(candidate=True, text_score=72.0)

    def compare(
        self,
        *,
        candidate: OcrExperimentRun | None = None,
        smoke: OcrLanguageSmokeRun | None = None,
        minimum_delta: float = 1.0,
    ):
        return compare_ocr_language_profile(
            self.control,
            candidate or self.candidate,
            multilingual_smoke=smoke or smoke_run(),
            minimum_text_accuracy_delta=minimum_delta,
        )

    def test_supported_profile_fixes_languages_traineddata_padding_and_smoke(self):
        result = self.compare()

        self.assertEqual(result.decision, OcrExperimentDecision.SUPPORTED)
        self.assertEqual(result.text_character_accuracy.delta, 2.0)
        self.assertTrue(all(check.passed for check in result.checks))
        report = result.to_dict()
        self.assertEqual(
            report["adoption_policy"]["allowed_runtime_differences"],
            ["languages", "traineddata"],
        )
        self.assertEqual(report["multilingual_smoke"]["status"], "pass")
        self.assertFalse(report["recovery"]["protected_literals"]["lost"])
        self.assertEqual(result.to_json(), result.to_json())

    def test_language_order_extra_model_jpn_digest_and_fixed_config_drift_are_invalid(self):
        mutations = {
            "language_order": lambda run, evidence: evidence["configuration"].update(
                languages=["eng", "jpn"]
            ),
            "extra_model": lambda run, evidence: evidence["traineddata"].append(
                {
                    "language": "eng",
                    "path": "/same/tessdata/eng.traineddata",
                    "size_bytes": 200,
                    "sha256": "2" * 64,
                }
            ),
            "jpn_digest": lambda run, evidence: evidence["traineddata"][0].update(
                sha256="f" * 64
            ),
            "psm": lambda run, evidence: evidence["configuration"].update(
                page_segmentation_mode=3
            ),
            "padding": lambda run, evidence: evidence["configuration"].update(
                region_padding_px=0
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                evidence = copy.deepcopy(dict(self.candidate.evidence))
                mutate(self.candidate, evidence)
                candidate = replace(self.candidate, evidence=evidence)

                result = self.compare(candidate=candidate)

                self.assertEqual(result.decision, OcrExperimentDecision.INVALID)

        changed_runtime = replace(
            self.candidate.quality.runtime,
            traineddata=(
                OcrTrainedDataEvidence(
                    language="jpn",
                    size_bytes=100,
                    sha256="f" * 64,
                ),
            ),
        )
        result = self.compare(
            candidate=replace(
                self.candidate,
                quality=replace(self.candidate.quality, runtime=changed_runtime),
            )
        )
        self.assertEqual(result.decision, OcrExperimentDecision.INVALID)

    def test_protected_literal_or_multilingual_smoke_loss_is_regressed(self):
        candidate = experiment_run(
            candidate=True,
            text_score=80.0,
            observed_text="PNG PDF DOCX JSON 30 90",
        )
        protected = self.compare(candidate=candidate)
        smoke = self.compare(smoke=smoke_run(observed_text="患者番号"))

        self.assertEqual(protected.decision, OcrExperimentDecision.REGRESSED)
        self.assertIn("regression:protected_literal:70", protected.reasons)
        self.assertEqual(smoke.decision, OcrExperimentDecision.REGRESSED)
        self.assertIn(
            "regression:multilingual_smoke:literal:AITEQNO",
            smoke.reasons,
        )

    def test_smoke_source_or_non_language_runtime_drift_is_invalid(self):
        source_drift = replace(smoke_run(), source_sha256="0" * 64)
        runtime_drift = replace(
            self.candidate.quality,
            runtime=replace(self.candidate.quality.runtime, provider_version="different"),
        )

        self.assertEqual(
            self.compare(smoke=source_drift).decision,
            OcrExperimentDecision.INVALID,
        )
        self.assertEqual(
            self.compare(
                candidate=replace(self.candidate, quality=runtime_drift)
            ).decision,
            OcrExperimentDecision.INVALID,
        )

    def test_same_normalized_text_with_token_split_and_id_changes_is_stable(self):
        control = experiment_run(
            candidate=False,
            text_score=75.0,
            observed_text="PNG PDF DOCX JSON 30 90 70",
        )
        candidate = experiment_run(
            candidate=True,
            text_score=75.0,
            observed_text="PNG\nPDF DOCX\tJSON 30 90 70",
        )
        result = compare_ocr_language_profile(
            control,
            candidate,
            multilingual_smoke=smoke_run(),
            minimum_text_accuracy_delta=0.0,
        )

        self.assertEqual(result.decision, OcrExperimentDecision.SUPPORTED)
        self.assertFalse(any(item.lost for item in result.protected_literals))

    def test_reference_threshold_nontext_and_topology_context_drift_are_invalid(self):
        changed_quality = replace(self.candidate.quality, reference_id="different")
        changed_document = document(
            split_text=True,
            parameters_digest="6" * 64,
            nontext_color="#ff0000",
        )

        self.assertEqual(
            self.compare(
                candidate=replace(self.candidate, quality=changed_quality)
            ).decision,
            OcrExperimentDecision.INVALID,
        )
        self.assertEqual(
            self.compare(
                candidate=replace(self.candidate, document=changed_document)
            ).decision,
            OcrExperimentDecision.INVALID,
        )


if __name__ == "__main__":
    unittest.main()
