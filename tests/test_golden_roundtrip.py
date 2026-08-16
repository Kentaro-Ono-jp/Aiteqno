import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

from aiteqno.adapters import (
    BundleAssetResolver,
    FakeOcrBackend,
    FakeOcrObservation,
    FilesystemDocumentBundleWriter,
    JsonSchemaDocumentIRValidator,
    LibreOfficeSnapshotRenderer,
    OpenCvStructureExtractor,
    PillowPngAssetEncoder,
    PillowPngDecoder,
    PillowPreviewRenderer,
    PythonDocxObserver,
    PythonDocxRenderer,
)
from aiteqno.adapters.json_schema import document_ir_from_file
from aiteqno.application import (
    EvaluationConfig,
    build_evaluation_reference,
    evaluate_restoration,
    render_docx,
)
from aiteqno.cli import CliRuntime, ExitCode, main
from aiteqno.domain import PixelBoundingBox, TextElement
from aiteqno.ports import (
    EvaluationState,
    RelationshipKind,
    SnapshotObservation,
    StructuralRelationship,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "e2e"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
REPRESENTATIVE_FIXTURE_ID = "representative-patient-form-v1"
REQUIRED_DOCX_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
}


def _manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _fixture(fixture_id):
    fixtures = _manifest()["fixtures"]
    return next(item for item in fixtures if item["id"] == fixture_id)


def _fixture_bytes(specification):
    source_path = (FIXTURE_ROOT / specification["source"]).resolve()
    encoded = source_path.read_text(encoding="ascii").strip()
    payload = base64.b64decode(encoded, validate=True)
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != specification["sha256"]:
        raise AssertionError(
            f"fixture digest changed: {actual_sha256} != {specification['sha256']}"
        )
    return payload


def _runtime(specification):
    observations = tuple(
        FakeOcrObservation(
            text=item["text"],
            bbox=PixelBoundingBox(**item["bbox"]),
            confidence=item["confidence"],
        )
        for item in specification["ocr"]["observations"]
    )
    return CliRuntime(
        decoder=PillowPngDecoder(),
        structure_extractor=OpenCvStructureExtractor(),
        ocr_backend=FakeOcrBackend(observations),
        asset_encoder=PillowPngAssetEncoder(),
        validator=JsonSchemaDocumentIRValidator(),
        bundle_writer=FilesystemDocumentBundleWriter(),
        docx_renderer_factory=lambda root: PythonDocxRenderer(
            asset_resolver=BundleAssetResolver(root)
        ),
        preview_renderer_factory=lambda root: PillowPreviewRenderer(
            asset_resolver=BundleAssetResolver(root)
        ),
    )


def _run_cli(arguments, specification):
    stdout = StringIO()
    stderr = StringIO()
    exit_code = main(
        arguments,
        runtime=_runtime(specification),
        stdout=stdout,
        stderr=stderr,
    )
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _golden_relationships(specification):
    topology = specification["reference"]["docx_topology"]
    body = topology["body"]
    table = topology["table"]
    cell = topology["cell"]
    paragraphs = tuple(
        f"docx-paragraph-{index:04d}" for index in range(topology["paragraph_count"])
    )
    texts = tuple(
        f"p001-text-{index:04d}" for index in range(topology["paragraph_count"] - 1)
    )

    relationships = [
        StructuralRelationship(
            kind=RelationshipKind.CONTAINMENT,
            source=table,
            target=cell,
        ),
        StructuralRelationship(
            kind=RelationshipKind.CONTAINMENT,
            source=cell,
            target=topology["rectangle_element"],
        ),
        StructuralRelationship(
            kind=RelationshipKind.CONTAINMENT,
            source=paragraphs[0],
            target=topology["image_element"],
        ),
        StructuralRelationship(
            kind=RelationshipKind.CONTAINMENT,
            source=cell,
            target=paragraphs[0],
        ),
    ]
    for index, paragraph in enumerate(paragraphs[1:]):
        relationships.append(
            StructuralRelationship(
                kind=RelationshipKind.CONTAINMENT,
                source=paragraph,
                target=texts[index],
            )
        )
        if index == 0:
            relationships.append(
                StructuralRelationship(
                    kind=RelationshipKind.CONTAINMENT,
                    source=paragraph,
                    target=topology["line_element"],
                )
            )
        relationships.extend(
            (
                StructuralRelationship(
                    kind=RelationshipKind.CONTAINMENT,
                    source=cell,
                    target=paragraph,
                ),
                StructuralRelationship(
                    kind=RelationshipKind.ADJACENCY,
                    source=paragraphs[index],
                    target=paragraph,
                ),
            )
        )
    relationships.append(
        StructuralRelationship(
            kind=RelationshipKind.CONTAINMENT,
            source=body,
            target=table,
        )
    )
    relationships.extend(
        StructuralRelationship(
            kind=RelationshipKind.READING_ORDER,
            source=source,
            target=target,
            essential=True,
        )
        for source, target in zip(texts, texts[1:], strict=False)
    )
    return tuple(relationships)


def _reference(document, specification):
    reference = specification["reference"]
    return build_evaluation_reference(
        document,
        reference_id=reference["reference_id"],
        reviewed=True,
        essential_element_ids=reference["essential_element_ids"],
        essential_text_anchors=reference["essential_text_anchors"],
        relationships=_golden_relationships(specification),
    )


def _assert_golden_ir(test_case, ir_path, specification):
    expected = specification["expected"]
    actual_ir_digest = hashlib.sha256(ir_path.read_bytes()).hexdigest()
    test_case.assertEqual(actual_ir_digest, expected["document_ir_sha256"])
    document = document_ir_from_file(ir_path)
    elements = tuple(element for page in document.pages for element in page.elements)
    counts = Counter(element.type.value for element in elements)
    test_case.assertEqual(dict(counts), expected["element_counts"])
    test_case.assertEqual(
        [element.text for element in elements if isinstance(element, TextElement)],
        expected["texts"],
    )
    test_case.assertEqual(len(document.assets), 1)
    asset = document.assets[0]
    test_case.assertEqual(asset.sha256, expected["asset_sha256"])
    resolved = BundleAssetResolver(ir_path.parent).resolve(asset)
    test_case.assertEqual(
        hashlib.sha256(resolved.data).hexdigest(),
        expected["asset_sha256"],
    )
    return document


def _prepare_evaluation(root, specification):
    source_path = root / "source.png"
    source_path.write_bytes(_fixture_bytes(specification))
    bundle = root / "evaluation-bundle"
    ir_path = bundle / "document.ir.json"
    exit_code, _, stderr = _run_cli(
        [
            "extract",
            str(source_path),
            "-o",
            str(ir_path),
            "--language",
            "eng",
        ],
        specification,
    )
    if exit_code != ExitCode.SUCCESS:
        raise AssertionError(stderr)
    source_path.unlink()
    document = _assert_golden_ir(unittest.TestCase(), ir_path, specification)
    docx_path = bundle / "evaluation.docx"
    render_result = render_docx(
        document,
        docx_path,
        renderer=PythonDocxRenderer(asset_resolver=BundleAssetResolver(bundle)),
    )
    return source_path, document, render_result


class GoldenFixtureManifestTest(unittest.TestCase):
    def test_fixture_license_provenance_and_source_hashes_are_tracked(self):
        manifest = _manifest()

        self.assertEqual(manifest["manifest_version"], 1)
        self.assertEqual(manifest["policy"]["license"], "MIT")
        self.assertIn(
            "explicit review target", manifest["policy"]["golden_update_rule"]
        )
        self.assertEqual(
            {item["role"] for item in manifest["fixtures"]},
            {"synthetic_ocr_smoke", "real_document_equivalent"},
        )
        for fixture in manifest["fixtures"]:
            self.assertEqual(fixture["license"], "MIT")
            self.assertFalse(fixture["contains_personal_data"])
            self.assertTrue(fixture["provenance"])
            self.assertEqual(
                hashlib.sha256(_fixture_bytes(fixture)).hexdigest(),
                fixture["sha256"],
            )


class GoldenRoundtripTest(unittest.TestCase):
    def setUp(self):
        self.specification = _fixture(REPRESENTATIVE_FIXTURE_ID)

    def test_four_cli_commands_roundtrip_without_the_source_png(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "Windows Linux 共通"
            root.mkdir()
            source_path = root / "代表 問診票.png"
            source_payload = _fixture_bytes(self.specification)
            source_path.write_bytes(source_payload)

            roundtrip_bundle = root / "roundtrip"
            code, stdout, stderr = _run_cli(
                [
                    "roundtrip",
                    str(source_path),
                    "-o",
                    str(roundtrip_bundle),
                    "--language",
                    "eng",
                    "--dpi",
                    "96",
                ],
                self.specification,
            )
            self.assertEqual(code, ExitCode.SUCCESS, stderr)
            self.assertIn(f"bundle={roundtrip_bundle.resolve()}", stdout)
            _assert_golden_ir(
                self,
                roundtrip_bundle / "document.ir.json",
                self.specification,
            )

            extracted_bundle = root / "ir-only"
            ir_path = extracted_bundle / "document.ir.json"
            code, _, stderr = _run_cli(
                [
                    "extract",
                    str(source_path),
                    "-o",
                    str(ir_path),
                    "--language",
                    "eng",
                ],
                self.specification,
            )
            self.assertEqual(code, ExitCode.SUCCESS, stderr)
            document = _assert_golden_ir(self, ir_path, self.specification)

            source_path.unlink()
            self.assertFalse(source_path.exists())
            docx_path = extracted_bundle / "from-ir.docx"
            preview_path = extracted_bundle / "from-ir.png"
            render_code, _, render_stderr = _run_cli(
                ["render", str(ir_path), "-o", str(docx_path)],
                self.specification,
            )
            preview_code, _, preview_stderr = _run_cli(
                [
                    "preview",
                    str(ir_path),
                    "-o",
                    str(preview_path),
                    "--dpi",
                    "96",
                ],
                self.specification,
            )

            self.assertEqual(render_code, ExitCode.SUCCESS, render_stderr)
            self.assertEqual(preview_code, ExitCode.SUCCESS, preview_stderr)
            self.assertFalse(source_path.exists())
            with ZipFile(docx_path) as package:
                self.assertIsNone(package.testzip())
                self.assertTrue(REQUIRED_DOCX_PARTS.issubset(package.namelist()))
            observation = PythonDocxObserver().observe(docx_path)
            self.assertTrue(observation.package_readable)
            self.assertTrue(observation.python_docx_reopenable)
            self.assertEqual(observation.external_relationships, ())
            self.assertEqual(observation.errors, ())
            self.assertEqual(
                [element.text for element in observation.elements if element.text],
                self.specification["expected"]["texts"],
            )
            with Image.open(preview_path) as preview:
                preview.verify()
            for asset in document.assets:
                asset_payload = (
                    BundleAssetResolver(extracted_bundle).resolve(asset).data
                )
                self.assertNotEqual(asset_payload, source_payload)

    def test_quality_contract_passes_near_70_without_geometry_credit(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path, document, render_result = _prepare_evaluation(
                root,
                self.specification,
            )
            snapshot = SnapshotObservation(
                renderer_name="golden-repair-free-office-contract",
                renderer_version="1",
                available=True,
                opened_without_repair=True,
            )
            result = evaluate_restoration(
                document,
                _reference(document, self.specification),
                render_result.output_path,
                render_result.report,
                observer=PythonDocxObserver(),
                snapshot=snapshot,
                config=EvaluationConfig(
                    threshold=self.specification["expected"]["restoration_threshold"]
                ),
            )

            self.assertFalse(source_path.exists())
            self.assertEqual(result.state, EvaluationState.PASS)
            self.assertEqual(
                result.overall_score,
                self.specification["expected"]["restoration_score"],
            )
            components = {item.name: item.score for item in result.components}
            self.assertEqual(components["geometry_similarity"], 0.0)
            self.assertTrue(all(gate.passed is True for gate in result.hard_gates))


class LibreOfficeSnapshotRendererTest(unittest.TestCase):
    def test_successful_headless_conversion_produces_repair_free_evidence(self):
        def fake_run(command, **_kwargs):
            if command[-1] == "--version":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "LibreOffice 25.2.4.2\n",
                    "",
                )
            output_directory = Path(command[command.index("--outdir") + 1])
            source = Path(command[-1])
            (output_directory / f"{source.stem}.pdf").write_bytes(
                b"%PDF-1.7\nfixture\n%%EOF\n"
            )
            return subprocess.CompletedProcess(command, 0, "convert complete", "")

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docx_path = root / "fixture.docx"
            docx_path.write_bytes(b"fixture")
            renderer = LibreOfficeSnapshotRenderer(executable_path=sys.executable)
            with patch(
                "aiteqno.adapters.libreoffice.subprocess.run",
                side_effect=fake_run,
            ):
                observation = renderer.observe(docx_path)

        self.assertTrue(observation.available)
        self.assertTrue(observation.opened_without_repair)
        self.assertEqual(observation.renderer_version, "LibreOffice 25.2.4.2")
        self.assertEqual(observation.regions, ())
        self.assertEqual(observation.errors, ())

    def test_missing_optional_runtime_is_explicit_unknown_evidence(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docx_path = root / "fixture.docx"
            docx_path.write_bytes(b"fixture")
            observation = LibreOfficeSnapshotRenderer(
                executable_path=root / "missing-soffice"
            ).observe(docx_path)

        self.assertFalse(observation.available)
        self.assertIsNone(observation.opened_without_repair)
        self.assertEqual(observation.renderer_version, "unavailable")


@unittest.skipUnless(
    os.environ.get("AITEQNO_RUN_LIBREOFFICE_INTEGRATION") == "1",
    "set AITEQNO_RUN_LIBREOFFICE_INTEGRATION=1 with LibreOffice installed",
)
class RealLibreOfficeGoldenIntegrationTest(unittest.TestCase):
    def test_libreoffice_opens_and_scores_the_golden_docx_without_repair(self):
        specification = _fixture(REPRESENTATIVE_FIXTURE_ID)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path, document, render_result = _prepare_evaluation(
                root,
                specification,
            )
            snapshot = LibreOfficeSnapshotRenderer().observe(render_result.output_path)
            result = evaluate_restoration(
                document,
                _reference(document, specification),
                render_result.output_path,
                render_result.report,
                observer=PythonDocxObserver(),
                snapshot=snapshot,
                config=EvaluationConfig(
                    threshold=specification["expected"]["restoration_threshold"]
                ),
            )

        self.assertFalse(source_path.exists())
        self.assertTrue(snapshot.available, snapshot.errors)
        self.assertTrue(snapshot.opened_without_repair, snapshot.errors)
        self.assertEqual(snapshot.errors, ())
        self.assertEqual(result.state, EvaluationState.PASS)
        self.assertEqual(
            result.overall_score,
            specification["expected"]["restoration_score"],
        )


if __name__ == "__main__":
    unittest.main()
