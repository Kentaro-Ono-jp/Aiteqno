"""Safe Pillow PNG decoding and deterministic OpenCV structure detection."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from io import BytesIO
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from aiteqno.domain import (
    Confidence,
    DpiSource,
    PageSource,
    PixelBoundingBox,
    Provenance,
    ProvenanceStage,
)
from aiteqno.ports.structure import (
    ImageInput,
    LineCandidate,
    LineOrientation,
    PageCandidate,
    PixelMode,
    PixelPoint,
    RectangleCandidate,
    RegionCandidate,
    RegionKind,
    StructureExtractionError,
    StructureExtractionResult,
)


DEFAULT_FALLBACK_DPI = 96.0
DEFAULT_MAX_PNG_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_PNG_PIXELS = 40_000_000
STRUCTURE_PROVIDER = "aiteqno.opencv-structure"
STRUCTURE_PROVIDER_VERSION = "1.0"

_ALGORITHM_PARAMETERS = {
    "algorithm_version": STRUCTURE_PROVIDER_VERSION,
    "adaptive_threshold_c": 11,
    "line_kernel_fraction": 0.05,
    "line_merge_gap_fraction": 0.01,
    "max_line_thickness_fraction": 0.025,
    "min_image_area_fraction": 0.0025,
    "min_line_fraction": 0.06,
    "text_join_fraction": 0.015,
}


@dataclass(frozen=True, slots=True)
class _RawLine:
    orientation: LineOrientation
    bbox: PixelBoundingBox
    occupancy: float

    @property
    def center(self) -> int:
        if self.orientation is LineOrientation.HORIZONTAL:
            return self.bbox.y + (self.bbox.height - 1) // 2
        return self.bbox.x + (self.bbox.width - 1) // 2

    @property
    def interval_start(self) -> int:
        if self.orientation is LineOrientation.HORIZONTAL:
            return self.bbox.x
        return self.bbox.y

    @property
    def interval_end(self) -> int:
        if self.orientation is LineOrientation.HORIZONTAL:
            return self.bbox.x + self.bbox.width - 1
        return self.bbox.y + self.bbox.height - 1

    @property
    def thickness(self) -> int:
        if self.orientation is LineOrientation.HORIZONTAL:
            return self.bbox.height
        return self.bbox.width


class PillowPngDecoder:
    """Decode exactly one PNG page to white-composited RGB8 pixels."""

    def __init__(
        self,
        *,
        max_file_bytes: int = DEFAULT_MAX_PNG_BYTES,
        max_image_pixels: int = DEFAULT_MAX_PNG_PIXELS,
        fallback_dpi: float = DEFAULT_FALLBACK_DPI,
    ) -> None:
        if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int):
            raise TypeError("max_file_bytes must be an integer")
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        if isinstance(max_image_pixels, bool) or not isinstance(max_image_pixels, int):
            raise TypeError("max_image_pixels must be an integer")
        if max_image_pixels <= 0:
            raise ValueError("max_image_pixels must be positive")
        if (
            isinstance(fallback_dpi, bool)
            or not isinstance(fallback_dpi, (int, float))
            or not math.isfinite(float(fallback_dpi))
            or float(fallback_dpi) <= 0
        ):
            raise ValueError("fallback_dpi must be a positive finite number")
        self._max_file_bytes = max_file_bytes
        self._max_image_pixels = max_image_pixels
        self._fallback_dpi = float(fallback_dpi)

    def decode(self, data: bytes) -> ImageInput:
        """Validate limits and format before fully decoding one PNG frame."""

        if not isinstance(data, bytes):
            raise TypeError("PNG data must be immutable bytes")
        if not data:
            raise StructureExtractionError("empty_png", "PNG data must not be empty")
        if len(data) > self._max_file_bytes:
            raise StructureExtractionError(
                "png_file_limit_exceeded",
                f"PNG contains {len(data)} bytes; limit is {self._max_file_bytes}",
            )

        try:
            opened = Image.open(BytesIO(data))
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise StructureExtractionError(
                "invalid_png",
                f"input is not a readable PNG: {exc}",
            ) from exc

        try:
            if opened.format != "PNG":
                raise StructureExtractionError(
                    "unsupported_image_format",
                    f"expected PNG input, received {opened.format or 'unknown'}",
                )
            frame_count = int(getattr(opened, "n_frames", 1))
            if frame_count != 1:
                raise StructureExtractionError(
                    "multi_frame_png_unsupported",
                    f"V1 requires one PNG frame; received {frame_count}",
                )
            width, height = opened.size
            pixel_count = width * height
            if width <= 0 or height <= 0:
                raise StructureExtractionError(
                    "invalid_png_dimensions",
                    f"PNG dimensions must be positive; received {width}x{height}",
                )
            if pixel_count > self._max_image_pixels:
                raise StructureExtractionError(
                    "png_pixel_limit_exceeded",
                    f"PNG contains {pixel_count} pixels; limit is "
                    f"{self._max_image_pixels}",
                )

            dpi_x, dpi_y, dpi_source = self._effective_dpi(opened.info.get("dpi"))
            opened.load()
            normalized = _white_composited_rgb(opened)
            pixels = normalized.tobytes()
        except StructureExtractionError:
            raise
        except (Image.DecompressionBombError, OSError, ValueError) as exc:
            raise StructureExtractionError(
                "png_decode_failed",
                f"PNG could not be decoded safely: {exc}",
            ) from exc
        finally:
            opened.close()

        return ImageInput(
            source=PageSource(
                pixel_width=width,
                pixel_height=height,
                dpi_x=dpi_x,
                dpi_y=dpi_y,
                dpi_source=dpi_source,
            ),
            mode=PixelMode.RGB8,
            pixels=pixels,
            source_sha256=hashlib.sha256(data).hexdigest(),
        )

    def _effective_dpi(
        self,
        declared: object,
    ) -> tuple[float, float, DpiSource]:
        if isinstance(declared, (tuple, list)) and len(declared) >= 2:
            try:
                dpi_x = float(declared[0])
                dpi_y = float(declared[1])
            except (TypeError, ValueError, OverflowError):
                dpi_x = dpi_y = 0.0
            if (
                math.isfinite(dpi_x)
                and math.isfinite(dpi_y)
                and 1.0 <= dpi_x <= 2400.0
                and 1.0 <= dpi_y <= 2400.0
            ):
                return round(dpi_x, 3), round(dpi_y, 3), DpiSource.DECLARED
        return self._fallback_dpi, self._fallback_dpi, DpiSource.INFERRED


class OpenCvStructureExtractor:
    """Detect V1 visual candidates without OCR or domain element assembly."""

    def __init__(
        self,
        *,
        max_image_pixels: int = DEFAULT_MAX_PNG_PIXELS,
    ) -> None:
        if isinstance(max_image_pixels, bool) or not isinstance(max_image_pixels, int):
            raise TypeError("max_image_pixels must be an integer")
        if max_image_pixels <= 0:
            raise ValueError("max_image_pixels must be positive")
        self._max_image_pixels = max_image_pixels
        parameters = {**_ALGORITHM_PARAMETERS, "max_image_pixels": max_image_pixels}
        encoded = json.dumps(
            parameters,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        self._parameters_digest = hashlib.sha256(encoded).hexdigest()

    def detect(self, image: ImageInput) -> StructureExtractionResult:
        """Return stable page, line, rectangle, text, and image candidates."""

        if not isinstance(image, ImageInput):
            raise TypeError("image must be an ImageInput")
        source = image.source
        pixel_count = source.pixel_width * source.pixel_height
        if pixel_count > self._max_image_pixels:
            raise StructureExtractionError(
                "structure_pixel_limit_exceeded",
                f"image contains {pixel_count} pixels; limit is "
                f"{self._max_image_pixels}",
            )

        rgb = np.frombuffer(image.pixels, dtype=np.uint8).reshape(
            source.pixel_height,
            source.pixel_width,
            3,
        )
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        binary = _binarize(gray)
        raw_lines, line_mask = _detect_raw_lines(binary)
        lines = self._line_candidates(raw_lines, source)
        rectangles = self._rectangle_candidates(line_mask, lines)
        content_mask = cv2.bitwise_and(binary, cv2.bitwise_not(_expand(line_mask, 3)))
        image_regions = self._image_candidates(content_mask, source)
        text_regions = self._text_candidates(content_mask, image_regions, source)

        page_bbox = PixelBoundingBox(
            x=0,
            y=0,
            width=source.pixel_width,
            height=source.pixel_height,
        )
        page_confidence = Confidence(overall=1.0, detection=1.0)
        page = PageCandidate(
            source=source,
            confidence=page_confidence,
            provenance=(
                self._provenance(
                    page_bbox,
                    "decoded single-page PNG normalized to RGB8",
                ),
            ),
        )
        return StructureExtractionResult(
            page=page,
            lines=tuple(lines),
            rectangles=tuple(rectangles),
            text_regions=tuple(text_regions),
            image_regions=tuple(image_regions),
        )

    def _line_candidates(
        self,
        raw_lines: list[_RawLine],
        source: PageSource,
    ) -> list[LineCandidate]:
        merged = _merge_raw_lines(raw_lines, source)
        candidates: list[LineCandidate] = []
        for raw in merged:
            if raw.orientation is LineOrientation.HORIZONTAL:
                start = PixelPoint(x=raw.interval_start, y=raw.center)
                end = PixelPoint(x=raw.interval_end, y=raw.center)
                axis_length = source.pixel_width
            else:
                start = PixelPoint(x=raw.center, y=raw.interval_start)
                end = PixelPoint(x=raw.center, y=raw.interval_end)
                axis_length = source.pixel_height
            length_ratio = (raw.interval_end - raw.interval_start + 1) / axis_length
            score = _score(0.55 + 0.25 * raw.occupancy + 0.20 * min(1.0, 4 * length_ratio))
            confidence = Confidence(overall=score, detection=score)
            candidates.append(
                LineCandidate(
                    orientation=raw.orientation,
                    start=start,
                    end=end,
                    bbox=raw.bbox,
                    confidence=confidence,
                    provenance=(
                        self._provenance(
                            raw.bbox,
                            f"{raw.orientation.value} morphology line candidate",
                        ),
                    ),
                )
            )
        return sorted(
            candidates,
            key=lambda item: (
                item.bbox.y,
                item.bbox.x,
                item.orientation.value,
                item.bbox.height,
                item.bbox.width,
            ),
        )

    def _rectangle_candidates(
        self,
        line_mask: np.ndarray,
        lines: list[LineCandidate],
    ) -> list[RectangleCandidate]:
        if not lines or not np.any(line_mask):
            return []
        contours, _ = cv2.findContours(
            _expand(line_mask, 3),
            cv2.RETR_LIST,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        horizontals = [
            line for line in lines if line.orientation is LineOrientation.HORIZONTAL
        ]
        verticals = [
            line for line in lines if line.orientation is LineOrientation.VERTICAL
        ]
        tolerance = max(
            3,
            max((line.bbox.height for line in horizontals), default=1) + 2,
            max((line.bbox.width for line in verticals), default=1) + 2,
        )
        found: dict[tuple[int, int, int, int], RectangleCandidate] = {}
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if width < 12 or height < 12:
                continue
            contour_area = abs(float(cv2.contourArea(contour)))
            if contour_area < width * height * 0.50:
                continue
            left = _nearest_axis(x, verticals, tolerance)
            right = _nearest_axis(x + width - 1, verticals, tolerance)
            top = _nearest_axis(y, horizontals, tolerance)
            bottom = _nearest_axis(y + height - 1, horizontals, tolerance)
            if None in (left, right, top, bottom):
                continue
            if right <= left or bottom <= top:
                continue
            edge_lines = _rectangle_edges(
                left,
                top,
                right,
                bottom,
                horizontals,
                verticals,
                tolerance,
            )
            if edge_lines is None:
                continue
            bbox = PixelBoundingBox(
                x=left,
                y=top,
                width=right - left + 1,
                height=bottom - top + 1,
            )
            rectangularity = min(1.0, contour_area / max(1.0, width * height))
            edge_score = sum(line.confidence.overall for line in edge_lines) / 4
            score = _score(0.55 * edge_score + 0.45 * rectangularity)
            candidate = RectangleCandidate(
                bbox=bbox,
                confidence=Confidence(overall=score, detection=score),
                provenance=(self._provenance(bbox, "closed line rectangle candidate"),),
            )
            key = (bbox.x, bbox.y, bbox.width, bbox.height)
            previous = found.get(key)
            if previous is None or candidate.confidence.overall > previous.confidence.overall:
                found[key] = candidate
        return sorted(
            found.values(),
            key=lambda item: (
                item.bbox.y,
                item.bbox.x,
                item.bbox.height,
                item.bbox.width,
            ),
        )

    def _image_candidates(
        self,
        content_mask: np.ndarray,
        source: PageSource,
    ) -> list[RegionCandidate]:
        height, width = content_mask.shape
        work = cv2.morphologyEx(
            content_mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)),
            iterations=1,
        )
        work = _expand(work, 3)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(work, 8)
        min_width = max(16, int(round(width * 0.04)))
        min_height = max(12, int(round(height * 0.04)))
        min_area = max(64, int(round(width * height * 0.0025)))
        candidates: list[RegionCandidate] = []
        for label in range(1, count):
            x = int(stats[label, cv2.CC_STAT_LEFT])
            y = int(stats[label, cv2.CC_STAT_TOP])
            box_width = int(stats[label, cv2.CC_STAT_WIDTH])
            box_height = int(stats[label, cv2.CC_STAT_HEIGHT])
            if box_width < min_width or box_height < min_height:
                continue
            label_window = labels[y : y + box_height, x : x + box_width] == label
            foreground_window = content_mask[y : y + box_height, x : x + box_width] > 0
            points = np.argwhere(label_window & foreground_window)
            if points.size == 0:
                continue
            raw_y_min, raw_x_min = points.min(axis=0)
            raw_y_max, raw_x_max = points.max(axis=0)
            raw_x = x + int(raw_x_min)
            raw_y = y + int(raw_y_min)
            raw_width = int(raw_x_max - raw_x_min + 1)
            raw_height = int(raw_y_max - raw_y_min + 1)
            raw_area = raw_width * raw_height
            if raw_width < min_width or raw_height < min_height or raw_area < min_area:
                continue
            raw_crop = content_mask[
                raw_y : raw_y + raw_height,
                raw_x : raw_x + raw_width,
            ]
            density = float(cv2.countNonZero(raw_crop)) / raw_area
            if density < 0.18:
                continue
            page_fraction = raw_area / (width * height)
            if raw_height <= max(24, int(round(height * 0.08))) and density < 0.55:
                continue
            bbox = PixelBoundingBox(
                x=raw_x,
                y=raw_y,
                width=raw_width,
                height=raw_height,
            )
            review_required = page_fraction >= 0.85
            score = _score(0.50 + 0.35 * density + 0.15 * min(1.0, 10 * page_fraction))
            if review_required:
                score = min(score, 0.40)
            note = "dense visual image region candidate"
            if review_required:
                note += "; page-covering candidate requires review"
            candidates.append(
                RegionCandidate(
                    kind=RegionKind.IMAGE,
                    bbox=bbox,
                    confidence=Confidence(overall=score, detection=score),
                    provenance=(self._provenance(bbox, note),),
                )
            )
        return _normalize_regions(candidates)

    def _text_candidates(
        self,
        content_mask: np.ndarray,
        image_regions: list[RegionCandidate],
        source: PageSource,
    ) -> list[RegionCandidate]:
        height, width = content_mask.shape
        text_mask = content_mask.copy()
        for image in image_regions:
            bbox = image.bbox
            x1 = max(0, bbox.x - 2)
            y1 = max(0, bbox.y - 2)
            x2 = min(width, bbox.x + bbox.width + 2)
            y2 = min(height, bbox.y + bbox.height + 2)
            text_mask[y1:y2, x1:x2] = 0

        join_width = max(3, min(15, int(round(width * 0.015))))
        join_height = max(1, min(3, int(round(height * 0.006))))
        joined = cv2.dilate(
            text_mask,
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (join_width, join_height),
            ),
            iterations=1,
        )
        count, labels, stats, _ = cv2.connectedComponentsWithStats(joined, 8)
        max_height = max(64, int(round(height * 0.20)))
        max_area = width * height * 0.20
        candidates: list[RegionCandidate] = []
        for label in range(1, count):
            x = int(stats[label, cv2.CC_STAT_LEFT])
            y = int(stats[label, cv2.CC_STAT_TOP])
            box_width = int(stats[label, cv2.CC_STAT_WIDTH])
            box_height = int(stats[label, cv2.CC_STAT_HEIGHT])
            if box_width < 2 or box_height < 2:
                continue
            label_window = labels[y : y + box_height, x : x + box_width] == label
            raw_window = text_mask[y : y + box_height, x : x + box_width] > 0
            points = np.argwhere(label_window & raw_window)
            if points.size == 0:
                continue
            raw_count = int(points.shape[0])
            if raw_count < 6:
                continue
            raw_y_min, raw_x_min = points.min(axis=0)
            raw_y_max, raw_x_max = points.max(axis=0)
            raw_x = x + int(raw_x_min)
            raw_y = y + int(raw_y_min)
            raw_width = int(raw_x_max - raw_x_min + 1)
            raw_height = int(raw_y_max - raw_y_min + 1)
            raw_area = raw_width * raw_height
            if raw_width < 4 or raw_height < 4:
                continue
            if raw_height > max_height or raw_area > max_area:
                continue
            if raw_height <= 2 and raw_width >= max(12, int(round(width * 0.06))):
                continue
            density = raw_count / raw_area
            if density < 0.015:
                continue
            bbox = PixelBoundingBox(
                x=raw_x,
                y=raw_y,
                width=raw_width,
                height=raw_height,
            )
            glyph_evidence = min(1.0, raw_count / 40.0)
            text_shape = min(1.0, raw_width / max(1.0, raw_height * 2.0))
            score = _score(0.48 + 0.25 * glyph_evidence + 0.17 * text_shape + 0.10 * min(1.0, density * 3))
            candidates.append(
                RegionCandidate(
                    kind=RegionKind.TEXT,
                    bbox=bbox,
                    confidence=Confidence(overall=score, detection=score),
                    provenance=(self._provenance(bbox, "connected text region candidate"),),
                )
            )
        return _normalize_regions(candidates)

    def _provenance(self, bbox: PixelBoundingBox, notes: str) -> Provenance:
        return Provenance(
            stage=ProvenanceStage.STRUCTURE,
            provider=STRUCTURE_PROVIDER,
            provider_version=STRUCTURE_PROVIDER_VERSION,
            source_bbox_px=bbox,
            parameters_digest=self._parameters_digest,
            notes=notes,
        )


def _white_composited_rgb(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def _binarize(gray: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, otsu = cv2.threshold(
        blurred,
        0,
        255,
        cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU,
    )
    shortest = min(gray.shape)
    block_size = max(15, min(51, (shortest // 8) | 1))
    adaptive = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        block_size,
        int(_ALGORITHM_PARAMETERS["adaptive_threshold_c"]),
    )
    return cv2.bitwise_or(otsu, adaptive)


def _detect_raw_lines(binary: np.ndarray) -> tuple[list[_RawLine], np.ndarray]:
    height, width = binary.shape
    detected: list[_RawLine] = []
    accepted_mask = np.zeros_like(binary)
    for orientation, axis_length in (
        (LineOrientation.HORIZONTAL, width),
        (LineOrientation.VERTICAL, height),
    ):
        kernel_length = max(
            12,
            int(round(axis_length * float(_ALGORITHM_PARAMETERS["line_kernel_fraction"]))),
        )
        if orientation is LineOrientation.HORIZONTAL:
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_length, 1))
            close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))
        else:
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_length))
            close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3))
        mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        minimum_length = max(
            12,
            int(round(axis_length * float(_ALGORITHM_PARAMETERS["min_line_fraction"]))),
        )
        maximum_thickness = max(
            5,
            int(round(min(width, height) * float(_ALGORITHM_PARAMETERS["max_line_thickness_fraction"]))),
        )
        for label in range(1, count):
            x = int(stats[label, cv2.CC_STAT_LEFT])
            y = int(stats[label, cv2.CC_STAT_TOP])
            box_width = int(stats[label, cv2.CC_STAT_WIDTH])
            box_height = int(stats[label, cv2.CC_STAT_HEIGHT])
            area = int(stats[label, cv2.CC_STAT_AREA])
            if orientation is LineOrientation.HORIZONTAL:
                length, thickness = box_width, box_height
            else:
                length, thickness = box_height, box_width
            if length < minimum_length or thickness > maximum_thickness:
                continue
            if length < thickness * 4:
                continue
            bbox = PixelBoundingBox(x=x, y=y, width=box_width, height=box_height)
            occupancy = min(1.0, area / max(1, box_width * box_height))
            detected.append(
                _RawLine(
                    orientation=orientation,
                    bbox=bbox,
                    occupancy=occupancy,
                )
            )
            accepted_mask[labels == label] = 255
    return detected, accepted_mask


def _merge_raw_lines(
    lines: Iterable[_RawLine],
    source: PageSource,
) -> list[_RawLine]:
    merged: list[_RawLine] = []
    for orientation in (LineOrientation.HORIZONTAL, LineOrientation.VERTICAL):
        axis_length = (
            source.pixel_width
            if orientation is LineOrientation.HORIZONTAL
            else source.pixel_height
        )
        maximum_gap = max(
            2,
            int(round(axis_length * float(_ALGORITHM_PARAMETERS["line_merge_gap_fraction"]))),
        )
        ordered = sorted(
            (line for line in lines if line.orientation is orientation),
            key=lambda line: (line.center, line.interval_start, line.interval_end),
        )
        for line in ordered:
            match_index: int | None = None
            for index, current in enumerate(merged):
                if current.orientation is not orientation:
                    continue
                center_tolerance = max(2, current.thickness, line.thickness)
                gap = max(
                    line.interval_start - current.interval_end - 1,
                    current.interval_start - line.interval_end - 1,
                    0,
                )
                if abs(current.center - line.center) <= center_tolerance and gap <= maximum_gap:
                    match_index = index
                    break
            if match_index is None:
                merged.append(line)
                continue
            current = merged[match_index]
            left = min(current.bbox.x, line.bbox.x)
            top = min(current.bbox.y, line.bbox.y)
            right = max(
                current.bbox.x + current.bbox.width,
                line.bbox.x + line.bbox.width,
            )
            bottom = max(
                current.bbox.y + current.bbox.height,
                line.bbox.y + line.bbox.height,
            )
            merged[match_index] = _RawLine(
                orientation=orientation,
                bbox=PixelBoundingBox(
                    x=left,
                    y=top,
                    width=right - left,
                    height=bottom - top,
                ),
                occupancy=max(current.occupancy, line.occupancy),
            )
    return merged


def _nearest_axis(
    value: int,
    lines: list[LineCandidate],
    tolerance: int,
) -> int | None:
    coordinates = [
        line.start.y
        if line.orientation is LineOrientation.HORIZONTAL
        else line.start.x
        for line in lines
    ]
    if not coordinates:
        return None
    nearest = min(coordinates, key=lambda coordinate: (abs(coordinate - value), coordinate))
    if abs(nearest - value) > tolerance:
        return None
    return nearest


def _rectangle_edges(
    left: int,
    top: int,
    right: int,
    bottom: int,
    horizontals: list[LineCandidate],
    verticals: list[LineCandidate],
    tolerance: int,
) -> tuple[LineCandidate, LineCandidate, LineCandidate, LineCandidate] | None:
    top_line = _covering_line(horizontals, top, left, right, tolerance)
    bottom_line = _covering_line(horizontals, bottom, left, right, tolerance)
    left_line = _covering_line(verticals, left, top, bottom, tolerance)
    right_line = _covering_line(verticals, right, top, bottom, tolerance)
    if None in (top_line, bottom_line, left_line, right_line):
        return None
    return top_line, bottom_line, left_line, right_line


def _covering_line(
    lines: list[LineCandidate],
    coordinate: int,
    interval_start: int,
    interval_end: int,
    tolerance: int,
) -> LineCandidate | None:
    matches: list[LineCandidate] = []
    for line in lines:
        if line.orientation is LineOrientation.HORIZONTAL:
            line_coordinate = line.start.y
            line_start, line_end = line.start.x, line.end.x
        else:
            line_coordinate = line.start.x
            line_start, line_end = line.start.y, line.end.y
        if (
            abs(line_coordinate - coordinate) <= tolerance
            and line_start <= interval_start + tolerance
            and line_end >= interval_end - tolerance
        ):
            matches.append(line)
    if not matches:
        return None
    def rank(line: LineCandidate) -> tuple[float, int]:
        if line.orientation is LineOrientation.HORIZONTAL:
            length = line.end.x - line.start.x
        else:
            length = line.end.y - line.start.y
        return line.confidence.overall, length

    return max(matches, key=rank)


def _expand(mask: np.ndarray, size: int) -> np.ndarray:
    return cv2.dilate(
        mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (size, size)),
        iterations=1,
    )


def _normalize_regions(candidates: list[RegionCandidate]) -> list[RegionCandidate]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            -item.confidence.overall,
            -(item.bbox.width * item.bbox.height),
            item.bbox.y,
            item.bbox.x,
        ),
    )
    kept: list[RegionCandidate] = []
    for candidate in ordered:
        if any(_substantially_same(candidate.bbox, item.bbox) for item in kept):
            continue
        kept.append(candidate)
    return sorted(
        kept,
        key=lambda item: (
            item.bbox.y,
            item.bbox.x,
            item.bbox.height,
            item.bbox.width,
        ),
    )


def _substantially_same(first: PixelBoundingBox, second: PixelBoundingBox) -> bool:
    left = max(first.x, second.x)
    top = max(first.y, second.y)
    right = min(first.x + first.width, second.x + second.width)
    bottom = min(first.y + first.height, second.y + second.height)
    intersection = max(0, right - left) * max(0, bottom - top)
    if intersection == 0:
        return False
    first_area = first.width * first.height
    second_area = second.width * second.height
    union = first_area + second_area - intersection
    return intersection / union >= 0.75 or intersection / min(first_area, second_area) >= 0.92


def _score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)
