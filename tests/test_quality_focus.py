import json
import unittest
from pathlib import Path

from aiteqno.application import StageFixtureMeasurement, evaluate_stage_gate
from aiteqno.cli import PRODUCTION_OCR_REGION_GROUPING
from scripts.run_stage_suite import _load_suite


FOCUS_ONE_SUITE_PATH = (
    Path(__file__).parent / "fixtures" / "focuses" / "quality-80-focus-1.json"
)


def measurement(score: float, *, previous: float | None = None):
    return StageFixtureMeasurement(
        fixture_id="synthetic-dense-japanese-form-v1",
        overall_score=score,
        integrity_passed=True,
        artifact_path="fixtures/synthetic-dense-japanese-form-v1",
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

    def test_public_cli_grouping_is_geometry_only_and_separator_safe(self):
        config = PRODUCTION_OCR_REGION_GROUPING

        self.assertTrue(config.enabled)
        self.assertEqual(config.minimum_vertical_overlap_ratio, 0.45)
        self.assertEqual(config.maximum_horizontal_gap_height_ratio, 2.0)
        self.assertTrue(config.block_vertical_separators)
        self.assertFalse(config.to_dict()["uses_ocr_text"])
        self.assertFalse(config.to_dict()["uses_ocr_confidence"])


if __name__ == "__main__":
    unittest.main()
