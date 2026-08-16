"""Filesystem adapter for safe, content-addressed Document IR assets."""

from __future__ import annotations

import hashlib
from io import BytesIO
from os import PathLike
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError

from aiteqno.domain import Asset, MediaType
from aiteqno.ports.assets import AssetResolutionError, ResolvedAsset


DEFAULT_MAX_ASSET_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_ASSET_PIXELS = 40_000_000

_PIL_FORMAT_BY_MEDIA_TYPE = {
    MediaType.PNG: "PNG",
    MediaType.JPEG: "JPEG",
}


class BundleAssetResolver:
    """Read verified image assets rooted inside one Document IR bundle."""

    def __init__(
        self,
        bundle_root: str | PathLike[str],
        *,
        max_asset_bytes: int = DEFAULT_MAX_ASSET_BYTES,
        max_asset_pixels: int = DEFAULT_MAX_ASSET_PIXELS,
    ) -> None:
        if max_asset_bytes <= 0:
            raise ValueError("max_asset_bytes must be positive")
        if max_asset_pixels <= 0:
            raise ValueError("max_asset_pixels must be positive")
        root = Path(bundle_root)
        if not root.is_dir():
            raise ValueError(f"bundle_root must be an existing directory: {root}")
        self._bundle_root = root.resolve(strict=True)
        self._max_asset_bytes = max_asset_bytes
        self._max_asset_pixels = max_asset_pixels

    @property
    def bundle_root(self) -> Path:
        return self._bundle_root

    def resolve(self, asset: Asset) -> ResolvedAsset:
        """Resolve a registry path and verify limits, digest, type, and dimensions."""

        candidate = self._bundle_root.joinpath(*PurePosixPath(asset.path).parts)
        try:
            resolved_path = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise AssetResolutionError(
                "asset_missing",
                asset.id,
                f"asset {asset.id!r} is missing at {asset.path!r}",
            ) from exc

        try:
            resolved_path.relative_to(self._bundle_root)
        except ValueError as exc:
            raise AssetResolutionError(
                "asset_path_outside_bundle",
                asset.id,
                f"asset {asset.id!r} resolves outside the bundle root",
            ) from exc
        if not resolved_path.is_file():
            raise AssetResolutionError(
                "asset_not_file",
                asset.id,
                f"asset {asset.id!r} does not resolve to a regular file",
            )

        byte_size = resolved_path.stat().st_size
        if byte_size > self._max_asset_bytes:
            raise AssetResolutionError(
                "asset_too_large",
                asset.id,
                f"asset {asset.id!r} is {byte_size} bytes; limit is {self._max_asset_bytes}",
            )
        try:
            data = resolved_path.read_bytes()
        except OSError as exc:
            raise AssetResolutionError(
                "asset_unreadable",
                asset.id,
                f"asset {asset.id!r} could not be read",
            ) from exc
        if len(data) > self._max_asset_bytes:
            raise AssetResolutionError(
                "asset_too_large",
                asset.id,
                f"asset {asset.id!r} exceeds the {self._max_asset_bytes}-byte limit",
            )

        actual_digest = hashlib.sha256(data).hexdigest()
        if actual_digest != asset.sha256:
            raise AssetResolutionError(
                "asset_digest_mismatch",
                asset.id,
                (
                    f"asset {asset.id!r} digest mismatch: expected {asset.sha256}, "
                    f"received {actual_digest}"
                ),
            )

        try:
            with Image.open(BytesIO(data)) as image:
                image_format = image.format
                width, height = image.size
                if width * height > self._max_asset_pixels:
                    raise AssetResolutionError(
                        "asset_pixel_limit_exceeded",
                        asset.id,
                        (
                            f"asset {asset.id!r} has {width * height} pixels; "
                            f"limit is {self._max_asset_pixels}"
                        ),
                    )
                image.verify()
        except AssetResolutionError:
            raise
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise AssetResolutionError(
                "asset_invalid_image",
                asset.id,
                f"asset {asset.id!r} is not a valid portable image",
            ) from exc

        expected_format = _PIL_FORMAT_BY_MEDIA_TYPE[asset.media_type]
        if image_format != expected_format:
            raise AssetResolutionError(
                "asset_media_type_mismatch",
                asset.id,
                (
                    f"asset {asset.id!r} declares {asset.media_type.value} but "
                    f"decodes as {image_format or 'unknown'}"
                ),
            )
        if (width, height) != (asset.pixel_width, asset.pixel_height):
            raise AssetResolutionError(
                "asset_dimensions_mismatch",
                asset.id,
                (
                    f"asset {asset.id!r} declares "
                    f"{asset.pixel_width}x{asset.pixel_height} but decodes as "
                    f"{width}x{height}"
                ),
            )
        return ResolvedAsset(asset_id=asset.id, source_path=resolved_path, data=data)
