"""Application orchestration for deterministic PNG previews."""

from __future__ import annotations

from os import PathLike

from aiteqno.domain import DocumentIR, validate_document
from aiteqno.ports import PreviewRenderer, PreviewRenderResult


def render_preview(
    document: DocumentIR,
    output_path: str | PathLike[str],
    *,
    renderer: PreviewRenderer,
    dpi: float = 144.0,
) -> PreviewRenderResult:
    """Validate and project Document IR through an injected PNG adapter."""

    if not isinstance(document, DocumentIR):
        raise TypeError("document must be a DocumentIR")
    validate_document(document)
    return renderer.render(document, output_path, dpi=dpi)
