"""Standard-library-only Python model for Document IR v0.1."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Sequence, TypeAlias, TypeVar

from .errors import DocumentIRValidationError, ValidationIssue


IR_VERSION = "0.1.0"
SUPPORTED_IR_VERSIONS = frozenset({IR_VERSION})
GEOMETRY_TOLERANCE_PT = 0.01

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
JSONObject: TypeAlias = Mapping[str, JSONValue]

_EXTENSION_KEY_PATTERN = re.compile(
    r"^[a-z0-9]+(?:[.-][a-z0-9][a-z0-9_-]*)+$"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ASSET_PATH_PATTERN = re.compile(
    r"^assets/sha256-(?P<digest>[0-9a-f]{64})\.(?P<extension>png|jpg|jpeg)$"
)


class Unit(str, Enum):
    """Canonical Document IR coordinate unit."""

    POINT = "pt"


class DpiSource(str, Enum):
    """How the effective source DPI was obtained."""

    DECLARED = "declared"
    INFERRED = "inferred"


class ElementType(str, Enum):
    """Discriminator values for every V1 element."""

    TEXT = "text"
    LINE = "line"
    RECTANGLE = "rectangle"
    IMAGE = "image"


class FontStyle(str, Enum):
    NORMAL = "normal"
    ITALIC = "italic"
    OBLIQUE = "oblique"


class TextAlign(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class LineDash(str, Enum):
    SOLID = "solid"
    DASHED = "dashed"
    DOTTED = "dotted"
    DASH_DOT = "dash-dot"


class ImageFit(str, Enum):
    CONTAIN = "contain"
    COVER = "cover"
    STRETCH = "stretch"


class ProvenanceStage(str, Enum):
    MANUAL = "manual"
    STRUCTURE = "structure"
    OCR = "ocr"
    NORMALIZE = "normalize"
    DERIVED = "derived"


class MediaType(str, Enum):
    PNG = "image/png"
    JPEG = "image/jpeg"


EnumT = TypeVar("EnumT", bound=Enum)


def _coerce_enum(value: Any, enum_type: type[EnumT], field_name: str) -> EnumT:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(repr(item.value) for item in enum_type)
        raise ValueError(f"{field_name} must be one of: {allowed}") from exc


def _require_string(value: Any, field_name: str, *, non_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if non_empty and not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _require_identifier(value: Any, field_name: str) -> str:
    return _require_string(value, field_name)


def _require_integer(
    value: Any,
    field_name: str,
    *,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}")
    return value


def _require_number(
    value: Any,
    field_name: str,
    *,
    minimum: float | None = None,
    exclusive_minimum: bool = False,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field_name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    if minimum is not None:
        invalid = number <= minimum if exclusive_minimum else number < minimum
        if invalid:
            comparator = "greater than" if exclusive_minimum else "at least"
            raise ValueError(f"{field_name} must be {comparator} {minimum:g}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name} must be at most {maximum:g}")
    return number


def _freeze_json(value: Any, field_name: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field_name} object keys must be strings")
            frozen[key] = _freeze_json(item, f"{field_name}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(
            _freeze_json(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )
    raise TypeError(f"{field_name} contains a value that is not JSON-compatible")


def _freeze_json_object(value: Any, field_name: str) -> JSONObject:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return _freeze_json(value, field_name)


def _freeze_extensions(value: Any) -> JSONObject:
    extensions = _freeze_json_object(value, "extensions")
    for key, item in extensions.items():
        if not _EXTENSION_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"extension key {key!r} is not namespaced")
        if not isinstance(item, Mapping):
            raise TypeError(f"extension {key!r} must contain an object")
    return extensions


def _require_color(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    color = _require_string(value, field_name)
    if not re.fullmatch(r"#[0-9a-f]{6}", color):
        raise ValueError(f"{field_name} must be lower-case #rrggbb or null")
    return color


@dataclass(frozen=True, slots=True, kw_only=True)
class Generator:
    name: str
    version: str

    def __post_init__(self) -> None:
        _require_string(self.name, "generator.name")
        _require_string(self.version, "generator.version")


@dataclass(frozen=True, slots=True, kw_only=True)
class Point:
    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _require_number(self.x, "point.x", minimum=0))
        object.__setattr__(self, "y", _require_number(self.y, "point.y", minimum=0))


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _require_number(self.x, "bbox.x", minimum=0))
        object.__setattr__(self, "y", _require_number(self.y, "bbox.y", minimum=0))
        object.__setattr__(
            self,
            "width",
            _require_number(self.width, "bbox.width", minimum=0),
        )
        object.__setattr__(
            self,
            "height",
            _require_number(self.height, "bbox.height", minimum=0),
        )

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def contains_point(
        self,
        point: Point,
        *,
        tolerance: float = GEOMETRY_TOLERANCE_PT,
    ) -> bool:
        return (
            self.x - tolerance <= point.x <= self.right + tolerance
            and self.y - tolerance <= point.y <= self.bottom + tolerance
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PixelBoundingBox:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        _require_integer(self.x, "source_bbox_px.x", minimum=0)
        _require_integer(self.y, "source_bbox_px.y", minimum=0)
        _require_integer(self.width, "source_bbox_px.width", minimum=1)
        _require_integer(self.height, "source_bbox_px.height", minimum=1)


@dataclass(frozen=True, slots=True, kw_only=True)
class PageSize:
    width: float
    height: float
    unit: Unit = Unit.POINT

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "width",
            _require_number(self.width, "page.size.width", minimum=0, exclusive_minimum=True),
        )
        object.__setattr__(
            self,
            "height",
            _require_number(
                self.height,
                "page.size.height",
                minimum=0,
                exclusive_minimum=True,
            ),
        )
        object.__setattr__(self, "unit", _coerce_enum(self.unit, Unit, "page.size.unit"))


@dataclass(frozen=True, slots=True, kw_only=True)
class PageSource:
    pixel_width: int
    pixel_height: int
    dpi_x: float
    dpi_y: float
    dpi_source: DpiSource

    def __post_init__(self) -> None:
        _require_integer(self.pixel_width, "page.source.pixel_width", minimum=1)
        _require_integer(self.pixel_height, "page.source.pixel_height", minimum=1)
        object.__setattr__(
            self,
            "dpi_x",
            _require_number(
                self.dpi_x,
                "page.source.dpi_x",
                minimum=0,
                exclusive_minimum=True,
            ),
        )
        object.__setattr__(
            self,
            "dpi_y",
            _require_number(
                self.dpi_y,
                "page.source.dpi_y",
                minimum=0,
                exclusive_minimum=True,
            ),
        )
        object.__setattr__(
            self,
            "dpi_source",
            _coerce_enum(self.dpi_source, DpiSource, "page.source.dpi_source"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Confidence:
    overall: float
    detection: float | None = None
    recognition: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "overall",
            _require_number(self.overall, "confidence.overall", minimum=0, maximum=1),
        )
        for field_name in ("detection", "recognition"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _require_number(
                        value,
                        f"confidence.{field_name}",
                        minimum=0,
                        maximum=1,
                    ),
                )


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    stage: ProvenanceStage
    provider: str
    provider_version: str
    source_refs: tuple[str, ...] = ()
    source_bbox_px: PixelBoundingBox | None = None
    parameters_digest: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stage",
            _coerce_enum(self.stage, ProvenanceStage, "provenance.stage"),
        )
        _require_string(self.provider, "provenance.provider")
        _require_string(self.provider_version, "provenance.provider_version")
        if isinstance(self.source_refs, (str, bytes, bytearray)):
            raise TypeError("provenance.source_refs must be a sequence of strings")
        refs = tuple(self.source_refs)
        for reference in refs:
            _require_identifier(reference, "provenance.source_refs item")
        if len(refs) != len(set(refs)):
            raise ValueError("provenance.source_refs must not contain duplicates")
        object.__setattr__(self, "source_refs", refs)
        if self.source_bbox_px is not None and not isinstance(
            self.source_bbox_px, PixelBoundingBox
        ):
            raise TypeError("provenance.source_bbox_px must be a PixelBoundingBox")
        if self.parameters_digest is not None and not _SHA256_PATTERN.fullmatch(
            self.parameters_digest
        ):
            raise ValueError("provenance.parameters_digest must be 64 lower-case hex digits")
        if self.notes is not None:
            _require_string(self.notes, "provenance.notes")


@dataclass(frozen=True, slots=True, kw_only=True)
class TextStyle:
    font_family: str
    font_size_pt: float
    font_weight: int
    font_style: FontStyle
    color: str | None
    align: TextAlign
    line_height: float
    rotation_deg: float
    opacity: float = 1.0

    def __post_init__(self) -> None:
        _require_string(self.font_family, "text.style.font_family")
        object.__setattr__(
            self,
            "font_size_pt",
            _require_number(
                self.font_size_pt,
                "text.style.font_size_pt",
                minimum=0,
                exclusive_minimum=True,
            ),
        )
        _require_integer(self.font_weight, "text.style.font_weight", minimum=1)
        if self.font_weight > 1000:
            raise ValueError("text.style.font_weight must be at most 1000")
        object.__setattr__(
            self,
            "font_style",
            _coerce_enum(self.font_style, FontStyle, "text.style.font_style"),
        )
        object.__setattr__(self, "color", _require_color(self.color, "text.style.color"))
        object.__setattr__(
            self,
            "align",
            _coerce_enum(self.align, TextAlign, "text.style.align"),
        )
        object.__setattr__(
            self,
            "line_height",
            _require_number(
                self.line_height,
                "text.style.line_height",
                minimum=0,
                exclusive_minimum=True,
            ),
        )
        object.__setattr__(
            self,
            "rotation_deg",
            _require_number(self.rotation_deg, "text.style.rotation_deg"),
        )
        object.__setattr__(
            self,
            "opacity",
            _require_number(self.opacity, "text.style.opacity", minimum=0, maximum=1),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class LineStyle:
    width_pt: float
    color: str | None
    dash: LineDash
    opacity: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "width_pt",
            _require_number(
                self.width_pt,
                "line.style.width_pt",
                minimum=0,
                exclusive_minimum=True,
            ),
        )
        object.__setattr__(self, "color", _require_color(self.color, "line.style.color"))
        object.__setattr__(
            self,
            "dash",
            _coerce_enum(self.dash, LineDash, "line.style.dash"),
        )
        object.__setattr__(
            self,
            "opacity",
            _require_number(self.opacity, "line.style.opacity", minimum=0, maximum=1),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RectangleStyle:
    stroke_color: str | None
    stroke_width_pt: float
    fill_color: str | None
    corner_radius_pt: float
    opacity: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stroke_color",
            _require_color(self.stroke_color, "rectangle.style.stroke_color"),
        )
        object.__setattr__(
            self,
            "stroke_width_pt",
            _require_number(
                self.stroke_width_pt,
                "rectangle.style.stroke_width_pt",
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "fill_color",
            _require_color(self.fill_color, "rectangle.style.fill_color"),
        )
        object.__setattr__(
            self,
            "corner_radius_pt",
            _require_number(
                self.corner_radius_pt,
                "rectangle.style.corner_radius_pt",
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "opacity",
            _require_number(
                self.opacity,
                "rectangle.style.opacity",
                minimum=0,
                maximum=1,
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Asset:
    id: str
    path: str
    media_type: MediaType
    sha256: str
    pixel_width: int
    pixel_height: int
    dpi_x: float | None = None
    dpi_y: float | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.id, "asset.id")
        path = _require_string(self.path, "asset.path")
        if "\\" in path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise ValueError("asset.path must be a safe bundle-relative POSIX path")
        path_match = _ASSET_PATH_PATTERN.fullmatch(path)
        if path_match is None:
            raise ValueError(
                "asset.path must use assets/sha256-<lower-case digest>.<png|jpg|jpeg>"
            )
        media_type = _coerce_enum(self.media_type, MediaType, "asset.media_type")
        object.__setattr__(self, "media_type", media_type)
        if not _SHA256_PATTERN.fullmatch(self.sha256):
            raise ValueError("asset.sha256 must be 64 lower-case hex digits")
        if path_match.group("digest") != self.sha256:
            raise ValueError("asset.path digest must match asset.sha256")
        extension = path_match.group("extension")
        if media_type is MediaType.PNG and extension != "png":
            raise ValueError("image/png assets must use the .png extension")
        if media_type is MediaType.JPEG and extension not in {"jpg", "jpeg"}:
            raise ValueError("image/jpeg assets must use .jpg or .jpeg")
        _require_integer(self.pixel_width, "asset.pixel_width", minimum=1)
        _require_integer(self.pixel_height, "asset.pixel_height", minimum=1)
        for field_name in ("dpi_x", "dpi_y"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _require_number(
                        value,
                        f"asset.{field_name}",
                        minimum=0,
                        exclusive_minimum=True,
                    ),
                )


@dataclass(frozen=True, slots=True, kw_only=True)
class Element:
    """Common immutable fields shared by all discriminated element classes."""

    id: str
    bbox: BoundingBox
    z_index: int
    confidence: Confidence | None
    provenance: tuple[Provenance, ...]
    extensions: JSONObject = field(default_factory=dict)

    element_type: ClassVar[ElementType]

    def __post_init__(self) -> None:
        if self.__class__ is Element:
            raise TypeError(
                "Element is abstract; construct TextElement, LineElement, "
                "RectangleElement, or ImageElement"
            )
        _require_identifier(self.id, "element.id")
        if not isinstance(self.bbox, BoundingBox):
            raise TypeError("element.bbox must be a BoundingBox")
        _require_integer(self.z_index, "element.z_index")
        if self.confidence is not None and not isinstance(self.confidence, Confidence):
            raise TypeError("element.confidence must be Confidence or null")
        provenance = tuple(self.provenance)
        if not provenance:
            raise ValueError("element.provenance must contain at least one record")
        if not all(isinstance(record, Provenance) for record in provenance):
            raise TypeError("element.provenance entries must be Provenance records")
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "extensions", _freeze_extensions(self.extensions))

    @property
    def type(self) -> ElementType:
        return self.element_type


@dataclass(frozen=True, slots=True, kw_only=True)
class TextElement(Element):
    text: str
    reading_order: int
    style: TextStyle

    element_type: ClassVar[ElementType] = ElementType.TEXT

    def __post_init__(self) -> None:
        Element.__post_init__(self)
        _require_string(self.text, "text.text", non_empty=False)
        _require_integer(self.reading_order, "text.reading_order", minimum=0)
        if not isinstance(self.style, TextStyle):
            raise TypeError("text.style must be a TextStyle")
        if self.bbox.width <= 0 or self.bbox.height <= 0:
            raise ValueError("text.bbox width and height must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class LineElement(Element):
    start: Point
    end: Point
    style: LineStyle

    element_type: ClassVar[ElementType] = ElementType.LINE

    def __post_init__(self) -> None:
        Element.__post_init__(self)
        if not isinstance(self.start, Point) or not isinstance(self.end, Point):
            raise TypeError("line.start and line.end must be Point values")
        if not isinstance(self.style, LineStyle):
            raise TypeError("line.style must be a LineStyle")
        if self.bbox.width == 0 and self.bbox.height == 0:
            raise ValueError("line.bbox width and height must not both be zero")
        if self.start == self.end:
            raise ValueError("line.start and line.end must differ")
        if not self.bbox.contains_point(self.start) or not self.bbox.contains_point(self.end):
            raise ValueError("line.bbox must enclose both endpoints")


@dataclass(frozen=True, slots=True, kw_only=True)
class RectangleElement(Element):
    style: RectangleStyle

    element_type: ClassVar[ElementType] = ElementType.RECTANGLE

    def __post_init__(self) -> None:
        Element.__post_init__(self)
        if not isinstance(self.style, RectangleStyle):
            raise TypeError("rectangle.style must be a RectangleStyle")
        if self.bbox.width <= 0 or self.bbox.height <= 0:
            raise ValueError("rectangle.bbox width and height must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageElement(Element):
    asset_id: str
    fit: ImageFit = ImageFit.CONTAIN
    alt_text: str | None = None

    element_type: ClassVar[ElementType] = ElementType.IMAGE

    def __post_init__(self) -> None:
        Element.__post_init__(self)
        _require_identifier(self.asset_id, "image.asset_id")
        object.__setattr__(self, "fit", _coerce_enum(self.fit, ImageFit, "image.fit"))
        if self.alt_text is not None:
            _require_string(self.alt_text, "image.alt_text", non_empty=False)
        if self.bbox.width <= 0 or self.bbox.height <= 0:
            raise ValueError("image.bbox width and height must be positive")


DocumentElement: TypeAlias = TextElement | LineElement | RectangleElement | ImageElement


@dataclass(frozen=True, slots=True, kw_only=True)
class Page:
    id: str
    number: int
    size: PageSize
    elements: tuple[DocumentElement, ...]
    source: PageSource | None = None
    extensions: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.id, "page.id")
        _require_integer(self.number, "page.number", minimum=1)
        if not isinstance(self.size, PageSize):
            raise TypeError("page.size must be a PageSize")
        if self.source is not None and not isinstance(self.source, PageSource):
            raise TypeError("page.source must be PageSource or null")
        elements = tuple(self.elements)
        if not all(isinstance(element, Element) for element in elements):
            raise TypeError("page.elements entries must be Document IR elements")
        object.__setattr__(self, "elements", elements)
        object.__setattr__(self, "extensions", _freeze_extensions(self.extensions))


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentIR:
    ir_version: str
    document_id: str
    generator: Generator
    pages: tuple[Page, ...]
    assets: tuple[Asset, ...]
    metadata: JSONObject = field(default_factory=dict)
    extensions: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_string(self.ir_version, "ir_version")
        _require_identifier(self.document_id, "document_id")
        if not isinstance(self.generator, Generator):
            raise TypeError("generator must be a Generator")
        pages = tuple(self.pages)
        assets = tuple(self.assets)
        if not pages:
            raise ValueError("pages must contain at least one page")
        if not all(isinstance(page, Page) for page in pages):
            raise TypeError("pages entries must be Page values")
        if not all(isinstance(asset, Asset) for asset in assets):
            raise TypeError("assets entries must be Asset values")
        object.__setattr__(self, "pages", pages)
        object.__setattr__(self, "assets", assets)
        object.__setattr__(self, "metadata", _freeze_json_object(self.metadata, "metadata"))
        object.__setattr__(self, "extensions", _freeze_extensions(self.extensions))
        validate_document(self)

    def to_dict(self) -> dict[str, JSONValue]:
        """Serialize this document into plain JSON-compatible Python values."""

        from .codec import document_to_dict

        return document_to_dict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize this document as deterministic UTF-8 JSON text."""

        from .codec import document_to_json

        return document_to_json(self, indent=indent)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DocumentIR:
        """Parse and semantically validate a Document IR object."""

        from .codec import document_from_dict

        return document_from_dict(data)

    @classmethod
    def from_json(cls, text: str | bytes | bytearray) -> DocumentIR:
        """Parse and semantically validate Document IR JSON text."""

        from .codec import document_from_json

        return document_from_json(text)


def validate_document(document: DocumentIR) -> None:
    """Validate cross-object invariants that JSON Schema cannot express."""

    issues: list[ValidationIssue] = []

    if document.ir_version not in SUPPORTED_IR_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_IR_VERSIONS))
        issues.append(
            ValidationIssue(
                path="$.ir_version",
                message=(
                    f"unsupported IR version {document.ir_version!r}; "
                    f"supported version: {supported}"
                ),
                code="unsupported_version",
            )
        )

    page_numbers = [page.number for page in document.pages]
    expected_page_numbers = list(range(1, len(document.pages) + 1))
    if page_numbers != expected_page_numbers:
        issues.append(
            ValidationIssue(
                path="$.pages",
                message=(
                    "page numbers must be contiguous, one-based, and match array order; "
                    f"expected {expected_page_numbers}, received {page_numbers}"
                ),
                code="invalid_page_order",
            )
        )

    seen_ids: dict[str, str] = {document.document_id: "$.document_id"}
    asset_ids: set[str] = set()
    asset_paths: dict[str, str] = {}

    def register_id(identifier: str, path: str) -> None:
        previous = seen_ids.get(identifier)
        if previous is not None:
            issues.append(
                ValidationIssue(
                    path=path,
                    message=f"ID {identifier!r} duplicates {previous}",
                    code="duplicate_id",
                )
            )
        else:
            seen_ids[identifier] = path

    for asset_index, asset in enumerate(document.assets):
        path = f"$.assets[{asset_index}].id"
        register_id(asset.id, path)
        asset_ids.add(asset.id)
        previous_asset_path = asset_paths.get(asset.path)
        if previous_asset_path is not None:
            issues.append(
                ValidationIssue(
                    path=f"$.assets[{asset_index}].path",
                    message=f"asset path duplicates {previous_asset_path}",
                    code="duplicate_asset_path",
                )
            )
        else:
            asset_paths[asset.path] = f"$.assets[{asset_index}].path"

    for page_index, page in enumerate(document.pages):
        page_path = f"$.pages[{page_index}]"
        register_id(page.id, f"{page_path}.id")

        text_orders: list[int] = []
        for element_index, element in enumerate(page.elements):
            element_path = f"{page_path}.elements[{element_index}]"
            register_id(element.id, f"{element_path}.id")

            if (
                element.bbox.x < -GEOMETRY_TOLERANCE_PT
                or element.bbox.y < -GEOMETRY_TOLERANCE_PT
                or element.bbox.right > page.size.width + GEOMETRY_TOLERANCE_PT
                or element.bbox.bottom > page.size.height + GEOMETRY_TOLERANCE_PT
            ):
                issues.append(
                    ValidationIssue(
                        path=f"{element_path}.bbox",
                        message=(
                            "bounding box must remain within page bounds "
                            f"{page.size.width:g} x {page.size.height:g} pt "
                            f"(tolerance {GEOMETRY_TOLERANCE_PT:g} pt)"
                        ),
                        code="out_of_page_geometry",
                    )
                )

            if isinstance(element, TextElement):
                text_orders.append(element.reading_order)
            elif isinstance(element, ImageElement) and element.asset_id not in asset_ids:
                issues.append(
                    ValidationIssue(
                        path=f"{element_path}.asset_id",
                        message=f"asset {element.asset_id!r} is not registered in $.assets",
                        code="unknown_asset",
                    )
                )

        expected_text_orders = list(range(len(text_orders)))
        if text_orders != expected_text_orders:
            issues.append(
                ValidationIssue(
                    path=f"{page_path}.elements",
                    message=(
                        "text reading_order values must be unique, contiguous, zero-based, "
                        f"and follow array order; expected {expected_text_orders}, "
                        f"received {text_orders}"
                    ),
                    code="invalid_reading_order",
                )
            )

        topology_key = "jp.reactorfront.aiteqno.table_topology"
        topology_value = page.extensions.get(topology_key)
        if topology_value is not None:
            # Imported lazily so the extension contract may reuse core geometry
            # types without creating a module-import cycle.
            from .table_topology import validate_table_topology_extension

            issues.extend(
                validate_table_topology_extension(
                    page,
                    topology_value,
                    path=f'{page_path}.extensions["{topology_key}"]',
                )
            )

    if issues:
        raise DocumentIRValidationError(issues)
