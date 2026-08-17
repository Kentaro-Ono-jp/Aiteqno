"""Tesseract 5.x OCR adapter isolated behind the portable OCR port."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import threading
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any, Iterator

import pytesseract
from PIL import Image, __version__ as PILLOW_VERSION

from aiteqno.domain import PixelBoundingBox, Provenance, ProvenanceStage
from aiteqno.ports.ocr import (
    DEFAULT_OCR_LANGUAGES,
    OcrBackendError,
    OcrCapabilities,
    OcrOptions,
    OcrRegion,
    OcrToken,
    normalize_ocr_languages,
    validate_ocr_request,
)
from aiteqno.ports.structure import ImageInput


TESSERACT_PROVIDER = "tesseract"
MIN_TESSERACT_MAJOR_VERSION = 5
DEFAULT_TESSERACT_TARGET_DPI: int | None = None
DEFAULT_MAX_TESSERACT_WORKING_PIXELS = 40_000_000
TESSERACT_RASTER_TRANSFORM_VERSION = "tesseract-raster-transform-v1"
TESSERACT_INVERSE_MAPPING_POLICY = (
    "clip-working-bbox; source-left-top=floor(edge*source/working); "
    "source-right-bottom=ceil(edge*source/working); clamp-source-crop; "
    "add-source-offset"
)
_RUNTIME_LOCK = threading.RLock()
_TSV_COLUMNS = ("text", "conf", "left", "top", "width", "height")


@dataclass(frozen=True, slots=True, kw_only=True)
class TesseractCropTransformEvidence:
    """Actual raster transform applied to one OCR target in source coordinates."""

    region_ref: str | None
    source_bbox: PixelBoundingBox
    source_width: int
    source_height: int
    working_width: int
    working_height: int
    actual_scale_x: float
    actual_scale_y: float
    resized: bool
    working_raster_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible representation."""

        return {
            "region_ref": self.region_ref,
            "source_bbox": {
                "x": self.source_bbox.x,
                "y": self.source_bbox.y,
                "width": self.source_bbox.width,
                "height": self.source_bbox.height,
            },
            "source_dimensions": {
                "width": self.source_width,
                "height": self.source_height,
            },
            "working_dimensions": {
                "width": self.working_width,
                "height": self.working_height,
            },
            "actual_scale": {
                "x": self.actual_scale_x,
                "y": self.actual_scale_y,
            },
            "resized": self.resized,
            "working_raster_sha256": self.working_raster_sha256,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class TesseractRasterTransformEvidence:
    """Immutable evidence for every OCR target in one recognize invocation."""

    schema_version: str
    transform_version: str
    enabled: bool
    target_dpi: int | None
    source_effective_dpi: float
    effective_ocr_dpi: int
    max_working_pixels: int
    pixel_mode: str
    resampling: str
    imaging_library: str
    imaging_library_version: str
    inverse_mapping_policy: str
    crops: tuple[TesseractCropTransformEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible representation."""

        return {
            "schema_version": self.schema_version,
            "transform_version": self.transform_version,
            "enabled": self.enabled,
            "target_dpi": self.target_dpi,
            "source_effective_dpi": self.source_effective_dpi,
            "effective_ocr_dpi": self.effective_ocr_dpi,
            "max_working_pixels": self.max_working_pixels,
            "pixel_mode": self.pixel_mode,
            "resampling": self.resampling,
            "imaging_library": {
                "name": self.imaging_library,
                "version": self.imaging_library_version,
            },
            "inverse_mapping_policy": self.inverse_mapping_policy,
            "crops": [crop.to_dict() for crop in self.crops],
        }


class TesseractOcrBackend:
    """Recognize source-pixel tokens through a configurable local Tesseract 5.x."""

    def __init__(
        self,
        *,
        executable_path: str | PathLike[str] | None = None,
        tessdata_prefix: str | PathLike[str] | None = None,
        required_languages: Sequence[str] = DEFAULT_OCR_LANGUAGES,
        target_dpi: int | None = DEFAULT_TESSERACT_TARGET_DPI,
        max_working_pixels: int = DEFAULT_MAX_TESSERACT_WORKING_PIXELS,
        transform_observer: Callable[[TesseractRasterTransformEvidence], None]
        | None = None,
    ) -> None:
        self._executable_path = (
            os.fspath(executable_path) if executable_path is not None else "tesseract"
        )
        if not self._executable_path.strip():
            raise ValueError("executable_path must not be empty")
        self._tessdata_prefix = (
            os.fspath(tessdata_prefix) if tessdata_prefix is not None else None
        )
        if self._tessdata_prefix is not None and not self._tessdata_prefix.strip():
            raise ValueError("tessdata_prefix must not be empty")
        self._required_languages = normalize_ocr_languages(required_languages)
        if target_dpi is not None and (
            isinstance(target_dpi, bool)
            or not isinstance(target_dpi, int)
            or target_dpi <= 0
        ):
            raise ValueError("target_dpi must be a positive integer or None")
        if (
            isinstance(max_working_pixels, bool)
            or not isinstance(max_working_pixels, int)
            or max_working_pixels <= 0
        ):
            raise ValueError("max_working_pixels must be a positive integer")
        if transform_observer is not None and not callable(transform_observer):
            raise TypeError("transform_observer must be callable or None")
        self._target_dpi = target_dpi
        self._max_working_pixels = max_working_pixels
        self._transform_observer = transform_observer

    def healthcheck(self) -> OcrCapabilities:
        """Verify executable, major version, and configured language data."""

        return self._probe(self._required_languages)

    def recognize(
        self,
        image: ImageInput,
        regions: Sequence[OcrRegion] = (),
        languages: Sequence[str] = DEFAULT_OCR_LANGUAGES,
        options: OcrOptions = OcrOptions(),
    ) -> tuple[OcrToken, ...]:
        """Recognize full-page or cropped regions and restore source coordinates."""

        collected_regions, normalized_languages = validate_ocr_request(
            image,
            regions,
            languages,
            options,
        )
        capabilities = self._probe(normalized_languages)
        try:
            page_image = Image.frombytes(
                "RGB",
                (image.source.pixel_width, image.source.pixel_height),
                image.pixels,
            )
        except (OSError, ValueError) as exc:
            raise OcrBackendError(
                "ocr_unreadable_input",
                f"normalized image pixels could not be opened: {exc}",
                provider=TESSERACT_PROVIDER,
            ) from exc

        targets: tuple[OcrRegion | None, ...]
        if collected_regions:
            targets = collected_regions
        else:
            targets = (None,)
        language_spec = "+".join(normalized_languages)
        model = f"tessdata:{language_spec}"
        source_effective_dpi = _source_effective_dpi(image)
        effective_ocr_dpi = _effective_ocr_dpi(
            source_effective_dpi,
            self._target_dpi,
        )
        parameters_digest = _parameters_digest(
            normalized_languages,
            options,
            capabilities.provider_version,
            image,
            tessdata_configured=self._tessdata_prefix is not None,
            target_dpi=self._target_dpi,
            max_working_pixels=self._max_working_pixels,
        )
        tokens: list[OcrToken] = []
        transform_crops: list[TesseractCropTransformEvidence] = []
        try:
            resolved_executable = capabilities.executable
            with self._configured_runtime(resolved_executable):
                for target in targets:
                    source_crop, offset_x, offset_y, region_ref = _target_image(
                        page_image,
                        target,
                    )
                    working_image = source_crop
                    try:
                        working_image, transform = _working_image(
                            source_crop,
                            offset_x=offset_x,
                            offset_y=offset_y,
                            region_ref=region_ref,
                            source_effective_dpi=source_effective_dpi,
                            target_dpi=self._target_dpi,
                            max_working_pixels=self._max_working_pixels,
                        )
                        transform_crops.append(transform)
                        config = self._config(options, effective_ocr_dpi)
                        try:
                            response = pytesseract.image_to_data(
                                working_image,
                                lang=language_spec,
                                config=config,
                                output_type=pytesseract.Output.DICT,
                                timeout=options.timeout_seconds,
                            )
                        except pytesseract.TesseractNotFoundError as exc:
                            raise OcrBackendError(
                                "ocr_executable_missing",
                                "Tesseract executable became unavailable: "
                                f"{resolved_executable}",
                                provider=TESSERACT_PROVIDER,
                            ) from exc
                        except pytesseract.TesseractError as exc:
                            raise OcrBackendError(
                                "ocr_engine_failure",
                                f"Tesseract OCR process failed: {exc}",
                                provider=TESSERACT_PROVIDER,
                            ) from exc
                        except RuntimeError as exc:
                            code = (
                                "ocr_timeout"
                                if "timeout" in str(exc).casefold()
                                else "ocr_engine_failure"
                            )
                            message = (
                                f"Tesseract exceeded {options.timeout_seconds:g} seconds"
                                if code == "ocr_timeout"
                                else f"Tesseract OCR process failed: {exc}"
                            )
                            raise OcrBackendError(
                                code,
                                message,
                                provider=TESSERACT_PROVIDER,
                            ) from exc
                        except OSError as exc:
                            raise OcrBackendError(
                                "ocr_engine_failure",
                                f"Tesseract OCR process could not start: {exc}",
                                provider=TESSERACT_PROVIDER,
                            ) from exc
                        tokens.extend(
                            _tokens_from_response(
                                response,
                                source_crop_width=source_crop.width,
                                source_crop_height=source_crop.height,
                                working_width=working_image.width,
                                working_height=working_image.height,
                                offset_x=offset_x,
                                offset_y=offset_y,
                                region_ref=region_ref,
                                languages=normalized_languages,
                                provider_version=capabilities.provider_version,
                                model=model,
                                options=options,
                                parameters_digest=parameters_digest,
                                transform=transform,
                                effective_ocr_dpi=effective_ocr_dpi,
                                target_dpi=self._target_dpi,
                            )
                        )
                    finally:
                        if working_image is not source_crop:
                            working_image.close()
                        if target is not None:
                            source_crop.close()
        finally:
            page_image.close()
        evidence = TesseractRasterTransformEvidence(
            schema_version="1.0",
            transform_version=TESSERACT_RASTER_TRANSFORM_VERSION,
            enabled=self._target_dpi is not None,
            target_dpi=self._target_dpi,
            source_effective_dpi=source_effective_dpi,
            effective_ocr_dpi=effective_ocr_dpi,
            max_working_pixels=self._max_working_pixels,
            pixel_mode="RGB",
            resampling="LANCZOS" if self._target_dpi is not None else "none",
            imaging_library="Pillow",
            imaging_library_version=PILLOW_VERSION,
            inverse_mapping_policy=TESSERACT_INVERSE_MAPPING_POLICY,
            crops=tuple(transform_crops),
        )
        if self._transform_observer is not None:
            self._transform_observer(evidence)
        return tuple(tokens)

    def _probe(self, required_languages: Sequence[str]) -> OcrCapabilities:
        normalized_languages = normalize_ocr_languages(required_languages)
        resolved_executable = self._resolve_executable()
        try:
            with self._configured_runtime(resolved_executable):
                version_value = pytesseract.get_tesseract_version()
                available_value = pytesseract.get_languages(config="")
        except pytesseract.TesseractNotFoundError as exc:
            raise OcrBackendError(
                "ocr_executable_missing",
                f"Tesseract executable is unavailable: {self._executable_path}",
                provider=TESSERACT_PROVIDER,
            ) from exc
        except pytesseract.TesseractError as exc:
            raise OcrBackendError(
                "ocr_engine_failure",
                f"Tesseract healthcheck failed: {exc}",
                provider=TESSERACT_PROVIDER,
            ) from exc
        except (OSError, RuntimeError) as exc:
            raise OcrBackendError(
                "ocr_engine_failure",
                f"Tesseract healthcheck failed: {exc}",
                provider=TESSERACT_PROVIDER,
            ) from exc

        provider_version, major_version = _parse_version(version_value)
        if major_version < MIN_TESSERACT_MAJOR_VERSION:
            raise OcrBackendError(
                "ocr_unsupported_version",
                f"Tesseract 5.x or newer is required; detected {provider_version}",
                provider=TESSERACT_PROVIDER,
            )
        try:
            available_languages = normalize_ocr_languages(
                tuple(sorted(set(available_value)))
            )
        except (TypeError, ValueError) as exc:
            raise OcrBackendError(
                "ocr_invalid_response",
                f"Tesseract returned an invalid language list: {exc}",
                provider=TESSERACT_PROVIDER,
            ) from exc
        missing = [
            language
            for language in normalized_languages
            if language not in available_languages
        ]
        if missing:
            raise OcrBackendError(
                "ocr_language_missing",
                "Tesseract trained data is missing: "
                + ", ".join(missing)
                + ". Install the language data or configure TESSDATA_PREFIX.",
                provider=TESSERACT_PROVIDER,
            )
        return OcrCapabilities(
            provider=TESSERACT_PROVIDER,
            provider_version=provider_version,
            executable=resolved_executable,
            available_languages=available_languages,
            default_languages=normalized_languages,
        )

    def _resolve_executable(self) -> str:
        configured = self._executable_path
        candidate = Path(configured).expanduser()
        has_path_component = candidate.is_absolute() or candidate.parent != Path(".")
        if has_path_component:
            if not candidate.is_file():
                raise OcrBackendError(
                    "ocr_executable_missing",
                    f"Tesseract executable does not exist: {candidate}",
                    provider=TESSERACT_PROVIDER,
                )
            return str(candidate.resolve())
        resolved = shutil.which(configured)
        if resolved is None:
            raise OcrBackendError(
                "ocr_executable_missing",
                f"Tesseract executable {configured!r} was not found on PATH",
                provider=TESSERACT_PROVIDER,
            )
        return str(Path(resolved).resolve())

    @contextmanager
    def _configured_runtime(self, executable: str) -> Iterator[None]:
        with _RUNTIME_LOCK:
            previous_command = pytesseract.pytesseract.tesseract_cmd
            prefix_existed = "TESSDATA_PREFIX" in os.environ
            previous_prefix = os.environ.get("TESSDATA_PREFIX")
            pytesseract.pytesseract.tesseract_cmd = executable
            if self._tessdata_prefix is not None:
                os.environ["TESSDATA_PREFIX"] = self._tessdata_prefix
            try:
                yield
            finally:
                pytesseract.pytesseract.tesseract_cmd = previous_command
                if self._tessdata_prefix is not None:
                    if prefix_existed and previous_prefix is not None:
                        os.environ["TESSDATA_PREFIX"] = previous_prefix
                    else:
                        os.environ.pop("TESSDATA_PREFIX", None)

    @staticmethod
    def _config(options: OcrOptions, effective_dpi: int) -> str:
        parts = [
            "--oem",
            str(options.engine_mode),
            "--psm",
            str(options.page_segmentation_mode),
            "--dpi",
            str(effective_dpi),
        ]
        if options.preserve_interword_spaces:
            parts.extend(("-c", "preserve_interword_spaces=1"))
        return " ".join(parts)


def _target_image(
    page_image: Image.Image,
    region: OcrRegion | None,
) -> tuple[Image.Image, int, int, str | None]:
    if region is None:
        return page_image, 0, 0, None
    bbox = region.bbox
    crop = page_image.crop(
        (
            bbox.x,
            bbox.y,
            bbox.x + bbox.width,
            bbox.y + bbox.height,
        )
    )
    return crop, bbox.x, bbox.y, region.region_ref


def _source_effective_dpi(image: ImageInput) -> float:
    return round((image.source.dpi_x + image.source.dpi_y) / 2.0, 6)


def _effective_ocr_dpi(
    source_effective_dpi: float,
    target_dpi: int | None,
) -> int:
    if target_dpi is not None and source_effective_dpi < target_dpi:
        return target_dpi
    return max(1, int(round(source_effective_dpi)))


def _working_image(
    source_crop: Image.Image,
    *,
    offset_x: int,
    offset_y: int,
    region_ref: str | None,
    source_effective_dpi: float,
    target_dpi: int | None,
    max_working_pixels: int,
) -> tuple[Image.Image, TesseractCropTransformEvidence]:
    source_width, source_height = source_crop.size
    scale = (
        target_dpi / source_effective_dpi
        if target_dpi is not None and source_effective_dpi < target_dpi
        else 1.0
    )
    working_width = _scaled_dimension(source_width, scale)
    working_height = _scaled_dimension(source_height, scale)
    working_pixels = working_width * working_height
    if working_pixels > max_working_pixels:
        raise OcrBackendError(
            "ocr_working_raster_limit",
            "Tesseract working raster exceeds the per-crop pixel limit: "
            f"{working_width}x{working_height}={working_pixels} > "
            f"{max_working_pixels}",
            provider=TESSERACT_PROVIDER,
        )

    resized = (working_width, working_height) != (source_width, source_height)
    working = source_crop
    if resized:
        try:
            working = source_crop.resize(
                (working_width, working_height),
                resample=Image.Resampling.LANCZOS,
            )
        except (MemoryError, OSError) as exc:
            raise OcrBackendError(
                "ocr_working_raster_failure",
                f"Tesseract working raster could not be created: {exc}",
                provider=TESSERACT_PROVIDER,
            ) from exc

    try:
        working_raster_sha256 = _working_raster_sha256(working)
    except (MemoryError, OSError, ValueError) as exc:
        if working is not source_crop:
            working.close()
        raise OcrBackendError(
            "ocr_working_raster_failure",
            f"Tesseract working raster could not be hashed: {exc}",
            provider=TESSERACT_PROVIDER,
        ) from exc

    evidence = TesseractCropTransformEvidence(
        region_ref=region_ref,
        source_bbox=PixelBoundingBox(
            x=offset_x,
            y=offset_y,
            width=source_width,
            height=source_height,
        ),
        source_width=source_width,
        source_height=source_height,
        working_width=working_width,
        working_height=working_height,
        actual_scale_x=round(working_width / source_width, 12),
        actual_scale_y=round(working_height / source_height, 12),
        resized=resized,
        working_raster_sha256=working_raster_sha256,
    )
    return working, evidence


def _scaled_dimension(source_dimension: int, scale: float) -> int:
    return max(1, int(math.floor(source_dimension * scale + 0.5)))


def _working_raster_sha256(image: Image.Image) -> str:
    digest = hashlib.sha256()
    digest.update(image.mode.encode("ascii"))
    digest.update(b"\0")
    digest.update(str(image.width).encode("ascii"))
    digest.update(b"x")
    digest.update(str(image.height).encode("ascii"))
    digest.update(b"\0")
    digest.update(image.tobytes())
    return digest.hexdigest()


def _tokens_from_response(
    response: object,
    *,
    source_crop_width: int,
    source_crop_height: int,
    working_width: int,
    working_height: int,
    offset_x: int,
    offset_y: int,
    region_ref: str | None,
    languages: tuple[str, ...],
    provider_version: str,
    model: str,
    options: OcrOptions,
    parameters_digest: str,
    transform: TesseractCropTransformEvidence,
    effective_ocr_dpi: int,
    target_dpi: int | None,
) -> list[OcrToken]:
    rows = _response_rows(response)
    tokens: list[OcrToken] = []
    for row in rows:
        text = _normalize_text(row["text"])
        if not text:
            continue
        confidence = _normalize_confidence(row["conf"])
        if confidence is None or confidence < options.min_confidence:
            continue
        working_bbox = _normalized_bbox(
            row,
            crop_width=working_width,
            crop_height=working_height,
        )
        if working_bbox is None:
            continue
        local_bbox = _source_bbox_from_working(
            working_bbox,
            source_width=source_crop_width,
            source_height=source_crop_height,
            working_width=working_width,
            working_height=working_height,
        )
        bbox = PixelBoundingBox(
            x=offset_x + local_bbox.x,
            y=offset_y + local_bbox.y,
            width=local_bbox.width,
            height=local_bbox.height,
        )
        source_refs = (region_ref,) if region_ref is not None else ()
        provenance = Provenance(
            stage=ProvenanceStage.OCR,
            provider=TESSERACT_PROVIDER,
            provider_version=provider_version,
            source_refs=source_refs,
            source_bbox_px=bbox,
            parameters_digest=parameters_digest,
            notes=(
                f"model={model}; languages={'+'.join(languages)}; "
                f"psm={options.page_segmentation_mode}; oem={options.engine_mode}; "
                f"transform={TESSERACT_RASTER_TRANSFORM_VERSION}; "
                f"target_dpi={target_dpi if target_dpi is not None else 'disabled'}; "
                f"effective_ocr_dpi={effective_ocr_dpi}; "
                f"source={transform.source_width}x{transform.source_height}; "
                f"working={transform.working_width}x{transform.working_height}; "
                f"scale={transform.actual_scale_x:g}x{transform.actual_scale_y:g}; "
                f"resized={str(transform.resized).lower()}; "
                f"resampling={'LANCZOS' if transform.resized else 'none'}"
            ),
        )
        tokens.append(
            OcrToken(
                text=text,
                bbox=bbox,
                confidence=confidence,
                provider=TESSERACT_PROVIDER,
                provider_version=provider_version,
                model=model,
                languages=languages,
                provenance=(provenance,),
                parent_region_ref=region_ref,
            )
        )
    return tokens


def _source_bbox_from_working(
    working_bbox: PixelBoundingBox,
    *,
    source_width: int,
    source_height: int,
    working_width: int,
    working_height: int,
) -> PixelBoundingBox:
    left = working_bbox.x * source_width // working_width
    top = working_bbox.y * source_height // working_height
    right = _ceil_div(
        (working_bbox.x + working_bbox.width) * source_width,
        working_width,
    )
    bottom = _ceil_div(
        (working_bbox.y + working_bbox.height) * source_height,
        working_height,
    )
    left = min(source_width, max(0, left))
    top = min(source_height, max(0, top))
    right = min(source_width, max(0, right))
    bottom = min(source_height, max(0, bottom))
    if right <= left or bottom <= top:
        raise OcrBackendError(
            "ocr_invalid_response",
            "Tesseract token geometry collapsed during source-coordinate mapping",
            provider=TESSERACT_PROVIDER,
        )
    return PixelBoundingBox(
        x=left,
        y=top,
        width=right - left,
        height=bottom - top,
    )


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def _response_rows(response: object) -> list[dict[str, object]]:
    if not isinstance(response, Mapping):
        raise OcrBackendError(
            "ocr_invalid_response",
            "Tesseract TSV response must be a mapping",
            provider=TESSERACT_PROVIDER,
        )
    columns: dict[str, Sequence[Any]] = {}
    for name in _TSV_COLUMNS:
        value = response.get(name)
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise OcrBackendError(
                "ocr_invalid_response",
                f"Tesseract TSV response is missing column {name!r}",
                provider=TESSERACT_PROVIDER,
            )
        columns[name] = value
    row_count = len(columns["text"])
    if any(len(values) != row_count for values in columns.values()):
        raise OcrBackendError(
            "ocr_invalid_response",
            "Tesseract TSV columns have inconsistent lengths",
            provider=TESSERACT_PROVIDER,
        )
    return [
        {name: columns[name][index] for name in _TSV_COLUMNS}
        for index in range(row_count)
    ]


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value))
    return " ".join(normalized.split())


def _normalize_confidence(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or numeric < 0:
        return None
    if numeric > 100:
        raise OcrBackendError(
            "ocr_invalid_response",
            f"Tesseract confidence must not exceed 100; received {numeric}",
            provider=TESSERACT_PROVIDER,
        )
    return round(numeric / 100.0, 6)


def _normalized_bbox(
    row: Mapping[str, object],
    *,
    crop_width: int,
    crop_height: int,
) -> PixelBoundingBox | None:
    try:
        left = _integer(row["left"])
        top = _integer(row["top"])
        width = _integer(row["width"])
        height = _integer(row["height"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise OcrBackendError(
            "ocr_invalid_response",
            f"Tesseract returned invalid token geometry: {exc}",
            provider=TESSERACT_PROVIDER,
        ) from exc
    if width <= 0 or height <= 0:
        return None
    right = min(crop_width, left + width)
    bottom = min(crop_height, top + height)
    left = max(0, left)
    top = max(0, top)
    if right <= left or bottom <= top:
        return None
    return PixelBoundingBox(
        x=left,
        y=top,
        width=right - left,
        height=bottom - top,
    )


def _integer(value: object) -> int:
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError(f"expected an integer, received {value!r}")
    return int(numeric)


def _parse_version(value: object) -> tuple[str, int]:
    rendered = str(value).strip()
    match = re.match(r"(?P<version>\d+(?:\.\d+){0,3})", rendered)
    if match is None:
        raise OcrBackendError(
            "ocr_invalid_response",
            f"Tesseract returned an unparseable version: {rendered!r}",
            provider=TESSERACT_PROVIDER,
        )
    version = match.group("version")
    return version, int(version.split(".", 1)[0])


def _parameters_digest(
    languages: tuple[str, ...],
    options: OcrOptions,
    provider_version: str,
    image: ImageInput,
    *,
    tessdata_configured: bool,
    target_dpi: int | None,
    max_working_pixels: int,
) -> str:
    payload = {
        "dpi_x": image.source.dpi_x,
        "dpi_y": image.source.dpi_y,
        "engine_mode": options.engine_mode,
        "languages": list(languages),
        "min_confidence": options.min_confidence,
        "page_segmentation_mode": options.page_segmentation_mode,
        "preserve_interword_spaces": options.preserve_interword_spaces,
        "provider": TESSERACT_PROVIDER,
        "provider_version": provider_version,
        "tessdata_configured": tessdata_configured,
        "timeout_seconds": options.timeout_seconds,
        "raster_transform": {
            "version": TESSERACT_RASTER_TRANSFORM_VERSION,
            "enabled": target_dpi is not None,
            "target_dpi": target_dpi,
            "max_working_pixels": max_working_pixels,
            "pixel_mode": "RGB",
            "resampling": "LANCZOS" if target_dpi is not None else "none",
            "imaging_library": {
                "name": "Pillow",
                "version": PILLOW_VERSION,
            },
            "inverse_mapping_policy": TESSERACT_INVERSE_MAPPING_POLICY,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()
