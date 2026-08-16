import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

from docx import Document as open_docx
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from aiteqno.adapters import DEFAULT_PAGE_MARGIN_PT, PythonDocxRenderer
from aiteqno.application import render_docx
from aiteqno.domain import (
    BoundingBox,
    DocumentIR,
    FontStyle,
    PageSize,
    TextAlign,
    TextElement,
)
from aiteqno.ports import DocxRenderError, RenderPolicy


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "document_ir" / "canonical.document.ir.json"
)


def load_canonical_document() -> DocumentIR:
    return DocumentIR.from_json(FIXTURE_PATH.read_text(encoding="utf-8"))


def text_only_document(*elements: TextElement) -> DocumentIR:
    canonical = load_canonical_document()
    page = replace(canonical.pages[0], elements=elements)
    return replace(canonical, pages=(page,), assets=())


class DocxRendererTest(unittest.TestCase):
    def test_canonical_fixture_generates_reopenable_docx_without_source_image(self):
        document = load_canonical_document()
        asset_path = FIXTURE_PATH.parent / document.assets[0].path
        self.assertFalse(asset_path.exists())

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "reconstructed.docx"
            result = render_docx(
                document,
                output_path,
                renderer=PythonDocxRenderer(),
            )

            self.assertEqual(result.output_path, output_path.resolve())
            self.assertTrue(output_path.is_file())
            with ZipFile(output_path) as package:
                package_files = set(package.namelist())
                document_xml = package.read("word/document.xml")
                relationships_xml = package.read("word/_rels/document.xml.rels")
            self.assertIn("[Content_Types].xml", package_files)
            self.assertIn("word/document.xml", package_files)
            self.assertFalse(
                any(name.startswith("word/media/") for name in package_files)
            )
            self.assertNotIn(document.assets[0].path.encode(), document_xml)
            self.assertNotIn(b'TargetMode="External"', relationships_xml)

            reopened = open_docx(output_path)
            self.assertEqual(
                [paragraph.text for paragraph in reopened.paragraphs],
                ["問診票"],
            )
            section = reopened.sections[0]
            self.assertAlmostEqual(section.page_width.pt, 595.28, places=1)
            self.assertAlmostEqual(section.page_height.pt, 841.89, places=1)
            self.assertEqual(section.orientation, WD_ORIENT.PORTRAIT)
            for margin in (
                section.left_margin,
                section.right_margin,
                section.top_margin,
                section.bottom_margin,
            ):
                self.assertAlmostEqual(margin.pt, DEFAULT_PAGE_MARGIN_PT, places=1)

            paragraph = reopened.paragraphs[0]
            self.assertAlmostEqual(paragraph.paragraph_format.left_indent.pt, 12.0)
            self.assertAlmostEqual(paragraph.paragraph_format.space_before.pt, 6.0)
            run = paragraph.runs[0]
            self.assertEqual(run.font.name, "Arial")
            self.assertAlmostEqual(run.font.size.pt, 18.0)
            self.assertTrue(run.bold)
            self.assertFalse(run.italic)
            self.assertEqual(str(run.font.color.rgb), "000000")
            run_fonts = run._element.rPr.rFonts
            self.assertEqual(run_fonts.get(qn("w:eastAsia")), "Arial")

            report = result.report
            self.assertEqual(report.rendered_element_ids, ("p001-text-0000",))
            self.assertEqual(
                report.omitted_element_ids,
                (
                    "p001-line-0001",
                    "p001-rectangle-0002",
                    "p001-image-0003",
                ),
            )
            self.assertEqual(report.fallback_element_ids, ("p001-text-0000",))
            self.assertEqual(
                report.font_substitutions[0].requested,
                "Noto Sans CJK JP",
            )
            self.assertEqual(report.font_substitutions[0].replacement, "Arial")
            self.assertEqual(
                report.output_sha256,
                hashlib.sha256(output_path.read_bytes()).hexdigest(),
            )
            json.dumps(report.to_dict())

    def test_reading_order_and_supported_styles_are_preserved(self):
        canonical_text = load_canonical_document().pages[0].elements[0]
        self.assertIsInstance(canonical_text, TextElement)
        first = replace(
            canonical_text,
            text="First",
            style=replace(canonical_text.style, font_family="Arial"),
        )
        second = replace(
            canonical_text,
            id="p001-text-0001",
            bbox=BoundingBox(x=72, y=96, width=240, height=18),
            text="Second",
            reading_order=1,
            style=replace(
                canonical_text.style,
                font_family="Times New Roman",
                font_size_pt=12,
                font_weight=400,
                font_style=FontStyle.ITALIC,
                color="#336699",
                align=TextAlign.RIGHT,
                line_height=1.5,
            ),
        )
        document = text_only_document(first, second)

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "reconstructed.docx"
            result = render_docx(
                document,
                output_path,
                renderer=PythonDocxRenderer(),
                policy=RenderPolicy.STRICT,
            )
            reopened = open_docx(output_path)

        self.assertEqual(
            [paragraph.text for paragraph in reopened.paragraphs],
            ["First", "Second"],
        )
        second_paragraph = reopened.paragraphs[1]
        self.assertEqual(second_paragraph.alignment, WD_ALIGN_PARAGRAPH.RIGHT)
        self.assertAlmostEqual(second_paragraph.paragraph_format.line_spacing, 1.5)
        second_run = second_paragraph.runs[0]
        self.assertEqual(second_run.font.name, "Times New Roman")
        self.assertAlmostEqual(second_run.font.size.pt, 12.0)
        self.assertFalse(second_run.bold)
        self.assertTrue(second_run.italic)
        self.assertEqual(str(second_run.font.color.rgb), "336699")
        self.assertEqual(result.report.fallback_element_ids, ())
        self.assertEqual(result.report.omitted_element_ids, ())
        self.assertEqual(result.report.warnings, ())

    def test_unsupported_styles_have_explicit_fallbacks_and_strict_rejects_them(self):
        canonical_text = load_canonical_document().pages[0].elements[0]
        self.assertIsInstance(canonical_text, TextElement)
        styled_text = replace(
            canonical_text,
            style=replace(
                canonical_text.style,
                font_family="Imaginary Sans",
                font_weight=650,
                font_style=FontStyle.OBLIQUE,
                rotation_deg=15,
                opacity=0.5,
            ),
        )
        document = text_only_document(styled_text)

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "best-effort.docx"
            result = render_docx(
                document,
                output_path,
                renderer=PythonDocxRenderer(),
            )
            strict_output = Path(temporary_directory) / "strict.docx"
            with self.assertRaisesRegex(DocxRenderError, "strict rendering rejected"):
                render_docx(
                    document,
                    strict_output,
                    renderer=PythonDocxRenderer(),
                    policy=RenderPolicy.STRICT,
                )
            self.assertFalse(strict_output.exists())

        warning_codes = {warning.code for warning in result.report.warnings}
        self.assertEqual(result.report.fallback_element_ids, (styled_text.id,))
        self.assertTrue(
            {
                "font_substituted",
                "font_weight_approximated",
                "oblique_mapped_to_italic",
                "rotation_omitted",
                "opacity_approximated",
            }.issubset(warning_codes)
        )

    def test_landscape_page_size_sets_section_orientation(self):
        canonical_text = load_canonical_document().pages[0].elements[0]
        self.assertIsInstance(canonical_text, TextElement)
        supported_text = replace(
            canonical_text,
            style=replace(canonical_text.style, font_family="Arial"),
        )
        document = text_only_document(supported_text)
        landscape_page = replace(
            document.pages[0],
            size=PageSize(width=841.89, height=595.28),
        )
        document = replace(document, pages=(landscape_page,))

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "landscape.docx"
            render_docx(
                document,
                output_path,
                renderer=PythonDocxRenderer(),
                policy=RenderPolicy.STRICT,
            )
            reopened = open_docx(output_path)

        section = reopened.sections[0]
        self.assertEqual(section.orientation, WD_ORIENT.LANDSCAPE)
        self.assertAlmostEqual(section.page_width.pt, 841.89, places=1)
        self.assertAlmostEqual(section.page_height.pt, 595.28, places=1)


if __name__ == "__main__":
    unittest.main()
