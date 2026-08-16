import base64
import hashlib
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from PIL import Image

from aiteqno.adapters import BundleAssetResolver
from aiteqno.domain import DocumentIR
from aiteqno.ports import AssetResolutionError


FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "document_ir"
FIXTURE_PATH = FIXTURE_DIRECTORY / "canonical.document.ir.json"
ASSET_B64_PATH = FIXTURE_DIRECTORY / "canonical-logo.png.b64"


def load_asset_and_bytes():
    document = DocumentIR.from_json(FIXTURE_PATH.read_text(encoding="utf-8"))
    data = base64.b64decode(ASSET_B64_PATH.read_text(encoding="ascii"))
    return document.assets[0], data


def write_asset(root: Path, asset, data: bytes) -> Path:
    path = root / asset.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class BundleAssetResolverTest(unittest.TestCase):
    def test_valid_content_addressed_png_is_returned_as_immutable_bytes(self):
        asset, data = load_asset_and_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), asset.sha256)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = write_asset(root, asset, data)
            resolved = BundleAssetResolver(root).resolve(asset)

        self.assertEqual(resolved.asset_id, asset.id)
        self.assertEqual(resolved.source_path, source_path.resolve())
        self.assertEqual(resolved.data, data)
        self.assertEqual(resolved.byte_size, len(data))

    def test_missing_and_tampered_assets_have_stable_diagnostic_codes(self):
        asset, data = load_asset_and_bytes()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            resolver = BundleAssetResolver(root)
            with self.assertRaises(AssetResolutionError) as missing_context:
                resolver.resolve(asset)
            write_asset(root, asset, data + b"tampered")
            with self.assertRaises(AssetResolutionError) as digest_context:
                resolver.resolve(asset)

        self.assertEqual(missing_context.exception.code, "asset_missing")
        self.assertEqual(digest_context.exception.code, "asset_digest_mismatch")
        self.assertEqual(digest_context.exception.asset_id, asset.id)

    def test_media_type_and_declared_dimensions_are_verified_after_digest(self):
        asset, data = load_asset_and_bytes()
        jpeg_buffer = BytesIO()
        Image.new("RGB", (128, 96), "white").save(jpeg_buffer, format="JPEG")
        jpeg_data = jpeg_buffer.getvalue()
        jpeg_digest = hashlib.sha256(jpeg_data).hexdigest()
        png_declaring_jpeg = replace(
            asset,
            path=f"assets/sha256-{jpeg_digest}.png",
            sha256=jpeg_digest,
        )
        wrong_dimensions = replace(asset, pixel_width=127)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_asset(root, png_declaring_jpeg, jpeg_data)
            write_asset(root, wrong_dimensions, data)
            resolver = BundleAssetResolver(root)
            with self.assertRaises(AssetResolutionError) as media_context:
                resolver.resolve(png_declaring_jpeg)
            with self.assertRaises(AssetResolutionError) as dimensions_context:
                resolver.resolve(wrong_dimensions)

        self.assertEqual(media_context.exception.code, "asset_media_type_mismatch")
        self.assertEqual(
            dimensions_context.exception.code,
            "asset_dimensions_mismatch",
        )

    def test_invalid_image_and_configurable_limits_are_rejected(self):
        asset, data = load_asset_and_bytes()
        invalid_data = b"not a png"
        invalid_digest = hashlib.sha256(invalid_data).hexdigest()
        invalid_asset = replace(
            asset,
            path=f"assets/sha256-{invalid_digest}.png",
            sha256=invalid_digest,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_asset(root, asset, data)
            write_asset(root, invalid_asset, invalid_data)
            with self.assertRaises(AssetResolutionError) as invalid_context:
                BundleAssetResolver(root).resolve(invalid_asset)
            with self.assertRaises(AssetResolutionError) as size_context:
                BundleAssetResolver(root, max_asset_bytes=len(data) - 1).resolve(asset)
            with self.assertRaises(AssetResolutionError) as pixel_context:
                BundleAssetResolver(root, max_asset_pixels=128 * 96 - 1).resolve(asset)

        self.assertEqual(invalid_context.exception.code, "asset_invalid_image")
        self.assertEqual(size_context.exception.code, "asset_too_large")
        self.assertEqual(pixel_context.exception.code, "asset_pixel_limit_exceeded")


if __name__ == "__main__":
    unittest.main()
