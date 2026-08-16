"""Deterministic Pillow projection of Document IR to an evaluation PNG."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from io import BytesIO
from os import PathLike
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from aiteqno._version import __version__
from aiteqno.domain import (
    Asset,
    DocumentElement,
    DocumentIR,
    FontStyle,
    ImageElement,
    ImageFit,
    LineDash,
    LineElement,
    Page,
    RectangleElement,
    TextAlign,
    TextElement,
    validate_document,
)
from aiteqno.ports import (
    AssetResolutionError,
    AssetResolver,
    PreviewFontSubstitution,
    PreviewRenderError,
    PreviewRenderReport,
    PreviewRenderResult,
    PreviewWarning,
    ResolvedAsset,
)


DEFAULT_PREVIEW_DPI = 144.0
DEFAULT_MAX_PREVIEW_PIXELS = 50_000_000
DEFAULT_PREVIEW_FONT_FALLBACKS = (
    "Noto Sans CJK JP",
    "Yu Gothic",
    "Meiryo",
    "DejaVu Sans",
)

_SOURCE_PAGE_COVERAGE_LIMIT = 0.90
_ITALIC_SHEAR = 0.20

_KNOWN_FONT_PATHS: dict[str, tuple[str, ...]] = {
    "noto sans cjk jp": (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ),
    "yu gothic": (
        "C:/Windows/Fonts/YuGothR.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
    ),
    "meiryo": ("C:/Windows/Fonts/meiryo.ttc",),
    "dejavu sans": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/local/share/fonts/DejaVuSans.ttf",
    ),
    "arial": ("C:/Windows/Fonts/arial.ttf",),
    "calibri": ("C:/Windows/Fonts/calibri.ttf",),
    "courier new": ("C:/Windows/Fonts/cour.ttf",),
    "times new roman": ("C:/Windows/Fonts/times.ttf",),
}


@dataclass(slots=True)
class _PreviewState:
    rendered_ids: list[str] = field(default_factory=list)
    rendered_seen: set[str] = field(default_factory=set)
    fallback_ids: list[str] = field(default_factory=list)
    fallback_seen: set[str] = field(default_factory=set)
    warnings: list[PreviewWarning] = field(default_factory=list)
    warning_seen: set[tuple[str, str, str]] = field(default_factory=set)
    substitutions: list[PreviewFontSubstitution] = field(default_factory=list)
    resolved_assets: dict[str, ResolvedAsset] = field(default_factory=dict)
    unavailable_images: dict[str, tuple[str, str]] = field(default_factory=dict)

    def record_rendered(self, element_id: str) -> None:
        if element_id not in self.rendered_seen:
            self.rendered_seen.add(element_id)
            self.rendered_ids.append(element_id)

    def warn_fallback(
        self,
        *,
        page_id: str,
        element_id: str,
        code: str,
        message: str,
    ) -> None:
        if element_id not in self.fallback_seen:
            self.fallback_seen.add(element_id)
            self.fallback_ids.append(element_id)
        warning_key = (code, page_id, element_id)
        if warning_key not in self.warning_seen:
            self.warning_seen.add(warning_key)
            self.warnings.append(
                PreviewWarning(
                    code=code,
                    message=message,
                    page_id=page_id,
                    element_id=element_id,
                )
            )


@dataclass(frozen=True, slots=True)
class _ResolvedFont:
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    family: str


class PillowPreviewRenderer:
    """Project one IR page to a deterministic, source-independent PNG."""

    renderer_name = "aiteqno-pillow-preview"
    renderer_version = __version__

    def __init__(
        self,
        *,
        asset_resolver: AssetResolver | None = None,
        font_paths: Mapping[str, str | PathLike[str]] | None = None,
        fallback_families: Iterable[str] = DEFAULT_PREVIEW_FONT_FALLBACKS,
        max_canvas_pixels: int = DEFAULT_MAX_PREVIEW_PIXELS,
    ) -> None:
        if max_canvas_pixels <= 0:
            raise ValueError("max_canvas_pixels must be positive")
        fallback_names = tuple(fallback_families)
        if any(not isinstance(name, str) or not name.strip() for name in fallback_names):
            raise ValueError("fallback_families must contain non-empty names")
        self._asset_resolver = asset_resolver
        self._fallback_families = fallback_names
        self._max_canvas_pixels = max_canvas_pixels
        self._font_paths = (
            None
            if font_paths is None
            else {
                name.casefold(): Path(path)
                for name, path in font_paths.items()
                if isinstance(name, str) and name.strip()
            }
        )
        self._font_cache: dict[tuple[str, int], _ResolvedFont] = {}

    def render(
        self,
        document: DocumentIR,
        output_path: str | PathLike[str],
        *,
        dpi: float = DEFAULT_PREVIEW_DPI,
    ) -> PreviewRenderResult:
        """Render a single-page RGB PNG atomically and return its report."""

        if not isinstance(document, DocumentIR):
            raise TypeError("document must be a DocumentIR")
        validate_document(document)
        selected_dpi = _validate_dpi(dpi)
        target = Path(output_path)
        if target.suffix.lower() != ".png":
            raise ValueError("output_path must use the .png extension")
        if len(document.pages) != 1:
            raise PreviewRenderError(
                "V1 preview rendering requires exactly one Document IR page"
            )

        page = document.pages[0]
        scale = selected_dpi / 72.0
        canvas_width = max(1, _round_pixel(page.size.width * scale))
        canvas_height = max(1, _round_pixel(page.size.height * scale))
        if canvas_width * canvas_height > self._max_canvas_pixels:
            raise PreviewRenderError(
                f"preview canvas has {canvas_width * canvas_height} pixels; "
                f"limit is {self._max_canvas_pixels}"
            )

        state = _PreviewState()
        self._prepare_assets(document, state)
        canvas = Image.new(
            "RGBA",
            (canvas_width, canvas_height),
            (255, 255, 255, 255),
        )
        paint_order = sorted(
            enumerate(page.elements),
            key=lambda item: (item[1].z_index, item[0]),
        )
        for _, element in paint_order:
            if isinstance(element, TextElement):
                self._draw_text(canvas, page, element, scale, state)
            elif isinstance(element, LineElement):
                self._draw_line(canvas, element, scale)
            elif isinstance(element, RectangleElement):
                self._draw_rectangle(canvas, element, scale)
            elif isinstance(element, ImageElement):
                self._draw_image(canvas, page, element, scale, state)
            state.record_rendered(element.id)

        self._save_atomically(canvas.convert("RGB"), target, selected_dpi)
        resolved_target = target.resolve()
        output_sha256 = hashlib.sha256(resolved_target.read_bytes()).hexdigest()
        report = PreviewRenderReport(
            renderer_name=self.renderer_name,
            renderer_version=self.renderer_version,
            ir_version=document.ir_version,
            output_path=str(resolved_target),
            output_sha256=output_sha256,
            dpi=selected_dpi,
            canvas_width_px=canvas_width,
            canvas_height_px=canvas_height,
            rendered_element_ids=tuple(state.rendered_ids),
            fallback_element_ids=tuple(state.fallback_ids),
            omitted_element_ids=(),
            warnings=tuple(state.warnings),
            font_substitutions=tuple(state.substitutions),
        )
        return PreviewRenderResult(output_path=resolved_target, report=report)

    def _prepare_assets(self, document: DocumentIR, state: _PreviewState) -> None:
        assets_by_id = {asset.id: asset for asset in document.assets}
        resolution_errors: dict[str, AssetResolutionError] = {}
        for page in document.pages:
            for element in page.elements:
                if not isinstance(element, ImageElement):
                    continue
                asset = assets_by_id[element.asset_id]
                background_reason = self._source_page_background_reason(
                    page,
                    element,
                    asset,
                )
                if background_reason is not None:
                    code = "source_page_background_rejected"
                    state.unavailable_images[element.id] = (code, background_reason)
                    state.warn_fallback(
                        page_id=page.id,
                        element_id=element.id,
                        code=code,
                        message=background_reason,
                    )
                    continue
                if self._asset_resolver is None:
                    code = "asset_resolver_unavailable"
                    message = (
                        f"image asset {asset.id!r} was replaced with a placeholder "
                        "because no bundle asset resolver was configured"
                    )
                    state.unavailable_images[element.id] = (code, message)
                    state.warn_fallback(
                        page_id=page.id,
                        element_id=element.id,
                        code=code,
                        message=message,
                    )
                    continue
                if asset.id not in state.resolved_assets and asset.id not in resolution_errors:
                    try:
                        resolved = self._asset_resolver.resolve(asset)
                        if resolved.asset_id != asset.id:
                            raise AssetResolutionError(
                                "asset_identity_mismatch",
                                asset.id,
                                (
                                    f"resolver returned asset {resolved.asset_id!r} "
                                    f"for registry entry {asset.id!r}"
                                ),
                            )
                        state.resolved_assets[asset.id] = resolved
                    except AssetResolutionError as exc:
                        resolution_errors[asset.id] = exc
                error = resolution_errors.get(asset.id)
                if error is not None:
                    message = f"{error}; image was replaced with a placeholder"
                    state.unavailable_images[element.id] = (error.code, message)
                    state.warn_fallback(
                        page_id=page.id,
                        element_id=element.id,
                        code=error.code,
                        message=message,
                    )

    @staticmethod
    def _source_page_background_reason(
        page: Page,
        element: ImageElement,
        asset: Asset,
    ) -> str | None:
        area_coverage = (
            element.bbox.width
            * element.bbox.height
            / (page.size.width * page.size.height)
        )
        source_size_match = (
            page.source is not None
            and asset.pixel_width == page.source.pixel_width
            and asset.pixel_height == page.source.pixel_height
        )
        width_coverage = element.bbox.width / page.size.width
        height_coverage = element.bbox.height / page.size.height
        if area_coverage >= _SOURCE_PAGE_COVERAGE_LIMIT or (
            source_size_match
            and width_coverage >= 0.75
            and height_coverage >= 0.75
        ):
            return (
                f"image {element.id!r} substantially covers page {page.id!r}; "
                "whole-source-page backgrounds are prohibited and a placeholder was used"
            )
        return None

    def _draw_text(
        self,
        canvas: Image.Image,
        page: Page,
        element: TextElement,
        scale: float,
        state: _PreviewState,
    ) -> None:
        left, top, right, bottom = _bbox_pixels(element, scale)
        width = max(1, right - left)
        height = max(1, bottom - top)
        font_size = max(1, _round_pixel(element.style.font_size_pt * scale))
        resolved_font = self._resolve_font(element.style.font_family, font_size)
        if resolved_font.family.casefold() != element.style.font_family.casefold():
            state.substitutions.append(
                PreviewFontSubstitution(
                    element_id=element.id,
                    requested=element.style.font_family,
                    replacement=resolved_font.family,
                )
            )
            state.warn_fallback(
                page_id=page.id,
                element_id=element.id,
                code="font_substituted",
                message=(
                    f"font {element.style.font_family!r} was replaced with "
                    f"{resolved_font.family!r}"
                ),
            )
        if element.style.font_weight not in {400, 700}:
            state.warn_fallback(
                page_id=page.id,
                element_id=element.id,
                code="font_weight_approximated",
                message=(
                    f"font weight {element.style.font_weight} was mapped to "
                    f"{'bold' if element.style.font_weight >= 600 else 'regular'}"
                ),
            )
        if element.style.font_style in {FontStyle.ITALIC, FontStyle.OBLIQUE}:
            state.warn_fallback(
                page_id=page.id,
                element_id=element.id,
                code="font_style_synthesized",
                message=(
                    f"{element.style.font_style.value} text was rendered with a "
                    "deterministic synthetic shear"
                ),
            )
        if element.style.align is TextAlign.JUSTIFY:
            state.warn_fallback(
                page_id=page.id,
                element_id=element.id,
                code="justify_approximated",
                message="justified text was approximated as left-aligned text",
            )

        fill = _rgba(element.style.color, element.style.opacity)
        if fill is None:
            state.warn_fallback(
                page_id=page.id,
                element_id=element.id,
                code="text_no_paint",
                message="text has no visible paint and produced no preview pixels",
            )
            return
        stroke_width = (
            max(1, _round_pixel(font_size / 24))
            if element.style.font_weight >= 600
            else 0
        )
        line_step = max(
            1,
            _round_pixel(font_size * element.style.line_height),
        )
        lines = element.text.split("\n")
        measure = ImageDraw.Draw(Image.new("L", (1, 1)))
        text_boxes = [
            measure.textbbox(
                (0, 0),
                line,
                font=resolved_font.font,
                stroke_width=stroke_width,
            )
            for line in lines
        ]
        content_width = max(
            1,
            *(text_box[2] - text_box[0] for text_box in text_boxes),
        )
        glyph_height = max(
            1,
            *(text_box[3] - text_box[1] for text_box in text_boxes),
        )
        content_height = max(1, (len(lines) - 1) * line_step + glyph_height)
        content = Image.new(
            "RGBA",
            (content_width, content_height),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(content)
        for line_index, (line, text_box) in enumerate(zip(lines, text_boxes, strict=True)):
            y = line_index * line_step
            text_width = text_box[2] - text_box[0]
            if element.style.align is TextAlign.CENTER:
                x = (content_width - text_width) / 2
            elif element.style.align is TextAlign.RIGHT:
                x = content_width - text_width
            else:
                x = 0
            draw.text(
                (x - text_box[0], y - text_box[1]),
                line,
                font=resolved_font.font,
                fill=fill,
                stroke_width=stroke_width,
                stroke_fill=fill,
            )

        if element.style.font_style in {FontStyle.ITALIC, FontStyle.OBLIQUE}:
            italic_width = content.width + math.ceil(
                _ITALIC_SHEAR * content.height
            )
            content = content.transform(
                (italic_width, content.height),
                Image.Transform.AFFINE,
                (
                    1,
                    _ITALIC_SHEAR,
                    -_ITALIC_SHEAR * content.height,
                    0,
                    1,
                    0,
                ),
                resample=Image.Resampling.BICUBIC,
            )
        rotation = element.style.rotation_deg % 360
        if not math.isclose(rotation, 0.0, abs_tol=1e-9):
            content = content.rotate(
                -rotation,
                resample=Image.Resampling.BICUBIC,
                expand=True,
            )
        if element.style.align is TextAlign.CENTER:
            content_x = (width - content.width) // 2
        elif element.style.align is TextAlign.RIGHT:
            content_x = width - content.width
        else:
            content_x = 0
        content_y = min(0, (height - content.height) // 2)
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        _alpha_composite_clipped(layer, content, content_x, content_y)
        canvas.alpha_composite(layer, dest=(left, top))

    def _resolve_font(self, requested_family: str, size: int) -> _ResolvedFont:
        cache_key = (requested_family.casefold(), size)
        cached = self._font_cache.get(cache_key)
        if cached is not None:
            return cached

        family_order = (requested_family, *self._fallback_families)
        seen: set[str] = set()
        for family in family_order:
            family_key = family.casefold()
            if family_key in seen:
                continue
            seen.add(family_key)
            for path in self._font_candidates(family):
                if not path.is_file():
                    continue
                try:
                    resolved = _ResolvedFont(
                        font=ImageFont.truetype(str(path), size=size),
                        family=family,
                    )
                except OSError:
                    continue
                self._font_cache[cache_key] = resolved
                return resolved

        resolved = _ResolvedFont(
            font=ImageFont.load_default(size=size),
            family="Pillow Default",
        )
        self._font_cache[cache_key] = resolved
        return resolved

    def _font_candidates(self, family: str) -> tuple[Path, ...]:
        family_key = family.casefold()
        if self._font_paths is not None:
            configured = self._font_paths.get(family_key)
            return () if configured is None else (configured,)
        return tuple(Path(path) for path in _KNOWN_FONT_PATHS.get(family_key, ()))

    @staticmethod
    def _draw_line(
        canvas: Image.Image,
        element: LineElement,
        scale: float,
    ) -> None:
        fill = _rgba(element.style.color, element.style.opacity)
        if fill is None:
            return
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        start = (_point_pixel(element.start.x, scale), _point_pixel(element.start.y, scale))
        end = (_point_pixel(element.end.x, scale), _point_pixel(element.end.y, scale))
        width = max(1, _round_pixel(element.style.width_pt * scale))
        _draw_patterned_line(draw, start, end, fill, width, element.style.dash)
        canvas.alpha_composite(overlay)

    @staticmethod
    def _draw_rectangle(
        canvas: Image.Image,
        element: RectangleElement,
        scale: float,
    ) -> None:
        left, top, right, bottom = _bbox_pixels(element, scale)
        right = max(left, right - 1)
        bottom = max(top, bottom - 1)
        fill = _rgba(element.style.fill_color, element.style.opacity)
        outline = (
            _rgba(element.style.stroke_color, element.style.opacity)
            if element.style.stroke_width_pt > 0
            else None
        )
        if fill is None and outline is None:
            return
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        stroke_width = (
            max(1, _round_pixel(element.style.stroke_width_pt * scale))
            if outline is not None
            else 1
        )
        radius = min(
            max(0, _round_pixel(element.style.corner_radius_pt * scale)),
            max(0, (right - left) // 2),
            max(0, (bottom - top) // 2),
        )
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=radius,
            fill=fill,
            outline=outline,
            width=stroke_width,
        )
        canvas.alpha_composite(overlay)

    def _draw_image(
        self,
        canvas: Image.Image,
        page: Page,
        element: ImageElement,
        scale: float,
        state: _PreviewState,
    ) -> None:
        left, top, right, bottom = _bbox_pixels(element, scale)
        width = max(1, right - left)
        height = max(1, bottom - top)
        resolved = state.resolved_assets.get(element.asset_id)
        if resolved is None or element.id in state.unavailable_images:
            self._draw_image_placeholder(canvas, (left, top, right, bottom))
            return
        try:
            with Image.open(BytesIO(resolved.data)) as opened:
                source = opened.convert("RGBA")
            if element.fit is ImageFit.STRETCH:
                projected = source.resize(
                    (width, height),
                    resample=Image.Resampling.LANCZOS,
                )
                destination = (left, top)
            elif element.fit is ImageFit.COVER:
                projected = ImageOps.fit(
                    source,
                    (width, height),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
                destination = (left, top)
            else:
                ratio = min(width / source.width, height / source.height)
                projected_width = max(1, _round_pixel(source.width * ratio))
                projected_height = max(1, _round_pixel(source.height * ratio))
                projected = source.resize(
                    (projected_width, projected_height),
                    resample=Image.Resampling.LANCZOS,
                )
                destination = (
                    left + (width - projected_width) // 2,
                    top + (height - projected_height) // 2,
                )
            canvas.alpha_composite(projected, dest=destination)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            state.warn_fallback(
                page_id=page.id,
                element_id=element.id,
                code="image_projection_failed",
                message=f"verified image could not be projected: {exc}",
            )
            self._draw_image_placeholder(canvas, (left, top, right, bottom))

    @staticmethod
    def _draw_image_placeholder(
        canvas: Image.Image,
        bbox: tuple[int, int, int, int],
    ) -> None:
        left, top, right, bottom = bbox
        right = max(left, right - 1)
        bottom = max(top, bottom - 1)
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        color = (127, 127, 127, 255)
        draw.rectangle((left, top, right, bottom), outline=color, width=1)
        draw.line((left, top, right, bottom), fill=color, width=1)
        draw.line((left, bottom, right, top), fill=color, width=1)
        canvas.alpha_composite(overlay)

    @staticmethod
    def _save_atomically(canvas: Image.Image, target: Path, dpi: float) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.stem}-",
            suffix=".tmp.png",
        )
        os.close(file_descriptor)
        temporary_path = Path(temporary_name)
        try:
            canvas.save(
                temporary_path,
                format="PNG",
                optimize=False,
                compress_level=9,
                dpi=(dpi, dpi),
            )
            with Image.open(temporary_path) as reopened:
                reopened.verify()
            os.replace(temporary_path, target)
        except Exception as exc:
            raise PreviewRenderError(
                f"failed to create PNG preview at {target}: {exc}"
            ) from exc
        finally:
            temporary_path.unlink(missing_ok=True)


def _validate_dpi(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("dpi must be a finite positive number")
    dpi = float(value)
    if not math.isfinite(dpi) or dpi <= 0:
        raise ValueError("dpi must be a finite positive number")
    return dpi


def _round_pixel(value: float) -> int:
    """Round a non-negative raster-boundary value half-up exactly once."""

    return math.floor(value + 0.5)


def _point_pixel(value: float, scale: float) -> int:
    return _round_pixel(value * scale)


def _bbox_pixels(
    element: DocumentElement,
    scale: float,
) -> tuple[int, int, int, int]:
    return (
        _point_pixel(element.bbox.x, scale),
        _point_pixel(element.bbox.y, scale),
        _point_pixel(element.bbox.right, scale),
        _point_pixel(element.bbox.bottom, scale),
    )


def _rgba(color: str | None, opacity: float) -> tuple[int, int, int, int] | None:
    if color is None or opacity <= 0:
        return None
    return (
        int(color[1:3], 16),
        int(color[3:5], 16),
        int(color[5:7], 16),
        max(0, min(255, _round_pixel(opacity * 255))),
    )


def _alpha_composite_clipped(
    destination: Image.Image,
    source: Image.Image,
    x: int,
    y: int,
) -> None:
    source_left = max(0, -x)
    source_top = max(0, -y)
    source_right = min(source.width, destination.width - x)
    source_bottom = min(source.height, destination.height - y)
    if source_left >= source_right or source_top >= source_bottom:
        return
    cropped = source.crop((source_left, source_top, source_right, source_bottom))
    destination.alpha_composite(
        cropped,
        dest=(max(0, x), max(0, y)),
    )


def _draw_patterned_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    fill: tuple[int, int, int, int],
    width: int,
    dash: LineDash,
) -> None:
    if dash is LineDash.SOLID:
        draw.line((*start, *end), fill=fill, width=width)
        return

    unit = max(1, width)
    patterns = {
        LineDash.DASHED: ((6 * unit, True), (4 * unit, False)),
        LineDash.DOTTED: ((unit, True), (2 * unit, False)),
        LineDash.DASH_DOT: (
            (6 * unit, True),
            (3 * unit, False),
            (unit, True),
            (3 * unit, False),
        ),
    }
    pattern = patterns[dash]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        return
    cursor = 0.0
    pattern_index = 0
    while cursor < length:
        segment_length, paint = pattern[pattern_index]
        segment_end = min(length, cursor + segment_length)
        if paint:
            ratio_start = cursor / length
            ratio_end = segment_end / length
            segment_start = (
                _round_pixel(start[0] + dx * ratio_start),
                _round_pixel(start[1] + dy * ratio_start),
            )
            segment_finish = (
                _round_pixel(start[0] + dx * ratio_end),
                _round_pixel(start[1] + dy * ratio_end),
            )
            draw.line(
                (*segment_start, *segment_finish),
                fill=fill,
                width=width,
            )
        cursor = segment_end
        pattern_index = (pattern_index + 1) % len(pattern)
