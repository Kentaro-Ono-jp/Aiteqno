import base64
import hashlib
import json
import unittest
from dataclasses import FrozenInstanceError
from io import BytesIO
from pathlib import Path

from PIL import Image

from aiteqno.adapters import (
    STRUCTURE_PROVIDER,
    STRUCTURE_PROVIDER_VERSION,
    OpenCvStructureExtractor,
    PillowPngDecoder,
)
from aiteqno.domain import DpiSource, PixelBoundingBox, ProvenanceStage
from aiteqno.ports import (
    LineOrientation,
    PixelMode,
    RegionKind,
    StructureExtractionError,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "structure"
QUESTIONNAIRE_FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "generalization"
    / "japanese-questionnaires-v1"
)


class StructureExtractorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.png_data = base64.b64decode(
            (FIXTURE_ROOT / "structured-page.png.b64").read_text(encoding="ascii")
        )
        cls.expected = json.loads(
            (FIXTURE_ROOT / "structured-page.expected.json").read_text(
                encoding="utf-8"
            )
        )
        cls.image = PillowPngDecoder().decode(cls.png_data)
        cls.extractor = OpenCvStructureExtractor()
        cls.result = cls.extractor.detect(cls.image)

    def test_png_decoder_preserves_dimensions_declared_dpi_and_immutable_rgb(self):
        source = self.image.source

        self.assertEqual(source.pixel_width, self.expected["pixel_width"])
        self.assertEqual(source.pixel_height, self.expected["pixel_height"])
        self.assertAlmostEqual(source.dpi_x, self.expected["dpi"], delta=0.02)
        self.assertAlmostEqual(source.dpi_y, self.expected["dpi"], delta=0.02)
        self.assertIs(source.dpi_source, DpiSource.DECLARED)
        self.assertIs(self.image.mode, PixelMode.RGB8)
        self.assertEqual(
            len(self.image.pixels),
            source.pixel_width * source.pixel_height * 3,
        )
        self.assertEqual(
            self.image.source_sha256,
            hashlib.sha256(self.png_data).hexdigest(),
        )
        with self.assertRaises(FrozenInstanceError):
            self.image.pixels = b""

    def test_page_and_major_structure_are_detected_in_source_pixels(self):
        result = self.result

        self.assertEqual(result.page.source, self.image.source)
        self.assertEqual(
            result.page.bbox,
            PixelBoundingBox(
                x=0,
                y=0,
                width=self.expected["pixel_width"],
                height=self.expected["pixel_height"],
            ),
        )
        self.assertEqual(len(result.lines), 6)
        self.assertGreaterEqual(len(result.rectangles), 5)
        self.assertGreaterEqual(len(result.text_regions), 3)
        self.assertEqual(len(result.image_regions), 1)

        for expected in self.expected["horizontal_lines"]:
            self.assertTrue(
                any(
                    line.orientation is LineOrientation.HORIZONTAL
                    and abs(line.start.y - expected["y"]) <= 3
                    and line.start.x <= expected["x1"] + 3
                    and line.end.x >= expected["x2"] - 3
                    for line in result.lines
                ),
                expected,
            )
        for expected in self.expected["vertical_lines"]:
            self.assertTrue(
                any(
                    line.orientation is LineOrientation.VERTICAL
                    and abs(line.start.x - expected["x"]) <= 3
                    and line.start.y <= expected["y1"] + 3
                    and line.end.y >= expected["y2"] - 3
                    for line in result.lines
                ),
                expected,
            )

        expected_outer = _bbox_from_dict(self.expected["outer_rectangle"])
        self.assertTrue(
            any(_bbox_near(rectangle.bbox, expected_outer, tolerance=3) for rectangle in result.rectangles)
        )
        for anchor_data in self.expected["text_anchors"]:
            anchor = _bbox_from_dict(anchor_data)
            self.assertTrue(
                any(_coverage(region.bbox, anchor) >= 0.55 for region in result.text_regions),
                anchor,
            )
        expected_image = _bbox_from_dict(self.expected["image_region"])
        self.assertTrue(
            any(_bbox_near(region.bbox, expected_image, tolerance=2) for region in result.image_regions)
        )

    def test_candidates_have_confidence_and_structure_provenance_without_ids(self):
        candidates = (
            self.result.page,
            *self.result.lines,
            *self.result.rectangles,
            *self.result.text_regions,
            *self.result.image_regions,
        )

        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assertGreaterEqual(candidate.confidence.overall, 0.0)
                self.assertLessEqual(candidate.confidence.overall, 1.0)
                self.assertIsNotNone(candidate.confidence.detection)
                self.assertFalse(hasattr(candidate, "id"))
                self.assertEqual(len(candidate.provenance), 1)
                provenance = candidate.provenance[0]
                self.assertIs(provenance.stage, ProvenanceStage.STRUCTURE)
                self.assertEqual(provenance.provider, STRUCTURE_PROVIDER)
                self.assertEqual(
                    provenance.provider_version,
                    STRUCTURE_PROVIDER_VERSION,
                )
                self.assertEqual(provenance.source_bbox_px, candidate.bbox)
                self.assertEqual(len(provenance.parameters_digest), 64)

        self.assertTrue(
            all(region.kind is RegionKind.TEXT for region in self.result.text_regions)
        )
        self.assertTrue(
            all(region.kind is RegionKind.IMAGE for region in self.result.image_regions)
        )

    def test_results_are_deterministic_normalized_and_inside_the_page(self):
        repeated = self.extractor.detect(self.image)
        self.assertEqual(self.result, repeated)

        source = self.result.page.source
        collections = (
            self.result.lines,
            self.result.rectangles,
            self.result.text_regions,
            self.result.image_regions,
        )
        for candidates in collections:
            keys = [
                (item.bbox.x, item.bbox.y, item.bbox.width, item.bbox.height)
                for item in candidates
            ]
            self.assertEqual(len(keys), len(set(keys)))
            for item in candidates:
                self.assertLessEqual(
                    item.bbox.x + item.bbox.width,
                    source.pixel_width,
                )
                self.assertLessEqual(
                    item.bbox.y + item.bbox.height,
                    source.pixel_height,
                )

        self.assertTrue(all(region.bbox.width >= 4 for region in self.result.text_regions))
        self.assertTrue(all(region.bbox.height >= 4 for region in self.result.text_regions))

    def test_landscape_detail_profile_recovers_diagram_and_response_outlines(self):
        image = PillowPngDecoder().decode(
            (QUESTIONNAIRE_FIXTURE_ROOT / "questionnaire-04-orthopedics.png").read_bytes()
        )
        result = OpenCvStructureExtractor().detect(image)

        diagram_lines = tuple(
            line
            for line in result.lines
            if "diagram stroke" in (line.provenance[0].notes or "")
        )
        circular_outlines = tuple(
            rectangle
            for rectangle in result.rectangles
            if "circular closed-outline" in (rectangle.provenance[0].notes or "")
        )
        filled_bands = tuple(
            rectangle
            for rectangle in result.rectangles
            if "filled horizontal section-band"
            in (rectangle.provenance[0].notes or "")
        )

        self.assertEqual(len(diagram_lines), 12)
        self.assertEqual(
            sum(
                line.orientation is LineOrientation.DIAGONAL
                for line in diagram_lines
            ),
            8,
        )
        self.assertEqual(len(circular_outlines), 5)
        self.assertEqual(len(filled_bands), 1)
        self.assertEqual(
            (
                filled_bands[0].bbox.x,
                filled_bands[0].bbox.y,
                filled_bands[0].bbox.width,
                filled_bands[0].bbox.height,
            ),
            (67, 286, 1621, 38),
        )

    def test_decoder_infers_dpi_and_composites_transparency_on_white(self):
        transparent = Image.new("RGBA", (2, 1), (255, 0, 0, 0))
        transparent.putpixel((1, 0), (0, 0, 0, 255))
        encoded = BytesIO()
        transparent.save(encoded, format="PNG")

        decoded = PillowPngDecoder(fallback_dpi=110.0).decode(encoded.getvalue())

        self.assertIs(decoded.source.dpi_source, DpiSource.INFERRED)
        self.assertEqual(decoded.source.dpi_x, 110.0)
        self.assertEqual(decoded.source.dpi_y, 110.0)
        self.assertEqual(decoded.pixels[:3], bytes((255, 255, 255)))
        self.assertEqual(decoded.pixels[3:6], bytes((0, 0, 0)))

    def test_untrusted_png_limits_format_and_frame_count_are_enforced(self):
        with self.assertRaises(StructureExtractionError) as byte_context:
            PillowPngDecoder(max_file_bytes=len(self.png_data) - 1).decode(
                self.png_data
            )
        self.assertEqual(byte_context.exception.code, "png_file_limit_exceeded")

        with self.assertRaises(StructureExtractionError) as pixel_context:
            PillowPngDecoder(
                max_image_pixels=self.expected["pixel_width"] * self.expected["pixel_height"] - 1
            ).decode(self.png_data)
        self.assertEqual(pixel_context.exception.code, "png_pixel_limit_exceeded")

        jpeg = BytesIO()
        Image.new("RGB", (4, 4), "white").save(jpeg, format="JPEG")
        with self.assertRaises(StructureExtractionError) as format_context:
            PillowPngDecoder().decode(jpeg.getvalue())
        self.assertEqual(format_context.exception.code, "unsupported_image_format")

        animated = BytesIO()
        first = Image.new("RGB", (4, 4), "white")
        second = Image.new("RGB", (4, 4), "black")
        first.save(
            animated,
            format="PNG",
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )
        with self.assertRaises(StructureExtractionError) as frame_context:
            PillowPngDecoder().decode(animated.getvalue())
        self.assertEqual(frame_context.exception.code, "multi_frame_png_unsupported")

        with self.assertRaises(StructureExtractionError) as detect_context:
            OpenCvStructureExtractor(max_image_pixels=100).detect(self.image)
        self.assertEqual(
            detect_context.exception.code,
            "structure_pixel_limit_exceeded",
        )

    def test_structure_detection_has_no_preview_renderer_dependency(self):
        repository_root = Path(__file__).resolve().parents[1]
        source = "\n".join(
            (repository_root / path).read_text(encoding="utf-8")
            for path in (
                "src/aiteqno/ports/structure.py",
                "src/aiteqno/adapters/structure.py",
            )
        )

        self.assertNotIn("aiteqno.adapters.preview", source)
        self.assertNotIn("PillowPreviewRenderer", source)


def _bbox_from_dict(value):
    return PixelBoundingBox(
        x=value["x"],
        y=value["y"],
        width=value["width"],
        height=value["height"],
    )


def _bbox_near(first, second, *, tolerance):
    return all(
        abs(getattr(first, field) - getattr(second, field)) <= tolerance
        for field in ("x", "y", "width", "height")
    )


def _coverage(candidate, expected):
    left = max(candidate.x, expected.x)
    top = max(candidate.y, expected.y)
    right = min(candidate.x + candidate.width, expected.x + expected.width)
    bottom = min(candidate.y + candidate.height, expected.y + expected.height)
    intersection = max(0, right - left) * max(0, bottom - top)
    return intersection / (expected.width * expected.height)


if __name__ == "__main__":
    unittest.main()
