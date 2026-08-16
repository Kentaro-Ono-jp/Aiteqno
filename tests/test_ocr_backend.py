import base64
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import pytesseract

from aiteqno.adapters import (
    FAKE_OCR_PROVIDER,
    TESSERACT_PROVIDER,
    FakeOcrBackend,
    FakeOcrObservation,
    PillowPngDecoder,
    TesseractOcrBackend,
)
from aiteqno.domain import PageSource, PixelBoundingBox, ProvenanceStage
from aiteqno.ports import (
    ImageInput,
    OcrBackend,
    OcrBackendError,
    OcrOptions,
    OcrRegion,
    PixelMode,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "ocr"
FAKE_EXECUTABLE = str((Path.cwd() / ".fake-runtime" / "tesseract").resolve())


class OcrPortAndFakeBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = _blank_image()

    def test_backend_is_replaceable_and_fake_maps_observations_to_regions(self):
        backend = FakeOcrBackend(
            (
                FakeOcrObservation(
                    text="患者",
                    bbox=PixelBoundingBox(x=10, y=12, width=40, height=18),
                    confidence=0.94,
                ),
                FakeOcrObservation(
                    text="AITEQNO-2026",
                    bbox=PixelBoundingBox(x=120, y=12, width=70, height=18),
                    confidence=0.88,
                ),
            )
        )
        regions = (
            OcrRegion(
                region_ref="region-left",
                bbox=PixelBoundingBox(x=0, y=0, width=100, height=60),
            ),
            OcrRegion(
                region_ref="region-right",
                bbox=PixelBoundingBox(x=100, y=0, width=100, height=60),
            ),
        )

        first = _recognize_through_port(backend, self.image, regions)
        second = _recognize_through_port(backend, self.image, regions)

        self.assertEqual(first, second)
        self.assertEqual([token.text for token in first], ["患者", "AITEQNO-2026"])
        self.assertEqual(
            [token.parent_region_ref for token in first],
            ["region-left", "region-right"],
        )
        capabilities = backend.healthcheck()
        self.assertEqual(capabilities.provider, FAKE_OCR_PROVIDER)
        self.assertEqual(capabilities.available_languages, ("jpn", "eng"))
        for token in first:
            self.assertEqual(token.provider, FAKE_OCR_PROVIDER)
            self.assertEqual(token.model, "static-fixture")
            self.assertEqual(token.provenance[0].stage, ProvenanceStage.OCR)
            self.assertEqual(token.provenance[0].source_bbox_px, token.bbox)
            self.assertEqual(
                token.provenance[0].source_refs,
                (token.parent_region_ref,),
            )
            self.assertEqual(len(token.provenance[0].parameters_digest), 64)

    def test_fake_full_page_filter_and_missing_language_are_deterministic(self):
        backend = FakeOcrBackend(
            (
                FakeOcrObservation(
                    text="low",
                    bbox=PixelBoundingBox(x=5, y=5, width=10, height=10),
                    confidence=0.2,
                ),
                FakeOcrObservation(
                    text="keep",
                    bbox=PixelBoundingBox(x=20, y=5, width=20, height=10),
                    confidence=0.9,
                ),
            )
        )

        tokens = backend.recognize(
            self.image,
            options=OcrOptions(min_confidence=0.5),
        )

        self.assertEqual([token.text for token in tokens], ["keep"])
        self.assertIsNone(tokens[0].parent_region_ref)
        with self.assertRaises(OcrBackendError) as context:
            backend.recognize(self.image, languages=("deu",))
        self.assertEqual(context.exception.code, "ocr_language_missing")

    def test_port_rejects_unsafe_options_regions_and_languages(self):
        with self.assertRaises(ValueError):
            OcrOptions(page_segmentation_mode=14)
        with self.assertRaises(ValueError):
            OcrOptions(timeout_seconds=0)
        with self.assertRaises(ValueError):
            OcrOptions(min_confidence=1.1)

        backend = FakeOcrBackend(())
        duplicate = OcrRegion(
            region_ref="same",
            bbox=PixelBoundingBox(x=0, y=0, width=10, height=10),
        )
        with self.assertRaisesRegex(ValueError, "unique"):
            backend.recognize(self.image, regions=(duplicate, duplicate))
        with self.assertRaisesRegex(ValueError, "inside"):
            backend.recognize(
                self.image,
                regions=(
                    OcrRegion(
                        region_ref="outside",
                        bbox=PixelBoundingBox(x=195, y=95, width=10, height=10),
                    ),
                ),
            )
        with self.assertRaisesRegex(ValueError, "identifiers"):
            backend.recognize(self.image, languages=("eng;--psm",))


class TesseractOcrBackendUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = _blank_image()
        cls.region = OcrRegion(
            region_ref="text-region-1",
            bbox=PixelBoundingBox(x=20, y=30, width=150, height=50),
        )

    def test_healthcheck_reports_version_languages_and_restores_global_runtime(self):
        backend = TesseractOcrBackend(
            executable_path="test-tesseract",
            tessdata_prefix="configured-tessdata",
        )
        original_command = pytesseract.pytesseract.tesseract_cmd

        def version_probe():
            self.assertEqual(
                pytesseract.pytesseract.tesseract_cmd,
                FAKE_EXECUTABLE,
            )
            self.assertEqual(os.environ.get("TESSDATA_PREFIX"), "configured-tessdata")
            return "5.5.3"

        with patch.dict(os.environ, {"TESSDATA_PREFIX": "original-tessdata"}):
            with _runtime_patches(version=version_probe):
                capabilities = backend.healthcheck()
            self.assertEqual(os.environ["TESSDATA_PREFIX"], "original-tessdata")

        self.assertEqual(pytesseract.pytesseract.tesseract_cmd, original_command)
        self.assertEqual(capabilities.provider, TESSERACT_PROVIDER)
        self.assertEqual(capabilities.provider_version, "5.5.3")
        self.assertEqual(capabilities.executable, FAKE_EXECUTABLE)
        self.assertEqual(
            capabilities.available_languages,
            ("eng", "jpn", "osd"),
        )

    def test_healthcheck_diagnoses_missing_executable_version_and_language(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing-tesseract.exe"
            backend = TesseractOcrBackend(executable_path=missing)
            with self.assertRaises(OcrBackendError) as executable_context:
                backend.healthcheck()
        self.assertEqual(
            executable_context.exception.code,
            "ocr_executable_missing",
        )

        backend = TesseractOcrBackend(executable_path="test-tesseract")
        with _runtime_patches(version="4.1.3"):
            with self.assertRaises(OcrBackendError) as version_context:
                backend.healthcheck()
        self.assertEqual(version_context.exception.code, "ocr_unsupported_version")

        with _runtime_patches(languages=("eng", "osd")):
            with self.assertRaises(OcrBackendError) as language_context:
                backend.healthcheck()
        self.assertEqual(language_context.exception.code, "ocr_language_missing")
        self.assertIn("jpn", str(language_context.exception))
        self.assertIn("TESSDATA_PREFIX", str(language_context.exception))

    def test_recognize_normalizes_text_bbox_confidence_and_provenance(self):
        response = {
            "text": ["", " ＡＩＴＥＱＮＯ　2026 ", "患者", "ignored"],
            "conf": ["-1", "95.5", "87", "-1"],
            "left": [0, 5, 100, 1],
            "top": [0, 4, 6, 1],
            "width": [0, 80, 60, 10],
            "height": [0, 20, 20, 10],
        }
        backend = TesseractOcrBackend(executable_path="test-tesseract")

        with _runtime_patches(response=response):
            tokens = backend.recognize(
                self.image,
                regions=(self.region,),
                options=OcrOptions(
                    page_segmentation_mode=6,
                    engine_mode=3,
                    timeout_seconds=12,
                    min_confidence=0.5,
                    preserve_interword_spaces=True,
                ),
            )

        self.assertEqual([token.text for token in tokens], ["AITEQNO 2026", "患者"])
        self.assertEqual(tokens[0].confidence, 0.955)
        self.assertEqual(
            tokens[0].bbox,
            PixelBoundingBox(x=25, y=34, width=80, height=20),
        )
        self.assertEqual(
            tokens[1].bbox,
            PixelBoundingBox(x=120, y=36, width=50, height=20),
        )
        for token in tokens:
            self.assertEqual(token.parent_region_ref, "text-region-1")
            self.assertEqual(token.provider, TESSERACT_PROVIDER)
            self.assertEqual(token.provider_version, "5.5.3")
            self.assertEqual(token.model, "tessdata:jpn+eng")
            self.assertEqual(token.languages, ("jpn", "eng"))
            provenance = token.provenance[0]
            self.assertEqual(provenance.stage, ProvenanceStage.OCR)
            self.assertEqual(provenance.source_refs, ("text-region-1",))
            self.assertEqual(provenance.source_bbox_px, token.bbox)
            self.assertNotIn(token.text, provenance.notes)

    def test_timeout_engine_failure_and_invalid_response_have_stable_codes(self):
        backend = TesseractOcrBackend(executable_path="test-tesseract")
        cases = (
            (RuntimeError("Tesseract process timeout"), "ocr_timeout"),
            (pytesseract.TesseractError(1, "engine failed"), "ocr_engine_failure"),
        )
        for error, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with _runtime_patches(response_error=error):
                    with self.assertRaises(OcrBackendError) as context:
                        backend.recognize(self.image)
                self.assertEqual(context.exception.code, expected_code)

        with _runtime_patches(response={"text": ["broken"]}):
            with self.assertRaises(OcrBackendError) as response_context:
                backend.recognize(self.image)
        self.assertEqual(response_context.exception.code, "ocr_invalid_response")


class TesseractOcrBackendIntegrationTest(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("AITEQNO_RUN_TESSERACT_INTEGRATION") == "1",
        "set AITEQNO_RUN_TESSERACT_INTEGRATION=1 with Tesseract jpn+eng installed",
    )
    def test_real_tesseract_reads_japanese_and_alphanumeric_fixture(self):
        expected = json.loads(
            (FIXTURE_ROOT / "jpn-eng.expected.json").read_text(encoding="utf-8")
        )
        png_data = base64.b64decode(
            (FIXTURE_ROOT / "jpn-eng.png.b64").read_text(encoding="ascii")
        )
        image = PillowPngDecoder().decode(png_data)
        backend = TesseractOcrBackend(
            executable_path=os.environ.get("AITEQNO_TESSERACT_EXECUTABLE"),
            tessdata_prefix=os.environ.get("AITEQNO_TESSDATA_PREFIX"),
            required_languages=tuple(expected["languages"]),
        )

        capabilities = backend.healthcheck()
        tokens = backend.recognize(
            image,
            languages=tuple(expected["languages"]),
            options=OcrOptions(
                page_segmentation_mode=6,
                timeout_seconds=30,
                min_confidence=0.1,
            ),
        )

        self.assertGreaterEqual(int(capabilities.provider_version.split(".")[0]), 5)
        self.assertEqual(image.source.pixel_width, expected["pixel_width"])
        self.assertEqual(image.source.pixel_height, expected["pixel_height"])
        self.assertAlmostEqual(image.source.dpi_x, expected["dpi"], delta=0.05)
        recognized = "".join(token.text.replace(" ", "") for token in tokens)
        for fragment in expected["must_contain"]:
            self.assertIn(fragment, recognized)
        self.assertTrue(
            any(
                fragment in recognized
                for fragment in expected["must_contain_any_japanese"]
            ),
            recognized,
        )
        for token in tokens:
            self.assertIsNotNone(token.confidence)
            self.assertGreaterEqual(token.confidence, 0.0)
            self.assertLessEqual(token.confidence, 1.0)
            self.assertLessEqual(
                token.bbox.x + token.bbox.width,
                image.source.pixel_width,
            )
            self.assertLessEqual(
                token.bbox.y + token.bbox.height,
                image.source.pixel_height,
            )
            self.assertEqual(token.provenance[0].source_bbox_px, token.bbox)


def _blank_image():
    return ImageInput(
        source=PageSource(
            pixel_width=200,
            pixel_height=100,
            dpi_x=96,
            dpi_y=96,
            dpi_source="inferred",
        ),
        mode=PixelMode.RGB8,
        pixels=bytes((255, 255, 255)) * (200 * 100),
        source_sha256="0" * 64,
    )


def _recognize_through_port(backend, image, regions):
    typed_backend: OcrBackend = backend
    return typed_backend.recognize(
        image,
        regions=regions,
        languages=("jpn", "eng"),
        options=OcrOptions(timeout_seconds=1),
    )


@contextmanager
def _runtime_patches(
    *,
    version="5.5.3",
    languages=("eng", "jpn", "osd"),
    response=None,
    response_error=None,
):
    if response is None:
        response = {
            "text": [],
            "conf": [],
            "left": [],
            "top": [],
            "width": [],
            "height": [],
        }
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "aiteqno.adapters.tesseract.shutil.which",
                return_value=FAKE_EXECUTABLE,
            )
        )
        stack.enter_context(
            patch(
                "aiteqno.adapters.tesseract.pytesseract.get_tesseract_version",
                side_effect=version if callable(version) else None,
                return_value=None if callable(version) else version,
            )
        )
        stack.enter_context(
            patch(
                "aiteqno.adapters.tesseract.pytesseract.get_languages",
                return_value=list(languages),
            )
        )
        image_to_data = stack.enter_context(
            patch(
                "aiteqno.adapters.tesseract.pytesseract.image_to_data",
                return_value=response,
            )
        )
        if response_error is not None:
            image_to_data.side_effect = response_error
        yield image_to_data


if __name__ == "__main__":
    unittest.main()
