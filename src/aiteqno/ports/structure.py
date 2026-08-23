"""Port contracts for source-pixel structural extraction from PNG pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from aiteqno.domain import (
    Confidence,
    PageSource,
    PixelBoundingBox,
    Provenance,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class StructureExtractionError(ValueError):
    """Raised when a PNG cannot be decoded or structurally inspected safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PixelMode(str, Enum):
    """Normalized byte layout accepted by the structure extractor."""

    RGB8 = "rgb8"


class LineOrientation(str, Enum):
    """Normalized source-line orientations emitted by structure extraction."""

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    DIAGONAL = "diagonal"


class RegionKind(str, Enum):
    """Visual region kinds that may be enriched by later application stages."""

    TEXT = "text"
    IMAGE = "image"


@dataclass(frozen=True, slots=True, kw_only=True)
class PixelPoint:
    """An integer point in the original PNG coordinate system."""

    x: int
    y: int

    def __post_init__(self) -> None:
        if isinstance(self.x, bool) or not isinstance(self.x, int) or self.x < 0:
            raise ValueError("pixel point x must be a non-negative integer")
        if isinstance(self.y, bool) or not isinstance(self.y, int) or self.y < 0:
            raise ValueError("pixel point y must be a non-negative integer")


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageInput:
    """Immutable, normalized pixels and source metadata passed through the port."""

    source: PageSource
    mode: PixelMode
    pixels: bytes
    source_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, PageSource):
            raise TypeError("image source must be a PageSource")
        if not isinstance(self.mode, PixelMode):
            try:
                object.__setattr__(self, "mode", PixelMode(self.mode))
            except (TypeError, ValueError) as exc:
                raise ValueError("image mode must be rgb8") from exc
        if not isinstance(self.pixels, bytes):
            raise TypeError("image pixels must be immutable bytes")
        expected_size = self.source.pixel_width * self.source.pixel_height * 3
        if len(self.pixels) != expected_size:
            raise ValueError(
                f"rgb8 pixels must contain exactly {expected_size} bytes; "
                f"received {len(self.pixels)}"
            )
        if not isinstance(self.source_sha256, str) or not _SHA256_PATTERN.fullmatch(
            self.source_sha256
        ):
            raise ValueError("source_sha256 must be 64 lower-case hex digits")


def _validate_evidence(
    confidence: Confidence,
    provenance: tuple[Provenance, ...],
) -> tuple[Provenance, ...]:
    if not isinstance(confidence, Confidence):
        raise TypeError("candidate confidence must be a Confidence")
    if isinstance(provenance, (str, bytes, bytearray)):
        raise TypeError("candidate provenance must be a sequence")
    records = tuple(provenance)
    if not records:
        raise ValueError("candidate provenance must not be empty")
    if any(not isinstance(record, Provenance) for record in records):
        raise TypeError("candidate provenance entries must be Provenance values")
    return records


@dataclass(frozen=True, slots=True, kw_only=True)
class PageCandidate:
    """The decoded page and its effective source-coordinate metadata."""

    source: PageSource
    confidence: Confidence
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source, PageSource):
            raise TypeError("page candidate source must be a PageSource")
        object.__setattr__(
            self,
            "provenance",
            _validate_evidence(self.confidence, self.provenance),
        )

    @property
    def bbox(self) -> PixelBoundingBox:
        return PixelBoundingBox(
            x=0,
            y=0,
            width=self.source.pixel_width,
            height=self.source.pixel_height,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class LineCandidate:
    """One normalized axis-aligned line in original PNG pixels."""

    orientation: LineOrientation
    start: PixelPoint
    end: PixelPoint
    bbox: PixelBoundingBox
    confidence: Confidence
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.orientation, LineOrientation):
            try:
                object.__setattr__(
                    self,
                    "orientation",
                    LineOrientation(self.orientation),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "line orientation must be horizontal, vertical, or diagonal"
                ) from exc
        if not isinstance(self.start, PixelPoint) or not isinstance(
            self.end, PixelPoint
        ):
            raise TypeError("line endpoints must be PixelPoint values")
        if not isinstance(self.bbox, PixelBoundingBox):
            raise TypeError("line bbox must be a PixelBoundingBox")
        if self.orientation is LineOrientation.HORIZONTAL:
            if self.start.y != self.end.y or self.start.x >= self.end.x:
                raise ValueError("horizontal line endpoints must advance along x")
        elif self.orientation is LineOrientation.VERTICAL:
            if self.start.x != self.end.x or self.start.y >= self.end.y:
                raise ValueError("vertical line endpoints must advance along y")
        elif (
            self.start.x == self.end.x
            or self.start.y == self.end.y
            or self.start.x >= self.end.x
        ):
            raise ValueError(
                "diagonal line endpoints must differ on both axes and advance along x"
            )
        if not _bbox_contains_point(self.bbox, self.start) or not _bbox_contains_point(
            self.bbox, self.end
        ):
            raise ValueError("line bbox must contain both endpoints")
        object.__setattr__(
            self,
            "provenance",
            _validate_evidence(self.confidence, self.provenance),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RectangleCandidate:
    """One closed rectangular structure candidate in source pixels."""

    bbox: PixelBoundingBox
    confidence: Confidence
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.bbox, PixelBoundingBox):
            raise TypeError("rectangle bbox must be a PixelBoundingBox")
        object.__setattr__(
            self,
            "provenance",
            _validate_evidence(self.confidence, self.provenance),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RegionCandidate:
    """A text or embedded-image region awaiting later enrichment."""

    kind: RegionKind
    bbox: PixelBoundingBox
    confidence: Confidence
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RegionKind):
            try:
                object.__setattr__(self, "kind", RegionKind(self.kind))
            except (TypeError, ValueError) as exc:
                raise ValueError("region kind must be text or image") from exc
        if not isinstance(self.bbox, PixelBoundingBox):
            raise TypeError("region bbox must be a PixelBoundingBox")
        object.__setattr__(
            self,
            "provenance",
            _validate_evidence(self.confidence, self.provenance),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class StructureExtractionResult:
    """All normalized, source-pixel candidates found on one PNG page."""

    page: PageCandidate
    lines: tuple[LineCandidate, ...] = ()
    rectangles: tuple[RectangleCandidate, ...] = ()
    text_regions: tuple[RegionCandidate, ...] = ()
    image_regions: tuple[RegionCandidate, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.page, PageCandidate):
            raise TypeError("result page must be a PageCandidate")
        collections = (
            ("lines", self.lines, LineCandidate),
            ("rectangles", self.rectangles, RectangleCandidate),
            ("text_regions", self.text_regions, RegionCandidate),
            ("image_regions", self.image_regions, RegionCandidate),
        )
        for field_name, values, item_type in collections:
            if isinstance(values, (str, bytes, bytearray)):
                raise TypeError(f"{field_name} must be a sequence")
            collected = tuple(values)
            if any(not isinstance(item, item_type) for item in collected):
                raise TypeError(f"{field_name} contains an invalid candidate")
            object.__setattr__(self, field_name, collected)

        if any(region.kind is not RegionKind.TEXT for region in self.text_regions):
            raise ValueError("text_regions must contain only text candidates")
        if any(region.kind is not RegionKind.IMAGE for region in self.image_regions):
            raise ValueError("image_regions must contain only image candidates")

        source = self.page.source
        for candidate in (
            *self.lines,
            *self.rectangles,
            *self.text_regions,
            *self.image_regions,
        ):
            _validate_bbox_inside(candidate.bbox, source)
        for line in self.lines:
            _validate_point_inside(line.start, source)
            _validate_point_inside(line.end, source)


def _bbox_contains_point(bbox: PixelBoundingBox, point: PixelPoint) -> bool:
    return (
        bbox.x <= point.x < bbox.x + bbox.width
        and bbox.y <= point.y < bbox.y + bbox.height
    )


def _validate_bbox_inside(bbox: PixelBoundingBox, source: PageSource) -> None:
    if (
        bbox.x + bbox.width > source.pixel_width
        or bbox.y + bbox.height > source.pixel_height
    ):
        raise ValueError("candidate bbox must remain inside the source page")


def _validate_point_inside(point: PixelPoint, source: PageSource) -> None:
    if point.x >= source.pixel_width or point.y >= source.pixel_height:
        raise ValueError("line endpoint must remain inside the source page")


class PngDecoder(Protocol):
    """Decode untrusted PNG bytes into one normalized immutable page."""

    def decode(self, data: bytes) -> ImageInput:
        """Return normalized RGB pixels after enforcing configured limits."""


class StructureExtractor(Protocol):
    """Detect visual structure without OCR, IDs, serialization, or rendering."""

    def detect(self, image: ImageInput) -> StructureExtractionResult:
        """Return deterministic candidates expressed in original source pixels."""
