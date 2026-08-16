"""Infrastructure implementations for external libraries and filesystems."""

from .docx import (
    DEFAULT_FALLBACK_FONT,
    DEFAULT_PAGE_MARGIN_PT,
    DEFAULT_SUPPORTED_FONTS,
    PythonDocxRenderer,
)

__all__ = [
    "DEFAULT_FALLBACK_FONT",
    "DEFAULT_PAGE_MARGIN_PT",
    "DEFAULT_SUPPORTED_FONTS",
    "PythonDocxRenderer",
]
