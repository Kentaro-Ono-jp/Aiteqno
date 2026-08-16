"""Protocols implemented by Aiteqno infrastructure adapters."""

from .assets import AssetResolutionError, AssetResolver, ResolvedAsset
from .docx import (
    DocxRenderError,
    DocxRenderer,
    DocxRenderReport,
    DocxRenderResult,
    FontSubstitution,
    RenderPolicy,
    RenderWarning,
)
from .preview import (
    PreviewFontSubstitution,
    PreviewRenderer,
    PreviewRenderError,
    PreviewRenderReport,
    PreviewRenderResult,
    PreviewWarning,
)

__all__ = [
    "AssetResolutionError",
    "AssetResolver",
    "DocxRenderError",
    "DocxRenderer",
    "DocxRenderReport",
    "DocxRenderResult",
    "FontSubstitution",
    "PreviewFontSubstitution",
    "PreviewRenderer",
    "PreviewRenderError",
    "PreviewRenderReport",
    "PreviewRenderResult",
    "PreviewWarning",
    "RenderPolicy",
    "RenderWarning",
    "ResolvedAsset",
]
