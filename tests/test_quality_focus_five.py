import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from aiteqno.application import StageFixtureMeasurement, evaluate_stage_gate
from scripts import run_stage_suite as runner


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = (
    Path(__file__).parent / "fixtures" / "focuses" / "quality-80-focus-5.json"
)
EXPECTED_IDS = (
    "synthetic-dense-japanese-form-v1",
    "questionnaire-01-general-medicine",
    "questionnaire-02-fever-respiratory",
    "questionnaire-03-gastroenterology",
    "questionnaire-04-orthopedics",
)
EXPECTED_MANIFESTS = (
    "tests/fixtures/baseline/synthetic-dense-japanese-form-v1/manifest.json",
    "tests/fixtures/generalization/japanese-questionnaires-v1/"
    "questionnaire-01-general-medicine.manifest.json",
    "tests/fixtures/generalization/japanese-questionnaires-v1/"
    "questionnaire-02-fever-respiratory.manifest.json",
    "tests/fixtures/generalization/japanese-questionnaires-v1/"
    "questionnaire-03-gastroenterology.manifest.json",
    "tests/fixtures/generalization/japanese-questionnaires-v1/"
    "questionnaire-04-orthopedics.manifest.json",
)
EXPECTED_IDENTITIES = (
    (
        "df0b724d8fcc1b5d5e0483a60401c2cb3882675f71d1e37ecdbcff9e687ffc25",
        "45d3322ee7eea3d86fe981d93dba5cc9ac83b27ca638259051a62868c8f15a31",
    ),
    (
        "e6aadded4a7ca5d92358c93d87679b65f5f81f9ebf886a13871968a1dd96a734",
        "b3c5670dce98dedfced0d1508ba95583801ec430e69bb6391a20945a10fa82cb",
    ),
    (
        "6c27901390f4a1b43729d681884aae144886d2725ac3d585d9570f4499137ba2",
        "94b073eb3d7cb6df5001054d61f212c99b476c7af0fc08df55385dfce1d12b0a",
    ),
    (
        "825bddca8853986288cb5762bb26e80143762120ffe5859793f4ec5a171f83a7",
        "d358c1b7f92ef52ca1b59b3667f3d7922e81538fdd3429ac02e964a229baf1df",
    ),
    (
        "0e322bc9b5e8593d5a0fda959bd314cb6dc2c46de79fc3c07467527dba6dc4cd",
        "21d5279d9da146f4170f3adc197fd0d0927d6a50c920e7896d4eb6e4d9ce09c2",
    ),
)


def measurement(fixture_id, score=80, *, integrity=True, previous=None):
    return StageFixtureMeasurement(
        fixture_id=fixture_id,
        overall_score=score,
        integrity_passed=integrity,
        artifact_path=f"fixtures/{fixture_id}",
        previous_overall_score=previous,
    )


class QualityFocusFiveContractTest(unittest.TestCase):
    def test_descriptor_pins_only_baseline_then_q01_then_q02_then_q03_then_q04(self):
        descriptor = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
        suite = runner._load_suite(SUITE_PATH)

        self.assertEqual(descriptor["parent_issue"], 95)
        self.assertEqual(descriptor["focus_issue"], 105)
        self.assertEqual(suite.stage_id, "quality-80-focus-5")
        self.assertEqual(suite.threshold, 80)
        self.assertEqual(
            descriptor["fixtures"],
            [
                {"fixture_id": fixture_id, "manifest": manifest}
                for fixture_id, manifest in zip(EXPECTED_IDS, EXPECTED_MANIFESTS)
            ],
        )
        self.assertEqual(tuple(item.fixture_id for item in suite.fixtures), EXPECTED_IDS)

    def test_pipeline_and_evaluator_contract_remain_unchanged(self):
        descriptor = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
        previous = json.loads(
            SUITE_PATH.with_name("quality-80-focus-4.json").read_text(encoding="utf-8")
        )
        suite = runner._load_suite(SUITE_PATH)

        self.assertEqual(descriptor["pipeline"], previous["pipeline"])
        self.assertEqual(descriptor["evaluation"], previous["evaluation"])
        self.assertEqual(suite.production_languages, ("jpn",))
        self.assertEqual(suite.visible_languages, ("jpn",))
        self.assertEqual(suite.preview_dpi, 144)
        self.assertEqual(suite.snapshot_dpi, 300)
        self.assertEqual(suite.visible_options.page_segmentation_mode, 6)
        self.assertEqual(suite.visible_options.engine_mode, 3)

    def test_active_identities_are_hash_pinned_and_reviewed(self):
        suite = runner._load_suite(SUITE_PATH)

        self.assertEqual(
            tuple((item.source_sha256, item.reference_sha256) for item in suite.fixtures),
            EXPECTED_IDENTITIES,
        )
        self.assertEqual(
            tuple(
                (item.source_width, item.source_height, item.source_dpi)
                for item in suite.fixtures
            ),
            (
                (700, 991, 96),
                (1240, 1754, 150),
                (1654, 2339, 200),
                (1240, 1754, 150),
                (1754, 1240, 150),
            ),
        )
        self.assertTrue(all(item.reference.reviewed for item in suite.fixtures))

    def test_only_explicit_manifests_are_read_without_fixture_discovery(self):
        with (
            patch.object(Path, "glob", side_effect=AssertionError("glob forbidden")),
            patch.object(Path, "rglob", side_effect=AssertionError("rglob forbidden")),
            patch.object(Path, "iterdir", side_effect=AssertionError("scan forbidden")),
            patch.object(runner, "_read_fixture", wraps=runner._read_fixture) as reader,
        ):
            runner._load_suite(SUITE_PATH)

        self.assertEqual(tuple(call.args[1] for call in reader.call_args_list), EXPECTED_IDS)
        self.assertEqual(reader.call_count, 5)

    def test_all_five_at_exactly_80_pass(self):
        result = evaluate_stage_gate(
            tuple(measurement(fixture_id) for fixture_id in EXPECTED_IDS),
            threshold=80,
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.minimum_overall, 80)
        self.assertTrue(all(item.passed for item in result.fixtures))

    def test_any_fixture_at_79_99_fails_despite_an_average_above_80(self):
        for failing_id in EXPECTED_IDS:
            with self.subTest(failing_id=failing_id):
                result = evaluate_stage_gate(
                    tuple(
                        measurement(fixture_id, 79.99 if fixture_id == failing_id else 100)
                        for fixture_id in EXPECTED_IDS
                    ),
                    threshold=80,
                )

                self.assertFalse(result.passed)
                self.assertEqual(result.minimum_overall, 79.99)
                self.assertGreater(result.average_overall_diagnostic, 80)

    def test_previous_85_to_current_80_remains_a_pass(self):
        result = evaluate_stage_gate(
            tuple(measurement(fixture_id, previous=85) for fixture_id in EXPECTED_IDS),
            threshold=80,
        )

        self.assertTrue(result.passed)
        self.assertTrue(all(item.passed for item in result.fixtures))
        self.assertTrue(
            all(
                item.measurement.to_dict()["score_delta_diagnostic"] == -5
                for item in result.fixtures
            )
        )

    def test_any_integrity_failure_blocks_otherwise_passing_scores(self):
        for failing_id in EXPECTED_IDS:
            with self.subTest(failing_id=failing_id):
                result = evaluate_stage_gate(
                    tuple(
                        measurement(fixture_id, integrity=fixture_id != failing_id)
                        for fixture_id in EXPECTED_IDS
                    ),
                    threshold=80,
                )

                self.assertFalse(result.passed)
                self.assertEqual(result.minimum_overall, 80)

    def test_runner_calls_exactly_five_fixtures_sequentially_and_records_order(self):
        calling_thread = threading.get_ident()
        calls = []

        def run_fixture(fixture, suite, fixture_output, *, previous_overall):
            self.assertEqual(threading.get_ident(), calling_thread)
            self.assertEqual(fixture_output.name, fixture.fixture_id)
            self.assertEqual(suite.stage_id, "quality-80-focus-5")
            self.assertIsNone(previous_overall)
            calls.append(fixture.fixture_id)
            return measurement(fixture.fixture_id), {
                "fixture_id": fixture.fixture_id,
                "source": {"sha256": fixture.source_sha256},
                "reference": {"sha256": fixture.reference_sha256},
                "legacy_evaluator_state_diagnostic": "fail",
                "components": {},
            }

        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(runner, "_runtime_record", return_value={}),
            patch.object(runner, "_run_fixture", side_effect=run_fixture) as executor,
        ):
            output = Path(root) / "focus-five"
            result = runner.run(SUITE_PATH, output)
            persisted = json.loads((output / "stage-summary.json").read_bytes())

        self.assertEqual(tuple(calls), EXPECTED_IDS)
        self.assertEqual(executor.call_count, 5)
        self.assertEqual(result["state"], "pass")
        self.assertEqual(
            result["execution"],
            {
                "mode": "sequential",
                "fixture_discovery": False,
                "active_fixture_count": 5,
                "order": list(EXPECTED_IDS),
            },
        )
        self.assertEqual(persisted, result)

    def test_previous_summary_cannot_add_a_fixture_outside_the_focus(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(runner, "_runtime_record", return_value={}),
            patch.object(runner, "_run_fixture") as executor,
        ):
            previous = Path(root) / "previous.json"
            previous.write_text(
                json.dumps(
                    {"fixtures": [{"fixture_id": "outside-focus", "overall_score": 100}]}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "fixtures outside this stage"):
                runner.run(SUITE_PATH, Path(root) / "focus-five", previous_summary=previous)

        executor.assert_not_called()

    def test_ci_quality_gate_executes_and_uploads_only_the_focus_five_descriptor(self):
        workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(workflow.count("scripts/run_stage_suite.py"), 1)
        self.assertEqual(
            workflow.count("--suite tests/fixtures/focuses/quality-80-focus-5.json"), 1
        )
        self.assertIn("Run Quality 80 Focus 5 exact-five gate", workflow)
        self.assertIn("--expect-state pass", workflow)
        self.assertIn("--output \"$AITEQNO_QUALITY_FOCUS_FIVE_OUTPUT\"", workflow)
        self.assertIn("name: quality-80-focus-5-${{ github.sha }}", workflow)
        self.assertIn("path: build/quality-80-focus-5", workflow)
        self.assertNotIn("tests/fixtures/stages/questionnaire-stage-", workflow)
        self.assertNotIn("tests/fixtures/focuses/quality-80-focus-1.json", workflow)
        self.assertNotIn("tests/fixtures/focuses/quality-80-focus-2.json", workflow)
        self.assertNotIn("tests/fixtures/focuses/quality-80-focus-3.json", workflow)
        self.assertNotIn("AITEQNO_QUALITY_FOCUS_TWO_OUTPUT", workflow)
        self.assertNotIn("tests/fixtures/focuses/quality-80-focus-4.json", workflow)
        self.assertNotIn("AITEQNO_QUALITY_FOCUS_THREE_OUTPUT", workflow)
        self.assertNotIn("AITEQNO_QUALITY_FOCUS_FOUR_OUTPUT", workflow)

    def test_production_structure_policy_has_no_active_fixture_identity(self):
        production = (
            REPOSITORY_ROOT / "src" / "aiteqno" / "adapters" / "structure.py"
        ).read_text(encoding="utf-8")
        suite = runner._load_suite(SUITE_PATH)

        for fixture in suite.fixtures:
            for forbidden in (
                fixture.fixture_id,
                fixture.source_sha256,
                fixture.reference_sha256,
            ):
                with self.subTest(forbidden=forbidden):
                    self.assertNotIn(forbidden, production)


if __name__ == "__main__":
    unittest.main()
