"""Deterministic in-process OCR backend for unit tests and orchestration tests."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Sequence

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


FAKE_OCR_PROVIDER = "aiteqno.fake-ocr"
FAKE_OCR_PROVIDER_VERSION = "1.0"
_FAKE_OCR_AVAILABLE_LANGUAGES = ("jpn", "eng")


@dataclass(frozen=True, slots=True, kw_only=True)
class FakeOcrObservation:
    """A source-pixel observation returned deterministically by the fake backend."""

    text: str
    bbox: PixelBoundingBox
    confidence: float | None = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("fake OCR text must be a non-empty string")
        if not isinstance(self.bbox, PixelBoundingBox):
            raise TypeError("fake OCR bbox must be a PixelBoundingBox")
        if self.confidence is not None:
            if (
                isinstance(self.confidence, bool)
                or not isinstance(self.confidence, (int, float))
                or not math.isfinite(float(self.confidence))
                or not 0.0 <= float(self.confidence) <= 1.0
            ):
                raise ValueError("fake OCR confidence must be null or between 0 and 1")
            object.__setattr__(self, "confidence", float(self.confidence))


class FakeOcrBackend:
    """Return configured observations without a process, network, or clock."""

    def __init__(
        self,
        observations: Sequence[FakeOcrObservation],
        *,
        available_languages: Sequence[str] = _FAKE_OCR_AVAILABLE_LANGUAGES,
    ) -> None:
        if isinstance(observations, (str, bytes, bytearray)):
            raise TypeError("observations must be a sequence")
        collected = tuple(observations)
        if any(not isinstance(item, FakeOcrObservation) for item in collected):
            raise TypeError("observations must contain FakeOcrObservation values")
        self._observations = collected
        self._available_languages = normalize_ocr_languages(available_languages)
        supported_defaults = tuple(
            language
            for language in DEFAULT_OCR_LANGUAGES
            if language in self._available_languages
        )
        self._default_languages = supported_defaults or self._available_languages

    def healthcheck(self) -> OcrCapabilities:
        return OcrCapabilities(
            provider=FAKE_OCR_PROVIDER,
            provider_version=FAKE_OCR_PROVIDER_VERSION,
            executable="in-process",
            available_languages=self._available_languages,
            default_languages=self._default_languages,
        )

    def recognize(
        self,
        image: ImageInput,
        regions: Sequence[OcrRegion] = (),
        languages: Sequence[str] = DEFAULT_OCR_LANGUAGES,
        options: OcrOptions = OcrOptions(),
    ) -> tuple[OcrToken, ...]:
        collected_regions, normalized_languages = validate_ocr_request(
            image,
            regions,
            languages,
            options,
        )
        missing = [
            language
            for language in normalized_languages
            if language not in self._available_languages
        ]
        if missing:
            raise OcrBackendError(
                "ocr_language_missing",
                "fake OCR backend does not provide: " + ", ".join(missing),
                provider=FAKE_OCR_PROVIDER,
            )

        targets: tuple[OcrRegion | None, ...]
        if collected_regions:
            targets = collected_regions
        else:
            targets = (None,)
        digest = _request_digest(normalized_languages, options)
        tokens: list[OcrToken] = []
        for target in targets:
            for observation in self._observations:
                if target is not None and not _center_inside(
                    observation.bbox,
                    target.bbox,
                ):
                    continue
                region_ref = target.region_ref if target is not None else None
                source_refs = (region_ref,) if region_ref is not None else ()
                provenance = Provenance(
                    stage=ProvenanceStage.OCR,
                    provider=FAKE_OCR_PROVIDER,
                    provider_version=FAKE_OCR_PROVIDER_VERSION,
                    source_refs=source_refs,
                    source_bbox_px=observation.bbox,
                    parameters_digest=digest,
                    notes=(
                        "deterministic fake OCR observation; "
                        f"languages={'+'.join(normalized_languages)}"
                    ),
                )
                token = OcrToken(
                    text=observation.text,
                    bbox=observation.bbox,
                    confidence=observation.confidence,
                    provider=FAKE_OCR_PROVIDER,
                    provider_version=FAKE_OCR_PROVIDER_VERSION,
                    model="static-fixture",
                    languages=normalized_languages,
                    provenance=(provenance,),
                    parent_region_ref=region_ref,
                )
                if token.confidence is None or token.confidence >= options.min_confidence:
                    tokens.append(token)
        return tuple(tokens)


def _center_inside(candidate: PixelBoundingBox, region: PixelBoundingBox) -> bool:
    center_x = candidate.x + (candidate.width - 1) / 2
    center_y = candidate.y + (candidate.height - 1) / 2
    return (
        region.x <= center_x < region.x + region.width
        and region.y <= center_y < region.y + region.height
    )


def _request_digest(languages: tuple[str, ...], options: OcrOptions) -> str:
    payload = {
        "engine_mode": options.engine_mode,
        "languages": list(languages),
        "min_confidence": options.min_confidence,
        "page_segmentation_mode": options.page_segmentation_mode,
        "preserve_interword_spaces": options.preserve_interword_spaces,
        "provider": FAKE_OCR_PROVIDER,
        "provider_version": FAKE_OCR_PROVIDER_VERSION,
        "timeout_seconds": options.timeout_seconds,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "ascii"
    )
    return hashlib.sha256(encoded).hexdigest()
