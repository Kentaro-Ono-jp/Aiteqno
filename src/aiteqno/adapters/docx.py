"""Flow-first DOCX adapter backed by ``python-docx``.

Document IR v0.1 does not encode page margins, so this adapter uses a
deterministic 36-point compatibility margin (reduced only for very small
pages). Text bounding boxes are approximated with paragraph indentation and
vertical spacing. Every unsupported style and omitted non-text element is
reported; nothing disappears silently.
"""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from collections.abc import Iterable
from os import PathLike
from pathlib import Path

from docx import Document as open_docx
from docx.document import Document as WordDocument
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.section import Section
from docx.shared import Pt, RGBColor
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from aiteqno._version import __version__
from aiteqno.domain import (
    GEOMETRY_TOLERANCE_PT,
    DocumentIR,
    FontStyle,
    Page,
    TextAlign,
    TextElement,
    TextStyle,
    validate_document,
)
from aiteqno.ports import (
    DocxRenderError,
    DocxRenderReport,
    DocxRenderResult,
    FontSubstitution,
    RenderPolicy,
    RenderWarning,
)


DEFAULT_PAGE_MARGIN_PT = 36.0
DEFAULT_FALLBACK_FONT = "Arial"
DEFAULT_SUPPORTED_FONTS = (
    "Arial",
    "Calibri",
    "Courier New",
    "Times New Roman",
    "Yu Gothic",
)

_ALIGNMENT_MAP = {
    TextAlign.LEFT: WD_ALIGN_PARAGRAPH.LEFT,
    TextAlign.CENTER: WD_ALIGN_PARAGRAPH.CENTER,
    TextAlign.RIGHT: WD_ALIGN_PARAGRAPH.RIGHT,
    TextAlign.JUSTIFY: WD_ALIGN_PARAGRAPH.JUSTIFY,
}


class PythonDocxRenderer:
    """Render page settings and text without consulting source image data."""

    renderer_name = "aiteqno-python-docx"
    renderer_version = __version__

    def __init__(
        self,
        *,
        fallback_font: str = DEFAULT_FALLBACK_FONT,
        supported_fonts: Iterable[str] | None = None,
    ) -> None:
        if not fallback_font.strip():
            raise ValueError("fallback_font must not be empty")
        font_names = tuple(
            DEFAULT_SUPPORTED_FONTS if supported_fonts is None else supported_fonts
        )
        if not font_names or any(
            not isinstance(name, str) or not name.strip() for name in font_names
        ):
            raise ValueError("supported_fonts must contain non-empty names")
        if fallback_font.casefold() not in {name.casefold() for name in font_names}:
            font_names = (*font_names, fallback_font)
        self._fallback_font = fallback_font
        self._supported_fonts = {name.casefold(): name for name in font_names}

    def render(
        self,
        document: DocumentIR,
        output_path: str | PathLike[str],
        *,
        policy: RenderPolicy = RenderPolicy.BEST_EFFORT,
    ) -> DocxRenderResult:
        """Render a valid DOCX atomically and return a complete report."""

        if not isinstance(document, DocumentIR):
            raise TypeError("document must be a DocumentIR")
        validate_document(document)
        selected_policy = RenderPolicy(policy)
        target = Path(output_path)
        if target.suffix.lower() != ".docx":
            raise ValueError("output_path must use the .docx extension")

        rendered_ids: list[str] = []
        fallback_ids: list[str] = []
        fallback_seen: set[str] = set()
        omitted_ids: list[str] = []
        warnings: list[RenderWarning] = []
        substitutions: list[FontSubstitution] = []

        word_document = open_docx()
        title = document.metadata.get("title")
        if isinstance(title, str):
            word_document.core_properties.title = title

        for page_index, page in enumerate(document.pages):
            if page_index == 0:
                section = word_document.sections[0]
            else:
                section = word_document.add_section(WD_SECTION.NEW_PAGE)
            horizontal_margin, vertical_margin = self._configure_section(section, page)
            self._render_page(
                word_document,
                page,
                horizontal_margin=horizontal_margin,
                vertical_margin=vertical_margin,
                rendered_ids=rendered_ids,
                fallback_ids=fallback_ids,
                fallback_seen=fallback_seen,
                omitted_ids=omitted_ids,
                warnings=warnings,
                substitutions=substitutions,
            )

        if selected_policy is RenderPolicy.STRICT and (fallback_ids or omitted_ids):
            details = ", ".join((*fallback_ids, *omitted_ids))
            raise DocxRenderError(
                f"strict rendering rejected approximated or omitted elements: {details}"
            )

        self._save_atomically(word_document, target)
        resolved_target = target.resolve()
        output_sha256 = hashlib.sha256(resolved_target.read_bytes()).hexdigest()
        report = DocxRenderReport(
            renderer_name=self.renderer_name,
            renderer_version=self.renderer_version,
            ir_version=document.ir_version,
            output_path=str(resolved_target),
            output_sha256=output_sha256,
            rendered_element_ids=tuple(rendered_ids),
            fallback_element_ids=tuple(fallback_ids),
            omitted_element_ids=tuple(omitted_ids),
            warnings=tuple(warnings),
            errors=(),
            font_substitutions=tuple(substitutions),
        )
        return DocxRenderResult(output_path=resolved_target, report=report)

    @staticmethod
    def _configure_section(section: Section, page: Page) -> tuple[float, float]:
        width = page.size.width
        height = page.size.height
        section.orientation = (
            WD_ORIENT.LANDSCAPE if width > height else WD_ORIENT.PORTRAIT
        )
        section.page_width = Pt(width)
        section.page_height = Pt(height)

        horizontal_margin = min(DEFAULT_PAGE_MARGIN_PT, width / 4)
        vertical_margin = min(DEFAULT_PAGE_MARGIN_PT, height / 4)
        section.left_margin = Pt(horizontal_margin)
        section.right_margin = Pt(horizontal_margin)
        section.top_margin = Pt(vertical_margin)
        section.bottom_margin = Pt(vertical_margin)
        section.gutter = Pt(0)
        return horizontal_margin, vertical_margin

    def _render_page(
        self,
        word_document: WordDocument,
        page: Page,
        *,
        horizontal_margin: float,
        vertical_margin: float,
        rendered_ids: list[str],
        fallback_ids: list[str],
        fallback_seen: set[str],
        omitted_ids: list[str],
        warnings: list[RenderWarning],
        substitutions: list[FontSubstitution],
    ) -> None:
        text_elements = sorted(
            (element for element in page.elements if isinstance(element, TextElement)),
            key=lambda element: (
                element.reading_order,
                element.z_index,
                element.bbox.y,
                element.bbox.x,
                element.id,
            ),
        )
        for element in page.elements:
            if isinstance(element, TextElement):
                continue
            omitted_ids.append(element.id)
            warnings.append(
                RenderWarning(
                    code="unsupported_element_omitted",
                    message=(
                        f"{element.type.value} rendering is deferred to the visual "
                        "element renderer"
                    ),
                    page_id=page.id,
                    element_id=element.id,
                )
            )

        previous_bottom = vertical_margin
        for element in text_elements:
            paragraph = word_document.add_paragraph()
            previous_bottom = self._apply_position(
                paragraph,
                page,
                element,
                horizontal_margin=horizontal_margin,
                previous_bottom=previous_bottom,
                fallback_ids=fallback_ids,
                fallback_seen=fallback_seen,
                warnings=warnings,
            )
            self._apply_text_style(
                paragraph,
                page,
                element,
                fallback_ids=fallback_ids,
                fallback_seen=fallback_seen,
                warnings=warnings,
                substitutions=substitutions,
            )
            rendered_ids.append(element.id)

    @staticmethod
    def _apply_position(
        paragraph: Paragraph,
        page: Page,
        element: TextElement,
        *,
        horizontal_margin: float,
        previous_bottom: float,
        fallback_ids: list[str],
        fallback_seen: set[str],
        warnings: list[RenderWarning],
    ) -> float:
        paragraph_format = paragraph.paragraph_format
        content_right = page.size.width - horizontal_margin
        left_indent = element.bbox.x - horizontal_margin
        right_indent = content_right - element.bbox.right
        space_before = element.bbox.y - previous_bottom

        if (
            left_indent < -GEOMETRY_TOLERANCE_PT
            or right_indent < -GEOMETRY_TOLERANCE_PT
        ):
            PythonDocxRenderer._record_fallback(
                element.id,
                fallback_ids,
                fallback_seen,
            )
            warnings.append(
                RenderWarning(
                    code="text_outside_layout_margins",
                    message="text was moved inside the DOCX compatibility margins",
                    page_id=page.id,
                    element_id=element.id,
                )
            )
        if space_before < -GEOMETRY_TOLERANCE_PT:
            PythonDocxRenderer._record_fallback(
                element.id,
                fallback_ids,
                fallback_seen,
            )
            warnings.append(
                RenderWarning(
                    code="vertical_position_approximated",
                    message=(
                        "reading order moves upward or overlaps; flow layout used zero "
                        "additional spacing"
                    ),
                    page_id=page.id,
                    element_id=element.id,
                )
            )

        paragraph_format.left_indent = Pt(max(0.0, left_indent))
        paragraph_format.right_indent = Pt(max(0.0, right_indent))
        paragraph_format.space_before = Pt(max(0.0, space_before))
        paragraph_format.space_after = Pt(0)
        paragraph_format.keep_together = True
        return max(previous_bottom, element.bbox.bottom)

    def _apply_text_style(
        self,
        paragraph: Paragraph,
        page: Page,
        element: TextElement,
        *,
        fallback_ids: list[str],
        fallback_seen: set[str],
        warnings: list[RenderWarning],
        substitutions: list[FontSubstitution],
    ) -> None:
        style = element.style
        paragraph.alignment = _ALIGNMENT_MAP[style.align]
        paragraph.paragraph_format.line_spacing = style.line_height
        run = paragraph.add_run(element.text)

        resolved_font = self._supported_fonts.get(style.font_family.casefold())
        if resolved_font is None:
            resolved_font = self._fallback_font
            substitutions.append(
                FontSubstitution(
                    element_id=element.id,
                    requested=style.font_family,
                    replacement=resolved_font,
                )
            )
            self._warn_style_fallback(
                page,
                element,
                code="font_substituted",
                message=(
                    f"font {style.font_family!r} was replaced with {resolved_font!r}"
                ),
                fallback_ids=fallback_ids,
                fallback_seen=fallback_seen,
                warnings=warnings,
            )

        self._set_run_font(run, resolved_font, style)
        if style.font_weight not in {400, 700}:
            mapped_weight = 700 if style.font_weight >= 600 else 400
            self._warn_style_fallback(
                page,
                element,
                code="font_weight_approximated",
                message=(
                    f"font weight {style.font_weight} was mapped to {mapped_weight}"
                ),
                fallback_ids=fallback_ids,
                fallback_seen=fallback_seen,
                warnings=warnings,
            )
        if style.font_style is FontStyle.OBLIQUE:
            self._warn_style_fallback(
                page,
                element,
                code="oblique_mapped_to_italic",
                message="oblique text was mapped to DOCX italic",
                fallback_ids=fallback_ids,
                fallback_seen=fallback_seen,
                warnings=warnings,
            )
        if not math.isclose(style.rotation_deg % 360, 0.0, abs_tol=1e-9):
            self._warn_style_fallback(
                page,
                element,
                code="rotation_omitted",
                message="text rotation is unsupported and was rendered unrotated",
                fallback_ids=fallback_ids,
                fallback_seen=fallback_seen,
                warnings=warnings,
            )
        if not math.isclose(style.opacity, 1.0, abs_tol=1e-9):
            self._warn_style_fallback(
                page,
                element,
                code="opacity_approximated",
                message="text opacity is unsupported and was rendered opaque",
                fallback_ids=fallback_ids,
                fallback_seen=fallback_seen,
                warnings=warnings,
            )

    @staticmethod
    def _set_run_font(run: Run, font_name: str, style: TextStyle) -> None:
        run.font.name = font_name
        run.font.size = Pt(style.font_size_pt)
        run.bold = style.font_weight >= 600
        run.italic = style.font_style in {FontStyle.ITALIC, FontStyle.OBLIQUE}
        if style.color is not None:
            run.font.color.rgb = RGBColor.from_string(style.color[1:].upper())

        run_properties = run._element.get_or_add_rPr()
        run_fonts = run_properties.get_or_add_rFonts()
        for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
            run_fonts.set(qn(f"w:{attribute}"), font_name)

    @staticmethod
    def _warn_style_fallback(
        page: Page,
        element: TextElement,
        *,
        code: str,
        message: str,
        fallback_ids: list[str],
        fallback_seen: set[str],
        warnings: list[RenderWarning],
    ) -> None:
        PythonDocxRenderer._record_fallback(
            element.id,
            fallback_ids,
            fallback_seen,
        )
        warnings.append(
            RenderWarning(
                code=code,
                message=message,
                page_id=page.id,
                element_id=element.id,
            )
        )

    @staticmethod
    def _record_fallback(
        element_id: str,
        fallback_ids: list[str],
        fallback_seen: set[str],
    ) -> None:
        if element_id not in fallback_seen:
            fallback_seen.add(element_id)
            fallback_ids.append(element_id)

    @staticmethod
    def _save_atomically(word_document: WordDocument, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.stem}-",
            suffix=".tmp.docx",
        )
        os.close(file_descriptor)
        temporary_path = Path(temporary_name)
        try:
            word_document.save(temporary_path)
            open_docx(temporary_path)
            os.replace(temporary_path, target)
        except Exception as exc:
            raise DocxRenderError(f"failed to create DOCX at {target}: {exc}") from exc
        finally:
            temporary_path.unlink(missing_ok=True)
