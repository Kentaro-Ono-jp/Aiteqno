"""Ports and immutable payloads for extracted Document IR bundles."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Protocol, Sequence

from aiteqno.domain import Asset, DocumentIR, MediaType, PixelBoundingBox
from aiteqno.ports.structure import ImageInput


class AssetEncodingError(RuntimeError):
    """Raised when one detected image region cannot become a portable asset."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BundleWriteError(RuntimeError):
    """Raised when a complete extraction bundle cannot be published atomically."""

    def __init__(self, code: str, message: str, *, output_path: Path) -> None:
        super().__init__(message)
        self.code = code
        self.output_path = output_path


class DocumentIRSchemaError(RuntimeError):
    """Raised when the canonical schema itself is unavailable or invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True, kw_only=True)
class EncodedImageAsset:
    """One portable encoded crop before it is registered in Document IR."""

    data: bytes
    media_type: MediaType
    extension: str
    pixel_width: int
    pixel_height: int
    dpi_x: float | None = None
    dpi_y: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("encoded image data must be non-empty immutable bytes")
        if not isinstance(self.media_type, MediaType):
            try:
                object.__setattr__(self, "media_type", MediaType(self.media_type))
            except (TypeError, ValueError) as exc:
                raise ValueError("encoded image media_type must be PNG or JPEG") from exc
        allowed_extensions = {
            MediaType.PNG: {"png"},
            MediaType.JPEG: {"jpg", "jpeg"},
        }
        if self.extension not in allowed_extensions[self.media_type]:
            raise ValueError("encoded image extension does not match its media type")
        for field_name in ("pixel_width", "pixel_height"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        for field_name in ("dpi_x", "dpi_y"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) <= 0
            ):
                raise ValueError(f"{field_name} must be a positive finite number")
            object.__setattr__(self, field_name, float(value))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetPayload:
    """Verified bytes paired with their final Document IR registry entry."""

    asset: Asset
    data: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset payload asset must be an Asset")
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("asset payload data must be non-empty immutable bytes")
        digest = hashlib.sha256(self.data).hexdigest()
        if digest != self.asset.sha256:
            raise ValueError("asset payload digest must match its registry entry")


@dataclass(frozen=True, slots=True, kw_only=True)
class BundleWriteResult:
    """Published paths for one self-contained extraction bundle."""

    bundle_root: Path
    document_path: Path
    asset_paths: tuple[Path, ...]

    def __post_init__(self) -> None:
        root = Path(self.bundle_root)
        document_path = Path(self.document_path)
        if isinstance(self.asset_paths, (str, bytes, bytearray)):
            raise TypeError("asset_paths must be a sequence of paths")
        asset_paths = tuple(Path(path) for path in self.asset_paths)
        if document_path.parent != root:
            raise ValueError("document_path must be directly inside bundle_root")
        for path in asset_paths:
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError("asset paths must remain inside bundle_root") from exc
        object.__setattr__(self, "bundle_root", root)
        object.__setattr__(self, "document_path", document_path)
        object.__setattr__(self, "asset_paths", asset_paths)


class ImageAssetEncoder(Protocol):
    """Encode one source-pixel image candidate as a portable bundle asset."""

    def encode_png_crop(
        self,
        image: ImageInput,
        bbox: PixelBoundingBox,
    ) -> EncodedImageAsset:
        """Return deterministic encoded bytes for exactly one source crop."""


class DocumentIRValidator(Protocol):
    """Validate a semantic model against the canonical formal schema."""

    def validate(self, document: DocumentIR) -> None:
        """Raise an actionable validation error when the model is not schema-valid."""


class DocumentBundleWriter(Protocol):
    """Publish a complete JSON-and-assets directory as one transaction."""

    def write(
        self,
        document: DocumentIR,
        assets: Sequence[AssetPayload],
        output_directory: str | PathLike[str],
    ) -> BundleWriteResult:
        """Atomically publish a new bundle without overwriting an existing path."""
