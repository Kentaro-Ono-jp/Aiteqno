"""Command-line presentation layer for Aiteqno application services."""

from .main import (
    ASSET_DIRECTORY_NAME,
    DOCUMENT_IR_FILENAME,
    PRODUCTION_OCR_OPTIONS,
    PRODUCTION_OCR_REGION_GROUPING,
    PRODUCTION_OCR_REGION_PADDING_PX,
    RECONSTRUCTED_DOCX_FILENAME,
    RECONSTRUCTED_PREVIEW_FILENAME,
    CliError,
    CliRuntime,
    ExitCode,
    build_parser,
    default_runtime,
    main,
)

__all__ = [
    "ASSET_DIRECTORY_NAME",
    "CliError",
    "CliRuntime",
    "DOCUMENT_IR_FILENAME",
    "ExitCode",
    "PRODUCTION_OCR_OPTIONS",
    "PRODUCTION_OCR_REGION_GROUPING",
    "PRODUCTION_OCR_REGION_PADDING_PX",
    "RECONSTRUCTED_DOCX_FILENAME",
    "RECONSTRUCTED_PREVIEW_FILENAME",
    "build_parser",
    "default_runtime",
    "main",
]
