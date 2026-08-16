"""Replaceable OCR backend contracts expressed only in source pixels."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from aiteqno.domain import PixelBoundingBox, Provenance, ProvenanceStage
from aiteqno.ports.structure import ImageInput


DEFAULT_OCR_LANGUAGES = ("jpn", "eng")
_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_/-]*$")


class OcrBackendError(RuntimeError):
    """An actionable OCR runtime, input, or response failure."""

    def __init__(self, code: str, message: str, *, provider: str) -> None:
        super().__init__(message)
        self.code = code
        self.provider = provider


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrOptions:
    """Portable OCR controls supported by every V1 backend."""

    page_segmentation_mode: int = 6
    engine_mode: int = 3
    timeout_seconds: float = 30.0
    min_confidence: float = 0.0
    preserve_interword_spaces: bool = False

    def __post_init__(self) -> None:
        if (
            isinstance(self.page_segmentation_mode, bool)
            or not isinstance(self.page_segmentation_mode, int)
            or not 0 <= self.page_segmentation_mode <= 13
        ):
            raise ValueError("page_segmentation_mode must be an integer from 0 to 13")
        if (
            isinstance(self.engine_mode, bool)
            or not isinstance(self.engine_mode, int)
            or not 0 <= self.engine_mode <= 3
        ):
            raise ValueError("engine_mode must be an integer from 0 to 3")
        object.__setattr__(
            self,
            "timeout_seconds",
            _finite_number(
                self.timeout_seconds,
                "timeout_seconds",
                minimum=0.0,
                exclusive_minimum=True,
            ),
        )
        object.__setattr__(
            self,
            "min_confidence",
            _finite_number(
                self.min_confidence,
                "min_confidence",
                minimum=0.0,
                maximum=1.0,
            ),
        )
        if not isinstance(self.preserve_interword_spaces, bool):
            raise TypeError("preserve_interword_spaces must be a boolean")


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrRegion:
    """One optional OCR crop with an application-owned correlation reference."""

    region_ref: str
    bbox: PixelBoundingBox

    def __post_init__(self) -> None:
        _non_empty_string(self.region_ref, "region_ref")
        if not isinstance(self.bbox, PixelBoundingBox):
            raise TypeError("OCR region bbox must be a PixelBoundingBox")


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrCapabilities:
    """Verified local runtime capabilities returned by healthcheck."""

    provider: str
    provider_version: str
    executable: str
    available_languages: tuple[str, ...]
    default_languages: tuple[str, ...]
    supports_regions: bool = True
    supports_confidence: bool = True
    supports_timeout: bool = True

    def __post_init__(self) -> None:
        _non_empty_string(self.provider, "provider")
        _non_empty_string(self.provider_version, "provider_version")
        _non_empty_string(self.executable, "executable")
        available = normalize_ocr_languages(self.available_languages)
        defaults = normalize_ocr_languages(self.default_languages)
        missing = [language for language in defaults if language not in available]
        if missing:
            raise ValueError(
                "default_languages must be present in available_languages: "
                + ", ".join(missing)
            )
        object.__setattr__(self, "available_languages", available)
        object.__setattr__(self, "default_languages", defaults)
        for field_name in (
            "supports_regions",
            "supports_confidence",
            "supports_timeout",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be a boolean")


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrToken:
    """One recognized token normalized to the original PNG coordinate system."""

    text: str
    bbox: PixelBoundingBox
    confidence: float | None
    provider: str
    provider_version: str
    model: str
    languages: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    parent_region_ref: str | None = None

    def __post_init__(self) -> None:
        _non_empty_string(self.text, "OCR token text")
        if not isinstance(self.bbox, PixelBoundingBox):
            raise TypeError("OCR token bbox must be a PixelBoundingBox")
        if self.confidence is not None:
            object.__setattr__(
                self,
                "confidence",
                _finite_number(
                    self.confidence,
                    "OCR token confidence",
                    minimum=0.0,
                    maximum=1.0,
                ),
            )
        _non_empty_string(self.provider, "provider")
        _non_empty_string(self.provider_version, "provider_version")
        _non_empty_string(self.model, "model")
        object.__setattr__(
            self,
            "languages",
            normalize_ocr_languages(self.languages),
        )
        if isinstance(self.provenance, (str, bytes, bytearray)):
            raise TypeError("OCR token provenance must be a sequence")
        records = tuple(self.provenance)
        if not records:
            raise ValueError("OCR token provenance must not be empty")
        if any(not isinstance(record, Provenance) for record in records):
            raise TypeError("OCR token provenance entries must be Provenance values")
        if not any(record.stage is ProvenanceStage.OCR for record in records):
            raise ValueError("OCR token provenance must contain an OCR stage")
        object.__setattr__(self, "provenance", records)
        if self.parent_region_ref is not None:
            _non_empty_string(self.parent_region_ref, "parent_region_ref")


def normalize_ocr_languages(languages: Sequence[str]) -> tuple[str, ...]:
    """Validate ordered Tesseract-style language identifiers."""

    if isinstance(languages, (str, bytes, bytearray)):
        raise TypeError("languages must be a sequence of language identifiers")
    normalized = tuple(languages)
    if not normalized:
        raise ValueError("languages must not be empty")
    for language in normalized:
        if not isinstance(language, str) or not _LANGUAGE_PATTERN.fullmatch(language):
            raise ValueError(
                "language identifiers must match [A-Za-z][A-Za-z0-9_/-]*"
            )
    if len(normalized) != len(set(normalized)):
        raise ValueError("languages must not contain duplicates")
    return normalized


def validate_ocr_request(
    image: ImageInput,
    regions: Sequence[OcrRegion],
    languages: Sequence[str],
    options: OcrOptions,
) -> tuple[tuple[OcrRegion, ...], tuple[str, ...]]:
    """Validate a backend-independent OCR call and return immutable sequences."""

    if not isinstance(image, ImageInput):
        raise TypeError("image must be an ImageInput")
    if isinstance(regions, (str, bytes, bytearray)):
        raise TypeError("regions must be a sequence")
    collected_regions = tuple(regions)
    if any(not isinstance(region, OcrRegion) for region in collected_regions):
        raise TypeError("regions must contain only OcrRegion values")
    refs = [region.region_ref for region in collected_regions]
    if len(refs) != len(set(refs)):
        raise ValueError("OCR region references must be unique")
    for region in collected_regions:
        if (
            region.bbox.x + region.bbox.width > image.source.pixel_width
            or region.bbox.y + region.bbox.height > image.source.pixel_height
        ):
            raise ValueError(
                f"OCR region {region.region_ref!r} must remain inside the source page"
            )
    normalized_languages = normalize_ocr_languages(languages)
    if not isinstance(options, OcrOptions):
        raise TypeError("options must be an OcrOptions")
    return collected_regions, normalized_languages


def _non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _finite_number(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    if (exclusive_minimum and number <= minimum) or (
        not exclusive_minimum and number < minimum
    ):
        comparator = "greater than" if exclusive_minimum else "at least"
        raise ValueError(f"{field_name} must be {comparator} {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name} must be at most {maximum}")
    return number


class OcrBackend(Protocol):
    """Replaceable local or remote OCR provider boundary."""

    def healthcheck(self) -> OcrCapabilities:
        """Return verified capabilities or raise an actionable backend error."""

    def recognize(
        self,
        image: ImageInput,
        regions: Sequence[OcrRegion] = (),
        languages: Sequence[str] = DEFAULT_OCR_LANGUAGES,
        options: OcrOptions = OcrOptions(),
    ) -> tuple[OcrToken, ...]:
        """Recognize full-page or region text in original source pixels."""
