import base64
import hashlib
import json
import math
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from PIL import Image

from aiteqno.adapters import BundleAssetResolver, PillowPreviewRenderer
from aiteqno.application import render_preview
from aiteqno.domain import (
    BoundingBox,
    DocumentIR,
    FontStyle,
    ImageElement,
    ImageFit,
    LineDash,
    Point,
    TextAlign,
    TextElement,
)
from aiteqno.ports import PreviewRenderError


FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "document_ir"
FIXTURE_PATH = FIXTURE_DIRECTORY / "canonical.document.ir.json"
ASSET_B64_PATH = FIXTURE_DIRECTORY / "canonical-logo.png.b64"


def load_canonical_document() -> DocumentIR:
    return DocumentIR.from_json(FIXTURE_PATH.read_text(encoding="utf-8"))


def materialize_canonical_asset(bundle_root: Path, document: DocumentIR) -> Path:
    asset_path = bundle_root / document.assets[0].path
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(
        base64.b64decode(ASSET_B64_PATH.read_text(encoding="ascii"))
    )
    return asset_path


def deterministic_renderer(bundle_root: Path | None = None) -> PillowPreviewRenderer:
    return PillowPreviewRenderer(
        asset_resolver=(
            None if bundle_root is None else BundleAssetResolver(bundle_root)
        ),
        font_paths={},
        fallback_families=(),
    )


class PillowPreviewRendererTest(unittest.TestCase):
    def test_canonical_preview_is_deterministic_and_matches_ir_geometry(self):
        document = load_canonical_document()
        original_source_path = FIXTURE_DIRECTORY / "fixture-source"
        self.assertFalse(original_source_path.exists())

        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle_root = Path(temporary_directory)
            materialize_canonical_asset(bundle_root, document)
            renderer = deterministic_renderer(bundle_root)
            first_path = bundle_root / "first.png"
            second_path = bundle_root / "second.png"
            first_result = render_preview(
                document,
                first_path,
                renderer=renderer,
            )
            second_result = render_preview(
                document,
                second_path,
                renderer=renderer,
            )
            first_bytes = first_path.read_bytes()
            second_bytes = second_path.read_bytes()
            with Image.open(first_path) as opened:
                preview = opened.copy()
                preview_dpi = opened.info.get("dpi")

        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(
            first_result.report.output_sha256,
            hashlib.sha256(first_bytes).hexdigest(),
        )
        self.assertEqual(
            first_result.report.output_sha256,
            second_result.report.output_sha256,
        )
        self.assertEqual(preview.mode, "RGB")
        self.assertEqual(preview.size, (1191, 1684))
        self.assertIsNotNone(preview_dpi)
        self.assertAlmostEqual(preview_dpi[0], 144.0, delta=0.1)
        self.assertAlmostEqual(preview_dpi[1], 144.0, delta=0.1)

        self.assertEqual(preview.getpixel((200, 200)), (51, 51, 51))
        self.assertEqual(preview.getpixel((96, 248)), (0, 0, 0))
        text_region = preview.crop((96, 84, 516, 132))
        self.assertGreater(
            sum(
                pixel != (255, 255, 255)
                for pixel in text_region.get_flattened_data()
            ),
            100,
        )
        image_region = preview.crop((624, 248, 816, 392))
        self.assertGreater(
            sum(
                pixel != (255, 255, 255)
                for pixel in image_region.get_flattened_data()
            ),
            5_000,
        )
        colors = preview.getcolors(maxcolors=100_000)
        self.assertIsNotNone(colors)
        self.assertNotIn((255, 0, 0), {color for _, color in colors})

        report = first_result.report
        self.assertEqual(report.dpi, 144.0)
        self.assertEqual(report.canvas_width_px, 1191)
        self.assertEqual(report.canvas_height_px, 1684)
        self.assertEqual(
            report.rendered_element_ids,
            (
                "p001-rectangle-0002",
                "p001-image-0003",
                "p001-line-0001",
                "p001-text-0000",
            ),
        )
        self.assertEqual(report.fallback_element_ids, ("p001-text-0000",))
        self.assertEqual(report.omitted_element_ids, ())
        self.assertEqual(
            [warning.code for warning in report.warnings],
            ["font_substituted"],
        )
        self.assertEqual(
            report.font_substitutions[0].replacement,
            "Pillow Default",
        )
        json.dumps(report.to_dict())

    def test_configurable_dpi_uses_half_up_point_to_pixel_conversion(self):
        document = load_canonical_document()
        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle_root = Path(temporary_directory)
            materialize_canonical_asset(bundle_root, document)
            output_path = bundle_root / "preview-72.png"
            result = render_preview(
                document,
                output_path,
                renderer=deterministic_renderer(bundle_root),
                dpi=72,
            )
            with Image.open(output_path) as opened:
                preview = opened.copy()

        self.assertEqual(preview.size, (595, 842))
        self.assertEqual((result.report.canvas_width_px, result.report.canvas_height_px), (595, 842))
        self.assertEqual(preview.getpixel((100, 100)), (51, 51, 51))
        self.assertEqual(preview.getpixel((48, 124)), (0, 0, 0))

    def test_higher_z_index_paints_later_regardless_of_array_order(self):
        canonical = load_canonical_document()
        rectangle = canonical.pages[0].elements[2]
        bbox = BoundingBox(x=50, y=50, width=50, height=50)
        lower_red = replace(
            rectangle,
            id="p001-rectangle-0100",
            bbox=bbox,
            z_index=1,
            style=replace(
                rectangle.style,
                stroke_color=None,
                stroke_width_pt=0,
                fill_color="#ff0000",
            ),
        )
        higher_blue = replace(
            rectangle,
            id="p001-rectangle-0101",
            bbox=bbox,
            z_index=2,
            style=replace(lower_red.style, fill_color="#0000ff"),
        )
        page = replace(
            canonical.pages[0],
            elements=(higher_blue, lower_red),
        )
        document = replace(canonical, pages=(page,), assets=())

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "z-order.png"
            result = render_preview(
                document,
                output_path,
                renderer=deterministic_renderer(),
                dpi=72,
            )
            with Image.open(output_path) as opened:
                center_pixel = opened.getpixel((75, 75))

        self.assertEqual(center_pixel, (0, 0, 255))
        self.assertEqual(
            result.report.rendered_element_ids,
            (lower_red.id, higher_blue.id),
        )

    def test_image_fit_modes_are_projected_inside_their_ir_boxes(self):
        canonical = load_canonical_document()
        image = canonical.pages[0].elements[3]
        self.assertIsInstance(image, ImageElement)
        buffer = BytesIO()
        Image.new("RGB", (8, 4), "black").save(
            buffer,
            format="PNG",
            optimize=False,
            compress_level=9,
        )
        asset_bytes = buffer.getvalue()
        digest = hashlib.sha256(asset_bytes).hexdigest()
        asset = replace(
            canonical.assets[0],
            path=f"assets/sha256-{digest}.png",
            sha256=digest,
            pixel_width=8,
            pixel_height=4,
        )
        contain = replace(
            image,
            id="p001-image-0100",
            bbox=BoundingBox(x=50, y=200, width=60, height=60),
            fit=ImageFit.CONTAIN,
        )
        cover = replace(
            image,
            id="p001-image-0101",
            bbox=BoundingBox(x=130, y=200, width=60, height=60),
            fit=ImageFit.COVER,
        )
        stretch = replace(
            image,
            id="p001-image-0102",
            bbox=BoundingBox(x=210, y=200, width=60, height=60),
            fit=ImageFit.STRETCH,
        )
        document = replace(
            canonical,
            pages=(
                replace(
                    canonical.pages[0],
                    elements=(contain, cover, stretch),
                ),
            ),
            assets=(asset,),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            asset_path = root / asset.path
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(asset_bytes)
            output_path = root / "fits.png"
            result = render_preview(
                document,
                output_path,
                renderer=deterministic_renderer(root),
                dpi=72,
            )
            with Image.open(output_path) as opened:
                preview = opened.copy()

        self.assertEqual(preview.getpixel((80, 205)), (255, 255, 255))
        self.assertEqual(preview.getpixel((80, 230)), (0, 0, 0))
        self.assertEqual(preview.getpixel((160, 205)), (0, 0, 0))
        self.assertEqual(preview.getpixel((240, 205)), (0, 0, 0))
        self.assertEqual(result.report.fallback_element_ids, ())

    def test_dash_and_opacity_styles_are_rendered_without_debug_overlays(self):
        canonical = load_canonical_document()
        line = canonical.pages[0].elements[1]
        rectangle = canonical.pages[0].elements[2]
        dotted = replace(
            line,
            id="p001-line-0100",
            bbox=BoundingBox(x=48, y=100, width=100, height=0),
            start=Point(x=48, y=100),
            end=Point(x=148, y=100),
            style=replace(line.style, dash=LineDash.DOTTED),
        )
        translucent = replace(
            rectangle,
            id="p001-rectangle-0100",
            bbox=BoundingBox(x=200, y=90, width=50, height=30),
            style=replace(
                rectangle.style,
                stroke_color=None,
                stroke_width_pt=0,
                fill_color="#ff0000",
                opacity=0.5,
            ),
        )
        document = replace(
            canonical,
            pages=(
                replace(
                    canonical.pages[0],
                    elements=(dotted, translucent),
                ),
            ),
            assets=(),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "styles.png"
            result = render_preview(
                document,
                output_path,
                renderer=deterministic_renderer(),
                dpi=72,
            )
            with Image.open(output_path) as opened:
                preview = opened.copy()

        dark_pixels = sum(
            preview.getpixel((x, 100)) != (255, 255, 255)
            for x in range(48, 149)
        )
        self.assertGreater(dark_pixels, 10)
        self.assertLess(dark_pixels, 80)
        self.assertEqual(preview.getpixel((225, 105)), (255, 127, 127))
        self.assertEqual(result.report.fallback_element_ids, ())

    def test_text_style_and_font_approximations_are_explicit(self):
        canonical = load_canonical_document()
        text = canonical.pages[0].elements[0]
        self.assertIsInstance(text, TextElement)
        styled_text = replace(
            text,
            style=replace(
                text.style,
                font_family="Imaginary CJK",
                font_weight=650,
                font_style=FontStyle.ITALIC,
                align=TextAlign.JUSTIFY,
                rotation_deg=12,
                opacity=0.5,
            ),
        )
        page = replace(canonical.pages[0], elements=(styled_text,))
        document = replace(canonical, pages=(page,), assets=())

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "styled.png"
            result = render_preview(
                document,
                output_path,
                renderer=deterministic_renderer(),
            )
            with Image.open(output_path) as opened:
                pixels = opened.crop((96, 84, 516, 132)).get_flattened_data()
                non_white_count = sum(pixel != (255, 255, 255) for pixel in pixels)

        warning_codes = {warning.code for warning in result.report.warnings}
        self.assertTrue(
            {
                "font_substituted",
                "font_weight_approximated",
                "font_style_synthesized",
                "justify_approximated",
            }.issubset(warning_codes)
        )
        self.assertEqual(result.report.fallback_element_ids, (styled_text.id,))
        self.assertGreater(non_white_count, 50)

    def test_missing_and_full_page_assets_use_non_red_placeholders(self):
        canonical = load_canonical_document()
        image = canonical.pages[0].elements[3]
        self.assertIsInstance(image, ImageElement)
        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle_root = Path(temporary_directory)
            missing_path = bundle_root / "missing.png"
            missing_result = render_preview(
                canonical,
                missing_path,
                renderer=deterministic_renderer(bundle_root),
            )
            with Image.open(missing_path) as opened:
                missing_preview = opened.copy()

            page = canonical.pages[0]
            page_image = replace(
                image,
                bbox=BoundingBox(
                    x=0,
                    y=0,
                    width=page.size.width,
                    height=page.size.height,
                ),
            )
            guarded_document = replace(
                canonical,
                pages=(replace(page, elements=(page_image,)),),
            )
            materialize_canonical_asset(bundle_root, guarded_document)
            guarded_path = bundle_root / "guarded.png"
            guarded_result = render_preview(
                guarded_document,
                guarded_path,
                renderer=deterministic_renderer(bundle_root),
                dpi=36,
            )
            with Image.open(guarded_path) as opened:
                guarded_preview = opened.copy()

        self.assertIn(
            "asset_missing",
            {warning.code for warning in missing_result.report.warnings},
        )
        self.assertEqual(missing_preview.getpixel((624, 248)), (127, 127, 127))
        self.assertIn(
            "source_page_background_rejected",
            {warning.code for warning in guarded_result.report.warnings},
        )
        guarded_colors = guarded_preview.getcolors(maxcolors=10_000)
        self.assertIsNotNone(guarded_colors)
        self.assertNotIn((255, 0, 0), {color for _, color in guarded_colors})

    def test_invalid_output_contracts_fail_without_creating_png(self):
        canonical = load_canonical_document()
        second_page = replace(
            canonical.pages[0],
            id="page-002",
            number=2,
            elements=(),
        )
        multi_page = replace(
            canonical,
            pages=(canonical.pages[0], second_page),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renderer = deterministic_renderer()
            with self.assertRaisesRegex(PreviewRenderError, "exactly one"):
                render_preview(
                    multi_page,
                    root / "multi.png",
                    renderer=renderer,
                )
            with self.assertRaisesRegex(ValueError, "finite positive"):
                render_preview(
                    canonical,
                    root / "nan.png",
                    renderer=renderer,
                    dpi=math.nan,
                )
            with self.assertRaisesRegex(ValueError, "extension"):
                render_preview(
                    canonical,
                    root / "wrong.jpg",
                    renderer=renderer,
                )
            with self.assertRaisesRegex(PreviewRenderError, "pixels"):
                render_preview(
                    canonical,
                    root / "large.png",
                    renderer=PillowPreviewRenderer(max_canvas_pixels=10),
                )

            self.assertEqual(list(root.glob("*.png")), [])


if __name__ == "__main__":
    unittest.main()
