"""Pillow image crops and atomic filesystem publication for extraction bundles."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from io import BytesIO
from os import PathLike
from pathlib import Path, PurePosixPath
from typing import Sequence

from PIL import Image

from aiteqno.domain import DocumentIR, MediaType, PixelBoundingBox
from aiteqno.ports.extraction import (
    AssetEncodingError,
    AssetPayload,
    BundleWriteError,
    BundleWriteResult,
    EncodedImageAsset,
)
from aiteqno.ports.structure import ImageInput


DOCUMENT_IR_FILENAME = "document.ir.json"
DEFAULT_MAX_ENCODED_ASSET_BYTES = 25 * 1024 * 1024


class PillowPngAssetEncoder:
    """Crop normalized RGB pixels and encode deterministic portable PNG assets."""

    def __init__(
        self,
        *,
        max_encoded_bytes: int = DEFAULT_MAX_ENCODED_ASSET_BYTES,
    ) -> None:
        if (
            isinstance(max_encoded_bytes, bool)
            or not isinstance(max_encoded_bytes, int)
            or max_encoded_bytes <= 0
        ):
            raise ValueError("max_encoded_bytes must be a positive integer")
        self._max_encoded_bytes = max_encoded_bytes

    def encode_png_crop(
        self,
        image: ImageInput,
        bbox: PixelBoundingBox,
    ) -> EncodedImageAsset:
        """Return a lossless PNG crop without embedding the full source page."""

        if not isinstance(image, ImageInput):
            raise TypeError("image must be an ImageInput")
        if not isinstance(bbox, PixelBoundingBox):
            raise TypeError("bbox must be a PixelBoundingBox")
        if (
            bbox.x + bbox.width > image.source.pixel_width
            or bbox.y + bbox.height > image.source.pixel_height
        ):
            raise AssetEncodingError(
                "asset_region_outside_page",
                "image asset region must remain inside the source page",
            )

        page: Image.Image | None = None
        crop: Image.Image | None = None
        try:
            page = Image.frombytes(
                "RGB",
                (image.source.pixel_width, image.source.pixel_height),
                image.pixels,
            )
            crop = page.crop(
                (
                    bbox.x,
                    bbox.y,
                    bbox.x + bbox.width,
                    bbox.y + bbox.height,
                )
            )
            encoded = BytesIO()
            crop.save(
                encoded,
                format="PNG",
                compress_level=9,
                optimize=False,
                dpi=(image.source.dpi_x, image.source.dpi_y),
            )
            data = encoded.getvalue()
        except (OSError, ValueError) as exc:
            raise AssetEncodingError(
                "asset_encode_failed",
                f"image region could not be encoded as PNG: {exc}",
            ) from exc
        finally:
            if crop is not None:
                crop.close()
            if page is not None:
                page.close()

        if len(data) > self._max_encoded_bytes:
            raise AssetEncodingError(
                "asset_encoded_size_exceeded",
                f"encoded image asset exceeds {self._max_encoded_bytes} bytes",
            )
        return EncodedImageAsset(
            data=data,
            media_type=MediaType.PNG,
            extension="png",
            pixel_width=bbox.width,
            pixel_height=bbox.height,
            dpi_x=image.source.dpi_x,
            dpi_y=image.source.dpi_y,
        )


class FilesystemDocumentBundleWriter:
    """Publish a validated extraction bundle through a same-filesystem rename."""

    def write(
        self,
        document: DocumentIR,
        assets: Sequence[AssetPayload],
        output_directory: str | PathLike[str],
    ) -> BundleWriteResult:
        """Write a new bundle atomically and never replace an existing target."""

        if not isinstance(document, DocumentIR):
            raise TypeError("document must be a DocumentIR")
        destination = Path(output_directory).expanduser().resolve(strict=False)
        if destination.exists():
            raise BundleWriteError(
                "bundle_exists",
                f"output bundle already exists: {destination}",
                output_path=destination,
            )

        ordered_payloads = _ordered_payloads(document, assets, destination)
        parent = destination.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            if not parent.is_dir():
                raise OSError(f"output parent is not a directory: {parent}")
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{destination.name}.tmp-",
                    dir=parent,
                )
            )
        except OSError as exc:
            raise BundleWriteError(
                "bundle_staging_failed",
                f"could not create bundle staging directory: {exc}",
                output_path=destination,
            ) from exc

        published = False
        try:
            (staging / "assets").mkdir()
            for payload in ordered_payloads:
                relative = PurePosixPath(payload.asset.path)
                target = staging.joinpath(*relative.parts)
                _write_verified_file(
                    target,
                    payload.data,
                    expected_sha256=payload.asset.sha256,
                )

            json_data = document.to_json(indent=2).encode("utf-8")
            _write_verified_file(
                staging / DOCUMENT_IR_FILENAME,
                json_data,
                expected_sha256=hashlib.sha256(json_data).hexdigest(),
            )
            if destination.exists():
                raise BundleWriteError(
                    "bundle_exists",
                    f"output bundle appeared during publication: {destination}",
                    output_path=destination,
                )
            os.rename(staging, destination)
            published = True
        except BundleWriteError:
            raise
        except OSError as exc:
            raise BundleWriteError(
                "bundle_write_failed",
                f"bundle could not be published atomically: {exc}",
                output_path=destination,
            ) from exc
        finally:
            if not published and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

        asset_paths = tuple(
            destination.joinpath(*PurePosixPath(asset.path).parts)
            for asset in document.assets
        )
        return BundleWriteResult(
            bundle_root=destination,
            document_path=destination / DOCUMENT_IR_FILENAME,
            asset_paths=asset_paths,
        )


def _ordered_payloads(
    document: DocumentIR,
    assets: Sequence[AssetPayload],
    destination: Path,
) -> tuple[AssetPayload, ...]:
    if isinstance(assets, (str, bytes, bytearray)):
        raise TypeError("assets must be a sequence of AssetPayload values")
    payloads = tuple(assets)
    if any(not isinstance(payload, AssetPayload) for payload in payloads):
        raise TypeError("assets must contain only AssetPayload values")
    payloads_by_id = {payload.asset.id: payload for payload in payloads}
    if len(payloads_by_id) != len(payloads):
        raise BundleWriteError(
            "bundle_asset_mismatch",
            "asset payload IDs must be unique",
            output_path=destination,
        )
    expected_ids = {asset.id for asset in document.assets}
    if set(payloads_by_id) != expected_ids:
        raise BundleWriteError(
            "bundle_asset_mismatch",
            "asset payloads must exactly match the Document IR registry",
            output_path=destination,
        )
    ordered: list[AssetPayload] = []
    for asset in document.assets:
        payload = payloads_by_id[asset.id]
        if payload.asset != asset:
            raise BundleWriteError(
                "bundle_asset_mismatch",
                f"asset payload metadata does not match registry entry {asset.id!r}",
                output_path=destination,
            )
        ordered.append(payload)
    return tuple(ordered)


def _write_verified_file(
    target: Path,
    data: bytes,
    *,
    expected_sha256: str,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        actual_sha256 = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise OSError(
                f"temporary file digest mismatch for {target.name}: "
                f"expected {expected_sha256}, received {actual_sha256}"
            )
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
