"""Port contracts for rendering validated Document IR as DOCX."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from os import PathLike
from pathlib import Path
from typing import Protocol

from aiteqno.domain import DocumentIR


class RenderPolicy(str, Enum):
    """How a renderer handles features it cannot represent exactly."""

    BEST_EFFORT = "best_effort"
    STRICT = "strict"


class DocxRenderError(RuntimeError):
    """Raised when a DOCX cannot be rendered under the requested policy."""


@dataclass(frozen=True, slots=True)
class RenderWarning:
    """One explicit approximation or omission made during rendering."""

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
class FontSubstitution:
    """A deterministic replacement for an unsupported font hint."""

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
class DocxRenderReport:
    """Machine-readable account of a successful DOCX render."""

    renderer_name: str
    renderer_version: str
    ir_version: str
    output_path: str
    output_sha256: str
    rendered_element_ids: tuple[str, ...]
    fallback_element_ids: tuple[str, ...]
    omitted_element_ids: tuple[str, ...]
    warnings: tuple[RenderWarning, ...]
    errors: tuple[str, ...]
    font_substitutions: tuple[FontSubstitution, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "renderer_name": self.renderer_name,
            "renderer_version": self.renderer_version,
            "ir_version": self.ir_version,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "rendered_element_ids": list(self.rendered_element_ids),
            "fallback_element_ids": list(self.fallback_element_ids),
            "omitted_element_ids": list(self.omitted_element_ids),
            "warnings": [warning.to_dict() for warning in self.warnings],
            "errors": list(self.errors),
            "font_substitutions": [
                substitution.to_dict() for substitution in self.font_substitutions
            ],
        }


@dataclass(frozen=True, slots=True)
class DocxRenderResult:
    """The generated artifact and its render report."""

    output_path: Path
    report: DocxRenderReport


class DocxRenderer(Protocol):
    """Adapter boundary used by the application render service."""

    def render(
        self,
        document: DocumentIR,
        output_path: str | PathLike[str],
        *,
        policy: RenderPolicy = RenderPolicy.BEST_EFFORT,
    ) -> DocxRenderResult:
        """Render ``document`` without consulting its original source image."""
