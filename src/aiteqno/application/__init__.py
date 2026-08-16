"""Use-case orchestration built on the domain and ports layers."""

from .extract import (
    EXTRACTION_PROVIDER,
    EXTRACTION_PROVIDER_VERSION,
    PAGE_COVERING_IMAGE_FRACTION,
    ExtractionDiagnostic,
    PngExtractionError,
    PngExtractionResult,
    extract_png,
)
from .evaluate import (
    COMPONENT_WEIGHTS,
    DEFAULT_RESTORATION_THRESHOLD,
    EVALUATOR_NAME,
    EVALUATOR_VERSION,
    GEOMETRY_CENTER_WEIGHT,
    GEOMETRY_IOU_WEIGHT,
    MIN_TEXT_ELEMENT_SIMILARITY,
    EvaluationConfig,
    build_evaluation_reference,
    evaluate_restoration,
    evaluate_restoration_input,
    normalize_evaluation_text,
)
from .preview import render_preview
from .render import render_docx

__all__ = [
    "EXTRACTION_PROVIDER",
    "EXTRACTION_PROVIDER_VERSION",
    "PAGE_COVERING_IMAGE_FRACTION",
    "ExtractionDiagnostic",
    "COMPONENT_WEIGHTS",
    "DEFAULT_RESTORATION_THRESHOLD",
    "EVALUATOR_NAME",
    "EVALUATOR_VERSION",
    "EvaluationConfig",
    "GEOMETRY_CENTER_WEIGHT",
    "GEOMETRY_IOU_WEIGHT",
    "MIN_TEXT_ELEMENT_SIMILARITY",
    "PngExtractionError",
    "PngExtractionResult",
    "extract_png",
    "build_evaluation_reference",
    "evaluate_restoration",
    "evaluate_restoration_input",
    "normalize_evaluation_text",
    "render_docx",
    "render_preview",
]
