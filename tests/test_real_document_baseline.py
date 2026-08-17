import base64
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from aiteqno.domain import DocumentIR
from aiteqno.ports import (
    OcrRuntimeEvidence,
    OcrTrainedDataEvidence,
    SourceBaselineReference,
)
from scripts.run_real_baseline import _read_fixture, run as run_real_baseline


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIRECTORY = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "baseline"
    / "synthetic-dense-japanese-form-v1"
)
LEGACY_UNLICENSED_SOURCE = REPOSITORY_ROOT / "input" / "form_blank_testClinic_v1.png"
EXPECTED_SOURCE_SHA256 = (
    "df0b724d8fcc1b5d5e0483a60401c2cb3882675f71d1e37ecdbcff9e687ffc25"
)
EXPECTED_REFERENCE_SHA256 = (
    "45d3322ee7eea3d86fe981d93dba5cc9ac83b27ca638259051a62868c8f15a31"
)


class RealDocumentBaselineContractTest(unittest.TestCase):
    def test_original_mit_source_and_reference_are_reviewed_and_hash_pinned(self):
        manifest = json.loads(
            (FIXTURE_DIRECTORY / "manifest.json").read_text(encoding="utf-8")
        )
        source = base64.b64decode(
            (FIXTURE_DIRECTORY / "source.png.b64").read_bytes().strip(),
            validate=True,
        )
        reference = SourceBaselineReference.from_json(
            (FIXTURE_DIRECTORY / "reference.json").read_bytes()
        )

        self.assertEqual(hashlib.sha256(source).hexdigest(), EXPECTED_SOURCE_SHA256)
        self.assertEqual(
            hashlib.sha256(
                (FIXTURE_DIRECTORY / "reference.json").read_bytes()
            ).hexdigest(),
            EXPECTED_REFERENCE_SHA256,
        )
        self.assertEqual(manifest["source"]["sha256"], EXPECTED_SOURCE_SHA256)
        self.assertEqual(reference.source_sha256, EXPECTED_SOURCE_SHA256)
        self.assertEqual(manifest["license"], "MIT")
        self.assertTrue(manifest["redistribution_allowed"])
        self.assertFalse(manifest["contains_personal_data"])
        self.assertEqual(manifest["review"]["status"], "reviewed")
        self.assertTrue(reference.reviewed)
        self.assertEqual(
            manifest["quality_contract"]["expected_current_state"],
            "fail",
        )
        with Image.open(BytesIO(source)) as image:
            self.assertEqual(image.size, (700, 991))
            self.assertEqual(image.format, "PNG")

    def test_reference_contract_has_component_floors_and_manual_gates(self):
        manifest = json.loads(
            (FIXTURE_DIRECTORY / "manifest.json").read_text(encoding="utf-8")
        )
        reference = SourceBaselineReference.from_json(
            (FIXTURE_DIRECTORY / "reference.json").read_bytes()
        )

        quality = manifest["quality_contract"]
        self.assertEqual(quality["overall_minimum"], 70.0)
        self.assertEqual(
            quality["component_minimums"],
            {
                "text_character_accuracy": 70.0,
                "logical_block_coverage": 60.0,
                "structure_similarity": 60.0,
                "geometry_similarity": 50.0,
            },
        )
        self.assertGreaterEqual(len(reference.text_regions), 40)
        self.assertGreaterEqual(len(reference.structural_items), 25)
        self.assertGreaterEqual(len(reference.relationships), 12)
        self.assertEqual(
            {item.kind.value for item in reference.relationships},
            {"reading_order", "containment", "adjacency"},
        )
        self.assertTrue(all(item.essential for item in reference.relationships))
        self.assertGreaterEqual(len(reference.essential_text_anchors), 10)
        self.assertEqual(reference.expected_page_count, 1)
        self.assertEqual(
            set(reference.required_manual_checks),
            {
                "no_fatal_text_overlap",
                "no_text_clipping",
                "layout_human_usable",
                "word_open_edit_save",
            },
        )

    def test_unverified_legacy_source_is_not_retained_in_current_tree(self):
        excluded = json.loads(
            (FIXTURE_DIRECTORY.parent / "excluded-sources.json").read_text(
                encoding="utf-8"
            )
        )
        record = excluded["sources"][0]

        self.assertFalse(LEGACY_UNLICENSED_SOURCE.exists())
        self.assertEqual(record["fixture_status"], "excluded")
        self.assertEqual(record["redistribution_status"], "unverified")
        self.assertEqual(
            record["sha256"],
            "f57c5246c248cf6a2f7abe548225d65ae9ab376d83a64d4183224a3e0f395369",
        )

    def test_runner_rejects_fixture_contract_drift_before_runtime_execution(self):
        cases = (
            ("fixture id", "manifest", ("fixture_id",), "other-fixture", "fixture_id"),
            (
                "source traversal",
                "manifest",
                ("source", "path"),
                "../source.png.b64",
                "source.path",
            ),
            (
                "source encoding",
                "manifest",
                ("source", "encoding"),
                "hex",
                "source.encoding",
            ),
            (
                "source hash",
                "manifest",
                ("source", "sha256"),
                "0" * 64,
                "source.sha256",
            ),
            (
                "manifest source width",
                "manifest",
                ("source", "pixel_width"),
                701,
                "manifest source dimensions",
            ),
            (
                "reference source height",
                "reference",
                ("source_dimensions", "pixel_height"),
                992,
                "reference JSON SHA-256",
            ),
            (
                "reference text",
                "reference",
                ("essential_text_anchors", 0),
                "都合のよい正解",
                "reference JSON SHA-256",
            ),
            (
                "ocr provider",
                "manifest",
                ("ocr_contract", "provider"),
                "other",
                "ocr provider",
            ),
            (
                "ocr languages",
                "manifest",
                ("ocr_contract", "languages"),
                ["eng", "jpn"],
                "ocr languages",
            ),
            (
                "ocr psm",
                "manifest",
                ("ocr_contract", "page_segmentation_mode"),
                3,
                "page segmentation mode",
            ),
            (
                "ocr oem",
                "manifest",
                ("ocr_contract", "engine_mode"),
                1,
                "engine mode",
            ),
            (
                "manifest source dpi",
                "manifest",
                ("source", "dpi"),
                300,
                "manifest source DPI",
            ),
            (
                "ocr source dpi",
                "manifest",
                ("ocr_contract", "source_dpi"),
                300,
                "ocr source DPI",
            ),
            (
                "quality page count",
                "manifest",
                ("quality_contract", "expected_page_count"),
                2,
                "expected_page_count",
            ),
            (
                "ocr text threshold",
                "manifest",
                (
                    "quality_contract",
                    "component_minimums",
                    "text_character_accuracy",
                ),
                60.0,
                "OCR text character minimum",
            ),
            (
                "ocr block threshold",
                "manifest",
                (
                    "quality_contract",
                    "component_minimums",
                    "logical_block_coverage",
                ),
                50.0,
                "OCR logical block coverage minimum",
            ),
            (
                "ocr anchor threshold",
                "manifest",
                ("quality_contract", "essential_anchor_recall"),
                90.0,
                "OCR essential anchor recall",
            ),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            for name, document_name, key_path, replacement, expected_message in cases:
                with self.subTest(name=name):
                    fixture = temporary_root / name.replace(" ", "-")
                    shutil.copytree(FIXTURE_DIRECTORY, fixture)
                    document_path = fixture / f"{document_name}.json"
                    document = json.loads(document_path.read_text(encoding="utf-8"))
                    target = document
                    for key in key_path[:-1]:
                        target = target[key]
                    target[key_path[-1]] = replacement
                    document_path.write_text(
                        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

                    with self.assertRaisesRegex(RuntimeError, expected_message):
                        _read_fixture(fixture)

    def test_existing_output_directory_is_not_modified_by_cli_failure_handling(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "already-exists"
            output.mkdir()
            sentinel = output / "sentinel.txt"
            sentinel.write_text("retain exactly\n", encoding="utf-8")
            before = {
                item.relative_to(output): item.read_bytes()
                for item in output.rglob("*")
                if item.is_file()
            }

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "scripts" / "run_real_baseline.py"),
                    "--output",
                    str(output),
                ],
                cwd=REPOSITORY_ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )

            self.assertEqual(completed.returncode, 1)
            self.assertIn("baseline output already exists", completed.stderr)
            after = {
                item.relative_to(output): item.read_bytes()
                for item in output.rglob("*")
                if item.is_file()
            }
            self.assertEqual(after, before)
            self.assertFalse((output / "operational-error.json").exists())

    def test_new_output_owned_by_runner_retains_preflight_failure_evidence(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            fixture = temporary_root / "invalid-fixture"
            shutil.copytree(FIXTURE_DIRECTORY, fixture)
            manifest_path = fixture / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source"]["encoding"] = "hex"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            output = temporary_root / "new-output"

            with self.assertRaisesRegex(RuntimeError, "source.encoding"):
                run_real_baseline(fixture, output, expect_state=None)

            evidence = json.loads(
                (output / "operational-error.json").read_text(encoding="utf-8")
            )
            self.assertEqual(evidence["error_type"], "RuntimeError")
            self.assertIn("source.encoding", evidence["message"])
            self.assertEqual(
                {item.name for item in output.iterdir()},
                {"operational-error.json"},
            )

    def test_ocr_report_survives_a_deterministic_downstream_render_failure(self):
        candidate_ir = DocumentIR.from_json(
            (
                Path(__file__).resolve().parent
                / "fixtures"
                / "document_ir"
                / "canonical.document.ir.json"
            ).read_bytes()
        )

        def runtime(effective_ocr_dpi):
            return OcrRuntimeEvidence(
                provider="tesseract",
                provider_version="5.0.0-test",
                executable="test-tesseract",
                languages=("jpn", "eng"),
                page_segmentation_mode=6,
                engine_mode=3,
                effective_ocr_dpi=effective_ocr_dpi,
                source_dpi_x=96.012,
                source_dpi_y=96.012,
                traineddata=(
                    OcrTrainedDataEvidence(
                        language="jpn",
                        size_bytes=1,
                        sha256="1" * 64,
                    ),
                    OcrTrainedDataEvidence(
                        language="eng",
                        size_bytes=1,
                        sha256="2" * 64,
                    ),
                ),
                operating_system="deterministic-test-os",
                python_version="3.test",
            )

        control_transform = Mock(effective_ocr_dpi=96)
        control_transform.to_dict.return_value = {
            "schema_version": "1.0",
            "enabled": False,
            "target_dpi": None,
            "effective_ocr_dpi": 96,
        }
        candidate_transform = Mock(effective_ocr_dpi=300)
        candidate_transform.to_dict.return_value = {
            "schema_version": "1.0",
            "enabled": True,
            "target_dpi": 300,
            "effective_ocr_dpi": 300,
        }
        comparison = Mock()
        comparison.decision.value = "supported"
        comparison.to_json.return_value = json.dumps(
            {
                "schema_version": "1.0",
                "decision": "supported",
            },
            sort_keys=True,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "downstream-failure"

            def completed_extraction(_source_data, bundle_directory, **_kwargs):
                bundle_directory.mkdir(parents=True)
                return Mock(document=candidate_ir, diagnostics=())

            with (
                patch(
                    "scripts.run_real_baseline._runtime",
                    return_value=(
                        Mock(),
                        Mock(),
                        Mock(),
                        Mock(),
                        [control_transform],
                        [candidate_transform],
                    ),
                ),
                patch(
                    "scripts.run_real_baseline._environment_record",
                    return_value={"phase": "deterministic-test"},
                ),
                patch(
                    "scripts.run_real_baseline.extract_png",
                    side_effect=completed_extraction,
                ),
                patch(
                    "scripts.run_real_baseline._ocr_runtime_evidence",
                    side_effect=(runtime(96), runtime(300)),
                ),
                patch(
                    "scripts.run_real_baseline.compare_ocr_resolution",
                    return_value=comparison,
                ),
                patch(
                    "scripts.run_real_baseline.render_docx",
                    side_effect=RuntimeError("forced downstream render failure"),
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "forced downstream render failure",
                ):
                    run_real_baseline(FIXTURE_DIRECTORY, output, expect_state=None)

            control_report = json.loads(
                (output / "ocr-quality-control-evaluation.json").read_text(
                    encoding="utf-8"
                )
            )
            report = json.loads(
                (output / "ocr-quality-evaluation.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                control_report["runtime"]["configuration"]["effective_ocr_dpi"],
                96,
            )
            self.assertEqual(report["schema_version"], "1.0")
            self.assertEqual(report["scope"]["text_source"], "candidate_ir")
            self.assertEqual(
                report["runtime"]["configuration"]["effective_ocr_dpi"],
                300,
            )
            self.assertEqual(report["state"], "fail")
            transform = json.loads(
                (output / "ocr-input-transform.json").read_text(encoding="utf-8")
            )
            self.assertFalse(transform["control"]["enabled"])
            self.assertTrue(transform["candidate"]["enabled"])
            resolution = json.loads(
                (output / "ocr-resolution-comparison.json").read_text(encoding="utf-8")
            )
            self.assertEqual(resolution["decision"], "supported")
            self.assertFalse((output / "source-quality-evaluation.json").exists())
            self.assertFalse((output / "bundle" / "reconstructed.docx").exists())
            failure = json.loads(
                (output / "operational-error.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failure["error_type"], "RuntimeError")
            self.assertIn("forced downstream render failure", failure["message"])

    def test_unsupported_ocr_comparison_stops_before_docx_and_retains_evidence(self):
        candidate_ir = DocumentIR.from_json(
            (
                Path(__file__).resolve().parent
                / "fixtures"
                / "document_ir"
                / "canonical.document.ir.json"
            ).read_bytes()
        )

        def runtime(effective_ocr_dpi):
            return OcrRuntimeEvidence(
                provider="tesseract",
                provider_version="5.0.0-test",
                executable="test-tesseract",
                languages=("jpn", "eng"),
                page_segmentation_mode=6,
                engine_mode=3,
                effective_ocr_dpi=effective_ocr_dpi,
                source_dpi_x=96.012,
                source_dpi_y=96.012,
                traineddata=(
                    OcrTrainedDataEvidence(
                        language="jpn",
                        size_bytes=1,
                        sha256="1" * 64,
                    ),
                    OcrTrainedDataEvidence(
                        language="eng",
                        size_bytes=1,
                        sha256="2" * 64,
                    ),
                ),
                operating_system="deterministic-test-os",
                python_version="3.test",
            )

        def transform(*, enabled, target_dpi, effective_ocr_dpi):
            evidence = Mock(effective_ocr_dpi=effective_ocr_dpi)
            evidence.to_dict.return_value = {
                "schema_version": "1.0",
                "enabled": enabled,
                "target_dpi": target_dpi,
                "effective_ocr_dpi": effective_ocr_dpi,
            }
            return evidence

        control_transform = transform(
            enabled=False,
            target_dpi=None,
            effective_ocr_dpi=96,
        )
        candidate_transform = transform(
            enabled=True,
            target_dpi=300,
            effective_ocr_dpi=300,
        )
        comparison = Mock()
        comparison.decision.value = "inconclusive"
        comparison.to_json.return_value = json.dumps(
            {
                "schema_version": "1.0",
                "decision": "inconclusive",
                "reasons": ["text_accuracy_delta_below_minimum:0<1"],
            },
            sort_keys=True,
        )
        render_docx_mock = Mock()

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "unsupported-comparison"

            def completed_extraction(_source_data, bundle_directory, **_kwargs):
                bundle_directory.mkdir(parents=True)
                return Mock(document=candidate_ir, diagnostics=())

            with (
                patch(
                    "scripts.run_real_baseline._runtime",
                    return_value=(
                        Mock(),
                        Mock(),
                        Mock(),
                        Mock(),
                        [control_transform],
                        [candidate_transform],
                    ),
                ),
                patch(
                    "scripts.run_real_baseline._environment_record",
                    return_value={"phase": "deterministic-test"},
                ),
                patch(
                    "scripts.run_real_baseline.extract_png",
                    side_effect=completed_extraction,
                ),
                patch(
                    "scripts.run_real_baseline._ocr_runtime_evidence",
                    side_effect=(runtime(96), runtime(300)),
                ),
                patch(
                    "scripts.run_real_baseline.compare_ocr_resolution",
                    return_value=comparison,
                ),
                patch(
                    "scripts.run_real_baseline.render_docx",
                    render_docx_mock,
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "not a supported improvement: inconclusive",
                ):
                    run_real_baseline(FIXTURE_DIRECTORY, output, expect_state=None)

            render_docx_mock.assert_not_called()
            self.assertTrue((output / "ocr-quality-control-evaluation.json").is_file())
            self.assertTrue((output / "ocr-quality-evaluation.json").is_file())
            self.assertTrue((output / "ocr-input-transform.json").is_file())
            comparison_report = json.loads(
                (output / "ocr-resolution-comparison.json").read_text(encoding="utf-8")
            )
            self.assertEqual(comparison_report["decision"], "inconclusive")
            self.assertFalse((output / "bundle" / "reconstructed.docx").exists())
            self.assertFalse((output / "source-quality-evaluation.json").exists())
            failure = json.loads(
                (output / "operational-error.json").read_text(encoding="utf-8")
            )
            self.assertIn("inconclusive", failure["message"])


@unittest.skipUnless(
    os.environ.get("AITEQNO_RUN_REAL_IMAGE_BASELINE") == "1",
    "set AITEQNO_RUN_REAL_IMAGE_BASELINE=1 for the real runtime baseline",
)
class RealDocumentBaselineIntegrationTest(unittest.TestCase):
    def test_real_tesseract_docx_libreoffice_baseline_remains_an_explicit_failure(self):
        raw_output = os.environ.get("AITEQNO_REAL_BASELINE_OUTPUT")
        self.assertTrue(raw_output, "AITEQNO_REAL_BASELINE_OUTPUT must be set")
        output = Path(raw_output).resolve(strict=False)
        self.assertFalse(output.exists(), "real baseline output must be create-only")

        completed = subprocess.run(
            [
                sys.executable,
                str(REPOSITORY_ROOT / "scripts" / "run_real_baseline.py"),
                "--output",
                str(output),
                "--expect-state",
                "fail",
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        summary = json.loads(
            (output / "baseline-summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary["final_state"], "fail")
        self.assertEqual(summary["expected_current_state"], "fail")
        self.assertEqual(
            summary["layers"]["source_to_candidate_ir_ocr"]["state"],
            "fail",
        )
        self.assertEqual(
            summary["layers"]["source_to_candidate_ir_ocr"]["text_evidence"],
            "candidate_ir",
        )
        self.assertEqual(
            summary["layers"]["source_to_actual_docx"]["state"],
            "fail",
        )
        self.assertEqual(
            summary["layers"]["source_to_actual_docx"]["text_evidence"],
            "rendered_visible",
        )
        source_evaluation = json.loads(
            (output / "source-quality-evaluation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(source_evaluation["state"], "fail")
        self.assertEqual(source_evaluation["text_evidence"], "rendered_visible")
        self.assertEqual(
            set(source_evaluation["components"]),
            {
                "text_accuracy",
                "logical_block_coverage",
                "structure_similarity",
                "geometry_similarity",
            },
        )
        self.assertEqual(
            {gate["name"] for gate in source_evaluation["hard_gates"]},
            {
                "source_digest_matches",
                "reference_reviewed",
                "candidate_ir_page_count",
                "rendered_docx_page_count",
                "essential_text_anchors",
                "essential_logical_blocks",
                "essential_structures",
                "essential_relationships",
                "manual_checks",
            },
        )
        self.assertTrue((output / "preflight-environment.json").is_file())
        self.assertTrue((output / "environment.json").is_file())
        control_ocr_evaluation = json.loads(
            (output / "ocr-quality-control-evaluation.json").read_text(encoding="utf-8")
        )
        ocr_evaluation = json.loads(
            (output / "ocr-quality-evaluation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(ocr_evaluation["schema_version"], "1.0")
        self.assertEqual(ocr_evaluation["scope"]["text_source"], "candidate_ir")
        self.assertEqual(
            ocr_evaluation["scope"]["ends_before"],
            ["docx", "preview", "libreoffice", "poppler", "rendered_page_ocr"],
        )
        self.assertEqual(ocr_evaluation["state"], "fail")
        self.assertEqual(
            ocr_evaluation["runtime"]["configuration"]["languages"],
            ["jpn", "eng"],
        )
        self.assertEqual(
            ocr_evaluation["runtime"]["configuration"]["page_segmentation_mode"],
            6,
        )
        self.assertEqual(
            ocr_evaluation["runtime"]["configuration"]["engine_mode"],
            3,
        )
        self.assertEqual(
            control_ocr_evaluation["runtime"]["configuration"]["effective_ocr_dpi"],
            96,
        )
        self.assertEqual(
            ocr_evaluation["runtime"]["configuration"]["effective_ocr_dpi"],
            300,
        )
        for report in (control_ocr_evaluation, ocr_evaluation):
            source_metadata_dpi = report["runtime"]["configuration"][
                "source_metadata_dpi"
            ]
            self.assertAlmostEqual(source_metadata_dpi["x"], 96.012, places=3)
            self.assertAlmostEqual(source_metadata_dpi["y"], 96.012, places=3)
        self.assertEqual(
            control_ocr_evaluation["source_digest"],
            ocr_evaluation["source_digest"],
        )
        self.assertEqual(
            control_ocr_evaluation["reference_id"],
            ocr_evaluation["reference_id"],
        )
        self.assertEqual(
            control_ocr_evaluation["runtime"]["provider"],
            ocr_evaluation["runtime"]["provider"],
        )
        self.assertEqual(
            control_ocr_evaluation["runtime"]["provider_version"],
            ocr_evaluation["runtime"]["provider_version"],
        )
        self.assertEqual(
            control_ocr_evaluation["runtime"]["configuration"]["languages"],
            ocr_evaluation["runtime"]["configuration"]["languages"],
        )
        self.assertEqual(
            control_ocr_evaluation["runtime"]["configuration"][
                "page_segmentation_mode"
            ],
            ocr_evaluation["runtime"]["configuration"]["page_segmentation_mode"],
        )
        self.assertEqual(
            control_ocr_evaluation["runtime"]["configuration"]["engine_mode"],
            ocr_evaluation["runtime"]["configuration"]["engine_mode"],
        )
        self.assertEqual(
            control_ocr_evaluation["runtime"]["traineddata"],
            ocr_evaluation["runtime"]["traineddata"],
        )
        self.assertEqual(
            ocr_evaluation["thresholds"]["text_character_accuracy"],
            70.0,
        )
        self.assertEqual(
            ocr_evaluation["thresholds"]["logical_block_coverage"],
            60.0,
        )
        self.assertEqual(
            ocr_evaluation["thresholds"]["essential_anchor_recall"],
            100.0,
        )
        transform = json.loads(
            (output / "ocr-input-transform.json").read_text(encoding="utf-8")
        )
        self.assertEqual(transform["schema_version"], "1.0")
        self.assertFalse(transform["control"]["enabled"])
        self.assertIsNone(transform["control"]["target_dpi"])
        self.assertEqual(transform["control"]["effective_ocr_dpi"], 96)
        self.assertTrue(transform["candidate"]["enabled"])
        self.assertEqual(transform["candidate"]["target_dpi"], 300)
        self.assertEqual(transform["candidate"]["effective_ocr_dpi"], 300)
        self.assertAlmostEqual(
            transform["candidate"]["source_effective_dpi"],
            96.012,
            places=3,
        )
        self.assertTrue(transform["candidate"]["crops"])
        self.assertEqual(
            len(transform["control"]["crops"]),
            len(transform["candidate"]["crops"]),
        )
        candidate_scale = 300 / transform["candidate"]["source_effective_dpi"]
        for control_crop, candidate_crop in zip(
            transform["control"]["crops"],
            transform["candidate"]["crops"],
        ):
            self.assertEqual(
                control_crop["region_ref"],
                candidate_crop["region_ref"],
            )
            self.assertEqual(
                control_crop["source_bbox"],
                candidate_crop["source_bbox"],
            )
            self.assertEqual(
                control_crop["source_dimensions"],
                candidate_crop["source_dimensions"],
            )
            self.assertFalse(control_crop["resized"])
            self.assertEqual(
                control_crop["working_dimensions"],
                control_crop["source_dimensions"],
            )
            self.assertTrue(candidate_crop["resized"])
            for axis in ("width", "height"):
                source_dimension = candidate_crop["source_dimensions"][axis]
                working_dimension = candidate_crop["working_dimensions"][axis]
                self.assertGreater(working_dimension, source_dimension)
                self.assertEqual(
                    working_dimension,
                    max(1, math.floor(source_dimension * candidate_scale + 0.5)),
                )
            self.assertLessEqual(
                candidate_crop["working_dimensions"]["width"]
                * candidate_crop["working_dimensions"]["height"],
                40_000_000,
            )
        comparison = json.loads(
            (output / "ocr-resolution-comparison.json").read_text(encoding="utf-8")
        )
        self.assertEqual(comparison["schema_version"], "1.0")
        self.assertEqual(comparison["decision"], "supported")
        self.assertGreaterEqual(
            comparison["metrics"]["text_character_accuracy"]["delta_percentage_points"],
            comparison["adoption_policy"][
                "minimum_text_accuracy_delta_percentage_points"
            ],
        )
        self.assertGreaterEqual(
            comparison["metrics"]["logical_block_coverage"]["candidate"],
            comparison["metrics"]["logical_block_coverage"]["control"],
        )
        self.assertGreaterEqual(
            comparison["metrics"]["essential_anchor_recall"]["candidate"],
            comparison["metrics"]["essential_anchor_recall"]["control"],
        )
        self.assertFalse(comparison["recovery"]["anchors"]["lost"])
        self.assertFalse(comparison["recovery"]["logical_blocks"]["lost"])
        self.assertLessEqual(
            comparison["recovery"]["essential_blocks"]["unrecovered_count_delta"],
            0,
        )
        self.assertTrue(
            all(check["status"] == "pass" for check in comparison["checks"].values())
        )
        self.assertEqual(
            summary["layers"]["ocr_input_resolution_comparison"]["decision"],
            "supported",
        )
        self.assertTrue((output / "ocr-quality-control-evaluation.json").is_file())
        self.assertTrue((output / "ocr-quality-evaluation.json").is_file())
        self.assertTrue((output / "ocr-input-transform.json").is_file())
        self.assertTrue((output / "ocr-resolution-comparison.json").is_file())
        self.assertTrue((output / "control-bundle" / "document.ir.json").is_file())
        self.assertTrue((output / "source-quality-evaluation.json").is_file())
        self.assertTrue((output / "ir-to-docx-restoration-evaluation.json").is_file())
        snapshot = output / "actual-docx-snapshot"
        self.assertTrue((snapshot / "snapshot.pdf").is_file())
        self.assertTrue((snapshot / "page-001.png").is_file())
        self.assertTrue((snapshot / "snapshot-evidence.json").is_file())
        self.assertTrue((snapshot / "visible-ocr.json").is_file())


if __name__ == "__main__":
    unittest.main()
