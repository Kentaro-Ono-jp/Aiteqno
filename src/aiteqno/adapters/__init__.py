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
from .libreoffice import (
    DEFAULT_LIBREOFFICE_TIMEOUT_SECONDS,
    DEFAULT_SNAPSHOT_DPI,
    LIBREOFFICE_RENDERER_NAME,
    PDFTOPPM_RASTERIZER_NAME,
    LibreOfficeSnapshotEvidence,
    LibreOfficeSnapshotPage,
    LibreOfficeSnapshotRenderer,
)
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
    DEFAULT_TESSERACT_REGION_PADDING_PX,
    MIN_TESSERACT_MAJOR_VERSION,
    TESSERACT_CROP_PADDING_MAPPING_POLICY,
    TESSERACT_CROP_PADDING_OPERATION_ORDER,
    TESSERACT_CROP_PADDING_VERSION,
    TESSERACT_INVOCATION_EVIDENCE_VERSION,
    TESSERACT_PROVIDER,
    TesseractCropPaddingEvidence,
    TesseractCropPaddingTargetEvidence,
    TesseractInvocationEvidence,
    TesseractOcrBackend,
    TesseractTrainedDataFileEvidence,
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
    "DEFAULT_LIBREOFFICE_TIMEOUT_SECONDS",
    "DEFAULT_SNAPSHOT_DPI",
    "DEFAULT_PAGE_MARGIN_PT",
    "DEFAULT_PREVIEW_DPI",
    "DEFAULT_PREVIEW_FONT_FALLBACKS",
    "DEFAULT_SUPPORTED_FONTS",
    "DEFAULT_TESSERACT_REGION_PADDING_PX",
    "DOCUMENT_IR_FILENAME",
    "FAKE_OCR_PROVIDER",
    "FAKE_OCR_PROVIDER_VERSION",
    "FakeOcrBackend",
    "FakeOcrObservation",
    "FilesystemEvaluationWriter",
    "FilesystemDocumentBundleWriter",
    "JsonSchemaDocumentIRValidator",
    "LIBREOFFICE_RENDERER_NAME",
    "PDFTOPPM_RASTERIZER_NAME",
    "LibreOfficeSnapshotEvidence",
    "LibreOfficeSnapshotPage",
    "LibreOfficeSnapshotRenderer",
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
    "TESSERACT_CROP_PADDING_MAPPING_POLICY",
    "TESSERACT_CROP_PADDING_OPERATION_ORDER",
    "TESSERACT_CROP_PADDING_VERSION",
    "TESSERACT_INVOCATION_EVIDENCE_VERSION",
    "TesseractCropPaddingEvidence",
    "TesseractCropPaddingTargetEvidence",
    "TesseractInvocationEvidence",
    "TesseractOcrBackend",
    "TesseractTrainedDataFileEvidence",
]
