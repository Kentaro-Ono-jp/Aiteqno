"""Application orchestration from one PNG to a validated Document IR bundle."""

from __future__ import annotations

import re
from dataclasses import dataclass
from os import PathLike
from typing import Sequence

from aiteqno import __version__
from aiteqno.domain import (
    IR_VERSION,
    Asset,
    BoundingBox,
    Confidence,
    DocumentIR,
    DocumentIRValidationError,
    FontStyle,
    Generator,
    ImageElement,
    ImageFit,
    LineDash,
    LineElement,
    LineStyle,
    Page,
    PageSize,
    PixelBoundingBox,
    Point,
    RectangleElement,
    RectangleStyle,
    TextAlign,
    TextElement,
    TextStyle,
)
from aiteqno.ports.extraction import (
    AssetEncodingError,
    AssetPayload,
    BundleWriteError,
    BundleWriteResult,
    DocumentBundleWriter,
    DocumentIRSchemaError,
    DocumentIRValidator,
    EncodedImageAsset,
    ImageAssetEncoder,
)
from aiteqno.ports.ocr import (
    DEFAULT_OCR_LANGUAGES,
    OcrBackend,
    OcrBackendError,
    OcrOptions,
    OcrRegion,
    OcrToken,
    normalize_ocr_languages,
)
from aiteqno.ports.structure import (
    ImageInput,
    LineCandidate,
    LineOrientation,
    PngDecoder,
    RectangleCandidate,
    RegionCandidate,
    StructureExtractionError,
    StructureExtractionResult,
    StructureExtractor,
)


EXTRACTION_PROVIDER = "aiteqno.png-extraction"
EXTRACTION_PROVIDER_VERSION = "1.0"
PAGE_COVERING_IMAGE_FRACTION = 0.85

_DIAGNOSTIC_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_EXTRACTION_STAGES = frozenset(
    {"decode", "structure", "ocr", "asset", "assemble", "validate", "write"}
)
_TEXT_Z_INDEX = 30
_LINE_Z_INDEX = 20
_IMAGE_Z_INDEX = 10
_RECTANGLE_Z_INDEX = 0


class PngExtractionError(RuntimeError):
    """An actionable fatal error from one extraction pipeline stage."""

    def __init__(self, code: str, stage: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractionDiagnostic:
    """A non-fatal omission or normalization decision without document text."""

    code: str
    stage: str
    message: str
    source_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _DIAGNOSTIC_CODE_PATTERN.fullmatch(
            self.code
        ):
            raise ValueError("diagnostic code must be lower-case snake_case")
        if self.stage not in _EXTRACTION_STAGES:
            raise ValueError("diagnostic stage is not an extraction pipeline stage")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("diagnostic message must not be empty")
        if self.source_ref is not None and (
            not isinstance(self.source_ref, str) or not self.source_ref.strip()
        ):
            raise ValueError("diagnostic source_ref must be null or non-empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class PngExtractionResult:
    """The validated semantic model, published paths, and non-fatal diagnostics."""

    document: DocumentIR
    bundle: BundleWriteResult
    diagnostics: tuple[ExtractionDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.document, DocumentIR):
            raise TypeError("extraction result document must be a DocumentIR")
        if not isinstance(self.bundle, BundleWriteResult):
            raise TypeError("extraction result bundle must be a BundleWriteResult")
        if isinstance(self.diagnostics, (str, bytes, bytearray)):
            raise TypeError("extraction diagnostics must be a sequence")
        diagnostics = tuple(self.diagnostics)
        if any(not isinstance(item, ExtractionDiagnostic) for item in diagnostics):
            raise TypeError("extraction diagnostics contain an invalid value")
        object.__setattr__(self, "diagnostics", diagnostics)


@dataclass(frozen=True, slots=True)
class _AssociatedToken:
    token: OcrToken
    region_ref: str | None
    region: RegionCandidate | None


@dataclass(slots=True)
class _TokenRow:
    items: list[_AssociatedToken]
    top: int
    bottom: int
    left: int


def extract_png(
    png_data: bytes,
    output_directory: str | PathLike[str],
    *,
    decoder: PngDecoder,
    structure_extractor: StructureExtractor,
    ocr_backend: OcrBackend,
    asset_encoder: ImageAssetEncoder,
    validator: DocumentIRValidator,
    bundle_writer: DocumentBundleWriter,
    languages: Sequence[str] = DEFAULT_OCR_LANGUAGES,
    ocr_options: OcrOptions = OcrOptions(),
) -> PngExtractionResult:
    """Extract, schema-validate, and atomically publish one PNG document bundle."""

    if not isinstance(png_data, bytes):
        raise TypeError("png_data must be immutable bytes")
    normalized_languages = normalize_ocr_languages(languages)
    if not isinstance(ocr_options, OcrOptions):
        raise TypeError("ocr_options must be an OcrOptions")
    diagnostics: list[ExtractionDiagnostic] = []

    try:
        image = decoder.decode(png_data)
    except StructureExtractionError as exc:
        raise _pipeline_error("decode", exc.code, str(exc)) from exc
    if not isinstance(image, ImageInput):
        raise PngExtractionError(
            "decode_invalid_response",
            "decode",
            "PNG decoder returned an invalid image type",
        )

    try:
        structure = structure_extractor.detect(image)
    except StructureExtractionError as exc:
        raise _pipeline_error("structure", exc.code, str(exc)) from exc
    _validate_structure_boundary(image, structure)

    lines = _normalize_lines(structure.lines)
    rectangles = _normalize_rectangles(structure.rectangles)
    text_regions = _normalize_regions(structure.text_regions)
    image_regions = _normalize_regions(structure.image_regions)
    region_entries = tuple(
        (f"p001-text-region-{index:04d}", region)
        for index, region in enumerate(text_regions)
    )
    ocr_regions = tuple(
        OcrRegion(region_ref=region_ref, bbox=region.bbox)
        for region_ref, region in region_entries
    )

    try:
        raw_tokens = tuple(
            ocr_backend.recognize(
                image,
                regions=ocr_regions,
                languages=normalized_languages,
                options=ocr_options,
            )
        )
    except OcrBackendError as exc:
        raise _pipeline_error("ocr", exc.code, str(exc)) from exc
    if any(not isinstance(token, OcrToken) for token in raw_tokens):
        raise PngExtractionError(
            "ocr_invalid_response",
            "ocr",
            "OCR backend returned a value that is not an OcrToken",
        )

    tokens_inside_page: list[OcrToken] = []
    for token in raw_tokens:
        if _bbox_inside(token.bbox, image):
            tokens_inside_page.append(token)
        else:
            diagnostics.append(
                ExtractionDiagnostic(
                    code="ocr_token_outside_page",
                    stage="ocr",
                    message="OCR token outside the source page was omitted",
                    source_ref=token.parent_region_ref,
                )
            )
    normalized_tokens, duplicate_count = _normalize_tokens(tokens_inside_page)
    if duplicate_count:
        diagnostics.append(
            ExtractionDiagnostic(
                code="ocr_duplicate_removed",
                stage="ocr",
                message=f"removed {duplicate_count} duplicate OCR token(s)",
            )
        )

    associated: list[_AssociatedToken] = []
    matched_region_refs: set[str] = set()
    for token in normalized_tokens:
        region_ref, region, inferred = _associate_region(token, region_entries)
        associated.append(
            _AssociatedToken(token=token, region_ref=region_ref, region=region)
        )
        if region_ref is not None:
            matched_region_refs.add(region_ref)
        if inferred:
            diagnostics.append(
                ExtractionDiagnostic(
                    code="ocr_region_inferred",
                    stage="ocr",
                    message="OCR token was associated to a region by source geometry",
                    source_ref=region_ref,
                )
            )
        elif region is None and region_entries:
            diagnostics.append(
                ExtractionDiagnostic(
                    code="ocr_token_unmatched",
                    stage="ocr",
                    message="OCR token did not overlap a detected text region",
                    source_ref=token.parent_region_ref,
                )
            )
    for region_ref, _ in region_entries:
        if region_ref not in matched_region_refs:
            diagnostics.append(
                ExtractionDiagnostic(
                    code="ocr_region_empty",
                    stage="ocr",
                    message="detected text region produced no OCR token",
                    source_ref=region_ref,
                )
            )
    if not associated and not region_entries:
        diagnostics.append(
            ExtractionDiagnostic(
                code="ocr_no_text",
                stage="ocr",
                message="no OCR token or text region was detected",
            )
        )

    text_elements = _text_elements(
        _reading_order(associated),
        image,
        diagnostics,
    )
    line_elements = _line_elements(lines, image)
    rectangle_elements = _rectangle_elements(rectangles, image)
    image_elements, assets, asset_payloads = _image_elements(
        image_regions,
        image,
        asset_encoder,
        diagnostics,
    )

    try:
        document = DocumentIR(
            ir_version=IR_VERSION,
            document_id=f"document-sha256-{image.source_sha256}",
            generator=Generator(name="aiteqno", version=__version__),
            pages=(
                Page(
                    id="page-001",
                    number=1,
                    size=PageSize(
                        width=_pt(image.source.pixel_width, image.source.dpi_x),
                        height=_pt(image.source.pixel_height, image.source.dpi_y),
                    ),
                    source=image.source,
                    elements=(
                        *text_elements,
                        *line_elements,
                        *rectangle_elements,
                        *image_elements,
                    ),
                ),
            ),
            assets=assets,
            extensions={
                "jp.reactorfront.aiteqno.extract": {
                    "pipeline_provider": EXTRACTION_PROVIDER,
                    "pipeline_version": EXTRACTION_PROVIDER_VERSION,
                    "source_sha256": image.source_sha256,
                }
            },
        )
    except DocumentIRValidationError as exc:
        raise _pipeline_error(
            "assemble",
            "document_ir_assembly_invalid",
            str(exc),
        ) from exc

    try:
        validator.validate(document)
    except DocumentIRValidationError as exc:
        raise _pipeline_error(
            "validate",
            "document_ir_schema_invalid",
            str(exc),
        ) from exc
    except DocumentIRSchemaError as exc:
        raise _pipeline_error("validate", exc.code, str(exc)) from exc

    try:
        bundle = bundle_writer.write(document, asset_payloads, output_directory)
    except BundleWriteError as exc:
        raise _pipeline_error("write", exc.code, str(exc)) from exc
    if not isinstance(bundle, BundleWriteResult):
        raise PngExtractionError(
            "bundle_invalid_response",
            "write",
            "bundle writer returned an invalid result type",
        )
    return PngExtractionResult(
        document=document,
        bundle=bundle,
        diagnostics=tuple(diagnostics),
    )


def _pipeline_error(
    stage: str,
    code: str,
    message: str,
) -> PngExtractionError:
    return PngExtractionError(code, stage, f"{stage} stage failed: {message}")


def _validate_structure_boundary(
    image: ImageInput,
    structure: StructureExtractionResult,
) -> None:
    if not isinstance(structure, StructureExtractionResult):
        raise PngExtractionError(
            "structure_invalid_response",
            "structure",
            "structure extractor returned an invalid result type",
        )
    if structure.page.source != image.source:
        raise PngExtractionError(
            "structure_source_mismatch",
            "structure",
            "structure result source metadata differs from decoded PNG metadata",
        )


def _normalize_lines(values: Sequence[LineCandidate]) -> tuple[LineCandidate, ...]:
    ordered = sorted(
        values,
        key=lambda item: (
            item.bbox.y,
            item.bbox.x,
            item.orientation.value,
            item.start.y,
            item.start.x,
            item.end.y,
            item.end.x,
            -item.confidence.overall,
            _provenance_key(item.provenance),
        ),
    )
    unique: dict[tuple[object, ...], LineCandidate] = {}
    for item in ordered:
        key = (
            item.orientation,
            item.start.x,
            item.start.y,
            item.end.x,
            item.end.y,
            *_bbox_key(item.bbox),
        )
        unique.setdefault(key, item)
    return tuple(unique.values())


def _normalize_rectangles(
    values: Sequence[RectangleCandidate],
) -> tuple[RectangleCandidate, ...]:
    ordered = sorted(
        values,
        key=lambda item: (
            *_bbox_key(item.bbox),
            -item.confidence.overall,
            _provenance_key(item.provenance),
        ),
    )
    unique: dict[tuple[int, int, int, int], RectangleCandidate] = {}
    for item in ordered:
        unique.setdefault(_bbox_key(item.bbox), item)
    return tuple(unique.values())


def _normalize_regions(
    values: Sequence[RegionCandidate],
) -> tuple[RegionCandidate, ...]:
    ordered = sorted(
        values,
        key=lambda item: (
            *_bbox_key(item.bbox),
            item.kind.value,
            -item.confidence.overall,
            _provenance_key(item.provenance),
        ),
    )
    unique: dict[tuple[object, ...], RegionCandidate] = {}
    for item in ordered:
        unique.setdefault((item.kind, *_bbox_key(item.bbox)), item)
    return tuple(unique.values())


def _normalize_tokens(tokens: Sequence[OcrToken]) -> tuple[tuple[OcrToken, ...], int]:
    ordered = sorted(
        tokens,
        key=lambda token: (
            *_bbox_key(token.bbox),
            token.text,
            -(token.confidence if token.confidence is not None else -1.0),
            token.parent_region_ref or "",
            token.provider,
            token.provider_version,
            token.model,
        ),
    )
    unique: dict[tuple[object, ...], OcrToken] = {}
    for token in ordered:
        unique.setdefault((*_bbox_key(token.bbox), token.text), token)
    return tuple(unique.values()), len(ordered) - len(unique)


def _associate_region(
    token: OcrToken,
    regions: Sequence[tuple[str, RegionCandidate]],
) -> tuple[str | None, RegionCandidate | None, bool]:
    by_ref = {region_ref: region for region_ref, region in regions}
    if token.parent_region_ref in by_ref:
        return token.parent_region_ref, by_ref[token.parent_region_ref], False

    center_x = token.bbox.x + (token.bbox.width - 1) / 2
    center_y = token.bbox.y + (token.bbox.height - 1) / 2
    containing = [
        (region_ref, region)
        for region_ref, region in regions
        if (
            region.bbox.x <= center_x < region.bbox.x + region.bbox.width
            and region.bbox.y <= center_y < region.bbox.y + region.bbox.height
        )
    ]
    if containing:
        region_ref, region = min(
            containing,
            key=lambda item: (
                item[1].bbox.width * item[1].bbox.height,
                item[1].bbox.y,
                item[1].bbox.x,
                item[0],
            ),
        )
        return region_ref, region, True

    overlapping = [
        (region_ref, region, _intersection_fraction(token.bbox, region.bbox))
        for region_ref, region in regions
    ]
    overlapping = [item for item in overlapping if item[2] >= 0.5]
    if overlapping:
        region_ref, region, _ = max(
            overlapping,
            key=lambda item: (
                item[2],
                -item[1].bbox.y,
                -item[1].bbox.x,
                item[0],
            ),
        )
        return region_ref, region, True
    return None, None, False


def _reading_order(tokens: Sequence[_AssociatedToken]) -> tuple[_AssociatedToken, ...]:
    ordered = sorted(tokens, key=_token_position_key)
    rows: list[_TokenRow] = []
    for item in ordered:
        bbox = item.token.bbox
        matches: list[tuple[float, int, _TokenRow]] = []
        for index, row in enumerate(rows):
            overlap = max(0, min(bbox.y + bbox.height, row.bottom) - max(bbox.y, row.top))
            denominator = min(bbox.height, row.bottom - row.top)
            ratio = overlap / denominator if denominator else 0.0
            if ratio >= 0.45:
                matches.append((ratio, -index, row))
        if matches:
            row = max(matches, key=lambda match: (match[0], match[1]))[2]
            row.items.append(item)
            row.top = min(row.top, bbox.y)
            row.bottom = max(row.bottom, bbox.y + bbox.height)
            row.left = min(row.left, bbox.x)
        else:
            rows.append(
                _TokenRow(
                    items=[item],
                    top=bbox.y,
                    bottom=bbox.y + bbox.height,
                    left=bbox.x,
                )
            )
    rows.sort(key=lambda row: (row.top, row.left, row.bottom))
    result: list[_AssociatedToken] = []
    for row in rows:
        result.extend(sorted(row.items, key=_token_horizontal_key))
    return tuple(result)


def _text_elements(
    tokens: Sequence[_AssociatedToken],
    image: ImageInput,
    diagnostics: list[ExtractionDiagnostic],
) -> tuple[TextElement, ...]:
    elements: list[TextElement] = []
    for reading_order, associated in enumerate(tokens):
        token = associated.token
        region = associated.region
        detection = None
        structure_provenance = ()
        if region is not None:
            detection = (
                region.confidence.detection
                if region.confidence.detection is not None
                else region.confidence.overall
            )
            structure_provenance = region.provenance
        recognition = token.confidence
        components = tuple(
            value for value in (detection, recognition) if value is not None
        )
        overall = round(min(components), 6) if components else 0.0
        if recognition is None:
            diagnostics.append(
                ExtractionDiagnostic(
                    code="ocr_confidence_missing",
                    stage="ocr",
                    message="OCR token had no recognition confidence",
                    source_ref=associated.region_ref,
                )
            )
        bbox = _point_bbox(token.bbox, image)
        font_size = max(1.0, round(bbox.height * 0.8, 6))
        elements.append(
            TextElement(
                id=f"p001-text-{reading_order:04d}",
                bbox=bbox,
                z_index=_TEXT_Z_INDEX,
                confidence=Confidence(
                    overall=overall,
                    detection=detection,
                    recognition=recognition,
                ),
                provenance=(*structure_provenance, *token.provenance),
                text=token.text,
                reading_order=reading_order,
                style=TextStyle(
                    font_family="Noto Sans CJK JP",
                    font_size_pt=font_size,
                    font_weight=400,
                    font_style=FontStyle.NORMAL,
                    color="#000000",
                    align=TextAlign.LEFT,
                    line_height=1.2,
                    rotation_deg=0.0,
                ),
                extensions={
                    "jp.reactorfront.aiteqno.ocr": {
                        "provider": token.provider,
                        "provider_version": token.provider_version,
                        "model": token.model,
                        "languages": list(token.languages),
                    }
                },
            )
        )
    return tuple(elements)


def _line_elements(
    candidates: Sequence[LineCandidate],
    image: ImageInput,
) -> tuple[LineElement, ...]:
    elements: list[LineElement] = []
    for index, candidate in enumerate(candidates):
        thickness_px = (
            candidate.bbox.height
            if candidate.orientation is LineOrientation.HORIZONTAL
            else candidate.bbox.width
        )
        thickness_dpi = (
            image.source.dpi_y
            if candidate.orientation is LineOrientation.HORIZONTAL
            else image.source.dpi_x
        )
        elements.append(
            LineElement(
                id=f"p001-line-{index:04d}",
                bbox=_point_bbox(candidate.bbox, image),
                z_index=_LINE_Z_INDEX,
                confidence=candidate.confidence,
                provenance=candidate.provenance,
                start=Point(
                    x=_pt(candidate.start.x, image.source.dpi_x),
                    y=_pt(candidate.start.y, image.source.dpi_y),
                ),
                end=Point(
                    x=_pt(candidate.end.x, image.source.dpi_x),
                    y=_pt(candidate.end.y, image.source.dpi_y),
                ),
                style=LineStyle(
                    width_pt=max(0.25, _pt(thickness_px, thickness_dpi)),
                    color="#000000",
                    dash=LineDash.SOLID,
                ),
            )
        )
    return tuple(elements)


def _rectangle_elements(
    candidates: Sequence[RectangleCandidate],
    image: ImageInput,
) -> tuple[RectangleElement, ...]:
    stroke_width = max(
        0.25,
        min(_pt(1, image.source.dpi_x), _pt(1, image.source.dpi_y)),
    )
    return tuple(
        RectangleElement(
            id=f"p001-rectangle-{index:04d}",
            bbox=_point_bbox(candidate.bbox, image),
            z_index=_RECTANGLE_Z_INDEX,
            confidence=candidate.confidence,
            provenance=candidate.provenance,
            style=RectangleStyle(
                stroke_color="#000000",
                stroke_width_pt=stroke_width,
                fill_color=None,
                corner_radius_pt=0.0,
            ),
        )
        for index, candidate in enumerate(candidates)
    )


def _image_elements(
    candidates: Sequence[RegionCandidate],
    image: ImageInput,
    encoder: ImageAssetEncoder,
    diagnostics: list[ExtractionDiagnostic],
) -> tuple[tuple[ImageElement, ...], tuple[Asset, ...], tuple[AssetPayload, ...]]:
    page_area = image.source.pixel_width * image.source.pixel_height
    registry: dict[str, tuple[Asset, AssetPayload]] = {}
    emitted: list[tuple[RegionCandidate, Asset]] = []
    for source_index, candidate in enumerate(candidates):
        source_ref = f"p001-image-region-{source_index:04d}"
        fraction = candidate.bbox.width * candidate.bbox.height / page_area
        if fraction >= PAGE_COVERING_IMAGE_FRACTION:
            diagnostics.append(
                ExtractionDiagnostic(
                    code="page_covering_image_skipped",
                    stage="asset",
                    message="page-covering image candidate was not embedded as an asset",
                    source_ref=source_ref,
                )
            )
            continue
        try:
            encoded = encoder.encode_png_crop(image, candidate.bbox)
        except AssetEncodingError as exc:
            diagnostics.append(
                ExtractionDiagnostic(
                    code=exc.code,
                    stage="asset",
                    message="image region was omitted because portable encoding failed",
                    source_ref=source_ref,
                )
            )
            continue
        if not isinstance(encoded, EncodedImageAsset):
            diagnostics.append(
                ExtractionDiagnostic(
                    code="asset_invalid_response",
                    stage="asset",
                    message="image region was omitted because the encoder response was invalid",
                    source_ref=source_ref,
                )
            )
            continue
        digest = encoded.sha256
        registered = registry.get(digest)
        if registered is None:
            asset = Asset(
                id=f"asset-sha256-{digest}",
                path=f"assets/sha256-{digest}.{encoded.extension}",
                media_type=encoded.media_type,
                sha256=digest,
                pixel_width=encoded.pixel_width,
                pixel_height=encoded.pixel_height,
                dpi_x=encoded.dpi_x,
                dpi_y=encoded.dpi_y,
            )
            payload = AssetPayload(asset=asset, data=encoded.data)
            registry[digest] = (asset, payload)
        else:
            asset, _ = registered
        emitted.append((candidate, asset))

    elements = tuple(
        ImageElement(
            id=f"p001-image-{index:04d}",
            bbox=_point_bbox(candidate.bbox, image),
            z_index=_IMAGE_Z_INDEX,
            confidence=candidate.confidence,
            provenance=candidate.provenance,
            asset_id=asset.id,
            fit=ImageFit.CONTAIN,
        )
        for index, (candidate, asset) in enumerate(emitted)
    )
    assets = tuple(pair[0] for pair in registry.values())
    payloads = tuple(pair[1] for pair in registry.values())
    return elements, assets, payloads


def _point_bbox(bbox: PixelBoundingBox, image: ImageInput) -> BoundingBox:
    left = _pt(bbox.x, image.source.dpi_x)
    top = _pt(bbox.y, image.source.dpi_y)
    right = _pt(bbox.x + bbox.width, image.source.dpi_x)
    bottom = _pt(bbox.y + bbox.height, image.source.dpi_y)
    return BoundingBox(
        x=left,
        y=top,
        width=round(right - left, 6),
        height=round(bottom - top, 6),
    )


def _pt(pixel_value: int, dpi: float) -> float:
    return round(pixel_value * 72.0 / dpi, 6)


def _bbox_inside(bbox: PixelBoundingBox, image: ImageInput) -> bool:
    return (
        bbox.x + bbox.width <= image.source.pixel_width
        and bbox.y + bbox.height <= image.source.pixel_height
    )


def _bbox_key(bbox: PixelBoundingBox) -> tuple[int, int, int, int]:
    return bbox.y, bbox.x, bbox.height, bbox.width


def _provenance_key(records: Sequence[object]) -> tuple[str, ...]:
    return tuple(repr(record) for record in records)


def _intersection_fraction(first: PixelBoundingBox, second: PixelBoundingBox) -> float:
    left = max(first.x, second.x)
    top = max(first.y, second.y)
    right = min(first.x + first.width, second.x + second.width)
    bottom = min(first.y + first.height, second.y + second.height)
    intersection = max(0, right - left) * max(0, bottom - top)
    return intersection / (first.width * first.height)


def _token_position_key(item: _AssociatedToken) -> tuple[object, ...]:
    bbox = item.token.bbox
    return (
        bbox.y,
        bbox.x,
        bbox.height,
        bbox.width,
        item.token.text,
        item.region_ref or "",
    )


def _token_horizontal_key(item: _AssociatedToken) -> tuple[object, ...]:
    bbox = item.token.bbox
    return (
        bbox.x,
        bbox.y,
        bbox.width,
        bbox.height,
        item.token.text,
        item.region_ref or "",
    )
