import base64
import os
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from aiteqno.adapters import (
    BundleAssetResolver,
    FakeOcrBackend,
    FakeOcrObservation,
    FilesystemDocumentBundleWriter,
    JsonSchemaDocumentIRValidator,
    OpenCvStructureExtractor,
    PillowPngAssetEncoder,
    PillowPngDecoder,
    TesseractOcrBackend,
)
from aiteqno.adapters.json_schema import document_ir_from_file
from aiteqno.application import PngExtractionError, extract_png
from aiteqno.domain import (
    Confidence,
    DocumentIRValidationError,
    ImageElement,
    LineElement,
    PixelBoundingBox,
    ProvenanceStage,
    RectangleElement,
    TextElement,
)
from aiteqno.ports import (
    AssetEncodingError,
    OcrBackendError,
    OcrOptions,
    OcrRegionGroupingConfig,
    RegionCandidate,
    RegionKind,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "structure"
QUESTIONNAIRE_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "generalization"
    / "japanese-questionnaires-v1"
    / "questionnaire-04-orthopedics.png"
)


class PngExtractionPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.png_data = base64.b64decode(
            (FIXTURE_ROOT / "structured-page.png.b64").read_text(encoding="ascii")
        )
        cls.decoder = PillowPngDecoder()
        cls.image = cls.decoder.decode(cls.png_data)
        cls.structure = OpenCvStructureExtractor().detect(cls.image)
        cls.observations = (
            FakeOcrObservation(
                text="PATIENT",
                bbox=PixelBoundingBox(x=42, y=47, width=72, height=13),
                confidence=0.95,
            ),
            FakeOcrObservation(
                text="FORM",
                bbox=PixelBoundingBox(x=120, y=47, width=48, height=13),
                confidence=0.95,
            ),
            FakeOcrObservation(
                text="NAME",
                bbox=PixelBoundingBox(x=42, y=129, width=37, height=10),
                confidence=0.96,
            ),
            FakeOcrObservation(
                text="JOHN",
                bbox=PixelBoundingBox(x=87, y=129, width=37, height=10),
                confidence=0.95,
            ),
            FakeOcrObservation(
                text="DOE",
                bbox=PixelBoundingBox(x=130, y=129, width=28, height=10),
                confidence=0.95,
            ),
            FakeOcrObservation(
                text="DATE",
                bbox=PixelBoundingBox(x=42, y=169, width=35, height=10),
                confidence=0.95,
            ),
            FakeOcrObservation(
                text="2026-08-16",
                bbox=PixelBoundingBox(x=85, y=169, width=71, height=10),
                confidence=0.90,
            ),
        )

    def test_fake_backend_builds_schema_valid_self_contained_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "document-bundle"
            result = self._extract(output)

            self.assertEqual(result.bundle.bundle_root, output.resolve())
            self.assertTrue(result.bundle.document_path.is_file())
            self.assertTrue((output / "assets").is_dir())
            self.assertEqual(result.diagnostics, ())
            self.assertEqual(
                document_ir_from_file(result.bundle.document_path), result.document
            )

            page = result.document.pages[0]
            texts = tuple(
                element for element in page.elements if isinstance(element, TextElement)
            )
            lines = tuple(
                element for element in page.elements if isinstance(element, LineElement)
            )
            rectangles = tuple(
                element
                for element in page.elements
                if isinstance(element, RectangleElement)
            )
            images = tuple(
                element
                for element in page.elements
                if isinstance(element, ImageElement)
            )
            self.assertAlmostEqual(page.size.width, 240.0, delta=0.02)
            self.assertAlmostEqual(page.size.height, 160.0, delta=0.02)
            self.assertEqual(len(texts), 7)
            self.assertEqual(len(lines), 6)
            self.assertEqual(len(rectangles), 5)
            self.assertEqual(len(images), 1)
            self.assertEqual(
                tuple(element.text for element in texts),
                ("PATIENT", "FORM", "NAME", "JOHN", "DOE", "DATE", "2026-08-16"),
            )
            self.assertEqual(
                tuple(element.id for element in texts),
                tuple(f"p001-text-{index:04d}" for index in range(7)),
            )
            self.assertEqual(
                tuple(element.reading_order for element in texts), tuple(range(7))
            )

            for text in texts:
                self.assertAlmostEqual(
                    text.style.font_size_pt,
                    max(1.0, round(text.bbox.height * 0.75, 6)),
                )
                self.assertIsNotNone(text.confidence.detection)
                self.assertIsNotNone(text.confidence.recognition)
                self.assertEqual(
                    tuple(record.stage for record in text.provenance),
                    (ProvenanceStage.STRUCTURE, ProvenanceStage.OCR),
                )
                ocr_extension = text.extensions["jp.reactorfront.aiteqno.ocr"]
                self.assertEqual(ocr_extension["provider"], "aiteqno.fake-ocr")
                self.assertEqual(ocr_extension["languages"], ("eng",))
            for element in (*lines, *rectangles, *images):
                self.assertIsNotNone(element.confidence)
                self.assertTrue(element.provenance)
                self.assertIs(element.provenance[0].stage, ProvenanceStage.STRUCTURE)

            self.assertEqual(len(result.document.assets), 1)
            asset = result.document.assets[0]
            resolved = BundleAssetResolver(output).resolve(asset)
            self.assertEqual(resolved.data, result.bundle.asset_paths[0].read_bytes())
            self.assertNotEqual(resolved.data, self.png_data)
            self.assertLess(asset.pixel_width, self.image.source.pixel_width)
            self.assertLess(asset.pixel_height, self.image.source.pixel_height)
            with Image.open(BytesIO(resolved.data)) as decoded_asset:
                self.assertEqual(
                    decoded_asset.size, (asset.pixel_width, asset.pixel_height)
                )
            serialized = result.bundle.document_path.read_text(encoding="utf-8")
            self.assertNotIn(
                base64.b64encode(self.png_data).decode("ascii"), serialized
            )

    def test_shuffled_candidates_and_tokens_produce_identical_ids_order_and_bytes(self):
        reversed_structure = replace(
            self.structure,
            lines=tuple(reversed(self.structure.lines)),
            rectangles=tuple(reversed(self.structure.rectangles)),
            text_regions=tuple(reversed(self.structure.text_regions)),
            image_regions=tuple(reversed(self.structure.image_regions)),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            first = self._extract(Path(temp_dir) / "first")
            second = self._extract(
                Path(temp_dir) / "second",
                structure_extractor=_StaticStructureExtractor(reversed_structure),
                ocr_backend=FakeOcrBackend(tuple(reversed(self.observations))),
            )

            self.assertEqual(first.document, second.document)
            self.assertEqual(
                first.bundle.document_path.read_bytes(),
                second.bundle.document_path.read_bytes(),
            )
            self.assertEqual(
                tuple(path.read_bytes() for path in first.bundle.asset_paths),
                tuple(path.read_bytes() for path in second.bundle.asset_paths),
            )

    def test_image_region_failure_is_non_fatal_and_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self._extract(
                Path(temp_dir) / "bundle",
                asset_encoder=_FailingAssetEncoder(),
            )

            self.assertEqual(result.document.assets, ())
            self.assertFalse(
                any(
                    isinstance(element, ImageElement)
                    for element in result.document.pages[0].elements
                )
            )
            self.assertIn(
                "asset_test_failure",
                {diagnostic.code for diagnostic in result.diagnostics},
            )
            self.assertTrue(result.bundle.document_path.is_file())

    def test_low_confidence_text_echo_of_preserved_compact_control_is_omitted(self):
        text_region = self.structure.text_regions[0]
        compact_control = replace(
            self.structure.rectangles[0],
            bbox=text_region.bbox,
            provenance=(
                replace(
                    self.structure.rectangles[0].provenance[0],
                    source_bbox_px=text_region.bbox,
                    notes="compact closed-outline rectangle candidate",
                ),
            ),
        )
        structure = replace(
            self.structure,
            rectangles=(compact_control,),
            text_regions=(text_region,),
            image_regions=(),
        )
        echo = FakeOcrObservation(
            text="口",
            bbox=text_region.bbox,
            confidence=0.24,
        )
        label = FakeOcrObservation(
            text="KEEP",
            bbox=text_region.bbox,
            confidence=0.25,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = self._extract(
                Path(temp_dir) / "bundle",
                structure_extractor=_StaticStructureExtractor(structure),
                ocr_backend=FakeOcrBackend((echo, label)),
                ocr_options=OcrOptions(
                    page_segmentation_mode=6,
                    timeout_seconds=10,
                    min_confidence=0.0,
                ),
            )

        self.assertEqual(
            tuple(
                element.text
                for element in result.document.pages[0].elements
                if isinstance(element, TextElement)
            ),
            ("KEEP",),
        )
        self.assertIn(
            "ocr_low_confidence_control_echo_omitted",
            {diagnostic.code for diagnostic in result.diagnostics},
        )

    def test_page_covering_image_is_never_embedded(self):
        page_bbox = PixelBoundingBox(
            x=0,
            y=0,
            width=self.image.source.pixel_width,
            height=self.image.source.pixel_height,
        )
        source_region = self.structure.image_regions[0]
        page_region = RegionCandidate(
            kind=RegionKind.IMAGE,
            bbox=page_bbox,
            confidence=Confidence(overall=0.4, detection=0.4),
            provenance=(
                replace(
                    source_region.provenance[0],
                    source_bbox_px=page_bbox,
                    notes="page-covering candidate for application test",
                ),
            ),
        )
        page_covering = replace(self.structure, image_regions=(page_region,))
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self._extract(
                Path(temp_dir) / "bundle",
                structure_extractor=_StaticStructureExtractor(page_covering),
                asset_encoder=_MustNotRunAssetEncoder(),
            )

            self.assertEqual(result.document.assets, ())
            self.assertIn(
                "page_covering_image_skipped",
                {diagnostic.code for diagnostic in result.diagnostics},
            )

    def test_validation_and_publication_failures_leave_no_partial_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rejected_output = root / "schema-rejected"
            with self.assertRaises(PngExtractionError) as schema_context:
                self._extract(rejected_output, validator=_RejectingValidator())
            self.assertEqual(schema_context.exception.stage, "validate")
            self.assertEqual(
                schema_context.exception.code,
                "document_ir_schema_invalid",
            )
            self.assertFalse(rejected_output.exists())

            failed_output = root / "publish-failed"
            with patch(
                "aiteqno.adapters.extraction.os.rename",
                side_effect=OSError("simulated publication failure"),
            ):
                with self.assertRaises(PngExtractionError) as write_context:
                    self._extract(failed_output)
            self.assertEqual(write_context.exception.stage, "write")
            self.assertEqual(write_context.exception.code, "bundle_write_failed")
            self.assertFalse(failed_output.exists())
            self.assertEqual(list(root.glob(".publish-failed.tmp-*")), [])

    def test_existing_bundle_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "existing"
            output.mkdir()
            marker = output / "owned-by-user.txt"
            marker.write_text("preserve", encoding="utf-8")

            with self.assertRaises(PngExtractionError) as context:
                self._extract(output)

            self.assertEqual(context.exception.stage, "write")
            self.assertEqual(context.exception.code, "bundle_exists")
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_ocr_backend_failure_keeps_stable_stage_and_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "bundle"
            with self.assertRaises(PngExtractionError) as context:
                self._extract(output, ocr_backend=_FailingOcrBackend())

            self.assertEqual(context.exception.stage, "ocr")
            self.assertEqual(context.exception.code, "ocr_test_failure")
            self.assertFalse(output.exists())

    def test_application_orchestration_keeps_adapter_boundaries(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "aiteqno"
            / "application"
            / "extract.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("aiteqno.adapters", source)
        self.assertNotIn("pytesseract", source)
        self.assertNotIn("cv2", source)

    def test_geometry_grouping_plan_is_observable_and_used_for_ocr_regions(self):
        title = self.structure.text_regions[0]
        left_bbox = PixelBoundingBox(x=41, y=46, width=60, height=15)
        right_bbox = PixelBoundingBox(x=105, y=46, width=64, height=15)
        split_title = (
            replace(
                title,
                bbox=left_bbox,
                provenance=(replace(title.provenance[0], source_bbox_px=left_bbox),),
            ),
            replace(
                title,
                bbox=right_bbox,
                provenance=(replace(title.provenance[0], source_bbox_px=right_bbox),),
            ),
        )
        structure = replace(
            self.structure,
            text_regions=(*split_title, *self.structure.text_regions[1:]),
        )
        observed = []
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self._extract(
                Path(temp_dir) / "bundle",
                structure_extractor=_StaticStructureExtractor(structure),
                ocr_region_grouping=OcrRegionGroupingConfig(enabled=True),
                ocr_region_grouping_observer=observed.append,
            )

        self.assertEqual(len(observed), 1)
        evidence = observed[0]
        self.assertTrue(evidence.configuration["enabled"])
        self.assertEqual(len(evidence.groups), 1)
        self.assertEqual(
            evidence.groups[0]["member_refs"],
            ["p001-text-region-0000", "p001-text-region-0001"],
        )
        texts = tuple(
            value
            for value in result.document.pages[0].elements
            if isinstance(value, TextElement)
        )
        title_refs = tuple(
            record.source_refs
            for value in texts[:2]
            for record in value.provenance
            if record.stage is ProvenanceStage.OCR
        )
        self.assertEqual(
            title_refs,
            (("p001-text-line-group-0000",),) * 2,
        )

    def test_landscape_columns_use_full_page_psm3_and_column_reading_order(self):
        png_data = QUESTIONNAIRE_FIXTURE.read_bytes()
        observations = (
            FakeOcrObservation(
                text="BOTTOM",
                bbox=PixelBoundingBox(x=900, y=1100, width=80, height=20),
            ),
            FakeOcrObservation(
                text="RIGHT",
                bbox=PixelBoundingBox(x=1000, y=400, width=70, height=20),
            ),
            FakeOcrObservation(
                text="LEFT",
                bbox=PixelBoundingBox(x=180, y=400, width=60, height=20),
            ),
            FakeOcrObservation(
                text="TOP",
                bbox=PixelBoundingBox(x=100, y=60, width=50, height=20),
            ),
        )
        backend = _RecordingOcrBackend(observations)
        with tempfile.TemporaryDirectory() as temp_dir:
            result = extract_png(
                png_data,
                Path(temp_dir) / "landscape-bundle",
                decoder=PillowPngDecoder(),
                structure_extractor=OpenCvStructureExtractor(),
                ocr_backend=backend,
                asset_encoder=PillowPngAssetEncoder(),
                validator=JsonSchemaDocumentIRValidator(),
                bundle_writer=FilesystemDocumentBundleWriter(),
                languages=("eng",),
                ocr_options=OcrOptions(page_segmentation_mode=6),
            )

        self.assertEqual(backend.regions, ())
        self.assertEqual(backend.options.page_segmentation_mode, 3)
        self.assertIn(
            "ocr_landscape_column_profile_applied",
            {diagnostic.code for diagnostic in result.diagnostics},
        )
        texts = tuple(
            element.text
            for element in result.document.pages[0].elements
            if isinstance(element, TextElement)
        )
        self.assertEqual(texts, ("TOP", "LEFT", "RIGHT", "BOTTOM"))

    def _extract(
        self,
        output,
        *,
        structure_extractor=None,
        ocr_backend=None,
        asset_encoder=None,
        validator=None,
        ocr_options=None,
        ocr_region_grouping=OcrRegionGroupingConfig(),
        ocr_region_grouping_observer=None,
    ):
        return extract_png(
            self.png_data,
            output,
            decoder=PillowPngDecoder(),
            structure_extractor=structure_extractor or OpenCvStructureExtractor(),
            ocr_backend=ocr_backend or FakeOcrBackend(self.observations),
            asset_encoder=asset_encoder or PillowPngAssetEncoder(),
            validator=validator or JsonSchemaDocumentIRValidator(),
            bundle_writer=FilesystemDocumentBundleWriter(),
            languages=("eng",),
            ocr_options=ocr_options
            or OcrOptions(
                page_segmentation_mode=6,
                timeout_seconds=10,
                min_confidence=0.1,
            ),
            ocr_region_grouping=ocr_region_grouping,
            ocr_region_grouping_observer=ocr_region_grouping_observer,
        )


class TesseractPngExtractionIntegrationTest(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("AITEQNO_RUN_TESSERACT_INTEGRATION") == "1",
        "set AITEQNO_RUN_TESSERACT_INTEGRATION=1 with Tesseract installed",
    )
    def test_real_backend_builds_ir_with_text_structure_and_asset(self):
        png_data = base64.b64decode(
            (FIXTURE_ROOT / "structured-page.png.b64").read_text(encoding="ascii")
        )
        backend = TesseractOcrBackend(
            executable_path=os.environ.get("AITEQNO_TESSERACT_EXECUTABLE"),
            tessdata_prefix=os.environ.get("AITEQNO_TESSDATA_PREFIX"),
            required_languages=("eng",),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = extract_png(
                png_data,
                Path(temp_dir) / "bundle",
                decoder=PillowPngDecoder(),
                structure_extractor=OpenCvStructureExtractor(),
                ocr_backend=backend,
                asset_encoder=PillowPngAssetEncoder(),
                validator=JsonSchemaDocumentIRValidator(),
                bundle_writer=FilesystemDocumentBundleWriter(),
                languages=("eng",),
                ocr_options=OcrOptions(
                    page_segmentation_mode=6,
                    timeout_seconds=30,
                    min_confidence=0.1,
                ),
            )

            texts = tuple(
                element.text
                for element in result.document.pages[0].elements
                if isinstance(element, TextElement)
            )
            recognized = " ".join(texts)
            self.assertIn("PATIENT", recognized)
            self.assertIn("FORM", recognized)
            self.assertIn("2026", recognized)
            self.assertTrue(
                any(
                    isinstance(element, LineElement)
                    for element in result.document.pages[0].elements
                )
            )
            self.assertEqual(len(result.document.assets), 1)
            self.assertEqual(
                document_ir_from_file(result.bundle.document_path), result.document
            )
            for element in result.document.pages[0].elements:
                if isinstance(element, TextElement):
                    self.assertIsNotNone(element.confidence.recognition)
                    self.assertIn(
                        ProvenanceStage.OCR,
                        {record.stage for record in element.provenance},
                    )


class _StaticStructureExtractor:
    def __init__(self, result):
        self._result = result

    def detect(self, image):
        return self._result


class _RecordingOcrBackend:
    def __init__(self, observations):
        self._delegate = FakeOcrBackend(observations)
        self.regions = None
        self.options = None

    def recognize(self, image, regions=(), languages=("jpn", "eng"), options=OcrOptions()):
        self.regions = tuple(regions)
        self.options = options
        return self._delegate.recognize(
            image,
            regions=regions,
            languages=languages,
            options=options,
        )


class _FailingAssetEncoder:
    def encode_png_crop(self, image, bbox):
        raise AssetEncodingError("asset_test_failure", "simulated asset failure")


class _MustNotRunAssetEncoder:
    def encode_png_crop(self, image, bbox):
        raise AssertionError("page-covering image must be skipped before encoding")


class _RejectingValidator:
    def validate(self, document):
        raise DocumentIRValidationError.single(
            "$.pages[0]",
            "simulated schema rejection",
            "test_rejection",
        )


class _FailingOcrBackend:
    def healthcheck(self):
        raise AssertionError("healthcheck is not required by application orchestration")

    def recognize(self, image, regions=(), languages=("eng",), options=OcrOptions()):
        raise OcrBackendError(
            "ocr_test_failure",
            "simulated OCR failure",
            provider="test",
        )


if __name__ == "__main__":
    unittest.main()
