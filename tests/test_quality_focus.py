import json
import unittest
from pathlib import Path

from aiteqno.application import StageFixtureMeasurement, evaluate_stage_gate
from aiteqno.cli import (
    PRODUCTION_OCR_OPTIONS,
    PRODUCTION_OCR_REGION_GROUPING,
    PRODUCTION_OCR_REGION_PADDING_PX,
)
from scripts.run_stage_suite import _load_suite


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FOCUS_ONE_SUITE_PATH = (
    Path(__file__).parent / "fixtures" / "focuses" / "quality-80-focus-1.json"
)
FOCUS_TWO_SUITE_PATH = (
    Path(__file__).parent / "fixtures" / "focuses" / "quality-80-focus-2.json"
)


def measurement(
    score: float,
    *,
    fixture_id: str = "synthetic-dense-japanese-form-v1",
    previous: float | None = None,
):
    return StageFixtureMeasurement(
        fixture_id=fixture_id,
        overall_score=score,
        integrity_passed=True,
        artifact_path=f"fixtures/{fixture_id}",
        previous_overall_score=previous,
    )


class QualityFocusOneContractTest(unittest.TestCase):
    def test_descriptor_pins_only_the_existing_baseline_at_threshold_80(self):
        descriptor = json.loads(FOCUS_ONE_SUITE_PATH.read_text(encoding="utf-8"))
        suite = _load_suite(FOCUS_ONE_SUITE_PATH)

        self.assertEqual(descriptor["parent_issue"], 95)
        self.assertEqual(descriptor["focus_issue"], 96)
        self.assertEqual(suite.stage_id, "quality-80-focus-1")
        self.assertEqual(suite.threshold, 80)
        self.assertEqual(suite.production_languages, ("jpn",))
        self.assertEqual(suite.visible_languages, ("jpn",))
        self.assertEqual(suite.preview_dpi, 144)
        self.assertEqual(suite.snapshot_dpi, 300)
        self.assertEqual(len(suite.fixtures), 1)

        fixture = suite.fixtures[0]
        self.assertEqual(fixture.fixture_id, "synthetic-dense-japanese-form-v1")
        self.assertEqual(
            fixture.source_sha256,
            "df0b724d8fcc1b5d5e0483a60401c2cb3882675f71d1e37ecdbcff9e687ffc25",
        )
        self.assertEqual(
            fixture.reference_sha256,
            "45d3322ee7eea3d86fe981d93dba5cc9ac83b27ca638259051a62868c8f15a31",
        )
        self.assertEqual((fixture.source_width, fixture.source_height), (700, 991))
        self.assertEqual(fixture.source_dpi, 96)
        self.assertTrue(fixture.reference.reviewed)

    def test_exactly_80_passes_and_79_99_fails(self):
        exact = evaluate_stage_gate((measurement(80),), threshold=80)
        below = evaluate_stage_gate((measurement(79.99),), threshold=80)

        self.assertTrue(exact.passed)
        self.assertEqual(exact.minimum_overall, 80)
        self.assertFalse(below.passed)
        self.assertEqual(below.minimum_overall, 79.99)

    def test_previous_85_to_current_80_still_passes(self):
        result = evaluate_stage_gate(
            (measurement(80, previous=85),),
            threshold=80,
        )

        self.assertTrue(result.passed)
        self.assertEqual(
            result.fixtures[0].measurement.to_dict()["score_delta_diagnostic"],
            -5,
        )

    def test_public_ocr_profile_is_geometry_only_and_separator_safe(self):
        config = PRODUCTION_OCR_REGION_GROUPING

        self.assertEqual(PRODUCTION_OCR_REGION_PADDING_PX, 4)
        self.assertEqual(PRODUCTION_OCR_OPTIONS.page_segmentation_mode, 8)
        self.assertEqual(PRODUCTION_OCR_OPTIONS.engine_mode, 3)
        self.assertTrue(config.enabled)
        self.assertEqual(config.minimum_vertical_overlap_ratio, 0.45)
        self.assertEqual(config.maximum_horizontal_gap_height_ratio, 2.0)
        self.assertTrue(config.block_vertical_separators)
        self.assertFalse(config.to_dict()["uses_ocr_text"])
        self.assertFalse(config.to_dict()["uses_ocr_confidence"])


class QualityFocusTwoContractTest(unittest.TestCase):
    def test_descriptor_pins_exactly_baseline_then_q01_at_threshold_80(self):
        descriptor = json.loads(FOCUS_TWO_SUITE_PATH.read_text(encoding="utf-8"))
        suite = _load_suite(FOCUS_TWO_SUITE_PATH)

        self.assertEqual(descriptor["parent_issue"], 95)
        self.assertEqual(descriptor["focus_issue"], 99)
        self.assertEqual(suite.stage_id, "quality-80-focus-2")
        self.assertEqual(suite.threshold, 80)
        self.assertEqual(suite.production_languages, ("jpn",))
        self.assertEqual(suite.visible_languages, ("jpn",))
        self.assertEqual(suite.preview_dpi, 144)
        self.assertEqual(suite.snapshot_dpi, 300)
        self.assertEqual(
            descriptor["fixtures"],
            [
                {
                    "fixture_id": "synthetic-dense-japanese-form-v1",
                    "manifest": "tests/fixtures/baseline/"
                    "synthetic-dense-japanese-form-v1/manifest.json",
                },
                {
                    "fixture_id": "questionnaire-01-general-medicine",
                    "manifest": "tests/fixtures/generalization/"
                    "japanese-questionnaires-v1/"
                    "questionnaire-01-general-medicine.manifest.json",
                },
            ],
        )
        self.assertEqual(
            [fixture.fixture_id for fixture in suite.fixtures],
            [
                "synthetic-dense-japanese-form-v1",
                "questionnaire-01-general-medicine",
            ],
        )

    def test_runner_records_declared_sequential_order_without_discovery(self):
        runner = (REPOSITORY_ROOT / "scripts" / "run_stage_suite.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"mode": "sequential"', runner)
        self.assertIn('"fixture_discovery": False', runner)
        self.assertIn(
            '"order": [fixture.fixture_id for fixture in suite.fixtures]',
            runner,
        )

    def test_active_identities_are_hash_pinned_and_reviewed(self):
        suite = _load_suite(FOCUS_TWO_SUITE_PATH)
        baseline, q01 = suite.fixtures

        self.assertEqual(
            (baseline.source_sha256, baseline.reference_sha256),
            (
                "df0b724d8fcc1b5d5e0483a60401c2cb3882675f71d1e37ecdbcff9e687ffc25",
                "45d3322ee7eea3d86fe981d93dba5cc9ac83b27ca638259051a62868c8f15a31",
            ),
        )
        self.assertEqual(
            (q01.source_sha256, q01.reference_sha256),
            (
                "e6aadded4a7ca5d92358c93d87679b65f5f81f9ebf886a13871968a1dd96a734",
                "b3c5670dce98dedfced0d1508ba95583801ec430e69bb6391a20945a10fa82cb",
            ),
        )
        self.assertEqual(
            [
                (fixture.source_width, fixture.source_height, fixture.source_dpi)
                for fixture in suite.fixtures
            ],
            [(700, 991, 96), (1240, 1754, 150)],
        )
        self.assertTrue(all(fixture.reference.reviewed for fixture in suite.fixtures))

    def test_each_fixture_must_reach_80_without_average_compensation(self):
        exact = evaluate_stage_gate(
            (
                measurement(80),
                measurement(80, fixture_id="questionnaire-01-general-medicine"),
            ),
            threshold=80,
        )
        compensated = evaluate_stage_gate(
            (
                measurement(100),
                measurement(79.99, fixture_id="questionnaire-01-general-medicine"),
            ),
            threshold=80,
        )

        self.assertTrue(exact.passed)
        self.assertEqual(exact.minimum_overall, 80)
        self.assertFalse(compensated.passed)
        self.assertEqual(compensated.minimum_overall, 79.99)
        self.assertGreater(compensated.average_overall_diagnostic, 80)

    def test_previous_85_to_current_80_remains_a_pass(self):
        result = evaluate_stage_gate(
            (
                measurement(80, previous=85),
                measurement(
                    80,
                    fixture_id="questionnaire-01-general-medicine",
                    previous=85,
                ),
            ),
            threshold=80,
        )

        self.assertTrue(result.passed)
        self.assertTrue(all(item.passed for item in result.fixtures))

    def test_production_structure_policy_has_no_active_fixture_identity_or_text(self):
        production = (
            REPOSITORY_ROOT / "src" / "aiteqno" / "adapters" / "structure.py"
        ).read_text(encoding="utf-8")

        for forbidden in (
            "questionnaire-01-general-medicine",
            "questionnaire-01-general-medicine.png",
            "e6aadded4a7ca5d92358c93d87679b65f5f81f9ebf886a13871968a1dd96a734",
            "b3c5670dce98dedfced0d1508ba95583801ec430e69bb6391a20945a10fa82cb",
            "内科初診問診票",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, production)


if __name__ == "__main__":
    unittest.main()
