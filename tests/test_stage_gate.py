import unittest

from aiteqno.application import StageFixtureMeasurement, evaluate_stage_gate


def measurement(
    fixture_id: str,
    score: float,
    *,
    integrity: bool = True,
    previous: float | None = None,
) -> StageFixtureMeasurement:
    return StageFixtureMeasurement(
        fixture_id=fixture_id,
        overall_score=score,
        integrity_passed=integrity,
        artifact_path=f"fixtures/{fixture_id}",
        previous_overall_score=previous,
    )


class StageGateTest(unittest.TestCase):
    def test_each_fixture_at_exactly_70_passes(self):
        result = evaluate_stage_gate(
            (
                measurement("baseline", 70),
                measurement("q01", 70),
                measurement("q02", 70),
                measurement("q03", 70),
                measurement("q04", 70),
                measurement("q05", 70),
                measurement("q06", 70),
                measurement("q07", 70),
            )
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.minimum_overall, 70)
        self.assertEqual(result.state, "pass")

    def test_high_average_cannot_compensate_for_low_fixtures(self):
        result = evaluate_stage_gate(
            (
                measurement("baseline", 100),
                measurement("q01", 100),
                measurement("q02", 100),
                measurement("q03", 100),
                measurement("q04", 100),
                measurement("q05", 100),
                measurement("q06", 10),
                measurement("q07", 10),
            )
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.average_overall_diagnostic, 77.5)
        self.assertFalse(result.to_dict()["average_used_for_decision"])

    def test_69_99_fails_even_when_every_other_fixture_is_100(self):
        result = evaluate_stage_gate(
            (
                measurement("baseline", 100),
                measurement("q01", 100),
                measurement("q02", 100),
                measurement("q03", 100),
                measurement("q04", 100),
                measurement("q05", 100),
                measurement("q06", 100),
                measurement("q07", 69.99),
            )
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.minimum_overall, 69.99)

    def test_previous_90_to_current_70_is_a_pass(self):
        result = evaluate_stage_gate(
            (
                measurement("baseline", 70, previous=90),
                measurement("q01", 75, previous=75),
                measurement("q02", 80, previous=80),
                measurement("q03", 85, previous=85),
                measurement("q04", 90, previous=90),
                measurement("q05", 95, previous=95),
                measurement("q06", 100, previous=100),
                measurement("q07", 100, previous=100),
            )
        )

        self.assertTrue(result.passed)
        baseline = next(
            item for item in result.fixtures if item.measurement.fixture_id == "baseline"
        )
        self.assertEqual(
            baseline.measurement.to_dict()["score_delta_diagnostic"],
            -20,
        )

    def test_integrity_failure_cannot_pass_with_a_high_score(self):
        result = evaluate_stage_gate(
            (
                measurement("baseline", 100),
                measurement("q01", 100, integrity=False),
            )
        )

        self.assertFalse(result.passed)
        q01 = next(
            item for item in result.fixtures if item.measurement.fixture_id == "q01"
        )
        self.assertIn("integrity_failed", q01.reasons)

    def test_order_does_not_change_scores_or_decision(self):
        values = (
            measurement("baseline", 72),
            measurement("q01", 81),
            measurement("q02", 76),
            measurement("q03", 79),
            measurement("q04", 74),
            measurement("q05", 78),
            measurement("q06", 83),
            measurement("q07", 88),
        )

        forward = evaluate_stage_gate(values).to_dict()
        reverse = evaluate_stage_gate(tuple(reversed(values))).to_dict()

        self.assertEqual(forward, reverse)
        self.assertEqual(
            len({item["artifact_path"] for item in forward["fixtures"]}),
            8,
        )

    def test_duplicate_fixture_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "fixture IDs must be unique"):
            evaluate_stage_gate(
                (measurement("same", 70), measurement("same", 80))
            )


if __name__ == "__main__":
    unittest.main()
