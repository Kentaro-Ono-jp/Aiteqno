"""Application orchestration for DOCX reconstruction."""

from __future__ import annotations

from os import PathLike

from aiteqno.domain import DocumentIR, validate_document
from aiteqno.ports import DocxRenderer, DocxRenderResult, RenderPolicy


def render_docx(
    document: DocumentIR,
    output_path: str | PathLike[str],
    *,
    renderer: DocxRenderer,
    policy: RenderPolicy = RenderPolicy.BEST_EFFORT,
) -> DocxRenderResult:
    """Validate and render Document IR through an injected DOCX adapter."""

    if not isinstance(document, DocumentIR):
        raise TypeError("document must be a DocumentIR")
    validate_document(document)
    return renderer.render(document, output_path, policy=RenderPolicy(policy))
