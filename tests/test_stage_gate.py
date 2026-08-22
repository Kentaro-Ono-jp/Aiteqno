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
            )
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.minimum_overall, 70)
        self.assertEqual(result.state, "pass")

    def test_average_70_cannot_compensate_for_a_50(self):
        result = evaluate_stage_gate(
            (
                measurement("baseline", 90),
                measurement("q01", 70),
                measurement("q02", 50),
            )
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.average_overall_diagnostic, 70)
        self.assertFalse(result.to_dict()["average_used_for_decision"])

    def test_69_99_fails_even_when_the_other_fixture_is_100(self):
        result = evaluate_stage_gate(
            (
                measurement("baseline", 100),
                measurement("q01", 100),
                measurement("q02", 69.99),
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
        )

        forward = evaluate_stage_gate(values).to_dict()
        reverse = evaluate_stage_gate(tuple(reversed(values))).to_dict()

        self.assertEqual(forward, reverse)
        self.assertEqual(
            len({item["artifact_path"] for item in forward["fixtures"]}),
            3,
        )

    def test_duplicate_fixture_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "fixture IDs must be unique"):
            evaluate_stage_gate(
                (measurement("same", 70), measurement("same", 80))
            )


if __name__ == "__main__":
    unittest.main()
