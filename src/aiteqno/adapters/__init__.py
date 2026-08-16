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
from .extraction import (
    DEFAULT_MAX_ENCODED_ASSET_BYTES,
    DOCUMENT_IR_FILENAME,
    FilesystemDocumentBundleWriter,
    PillowPngAssetEncoder,
)
from .evaluation import FilesystemEvaluationWriter, PythonDocxObserver
from .json_schema import JsonSchemaDocumentIRValidator
from .ocr_fake import (
    FAKE_OCR_PROVIDER,
    FAKE_OCR_PROVIDER_VERSION,
    FakeOcrBackend,
    FakeOcrObservation,
)
from .preview import (
    DEFAULT_MAX_PREVIEW_PIXELS,
    DEFAULT_PREVIEW_DPI,
    DEFAULT_PREVIEW_FONT_FALLBACKS,
    PillowPreviewRenderer,
)
from .structure import (
    DEFAULT_FALLBACK_DPI,
    DEFAULT_MAX_PNG_BYTES,
    DEFAULT_MAX_PNG_PIXELS,
    STRUCTURE_PROVIDER,
    STRUCTURE_PROVIDER_VERSION,
    OpenCvStructureExtractor,
    PillowPngDecoder,
)
from .tesseract import (
    MIN_TESSERACT_MAJOR_VERSION,
    TESSERACT_PROVIDER,
    TesseractOcrBackend,
)

__all__ = [
    "DEFAULT_FALLBACK_FONT",
    "DEFAULT_FALLBACK_DPI",
    "DEFAULT_MAX_ASSET_BYTES",
    "DEFAULT_MAX_ASSET_PIXELS",
    "DEFAULT_MAX_ENCODED_ASSET_BYTES",
    "DEFAULT_MAX_PREVIEW_PIXELS",
    "DEFAULT_MAX_PNG_BYTES",
    "DEFAULT_MAX_PNG_PIXELS",
    "DEFAULT_PAGE_MARGIN_PT",
    "DEFAULT_PREVIEW_DPI",
    "DEFAULT_PREVIEW_FONT_FALLBACKS",
    "DEFAULT_SUPPORTED_FONTS",
    "DOCUMENT_IR_FILENAME",
    "FAKE_OCR_PROVIDER",
    "FAKE_OCR_PROVIDER_VERSION",
    "FakeOcrBackend",
    "FakeOcrObservation",
    "FilesystemEvaluationWriter",
    "FilesystemDocumentBundleWriter",
    "JsonSchemaDocumentIRValidator",
    "BundleAssetResolver",
    "PillowPreviewRenderer",
    "PillowPngDecoder",
    "PillowPngAssetEncoder",
    "PythonDocxRenderer",
    "PythonDocxObserver",
    "OpenCvStructureExtractor",
    "MIN_TESSERACT_MAJOR_VERSION",
    "STRUCTURE_PROVIDER",
    "STRUCTURE_PROVIDER_VERSION",
    "TESSERACT_PROVIDER",
    "TesseractOcrBackend",
]
