"""Infrastructure implementations for external libraries and filesystems."""

from .assets import (
    DEFAULT_MAX_ASSET_BYTES,
    DEFAULT_MAX_ASSET_PIXELS,
    BundleAssetResolver,
)
from .docx import (
    DEFAULT_FALLBACK_FONT,
    DEFAULT_PAGE_MARGIN_PT,
    DEFAULT_SUPPORTED_FONTS,
    PythonDocxRenderer,
)
from .preview import (
    DEFAULT_MAX_PREVIEW_PIXELS,
    DEFAULT_PREVIEW_DPI,
    DEFAULT_PREVIEW_FONT_FALLBACKS,
    PillowPreviewRenderer,
)

__all__ = [
    "DEFAULT_FALLBACK_FONT",
    "DEFAULT_MAX_ASSET_BYTES",
    "DEFAULT_MAX_ASSET_PIXELS",
    "DEFAULT_MAX_PREVIEW_PIXELS",
    "DEFAULT_PAGE_MARGIN_PT",
    "DEFAULT_PREVIEW_DPI",
    "DEFAULT_PREVIEW_FONT_FALLBACKS",
    "DEFAULT_SUPPORTED_FONTS",
    "BundleAssetResolver",
    "PillowPreviewRenderer",
    "PythonDocxRenderer",
]
