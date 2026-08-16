"""Port contracts for deterministic Document IR PNG previews."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Protocol

from aiteqno.domain import DocumentIR


class PreviewRenderError(RuntimeError):
    """Raised when a PNG preview cannot be rendered safely."""


@dataclass(frozen=True, slots=True)
class PreviewWarning:
    """One explicit preview approximation or fallback."""

    code: str
    message: str
    page_id: str | None = None
    element_id: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "page_id": self.page_id,
            "element_id": self.element_id,
        }


@dataclass(frozen=True, slots=True)
class PreviewFontSubstitution:
    """A deterministic replacement selected for one text element."""

    element_id: str
    requested: str
    replacement: str

    def to_dict(self) -> dict[str, str]:
        return {
            "element_id": self.element_id,
            "requested": self.requested,
            "replacement": self.replacement,
        }


@dataclass(frozen=True, slots=True)
class PreviewRenderReport:
    """Machine-readable account of a successful preview projection."""

    renderer_name: str
    renderer_version: str
    ir_version: str
    output_path: str
    output_sha256: str
    dpi: float
    canvas_width_px: int
    canvas_height_px: int
    rendered_element_ids: tuple[str, ...]
    fallback_element_ids: tuple[str, ...]
    omitted_element_ids: tuple[str, ...]
    warnings: tuple[PreviewWarning, ...]
    font_substitutions: tuple[PreviewFontSubstitution, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "renderer_name": self.renderer_name,
            "renderer_version": self.renderer_version,
            "ir_version": self.ir_version,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "dpi": self.dpi,
            "canvas_width_px": self.canvas_width_px,
            "canvas_height_px": self.canvas_height_px,
            "rendered_element_ids": list(self.rendered_element_ids),
            "fallback_element_ids": list(self.fallback_element_ids),
            "omitted_element_ids": list(self.omitted_element_ids),
            "warnings": [warning.to_dict() for warning in self.warnings],
            "font_substitutions": [
                substitution.to_dict() for substitution in self.font_substitutions
            ],
        }


@dataclass(frozen=True, slots=True)
class PreviewRenderResult:
    """The generated PNG and its projection report."""

    output_path: Path
    report: PreviewRenderReport


class PreviewRenderer(Protocol):
    """Adapter boundary used by the application preview service."""

    def render(
        self,
        document: DocumentIR,
        output_path: str | PathLike[str],
        *,
        dpi: float,
    ) -> PreviewRenderResult:
        """Project validated Document IR to a deterministic PNG."""
