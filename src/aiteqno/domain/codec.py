"""JSON serialization and parsing for the Document IR domain model."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from typing import Any, TypeVar

from .errors import DocumentIRValidationError
from .model import (
    Asset,
    BoundingBox,
    Confidence,
    DocumentElement,
    DocumentIR,
    Generator,
    ImageElement,
    LineElement,
    LineStyle,
    Page,
    PageSize,
    PageSource,
    PixelBoundingBox,
    Point,
    Provenance,
    RectangleElement,
    RectangleStyle,
    TextElement,
    TextStyle,
)


T = TypeVar("T")


def _fail(path: str, message: str, code: str = "invalid_structure") -> None:
    raise DocumentIRValidationError.single(path, message, code)


def _as_object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "expected an object", "invalid_type")
    for key in value:
        if not isinstance(key, str):
            _fail(path, "object keys must be strings", "invalid_type")
    return value


def _as_array(value: Any, path: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _fail(path, "expected an array", "invalid_type")
    return value


def _check_fields(
    value: Mapping[str, Any],
    path: str,
    *,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    missing = sorted(required - value.keys())
    if missing:
        _fail(
            path,
            "missing required field(s): " + ", ".join(missing),
            "missing_field",
        )
    unknown = sorted(value.keys() - required - optional)
    if unknown:
        _fail(
            path,
            "unknown field(s): " + ", ".join(unknown),
            "unknown_field",
        )


def _construct(factory: Callable[..., T], object_path: str, **values: Any) -> T:
    try:
        return factory(**values)
    except DocumentIRValidationError:
        raise
    except (TypeError, ValueError) as exc:
        _fail(object_path, str(exc), "invalid_value")


def _parse_point(value: Any, path: str) -> Point:
    obj = _as_object(value, path)
    _check_fields(obj, path, required={"x", "y"})
    return _construct(Point, path, x=obj["x"], y=obj["y"])


def _parse_bbox(value: Any, path: str) -> BoundingBox:
    obj = _as_object(value, path)
    _check_fields(obj, path, required={"x", "y", "width", "height"})
    return _construct(
        BoundingBox,
        path,
        x=obj["x"],
        y=obj["y"],
        width=obj["width"],
        height=obj["height"],
    )


def _parse_pixel_bbox(value: Any, path: str) -> PixelBoundingBox:
    obj = _as_object(value, path)
    _check_fields(obj, path, required={"x", "y", "width", "height"})
    return _construct(
        PixelBoundingBox,
        path,
        x=obj["x"],
        y=obj["y"],
        width=obj["width"],
        height=obj["height"],
    )


def _parse_generator(value: Any, path: str) -> Generator:
    obj = _as_object(value, path)
    _check_fields(obj, path, required={"name", "version"})
    return _construct(Generator, path, name=obj["name"], version=obj["version"])


def _parse_page_size(value: Any, path: str) -> PageSize:
    obj = _as_object(value, path)
    _check_fields(obj, path, required={"width", "height", "unit"})
    return _construct(
        PageSize,
        path,
        width=obj["width"],
        height=obj["height"],
        unit=obj["unit"],
    )


def _parse_page_source(value: Any, path: str) -> PageSource:
    obj = _as_object(value, path)
    _check_fields(
        obj,
        path,
        required={"pixel_width", "pixel_height", "dpi_x", "dpi_y", "dpi_source"},
    )
    return _construct(
        PageSource,
        path,
        pixel_width=obj["pixel_width"],
        pixel_height=obj["pixel_height"],
        dpi_x=obj["dpi_x"],
        dpi_y=obj["dpi_y"],
        dpi_source=obj["dpi_source"],
    )


def _parse_confidence(value: Any, path: str) -> Confidence | None:
    if value is None:
        return None
    obj = _as_object(value, path)
    _check_fields(
        obj,
        path,
        required={"overall"},
        optional={"detection", "recognition"},
    )
    return _construct(
        Confidence,
        path,
        overall=obj["overall"],
        detection=obj.get("detection"),
        recognition=obj.get("recognition"),
    )


def _parse_provenance(value: Any, path: str) -> Provenance:
    obj = _as_object(value, path)
    _check_fields(
        obj,
        path,
        required={"stage", "provider", "provider_version", "source_refs"},
        optional={"source_bbox_px", "parameters_digest", "notes"},
    )
    refs = _as_array(obj["source_refs"], f"{path}.source_refs")
    source_bbox = None
    if "source_bbox_px" in obj:
        source_bbox = _parse_pixel_bbox(obj["source_bbox_px"], f"{path}.source_bbox_px")
    return _construct(
        Provenance,
        path,
        stage=obj["stage"],
        provider=obj["provider"],
        provider_version=obj["provider_version"],
        source_refs=tuple(refs),
        source_bbox_px=source_bbox,
        parameters_digest=obj.get("parameters_digest"),
        notes=obj.get("notes"),
    )


def _parse_provenance_array(value: Any, path: str) -> tuple[Provenance, ...]:
    items = _as_array(value, path)
    return tuple(
        _parse_provenance(item, f"{path}[{index}]")
        for index, item in enumerate(items)
    )


def _parse_text_style(value: Any, path: str) -> TextStyle:
    obj = _as_object(value, path)
    _check_fields(
        obj,
        path,
        required={
            "font_family",
            "font_size_pt",
            "font_weight",
            "font_style",
            "color",
            "align",
            "line_height",
            "rotation_deg",
        },
        optional={"opacity"},
    )
    return _construct(
        TextStyle,
        path,
        font_family=obj["font_family"],
        font_size_pt=obj["font_size_pt"],
        font_weight=obj["font_weight"],
        font_style=obj["font_style"],
        color=obj["color"],
        align=obj["align"],
        line_height=obj["line_height"],
        rotation_deg=obj["rotation_deg"],
        opacity=obj.get("opacity", 1.0),
    )


def _parse_line_style(value: Any, path: str) -> LineStyle:
    obj = _as_object(value, path)
    _check_fields(
        obj,
        path,
        required={"width_pt", "color", "dash"},
        optional={"opacity"},
    )
    return _construct(
        LineStyle,
        path,
        width_pt=obj["width_pt"],
        color=obj["color"],
        dash=obj["dash"],
        opacity=obj.get("opacity", 1.0),
    )


def _parse_rectangle_style(value: Any, path: str) -> RectangleStyle:
    obj = _as_object(value, path)
    _check_fields(
        obj,
        path,
        required={
            "stroke_color",
            "stroke_width_pt",
            "fill_color",
            "corner_radius_pt",
        },
        optional={"opacity"},
    )
    return _construct(
        RectangleStyle,
        path,
        stroke_color=obj["stroke_color"],
        stroke_width_pt=obj["stroke_width_pt"],
        fill_color=obj["fill_color"],
        corner_radius_pt=obj["corner_radius_pt"],
        opacity=obj.get("opacity", 1.0),
    )


_COMMON_ELEMENT_FIELDS = {
    "id",
    "type",
    "bbox",
    "z_index",
    "confidence",
    "provenance",
}


def _common_element_values(obj: Mapping[str, Any], path: str) -> dict[str, Any]:
    return {
        "id": obj["id"],
        "bbox": _parse_bbox(obj["bbox"], f"{path}.bbox"),
        "z_index": obj["z_index"],
        "confidence": _parse_confidence(obj["confidence"], f"{path}.confidence"),
        "provenance": _parse_provenance_array(
            obj["provenance"], f"{path}.provenance"
        ),
        "extensions": obj.get("extensions", {}),
    }


def _parse_element(value: Any, path: str) -> DocumentElement:
    obj = _as_object(value, path)
    element_type = obj.get("type")
    if not isinstance(element_type, str):
        _fail(f"{path}.type", "element discriminator must be a string", "invalid_type")

    common_optional = {"extensions"}
    if element_type == "text":
        _check_fields(
            obj,
            path,
            required=_COMMON_ELEMENT_FIELDS | {"text", "reading_order", "style"},
            optional=common_optional,
        )
        return _construct(
            TextElement,
            path,
            **_common_element_values(obj, path),
            text=obj["text"],
            reading_order=obj["reading_order"],
            style=_parse_text_style(obj["style"], f"{path}.style"),
        )
    if element_type == "line":
        _check_fields(
            obj,
            path,
            required=_COMMON_ELEMENT_FIELDS | {"start", "end", "style"},
            optional=common_optional,
        )
        return _construct(
            LineElement,
            path,
            **_common_element_values(obj, path),
            start=_parse_point(obj["start"], f"{path}.start"),
            end=_parse_point(obj["end"], f"{path}.end"),
            style=_parse_line_style(obj["style"], f"{path}.style"),
        )
    if element_type == "rectangle":
        _check_fields(
            obj,
            path,
            required=_COMMON_ELEMENT_FIELDS | {"style"},
            optional=common_optional,
        )
        return _construct(
            RectangleElement,
            path,
            **_common_element_values(obj, path),
            style=_parse_rectangle_style(obj["style"], f"{path}.style"),
        )
    if element_type == "image":
        _check_fields(
            obj,
            path,
            required=_COMMON_ELEMENT_FIELDS | {"asset_id", "fit"},
            optional=common_optional | {"alt_text"},
        )
        return _construct(
            ImageElement,
            path,
            **_common_element_values(obj, path),
            asset_id=obj["asset_id"],
            fit=obj["fit"],
            alt_text=obj.get("alt_text"),
        )

    _fail(
        f"{path}.type",
        f"unsupported element discriminator {element_type!r}; expected text, line, rectangle, or image",
        "invalid_discriminator",
    )


def _parse_page(value: Any, path: str) -> Page:
    obj = _as_object(value, path)
    _check_fields(
        obj,
        path,
        required={"id", "number", "size", "elements"},
        optional={"source", "extensions"},
    )
    elements = _as_array(obj["elements"], f"{path}.elements")
    source = None
    if "source" in obj:
        source = _parse_page_source(obj["source"], f"{path}.source")
    return _construct(
        Page,
        path,
        id=obj["id"],
        number=obj["number"],
        size=_parse_page_size(obj["size"], f"{path}.size"),
        source=source,
        elements=tuple(
            _parse_element(element, f"{path}.elements[{index}]")
            for index, element in enumerate(elements)
        ),
        extensions=obj.get("extensions", {}),
    )


def _parse_asset(value: Any, path: str) -> Asset:
    obj = _as_object(value, path)
    _check_fields(
        obj,
        path,
        required={
            "id",
            "path",
            "media_type",
            "sha256",
            "pixel_width",
            "pixel_height",
        },
        optional={"dpi_x", "dpi_y"},
    )
    return _construct(
        Asset,
        path,
        id=obj["id"],
        path=obj["path"],
        media_type=obj["media_type"],
        sha256=obj["sha256"],
        pixel_width=obj["pixel_width"],
        pixel_height=obj["pixel_height"],
        dpi_x=obj.get("dpi_x"),
        dpi_y=obj.get("dpi_y"),
    )


def document_from_dict(data: Mapping[str, Any]) -> DocumentIR:
    """Parse plain Python data and enforce all Document IR invariants."""

    obj = _as_object(data, "$")
    _check_fields(
        obj,
        "$",
        required={"ir_version", "document_id", "generator", "pages", "assets"},
        optional={"metadata", "extensions"},
    )
    pages = _as_array(obj["pages"], "$.pages")
    assets = _as_array(obj["assets"], "$.assets")
    return _construct(
        DocumentIR,
        "$",
        ir_version=obj["ir_version"],
        document_id=obj["document_id"],
        generator=_parse_generator(obj["generator"], "$.generator"),
        pages=tuple(
            _parse_page(page, f"$.pages[{index}]")
            for index, page in enumerate(pages)
        ),
        assets=tuple(
            _parse_asset(asset, f"$.assets[{index}]")
            for index, asset in enumerate(assets)
        ),
        metadata=obj.get("metadata", {}),
        extensions=obj.get("extensions", {}),
    )


def _reject_json_constant(value: str) -> None:
    _fail("$", f"non-finite JSON number {value!r} is forbidden", "invalid_json")


def document_from_json(text: str | bytes | bytearray) -> DocumentIR:
    """Parse UTF-8 JSON text and enforce all Document IR invariants."""

    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError as exc:
            _fail(
                "$",
                f"Document IR must be UTF-8: byte {exc.start} is invalid",
                "invalid_json",
            )
    if not isinstance(text, str):
        _fail("$", "JSON input must be str, bytes, or bytearray", "invalid_type")
    try:
        data = json.loads(text, parse_constant=_reject_json_constant)
    except DocumentIRValidationError:
        raise
    except json.JSONDecodeError as exc:
        _fail(
            "$",
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            "invalid_json",
        )
    return document_from_dict(data)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _point_to_dict(point: Point) -> dict[str, Any]:
    return {"x": point.x, "y": point.y}


def _bbox_to_dict(bbox: BoundingBox | PixelBoundingBox) -> dict[str, Any]:
    return {
        "x": bbox.x,
        "y": bbox.y,
        "width": bbox.width,
        "height": bbox.height,
    }


def _confidence_to_dict(confidence: Confidence | None) -> dict[str, Any] | None:
    if confidence is None:
        return None
    result: dict[str, Any] = {"overall": confidence.overall}
    if confidence.detection is not None:
        result["detection"] = confidence.detection
    if confidence.recognition is not None:
        result["recognition"] = confidence.recognition
    return result


def _provenance_to_dict(provenance: Provenance) -> dict[str, Any]:
    result: dict[str, Any] = {
        "stage": provenance.stage.value,
        "provider": provenance.provider,
        "provider_version": provenance.provider_version,
        "source_refs": list(provenance.source_refs),
    }
    if provenance.source_bbox_px is not None:
        result["source_bbox_px"] = _bbox_to_dict(provenance.source_bbox_px)
    if provenance.parameters_digest is not None:
        result["parameters_digest"] = provenance.parameters_digest
    if provenance.notes is not None:
        result["notes"] = provenance.notes
    return result


def _common_element_to_dict(element: DocumentElement) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": element.id,
        "type": element.type.value,
        "bbox": _bbox_to_dict(element.bbox),
        "z_index": element.z_index,
        "confidence": _confidence_to_dict(element.confidence),
        "provenance": [
            _provenance_to_dict(record) for record in element.provenance
        ],
    }
    if element.extensions:
        result["extensions"] = _thaw_json(element.extensions)
    return result


def _element_to_dict(element: DocumentElement) -> dict[str, Any]:
    result = _common_element_to_dict(element)
    if isinstance(element, TextElement):
        result.update(
            {
                "text": element.text,
                "reading_order": element.reading_order,
                "style": {
                    "font_family": element.style.font_family,
                    "font_size_pt": element.style.font_size_pt,
                    "font_weight": element.style.font_weight,
                    "font_style": element.style.font_style.value,
                    "color": element.style.color,
                    "align": element.style.align.value,
                    "line_height": element.style.line_height,
                    "rotation_deg": element.style.rotation_deg,
                    "opacity": element.style.opacity,
                },
            }
        )
    elif isinstance(element, LineElement):
        result.update(
            {
                "start": _point_to_dict(element.start),
                "end": _point_to_dict(element.end),
                "style": {
                    "width_pt": element.style.width_pt,
                    "color": element.style.color,
                    "dash": element.style.dash.value,
                    "opacity": element.style.opacity,
                },
            }
        )
    elif isinstance(element, RectangleElement):
        result["style"] = {
            "stroke_color": element.style.stroke_color,
            "stroke_width_pt": element.style.stroke_width_pt,
            "fill_color": element.style.fill_color,
            "corner_radius_pt": element.style.corner_radius_pt,
            "opacity": element.style.opacity,
        }
    elif isinstance(element, ImageElement):
        result.update({"asset_id": element.asset_id, "fit": element.fit.value})
        if element.alt_text is not None:
            result["alt_text"] = element.alt_text
    else:
        raise TypeError(f"unsupported Document IR element: {type(element).__name__}")
    return result


def _page_to_dict(page: Page) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": page.id,
        "number": page.number,
        "size": {
            "width": page.size.width,
            "height": page.size.height,
            "unit": page.size.unit.value,
        },
        "elements": [_element_to_dict(element) for element in page.elements],
    }
    if page.source is not None:
        result["source"] = {
            "pixel_width": page.source.pixel_width,
            "pixel_height": page.source.pixel_height,
            "dpi_x": page.source.dpi_x,
            "dpi_y": page.source.dpi_y,
            "dpi_source": page.source.dpi_source.value,
        }
    if page.extensions:
        result["extensions"] = _thaw_json(page.extensions)
    return result


def _asset_to_dict(asset: Asset) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": asset.id,
        "path": asset.path,
        "media_type": asset.media_type.value,
        "sha256": asset.sha256,
        "pixel_width": asset.pixel_width,
        "pixel_height": asset.pixel_height,
    }
    if asset.dpi_x is not None:
        result["dpi_x"] = asset.dpi_x
    if asset.dpi_y is not None:
        result["dpi_y"] = asset.dpi_y
    return result


def document_to_dict(document: DocumentIR) -> dict[str, Any]:
    """Serialize a validated model into plain JSON-compatible values."""

    result: dict[str, Any] = {
        "ir_version": document.ir_version,
        "document_id": document.document_id,
        "generator": {
            "name": document.generator.name,
            "version": document.generator.version,
        },
        "pages": [_page_to_dict(page) for page in document.pages],
        "assets": [_asset_to_dict(asset) for asset in document.assets],
    }
    if document.metadata:
        result["metadata"] = _thaw_json(document.metadata)
    if document.extensions:
        result["extensions"] = _thaw_json(document.extensions)
    return result


def document_to_json(document: DocumentIR, *, indent: int | None = 2) -> str:
    """Serialize a validated model as deterministic UTF-8 JSON text."""

    serialized = json.dumps(
        document_to_dict(document),
        ensure_ascii=False,
        allow_nan=False,
        indent=indent,
        separators=None if indent is not None else (",", ":"),
    )
    return serialized + "\n"
