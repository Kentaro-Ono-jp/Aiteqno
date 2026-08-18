import base64
import hashlib
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import FrozenInstanceError
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
    DEFAULT_OCR_LANGUAGES,
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
        self.assertEqual(DEFAULT_OCR_LANGUAGES, ("jpn",))
        self.assertEqual(capabilities.available_languages, ("jpn", "eng"))
        self.assertEqual(capabilities.default_languages, ("jpn",))
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

    def test_transform_configuration_rejects_unsafe_values(self):
        for target_dpi in (True, 0, -1, 300.0):
            with self.subTest(target_dpi=target_dpi):
                with self.assertRaises(ValueError):
                    TesseractOcrBackend(target_dpi=target_dpi)
        for max_working_pixels in (True, 0, -1, 40_000_000.0):
            with self.subTest(max_working_pixels=max_working_pixels):
                with self.assertRaises(ValueError):
                    TesseractOcrBackend(
                        max_working_pixels=max_working_pixels,
                    )
        for region_padding_px in (True, -1, 2.0):
            with self.subTest(region_padding_px=region_padding_px):
                with self.assertRaises(ValueError):
                    TesseractOcrBackend(region_padding_px=region_padding_px)
        with self.assertRaises(ValueError):
            TesseractOcrBackend(target_dpi=300, region_padding_px=2)
        with self.assertRaises(TypeError):
            TesseractOcrBackend(transform_observer="not-callable")
        with self.assertRaises(TypeError):
            TesseractOcrBackend(padding_observer="not-callable")
        with self.assertRaises(TypeError):
            TesseractOcrBackend(invocation_observer="not-callable")

    def test_invocation_evidence_hashes_only_the_models_actually_passed(self):
        response = {
            "text": ["患者"],
            "conf": ["95"],
            "left": [2],
            "top": [3],
            "width": [20],
            "height": [10],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            tessdata = Path(temp_dir)
            jpn_data = b"candidate-japanese-traineddata"
            eng_data = b"control-english-traineddata"
            (tessdata / "jpn.traineddata").write_bytes(jpn_data)
            (tessdata / "eng.traineddata").write_bytes(eng_data)
            invocations = []
            backend = TesseractOcrBackend(
                executable_path="test-tesseract",
                tessdata_prefix=tessdata,
                required_languages=("jpn",),
                invocation_observer=invocations.append,
            )

            with _runtime_patches(response=response):
                tokens = backend.recognize(
                    self.image,
                    regions=(self.region,),
                    languages=("jpn",),
                    options=OcrOptions(page_segmentation_mode=6, engine_mode=3),
                )

        self.assertEqual(len(invocations), 1)
        evidence = invocations[0]
        rendered = evidence.to_dict()
        self.assertEqual(rendered["configuration"]["languages"], ["jpn"])
        self.assertEqual(
            [item["language"] for item in rendered["traineddata"]],
            ["jpn"],
        )
        self.assertEqual(rendered["traineddata"][0]["size_bytes"], len(jpn_data))
        self.assertEqual(
            rendered["traineddata"][0]["sha256"],
            hashlib.sha256(jpn_data).hexdigest(),
        )
        self.assertNotIn("eng.traineddata", json.dumps(rendered))
        self.assertEqual(
            tokens[0].provenance[0].parameters_digest,
            rendered["parameters_digest"],
        )
        self.assertEqual(rendered["configuration"]["region_padding_px"], 2)
        self.assertEqual(rendered["crops"][0]["padding_pixels"], 2)

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
        backend = TesseractOcrBackend(
            executable_path="test-tesseract",
            target_dpi=None,
            region_padding_px=0,
        )

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
            self.assertEqual(token.model, "tessdata:jpn")
            self.assertEqual(token.languages, ("jpn",))
            provenance = token.provenance[0]
            self.assertEqual(provenance.stage, ProvenanceStage.OCR)
            self.assertEqual(provenance.source_refs, ("text-region-1",))
            self.assertEqual(provenance.source_bbox_px, token.bbox)
            self.assertNotIn(token.text, provenance.notes)

    def test_two_pixel_white_region_padding_restores_exact_source_coordinates(self):
        response = {
            "text": ["full", "inner", "padding-only", "edge"],
            "conf": ["99"] * 4,
            "left": [0, 7, 0, 1],
            "top": [0, 6, 0, 1],
            "width": [154, 80, 2, 3],
            "height": [54, 20, 2, 3],
        }
        observed = []
        captured = {}

        def inspect_raster(image, **_kwargs):
            captured["mode"] = image.mode
            captured["size"] = image.size
            captured["corners"] = (
                image.getpixel((0, 0)),
                image.getpixel((153, 53)),
            )
            captured["inside"] = image.getpixel((2, 2))
            return response

        backend = TesseractOcrBackend(
            executable_path="test-tesseract",
            target_dpi=None,
            region_padding_px=2,
            padding_observer=observed.append,
        )
        black_image = ImageInput(
            source=self.image.source,
            mode=PixelMode.RGB8,
            pixels=bytes((0, 0, 0))
            * (self.image.source.pixel_width * self.image.source.pixel_height),
            source_sha256="b" * 64,
        )

        with _runtime_patches(response_error=inspect_raster):
            tokens = backend.recognize(black_image, regions=(self.region,))

        self.assertEqual(captured["mode"], "RGB")
        self.assertEqual(captured["size"], (154, 54))
        self.assertEqual(captured["corners"], ((255, 255, 255),) * 2)
        self.assertEqual(captured["inside"], (0, 0, 0))
        self.assertEqual(
            [(token.text, token.bbox) for token in tokens],
            [
                (
                    "full",
                    PixelBoundingBox(x=20, y=30, width=150, height=50),
                ),
                (
                    "inner",
                    PixelBoundingBox(x=25, y=34, width=80, height=20),
                ),
                ("edge", PixelBoundingBox(x=20, y=30, width=2, height=2)),
            ],
        )
        self.assertNotIn("padding-only", [token.text for token in tokens])
        for token in tokens:
            self.assertEqual(token.parent_region_ref, "text-region-1")
            self.assertEqual(token.bbox, token.provenance[0].source_bbox_px)
            self.assertIn("region_padding_px=2", token.provenance[0].notes)
        self.assertEqual(len(observed), 1)
        evidence = observed[0]
        self.assertTrue(evidence.enabled)
        self.assertEqual(evidence.configured_padding_pixels, 2)
        self.assertEqual(evidence.border_color, (255, 255, 255))
        self.assertEqual(evidence.scope, "region-crops-only")
        crop = evidence.crops[0]
        self.assertEqual(crop.source_bbox, self.region.bbox)
        self.assertEqual((crop.source_width, crop.source_height), (150, 50))
        self.assertEqual((crop.pre_padding_width, crop.pre_padding_height), (150, 50))
        self.assertEqual((crop.working_width, crop.working_height), (154, 54))
        self.assertEqual(crop.padding_pixels, 2)
        self.assertTrue(crop.applied)
        self.assertEqual(len(crop.working_raster_sha256), 64)
        with self.assertRaises(FrozenInstanceError):
            evidence.configured_padding_pixels = 0

    def test_region_padding_is_noop_for_full_page_and_changes_region_digest(self):
        def run(padding=None, *, regions=(), omit_padding=False):
            configured_padding = 2 if omit_padding else padding
            applied_padding = configured_padding if regions else 0
            response = {
                "text": ["same"],
                "conf": ["99"],
                "left": [10 + applied_padding],
                "top": [11 + applied_padding],
                "width": [20],
                "height": [13],
            }
            observed = []
            padding_arguments = (
                {} if omit_padding else {"region_padding_px": configured_padding}
            )
            backend = TesseractOcrBackend(
                executable_path="test-tesseract",
                target_dpi=None,
                padding_observer=observed.append,
                **padding_arguments,
            )
            with _runtime_patches(response=response) as image_to_data:
                token = backend.recognize(self.image, regions=regions)[0]
            return token, observed[0], image_to_data.call_args.args[0].size

        control, control_evidence, _ = run(0, regions=(self.region,))
        candidate, candidate_evidence, _ = run(2, regions=(self.region,))
        default, default_evidence, _ = run(
            regions=(self.region,),
            omit_padding=True,
        )
        full_page, full_page_evidence, full_page_size = run(2)

        self.assertEqual(control.bbox, candidate.bbox)
        self.assertNotEqual(
            control.provenance[0].parameters_digest,
            candidate.provenance[0].parameters_digest,
        )
        self.assertFalse(control_evidence.enabled)
        self.assertEqual(control_evidence.crops[0].padding_pixels, 0)
        self.assertEqual(candidate_evidence.crops[0].padding_pixels, 2)
        self.assertEqual(default, candidate)
        self.assertEqual(default_evidence, candidate_evidence)
        self.assertEqual(full_page_size, (200, 100))
        self.assertEqual(
            full_page.bbox,
            PixelBoundingBox(x=10, y=11, width=20, height=13),
        )
        self.assertTrue(full_page_evidence.enabled)
        self.assertIsNone(full_page_evidence.crops[0].region_ref)
        self.assertFalse(full_page_evidence.crops[0].applied)
        self.assertEqual(full_page_evidence.crops[0].padding_pixels, 0)

    def test_explicit_300_dpi_transform_scales_page_and_reports_evidence(
        self,
    ):
        observed = []
        response = {
            "text": ["境界"],
            "conf": ["99"],
            "left": [31],
            "top": [32],
            "width": [4],
            "height": [4],
        }
        backend = TesseractOcrBackend(
            executable_path="test-tesseract",
            target_dpi=300,
            region_padding_px=0,
            transform_observer=observed.append,
        )

        with _runtime_patches(response=response) as image_to_data:
            tokens = backend.recognize(self.image)

        working_image = image_to_data.call_args.args[0]
        self.assertEqual(working_image.mode, "RGB")
        self.assertEqual(working_image.size, (625, 313))
        self.assertIn("--dpi 300", image_to_data.call_args.kwargs["config"])
        self.assertEqual(
            tokens[0].bbox,
            PixelBoundingBox(x=9, y=10, width=3, height=2),
        )
        self.assertEqual(tokens[0].bbox, tokens[0].provenance[0].source_bbox_px)
        self.assertEqual(len(observed), 1)
        evidence = observed[0]
        self.assertTrue(evidence.enabled)
        self.assertEqual(evidence.target_dpi, 300)
        self.assertEqual(evidence.source_effective_dpi, 96.0)
        self.assertEqual(evidence.effective_ocr_dpi, 300)
        self.assertEqual(evidence.resampling, "LANCZOS")
        self.assertEqual(evidence.pixel_mode, "RGB")
        self.assertEqual(len(evidence.crops), 1)
        crop = evidence.crops[0]
        self.assertIsNone(crop.region_ref)
        self.assertEqual((crop.source_width, crop.source_height), (200, 100))
        self.assertEqual((crop.working_width, crop.working_height), (625, 313))
        self.assertEqual(crop.actual_scale_x, 3.125)
        self.assertEqual(crop.actual_scale_y, 3.13)
        self.assertTrue(crop.resized)
        self.assertEqual(len(crop.working_raster_sha256), 64)
        self.assertEqual(
            evidence.to_dict()["crops"][0]["working_dimensions"],
            {"width": 625, "height": 313},
        )
        with self.assertRaises(FrozenInstanceError):
            evidence.target_dpi = 96

    def test_crop_inverse_mapping_uses_integer_dimensions_floor_ceil_and_clamp(
        self,
    ):
        region = OcrRegion(
            region_ref="odd-region",
            bbox=PixelBoundingBox(x=21, y=31, width=149, height=49),
        )
        response = {
            "text": ["full", "odd", "edge", "clipped"],
            "conf": ["99"] * 4,
            "left": [0, 5, 465, -2],
            "top": [0, 4, 152, -3],
            "width": [466, 80, 10, 3],
            "height": [153, 20, 10, 4],
        }
        observed = []
        backend = TesseractOcrBackend(
            executable_path="test-tesseract",
            target_dpi=300,
            region_padding_px=0,
            transform_observer=observed.append,
        )

        with _runtime_patches(response=response) as image_to_data:
            tokens = backend.recognize(self.image, regions=(region,))

        self.assertEqual(image_to_data.call_args.args[0].size, (466, 153))
        self.assertEqual(
            [token.bbox for token in tokens],
            [
                PixelBoundingBox(x=21, y=31, width=149, height=49),
                PixelBoundingBox(x=22, y=32, width=27, height=7),
                PixelBoundingBox(x=169, y=79, width=1, height=1),
                PixelBoundingBox(x=21, y=31, width=1, height=1),
            ],
        )
        for token in tokens:
            self.assertEqual(token.parent_region_ref, "odd-region")
            self.assertEqual(token.bbox, token.provenance[0].source_bbox_px)
            self.assertLessEqual(token.bbox.x + token.bbox.width, 200)
            self.assertLessEqual(token.bbox.y + token.bbox.height, 100)
        crop = observed[0].crops[0]
        self.assertEqual(crop.region_ref, "odd-region")
        self.assertEqual(crop.source_bbox, region.bbox)
        self.assertEqual((crop.working_width, crop.working_height), (466, 153))

    def test_300_dpi_or_higher_source_is_not_downscaled(self):
        image = _blank_image(width=7, height=5, dpi=400)
        observed = []
        backend = TesseractOcrBackend(
            executable_path="test-tesseract",
            target_dpi=300,
            region_padding_px=0,
            transform_observer=observed.append,
        )

        with _runtime_patches() as image_to_data:
            backend.recognize(image)

        self.assertEqual(image_to_data.call_args.args[0].size, (7, 5))
        self.assertIn("--dpi 400", image_to_data.call_args.kwargs["config"])
        evidence = observed[0]
        self.assertEqual(evidence.target_dpi, 300)
        self.assertEqual(evidence.effective_ocr_dpi, 400)
        self.assertFalse(evidence.crops[0].resized)
        self.assertEqual(evidence.crops[0].actual_scale_x, 1.0)
        self.assertEqual(evidence.crops[0].actual_scale_y, 1.0)

    def test_default_transform_is_disabled_and_has_distinct_candidate_digest(self):
        response = {
            "text": ["same"],
            "conf": ["99"],
            "left": [10],
            "top": [11],
            "width": [20],
            "height": [13],
        }

        def run(target_dpi=None, *, omit_target_dpi=False):
            observed = []
            if omit_target_dpi:
                target_arguments = {}
            elif target_dpi is None:
                target_arguments = {"target_dpi": None}
            else:
                target_arguments = {
                    "target_dpi": target_dpi,
                    "region_padding_px": 0,
                }
            backend = TesseractOcrBackend(
                executable_path="test-tesseract",
                transform_observer=observed.append,
                **target_arguments,
            )
            with _runtime_patches(response=response) as image_to_data:
                token = backend.recognize(self.image)[0]
            return token, observed[0], image_to_data.call_args

        default, default_evidence, default_call = run(omit_target_dpi=True)
        control, control_evidence, control_call = run(None)
        repeated, repeated_evidence, _ = run(None)
        candidate, candidate_evidence, _ = run(300)

        self.assertEqual(default, control)
        self.assertEqual(default_evidence, control_evidence)
        self.assertEqual(default_call.args[0].size, (200, 100))
        self.assertEqual(
            control.bbox,
            PixelBoundingBox(x=10, y=11, width=20, height=13),
        )
        self.assertEqual(control, repeated)
        self.assertEqual(control_evidence, repeated_evidence)
        self.assertFalse(control_evidence.enabled)
        self.assertIsNone(control_evidence.target_dpi)
        self.assertEqual(control_evidence.effective_ocr_dpi, 96)
        self.assertEqual(control_evidence.resampling, "none")
        self.assertEqual(control_call.args[0].size, (200, 100))
        self.assertIn("--dpi 96", control_call.kwargs["config"])
        self.assertNotEqual(
            control.provenance[0].parameters_digest,
            candidate.provenance[0].parameters_digest,
        )
        self.assertNotEqual(control_evidence, candidate_evidence)

    def test_working_pixel_limit_is_stable_and_closes_region_crop(self):
        backend = TesseractOcrBackend(
            executable_path="test-tesseract",
            target_dpi=300,
            region_padding_px=0,
            max_working_pixels=100,
        )
        captured_crops = []

        from aiteqno.adapters import tesseract as tesseract_adapter

        target_image = tesseract_adapter._target_image

        def capture_target(*args, **kwargs):
            result = target_image(*args, **kwargs)
            captured_crops.append(result[0])
            return result

        with _runtime_patches() as image_to_data:
            with patch(
                "aiteqno.adapters.tesseract._target_image",
                side_effect=capture_target,
            ):
                with self.assertRaises(OcrBackendError) as context:
                    backend.recognize(self.image, regions=(self.region,))

        self.assertEqual(context.exception.code, "ocr_working_raster_limit")
        image_to_data.assert_not_called()
        self.assertEqual(len(captured_crops), 1)
        with self.assertRaises(ValueError):
            captured_crops[0].load()

    def test_tesseract_failure_closes_source_crop_and_resized_working_image(self):
        backend = TesseractOcrBackend(
            executable_path="test-tesseract",
            target_dpi=300,
            region_padding_px=0,
        )
        captured_crops = []
        captured_working = []

        from aiteqno.adapters import tesseract as tesseract_adapter

        target_image = tesseract_adapter._target_image

        def capture_target(*args, **kwargs):
            result = target_image(*args, **kwargs)
            captured_crops.append(result[0])
            return result

        def fail_tesseract(image, **_kwargs):
            captured_working.append(image)
            raise pytesseract.TesseractError(1, "engine failed")

        with _runtime_patches(response_error=fail_tesseract):
            with patch(
                "aiteqno.adapters.tesseract._target_image",
                side_effect=capture_target,
            ):
                with self.assertRaises(OcrBackendError) as context:
                    backend.recognize(self.image, regions=(self.region,))

        self.assertEqual(context.exception.code, "ocr_engine_failure")
        self.assertEqual(len(captured_crops), 1)
        self.assertEqual(len(captured_working), 1)
        self.assertIsNot(captured_crops[0], captured_working[0])
        for raster in (*captured_crops, *captured_working):
            with self.assertRaises(ValueError):
                raster.load()

    def test_tesseract_failure_closes_source_crop_and_padded_working_image(self):
        backend = TesseractOcrBackend(
            executable_path="test-tesseract",
            target_dpi=None,
            region_padding_px=2,
        )
        captured_crops = []
        captured_working = []

        from aiteqno.adapters import tesseract as tesseract_adapter

        target_image = tesseract_adapter._target_image

        def capture_target(*args, **kwargs):
            result = target_image(*args, **kwargs)
            captured_crops.append(result[0])
            return result

        def fail_tesseract(image, **_kwargs):
            captured_working.append(image)
            raise pytesseract.TesseractError(1, "engine failed")

        with _runtime_patches(response_error=fail_tesseract):
            with patch(
                "aiteqno.adapters.tesseract._target_image",
                side_effect=capture_target,
            ):
                with self.assertRaises(OcrBackendError) as context:
                    backend.recognize(self.image, regions=(self.region,))

        self.assertEqual(context.exception.code, "ocr_engine_failure")
        self.assertEqual(len(captured_crops), 1)
        self.assertEqual(len(captured_working), 1)
        self.assertIsNot(captured_crops[0], captured_working[0])
        for raster in (*captured_crops, *captured_working):
            with self.assertRaises(ValueError):
                raster.load()

    def test_resize_resource_failure_has_stable_code_and_closes_source_crop(self):
        backend = TesseractOcrBackend(
            executable_path="test-tesseract",
            target_dpi=300,
            region_padding_px=0,
        )
        captured_crops = []

        from aiteqno.adapters import tesseract as tesseract_adapter

        target_image = tesseract_adapter._target_image

        def capture_target(*args, **kwargs):
            result = target_image(*args, **kwargs)
            captured_crops.append(result[0])
            return result

        with _runtime_patches() as image_to_data:
            with patch(
                "aiteqno.adapters.tesseract._target_image",
                side_effect=capture_target,
            ):
                with patch(
                    "aiteqno.adapters.tesseract.Image.Image.resize",
                    side_effect=MemoryError("allocation failed"),
                ):
                    with self.assertRaises(OcrBackendError) as context:
                        backend.recognize(self.image, regions=(self.region,))

        self.assertEqual(context.exception.code, "ocr_working_raster_failure")
        image_to_data.assert_not_called()
        self.assertEqual(len(captured_crops), 1)
        with self.assertRaises(ValueError):
            captured_crops[0].load()

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

    @unittest.skipUnless(
        os.environ.get("AITEQNO_RUN_TESSERACT_INTEGRATION") == "1",
        "set AITEQNO_RUN_TESSERACT_INTEGRATION=1 with Tesseract jpn installed",
    )
    def test_real_tesseract_jpn_only_retains_mixed_language_smoke_literals(self):
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
            required_languages=("jpn",),
            target_dpi=None,
            region_padding_px=2,
        )

        tokens = backend.recognize(
            image,
            languages=("jpn",),
            options=OcrOptions(page_segmentation_mode=6, engine_mode=3),
        )

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
        self.assertTrue(tokens)
        self.assertTrue(all(token.languages == ("jpn",) for token in tokens))
        self.assertTrue(all(token.model == "tessdata:jpn" for token in tokens))


def _blank_image(*, width=200, height=100, dpi=96):
    return ImageInput(
        source=PageSource(
            pixel_width=width,
            pixel_height=height,
            dpi_x=dpi,
            dpi_y=dpi,
            dpi_source="inferred",
        ),
        mode=PixelMode.RGB8,
        pixels=bytes((255, 255, 255)) * (width * height),
        source_sha256="0" * 64,
    )


def _recognize_through_port(backend, image, regions):
    typed_backend: OcrBackend = backend
    return typed_backend.recognize(
        image,
        regions=regions,
        languages=DEFAULT_OCR_LANGUAGES,
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
