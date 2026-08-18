from __future__ import annotations

import unittest
from dataclasses import replace

from aiteqno.application import compare_ocr_experiment
from aiteqno.ports import (
    OcrExperimentCheck,
    OcrExperimentContract,
    OcrExperimentDecision,
    OcrExperimentRun,
)
from tests.test_ocr_resolution import run


def experiment_run(*, candidate: bool, text_score: float) -> OcrExperimentRun:
    resolution_run = run(
        candidate=candidate,
        text_score=text_score,
        block_score=100.0 if candidate else 50.0,
        anchor_score=100.0 if candidate else 50.0,
        recovered_blocks=("heading", "details") if candidate else ("heading",),
        recovered_anchors=("文書解析", "対象形式") if candidate else ("文書解析",),
    )
    return OcrExperimentRun(
        quality=resolution_run.quality,
        document=resolution_run.document,
        evidence=resolution_run.transform,
    )


def contract(
    *,
    required_checks: tuple[str, ...] = ("fixed_hypothesis",),
    allowed_runtime_differences: tuple[str, ...] = ("effective_ocr_dpi",),
) -> OcrExperimentContract:
    return OcrExperimentContract(
        experiment_id="test_fixed_hypothesis",
        control_label="control",
        candidate_label="candidate",
        evaluator_name="test-ocr-experiment",
        evaluator_version="1.0.0",
        required_hypothesis_checks=required_checks,
        allowed_runtime_differences=allowed_runtime_differences,
    )


def check(*, passed: bool = True, name: str = "fixed_hypothesis") -> OcrExperimentCheck:
    return OcrExperimentCheck(
        name=name,
        passed=passed,
        reasons=("fixed hypothesis is valid" if passed else "candidate drift",),
        details={"fixed": True},
    )


class OcrExperimentComparisonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.control = experiment_run(candidate=False, text_score=40.0)
        self.candidate = experiment_run(candidate=True, text_score=42.0)

    def compare(
        self,
        *,
        candidate: OcrExperimentRun | None = None,
        hypothesis_checks: tuple[OcrExperimentCheck, ...] = (check(),),
        experiment_contract: OcrExperimentContract | None = None,
    ):
        return compare_ocr_experiment(
            self.control,
            candidate or self.candidate,
            contract=experiment_contract or contract(),
            hypothesis_checks=hypothesis_checks,
        )

    def test_supported_report_has_fixed_scope_policy_and_deterministic_json(
        self,
    ) -> None:
        result = self.compare()

        self.assertEqual(result.decision, OcrExperimentDecision.SUPPORTED)
        self.assertEqual(result.text_character_accuracy.delta, 2.0)
        self.assertEqual(result.blocks.gained, ("details",))
        self.assertEqual(result.anchors.gained, ("対象形式",))
        self.assertTrue(all(item.passed for item in result.checks))
        report = result.to_dict()
        self.assertEqual(report["scope"]["experiment"], "test_fixed_hypothesis")
        self.assertEqual(
            report["adoption_policy"]["required_hypothesis_checks"],
            ["fixed_hypothesis"],
        )
        self.assertEqual(result.to_json(), result.to_json())

    def test_failed_hypothesis_check_makes_comparison_invalid(self) -> None:
        result = self.compare(hypothesis_checks=(check(passed=False),))

        self.assertEqual(result.decision, OcrExperimentDecision.INVALID)
        self.assertIn("comparison_invalid:fixed_hypothesis", result.reasons)

    def test_undeclared_runtime_difference_is_invalid(self) -> None:
        changed_runtime = replace(
            self.candidate.quality.runtime,
            provider_version="different",
        )
        candidate = replace(
            self.candidate,
            quality=replace(self.candidate.quality, runtime=changed_runtime),
        )

        result = self.compare(candidate=candidate)

        self.assertEqual(result.decision, OcrExperimentDecision.INVALID)
        runtime = next(
            item for item in result.checks if item.name == "runtime_equivalence"
        )
        self.assertEqual(runtime.reasons, ("mismatch:provider_version",))

    def test_allowed_runtime_difference_is_declared_by_contract(self) -> None:
        result = self.compare()
        strict = self.compare(
            experiment_contract=contract(allowed_runtime_differences=()),
        )

        self.assertEqual(result.decision, OcrExperimentDecision.SUPPORTED)
        self.assertEqual(strict.decision, OcrExperimentDecision.INVALID)
        runtime = next(
            item for item in strict.checks if item.name == "runtime_equivalence"
        )
        self.assertEqual(runtime.reasons, ("mismatch:effective_ocr_dpi",))

    def test_inconclusive_and_regressed_decisions_are_common_policy(self) -> None:
        inconclusive = experiment_run(candidate=True, text_score=40.5)
        regressed = experiment_run(candidate=True, text_score=39.0)

        self.assertEqual(
            self.compare(candidate=inconclusive).decision,
            OcrExperimentDecision.INCONCLUSIVE,
        )
        self.assertEqual(
            self.compare(candidate=regressed).decision,
            OcrExperimentDecision.REGRESSED,
        )

    def test_required_hypothesis_checks_are_exact_and_ordered_by_contract(self) -> None:
        two_checks = contract(required_checks=("second", "first"))
        result = self.compare(
            experiment_contract=two_checks,
            hypothesis_checks=(check(name="first"), check(name="second")),
        )

        names = [item.name for item in result.checks]
        self.assertLess(names.index("second"), names.index("first"))
        with self.assertRaisesRegex(ValueError, "match required_hypothesis_checks"):
            self.compare(hypothesis_checks=())

    def test_duplicate_and_common_hypothesis_check_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be unique"):
            self.compare(hypothesis_checks=(check(), check()))

        common_name_contract = contract(required_checks=("runtime_equivalence",))
        with self.assertRaisesRegex(ValueError, "conflict with common checks"):
            self.compare(
                experiment_contract=common_name_contract,
                hypothesis_checks=(check(name="runtime_equivalence"),),
            )

    def test_contract_rejects_unknown_runtime_field_and_empty_hypothesis_set(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            contract(allowed_runtime_differences=("imaginary",))
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            contract(allowed_runtime_differences=("provider_version",))
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            contract(required_checks=())


if __name__ == "__main__":
    unittest.main()
