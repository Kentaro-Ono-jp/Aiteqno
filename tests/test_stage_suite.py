import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from docx import Document

from scripts.run_stage_suite import (
    DEFAULT_SUITE_PATH,
    _docx_media_evidence,
    _hidden_text_check,
    _integrity_report,
    _load_suite,
    _read_fixture,
    run,
)


Q01_DIRECTORY = (
    Path(__file__).parent
    / "fixtures"
    / "generalization"
    / "japanese-questionnaires-v1"
)
Q01_MANIFEST = Q01_DIRECTORY / "questionnaire-01-general-medicine.manifest.json"
Q01_ID = "questionnaire-01-general-medicine"
Q01_SOURCE_SHA256 = "e6aadded4a7ca5d92358c93d87679b65f5f81f9ebf886a13871968a1dd96a734"
Q01_REFERENCE_SHA256 = "b3c5670dce98dedfced0d1508ba95583801ec430e69bb6391a20945a10fa82cb"


class StageSuiteContractTest(unittest.TestCase):
    def test_stage_one_has_exactly_the_two_pinned_active_fixtures(self):
        suite = _load_suite(DEFAULT_SUITE_PATH)

        self.assertEqual(
            [item.fixture_id for item in suite.fixtures],
            ["synthetic-dense-japanese-form-v1", Q01_ID],
        )
        self.assertEqual(
            [item.source_encoding for item in suite.fixtures],
            ["base64", "raw"],
        )
        self.assertEqual([item.source_dpi for item in suite.fixtures], [96, 150])
        self.assertEqual(suite.threshold, 70)
        self.assertEqual(suite.production_languages, ("jpn",))
        self.assertEqual(suite.visible_languages, suite.production_languages)
        self.assertEqual(suite.snapshot_dpi, 300)

    def test_q01_reference_is_reviewed_source_grounded_and_complete(self):
        fixture = _read_fixture(Q01_MANIFEST, Q01_ID)
        reference = fixture.reference

        self.assertEqual(fixture.source_sha256, Q01_SOURCE_SHA256)
        self.assertEqual(fixture.reference_sha256, Q01_REFERENCE_SHA256)
        self.assertEqual((fixture.source_width, fixture.source_height), (1240, 1754))
        self.assertTrue(reference.reviewed)
        self.assertGreaterEqual(len(reference.text_regions), 35)
        self.assertGreaterEqual(len(reference.structural_items), 35)
        self.assertGreaterEqual(len(reference.relationships), 14)
        expected_text = "".join(item.text for item in reference.text_regions)
        for phrase in (
            "内科 初診問診票",
            "本日はどのような症状で受診されましたか。",
            "現在飲んでいる薬やサプリメントをご記入ください。",
            "医師へ伝えておきたいこと",
            "ご記入後、受付へお渡しください。",
        ):
            self.assertIn(phrase, expected_text)
        reference_json = json.loads(fixture.reference_path.read_text(encoding="utf-8"))
        self.assertEqual(reference_json["review"]["exclusions"], [])

    def _copied_q01_fixture(self, root: Path) -> Path:
        destination = root / "fixture"
        destination.mkdir()
        for name in (
            "questionnaire-01-general-medicine.png",
            "questionnaire-01-general-medicine.manifest.json",
            "questionnaire-01-general-medicine.reference.json",
        ):
            shutil.copy2(Q01_DIRECTORY / name, destination / name)
        return destination / "questionnaire-01-general-medicine.manifest.json"

    def test_source_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path = self._copied_q01_fixture(Path(root))
            source_path = manifest_path.parent / "questionnaire-01-general-medicine.png"
            source_path.write_bytes(source_path.read_bytes() + b"tamper")

            with self.assertRaisesRegex(RuntimeError, "source SHA-256 mismatch"):
                _read_fixture(manifest_path, Q01_ID)

    def test_reference_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path = self._copied_q01_fixture(Path(root))
            reference_path = (
                manifest_path.parent
                / "questionnaire-01-general-medicine.reference.json"
            )
            reference_path.write_bytes(reference_path.read_bytes() + b" ")

            with self.assertRaisesRegex(RuntimeError, "reference SHA-256 mismatch"):
                _read_fixture(manifest_path, Q01_ID)

    def test_unreviewed_reference_is_rejected_even_with_a_matching_hash(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path = self._copied_q01_fixture(Path(root))
            reference_path = (
                manifest_path.parent
                / "questionnaire-01-general-medicine.reference.json"
            )
            reference = json.loads(reference_path.read_text(encoding="utf-8"))
            reference["reviewed"] = False
            reference_path.write_text(
                json.dumps(reference, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["reference"]["sha256"] = hashlib.sha256(
                reference_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "not human-reviewed"):
                _read_fixture(manifest_path, Q01_ID)

    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path = self._copied_q01_fixture(Path(root))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source"]["path"] = "../outside.png"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "without traversal"):
                _read_fixture(manifest_path, Q01_ID)

    def test_existing_output_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "already-there"
            output.mkdir()

            with self.assertRaises(FileExistsError):
                run(DEFAULT_SUITE_PATH, output)

    def test_source_page_image_and_hidden_text_are_rejected(self):
        fixture = _read_fixture(Q01_MANIFEST, Q01_ID)
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            image_docx = root_path / "image.docx"
            document = Document()
            document.add_picture(str(fixture.source_path))
            document.save(image_docx)
            passed, reason, _ = _docx_media_evidence(
                image_docx,
                source_sha256=fixture.source_sha256,
                source_dimensions=(fixture.source_width, fixture.source_height),
            )
            self.assertFalse(passed)
            self.assertIn("source-sized", reason)

            hidden_docx = root_path / "hidden.docx"
            hidden = Document()
            run_element = hidden.add_paragraph().add_run("invisible truth")
            run_element.font.hidden = True
            hidden.save(hidden_docx)
            hidden_passed, hidden_reason = _hidden_text_check(hidden_docx)
            self.assertFalse(hidden_passed)
            self.assertIn("hidden-text", hidden_reason)

            layout_docx = root_path / "layout-spacer.docx"
            layout = Document()
            spacer = layout.add_paragraph().add_run("\u200b")
            spacer.font.hidden = True
            layout.save(layout_docx)
            layout_passed, layout_reason = _hidden_text_check(layout_docx)
            self.assertTrue(layout_passed)
            self.assertIn("no hidden semantic text", layout_reason)

    def test_external_relationship_makes_integrity_fail(self):
        fixture = _read_fixture(Q01_MANIFEST, Q01_ID)
        with tempfile.TemporaryDirectory() as root:
            docx_path = Path(root) / "plain.docx"
            document = Document()
            document.add_paragraph("visible")
            document.save(docx_path)
            observation = SimpleNamespace(
                package_readable=True,
                python_docx_reopenable=True,
                errors=(),
                external_relationships=("https://example.invalid/",),
            )
            snapshot = SimpleNamespace(
                page_count=1,
                pages=(object(),),
                renderer_name="test",
                renderer_version="1",
                rasterizer_name="test",
                rasterizer_version="1",
            )

            result = _integrity_report(
                fixture,
                docx_path=docx_path,
                observation=observation,
                snapshot=snapshot,
                visible_text="visible",
            )

            self.assertFalse(result["passed"])
            external = next(
                item for item in result["checks"] if item["name"] == "external_relationships"
            )
            self.assertFalse(external["passed"])


if __name__ == "__main__":
    unittest.main()
