"""Windows-friendly command-line composition root for Aiteqno V1."""

from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import TextIO

from aiteqno import __version__
from aiteqno.adapters import (
    BundleAssetResolver,
    FilesystemDocumentBundleWriter,
    JsonSchemaDocumentIRValidator,
    OpenCvStructureExtractor,
    PillowPngAssetEncoder,
    PillowPngDecoder,
    PillowPreviewRenderer,
    PythonDocxRenderer,
    TesseractOcrBackend,
)
from aiteqno.adapters.json_schema import document_ir_from_file
from aiteqno.application import (
    PngExtractionError,
    PngExtractionResult,
    extract_png,
    render_docx,
    render_preview,
)
from aiteqno.domain import DocumentIR, DocumentIRValidationError
from aiteqno.ports import (
    DEFAULT_OCR_LANGUAGES,
    DocxRenderError,
    DocxRenderer,
    DocumentBundleWriter,
    DocumentIRValidator,
    ImageAssetEncoder,
    OcrBackend,
    OcrOptions,
    OcrRegionGroupingConfig,
    PngDecoder,
    PreviewRenderError,
    PreviewRenderer,
    StructureExtractor,
)


DOCUMENT_IR_FILENAME = "document.ir.json"
RECONSTRUCTED_DOCX_FILENAME = "reconstructed.docx"
RECONSTRUCTED_PREVIEW_FILENAME = "reconstructed.png"
ASSET_DIRECTORY_NAME = "assets"

# Public OCR regions are short source-detected lines or labels. The profile is
# independent of OCR text and fixture identity: source geometry determines the
# same-row crop plan, while a small white margin protects edge glyphs.
PRODUCTION_OCR_REGION_PADDING_PX = 4
PRODUCTION_OCR_OPTIONS = OcrOptions(page_segmentation_mode=8)
PRODUCTION_OCR_REGION_GROUPING = OcrRegionGroupingConfig(
    enabled=True,
    minimum_vertical_overlap_ratio=0.45,
    maximum_horizontal_gap_height_ratio=2.0,
    block_vertical_separators=True,
)

_DEPENDENCY_ERROR_CODES = frozenset(
    {
        "document_ir_schema_unavailable",
        "ocr_executable_missing",
        "ocr_language_missing",
        "ocr_unsupported_version",
    }
)


class ExitCode(IntEnum):
    """Stable process status codes for scripts and CI."""

    SUCCESS = 0
    OPERATIONAL_ERROR = 1
    USAGE_ERROR = 2
    INPUT_ERROR = 3
    OUTPUT_CONFLICT = 4
    DEPENDENCY_ERROR = 5


class CliError(RuntimeError):
    """A user-facing CLI failure with stable machine-readable metadata."""

    def __init__(self, code: str, message: str, exit_code: ExitCode) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class CliRuntime:
    """Injected adapters used by the thin command layer."""

    decoder: PngDecoder
    structure_extractor: StructureExtractor
    ocr_backend: OcrBackend
    asset_encoder: ImageAssetEncoder
    validator: DocumentIRValidator
    bundle_writer: DocumentBundleWriter
    docx_renderer_factory: Callable[[Path], DocxRenderer]
    preview_renderer_factory: Callable[[Path], PreviewRenderer]
    ocr_options: OcrOptions = OcrOptions()
    ocr_region_grouping: OcrRegionGroupingConfig = OcrRegionGroupingConfig()


def default_runtime() -> CliRuntime:
    """Build the local V1 adapter set without probing Tesseract eagerly."""

    executable = os.environ.get("AITEQNO_TESSERACT_EXECUTABLE") or None
    tessdata = os.environ.get("AITEQNO_TESSDATA_PREFIX") or None
    return CliRuntime(
        decoder=PillowPngDecoder(),
        structure_extractor=OpenCvStructureExtractor(),
        ocr_backend=TesseractOcrBackend(
            executable_path=executable,
            tessdata_prefix=tessdata,
            region_padding_px=PRODUCTION_OCR_REGION_PADDING_PX,
        ),
        asset_encoder=PillowPngAssetEncoder(),
        validator=JsonSchemaDocumentIRValidator(),
        bundle_writer=FilesystemDocumentBundleWriter(),
        docx_renderer_factory=lambda bundle_root: PythonDocxRenderer(
            asset_resolver=BundleAssetResolver(bundle_root)
        ),
        preview_renderer_factory=lambda bundle_root: PillowPreviewRenderer(
            asset_resolver=BundleAssetResolver(bundle_root)
        ),
        ocr_options=PRODUCTION_OCR_OPTIONS,
        ocr_region_grouping=PRODUCTION_OCR_REGION_GROUPING,
    )


def build_parser() -> argparse.ArgumentParser:
    """Create the public CLI grammar and help text."""

    parser = argparse.ArgumentParser(
        prog="aiteqno",
        description="Extract and reconstruct a single-page PNG through Document IR.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    extract_parser = commands.add_parser(
        "extract",
        help="extract one PNG to Document IR and sibling assets",
    )
    _add_png_input(extract_parser)
    extract_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="DOCUMENT_IR_JSON",
        help="new JSON path; assets are written to a sibling assets directory",
    )
    _add_languages(extract_parser)

    render_parser = commands.add_parser(
        "render",
        help="render Document IR and its assets to DOCX",
    )
    _add_ir_input(render_parser)
    render_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="DOCX",
        help="new .docx output path",
    )

    preview_parser = commands.add_parser(
        "preview",
        help="render Document IR and its assets to a PNG preview",
    )
    _add_ir_input(preview_parser)
    preview_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="PNG",
        help="new .png output path",
    )
    preview_parser.add_argument(
        "--dpi",
        type=_positive_float,
        default=144.0,
        metavar="DPI",
        help="preview resolution in dots per inch (default: 144)",
    )

    roundtrip_parser = commands.add_parser(
        "roundtrip",
        help="extract, render DOCX, and render PNG into one new directory",
    )
    _add_png_input(roundtrip_parser)
    roundtrip_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="DIRECTORY",
        help="new output directory for IR, assets, DOCX, and PNG",
    )
    _add_languages(roundtrip_parser)
    roundtrip_parser.add_argument(
        "--dpi",
        type=_positive_float,
        default=144.0,
        metavar="DPI",
        help="preview resolution in dots per inch (default: 144)",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime: CliRuntime | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one command and return a stable process exit code."""

    output_stream = stdout if stdout is not None else sys.stdout
    error_stream = stderr if stderr is not None else sys.stderr
    try:
        with redirect_stdout(output_stream), redirect_stderr(error_stream):
            arguments = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        selected_runtime = runtime if runtime is not None else default_runtime()
        if arguments.command == "extract":
            _command_extract(arguments, selected_runtime, output_stream, error_stream)
        elif arguments.command == "render":
            _command_render(arguments, selected_runtime, output_stream, error_stream)
        elif arguments.command == "preview":
            _command_preview(arguments, selected_runtime, output_stream, error_stream)
        elif arguments.command == "roundtrip":
            _command_roundtrip(arguments, selected_runtime, output_stream, error_stream)
        else:  # pragma: no cover - argparse guarantees the command set
            raise CliError(
                "unknown_command",
                f"unsupported command: {arguments.command}",
                ExitCode.USAGE_ERROR,
            )
    except CliError as exc:
        _print_error(exc, error_stream)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        print("aiteqno: error [interrupted]: operation interrupted", file=error_stream)
        return 130
    except (ModuleNotFoundError, ImportError) as exc:
        error = CliError(
            "python_dependency_missing",
            f"required Python dependency is unavailable: {exc}",
            ExitCode.DEPENDENCY_ERROR,
        )
        _print_error(error, error_stream)
        return int(error.exit_code)
    except Exception as exc:  # pragma: no cover - defensive process boundary
        error = CliError(
            "unexpected_failure",
            f"unexpected operation failure: {exc}",
            ExitCode.OPERATIONAL_ERROR,
        )
        _print_error(error, error_stream)
        return int(error.exit_code)
    return int(ExitCode.SUCCESS)


def _add_png_input(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input", metavar="INPUT_PNG", help="single-page PNG input")


def _add_ir_input(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "input",
        metavar="DOCUMENT_IR_JSON",
        help="Document IR JSON; assets are resolved relative to this file",
    )


def _add_languages(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-l",
        "--language",
        action="append",
        dest="languages",
        metavar="LANGUAGE",
        help="OCR language identifier; repeat to set order (default: jpn)",
    )


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(number) or not number > 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def _command_extract(
    arguments: argparse.Namespace,
    runtime: CliRuntime,
    stdout: TextIO,
    stderr: TextIO,
) -> None:
    input_path = _input_file(arguments.input, ".png", "PNG")
    output_path = _output_file(arguments.output, ".json", "Document IR JSON")
    assets_path = output_path.parent / ASSET_DIRECTORY_NAME
    _refuse_existing(assets_path, "asset directory")
    languages = tuple(arguments.languages or DEFAULT_OCR_LANGUAGES)

    container = _temporary_container(output_path.parent, "extract")
    try:
        staged_bundle = container / "bundle"
        result = _extract_to_bundle(input_path, staged_bundle, languages, runtime)
        _publish_extract_result(result, output_path)
    finally:
        _remove_temporary_container(container)

    _print_extraction_diagnostics(result, stderr)
    print(f"document_ir={output_path}", file=stdout)
    print(f"assets={assets_path}", file=stdout)


def _command_render(
    arguments: argparse.Namespace,
    runtime: CliRuntime,
    stdout: TextIO,
    stderr: TextIO,
) -> None:
    input_path = _input_file(arguments.input, ".json", "Document IR JSON")
    output_path = _output_file(arguments.output, ".docx", "DOCX")
    document = _load_document(input_path)
    renderer = _renderer(runtime.docx_renderer_factory, input_path.parent, "DOCX")

    container = _temporary_container(output_path.parent, "render")
    try:
        staged_output = container / output_path.name
        try:
            result = render_docx(document, staged_output, renderer=renderer)
        except (DocxRenderError, OSError, ValueError) as exc:
            raise CliError(
                "docx_render_failed",
                str(exc),
                ExitCode.OPERATIONAL_ERROR,
            ) from exc
        _copy_file_exclusive(staged_output, output_path)
    finally:
        _remove_temporary_container(container)

    _print_report_warnings(result.report.warnings, "render", stderr)
    print(f"docx={output_path}", file=stdout)


def _command_preview(
    arguments: argparse.Namespace,
    runtime: CliRuntime,
    stdout: TextIO,
    stderr: TextIO,
) -> None:
    input_path = _input_file(arguments.input, ".json", "Document IR JSON")
    output_path = _output_file(arguments.output, ".png", "PNG preview")
    document = _load_document(input_path)
    renderer = _renderer(runtime.preview_renderer_factory, input_path.parent, "preview")

    container = _temporary_container(output_path.parent, "preview")
    try:
        staged_output = container / output_path.name
        try:
            result = render_preview(
                document,
                staged_output,
                renderer=renderer,
                dpi=arguments.dpi,
            )
        except (PreviewRenderError, OSError, ValueError) as exc:
            raise CliError(
                "preview_render_failed",
                str(exc),
                ExitCode.OPERATIONAL_ERROR,
            ) from exc
        _copy_file_exclusive(staged_output, output_path)
    finally:
        _remove_temporary_container(container)

    _print_report_warnings(result.report.warnings, "preview", stderr)
    print(f"preview={output_path}", file=stdout)


def _command_roundtrip(
    arguments: argparse.Namespace,
    runtime: CliRuntime,
    stdout: TextIO,
    stderr: TextIO,
) -> None:
    input_path = _input_file(arguments.input, ".png", "PNG")
    output_directory = _output_directory(arguments.output)
    languages = tuple(arguments.languages or DEFAULT_OCR_LANGUAGES)

    container = _temporary_container(output_directory.parent, "roundtrip")
    try:
        staged_bundle = container / "bundle"
        extraction = _extract_to_bundle(
            input_path,
            staged_bundle,
            languages,
            runtime,
        )
        document = extraction.document
        docx_renderer = _renderer(
            runtime.docx_renderer_factory,
            staged_bundle,
            "DOCX",
        )
        preview_renderer = _renderer(
            runtime.preview_renderer_factory,
            staged_bundle,
            "preview",
        )
        try:
            docx_result = render_docx(
                document,
                staged_bundle / RECONSTRUCTED_DOCX_FILENAME,
                renderer=docx_renderer,
            )
            preview_result = render_preview(
                document,
                staged_bundle / RECONSTRUCTED_PREVIEW_FILENAME,
                renderer=preview_renderer,
                dpi=arguments.dpi,
            )
        except (DocxRenderError, PreviewRenderError, OSError, ValueError) as exc:
            raise CliError(
                "roundtrip_render_failed",
                str(exc),
                ExitCode.OPERATIONAL_ERROR,
            ) from exc
        _copy_directory_exclusive(staged_bundle, output_directory)
    finally:
        _remove_temporary_container(container)

    _print_extraction_diagnostics(extraction, stderr)
    _print_report_warnings(docx_result.report.warnings, "render", stderr)
    _print_report_warnings(preview_result.report.warnings, "preview", stderr)
    print(f"bundle={output_directory}", file=stdout)
    print(
        f"document_ir={output_directory / DOCUMENT_IR_FILENAME}",
        file=stdout,
    )
    print(
        f"docx={output_directory / RECONSTRUCTED_DOCX_FILENAME}",
        file=stdout,
    )
    print(
        f"preview={output_directory / RECONSTRUCTED_PREVIEW_FILENAME}",
        file=stdout,
    )


def _input_file(raw_path: str, suffix: str, label: str) -> Path:
    path = _resolved(raw_path)
    if path.suffix.lower() != suffix:
        raise CliError(
            "invalid_input_extension",
            f"{label} input must use the {suffix} extension: {path}",
            ExitCode.INPUT_ERROR,
        )
    if not path.is_file():
        raise CliError(
            "input_not_found",
            f"{label} input file does not exist: {path}",
            ExitCode.INPUT_ERROR,
        )
    return path


def _output_file(raw_path: str, suffix: str, label: str) -> Path:
    path = _resolved(raw_path)
    if path.suffix.lower() != suffix:
        raise CliError(
            "invalid_output_extension",
            f"{label} output must use the {suffix} extension: {path}",
            ExitCode.USAGE_ERROR,
        )
    _refuse_existing(path, f"{label} output")
    _ensure_parent(path.parent)
    return path


def _output_directory(raw_path: str) -> Path:
    path = _resolved(raw_path)
    _refuse_existing(path, "roundtrip output directory")
    _ensure_parent(path.parent)
    return path


def _resolved(raw_path: str) -> Path:
    return Path(raw_path).expanduser().resolve(strict=False)


def _ensure_parent(parent: Path) -> None:
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CliError(
            "output_parent_unavailable",
            f"could not create output parent {parent}: {exc}",
            ExitCode.OPERATIONAL_ERROR,
        ) from exc
    if not parent.is_dir():
        raise CliError(
            "output_parent_not_directory",
            f"output parent is not a directory: {parent}",
            ExitCode.OPERATIONAL_ERROR,
        )


def _refuse_existing(path: Path, label: str) -> None:
    if path.exists():
        raise CliError(
            "output_exists",
            f"{label} already exists; Aiteqno never overwrites it: {path}",
            ExitCode.OUTPUT_CONFLICT,
        )


def _temporary_container(parent: Path, operation: str) -> Path:
    _ensure_parent(parent)
    try:
        return Path(
            tempfile.mkdtemp(
                prefix=f".aiteqno-{operation}-",
                dir=parent,
            )
        )
    except OSError as exc:
        raise CliError(
            "staging_failed",
            f"could not create temporary output beside {parent}: {exc}",
            ExitCode.OPERATIONAL_ERROR,
        ) from exc


def _remove_temporary_container(container: Path) -> None:
    if container.exists():
        shutil.rmtree(container, ignore_errors=True)


def _extract_to_bundle(
    input_path: Path,
    output_directory: Path,
    languages: tuple[str, ...],
    runtime: CliRuntime,
) -> PngExtractionResult:
    try:
        png_data = input_path.read_bytes()
    except OSError as exc:
        raise CliError(
            "input_unreadable",
            f"could not read PNG input {input_path}: {exc}",
            ExitCode.INPUT_ERROR,
        ) from exc
    try:
        return extract_png(
            png_data,
            output_directory,
            decoder=runtime.decoder,
            structure_extractor=runtime.structure_extractor,
            ocr_backend=runtime.ocr_backend,
            asset_encoder=runtime.asset_encoder,
            validator=runtime.validator,
            bundle_writer=runtime.bundle_writer,
            languages=languages,
            ocr_options=runtime.ocr_options,
            ocr_region_grouping=runtime.ocr_region_grouping,
            enrich_table_topology=True,
        )
    except PngExtractionError as exc:
        raise _cli_error_from_extraction(exc) from exc
    except (TypeError, ValueError) as exc:
        raise CliError(
            "invalid_extraction_request",
            str(exc),
            ExitCode.INPUT_ERROR,
        ) from exc


def _cli_error_from_extraction(exc: PngExtractionError) -> CliError:
    if exc.code in _DEPENDENCY_ERROR_CODES:
        exit_code = ExitCode.DEPENDENCY_ERROR
    elif exc.code == "bundle_exists":
        exit_code = ExitCode.OUTPUT_CONFLICT
    elif exc.stage in {"decode", "structure"}:
        exit_code = ExitCode.INPUT_ERROR
    else:
        exit_code = ExitCode.OPERATIONAL_ERROR
    return CliError(exc.code, str(exc), exit_code)


def _load_document(path: Path) -> DocumentIR:
    try:
        return document_ir_from_file(path)
    except DocumentIRValidationError as exc:
        raise CliError(
            "invalid_document_ir",
            str(exc),
            ExitCode.INPUT_ERROR,
        ) from exc
    except FileNotFoundError as exc:
        raise CliError(
            "document_ir_schema_unavailable",
            f"Document IR schema or input became unavailable: {exc}",
            ExitCode.DEPENDENCY_ERROR,
        ) from exc
    except OSError as exc:
        raise CliError(
            "input_unreadable",
            f"could not read Document IR input {path}: {exc}",
            ExitCode.INPUT_ERROR,
        ) from exc


def _renderer(
    factory: Callable[[Path], object],
    bundle_root: Path,
    label: str,
) -> object:
    try:
        return factory(bundle_root)
    except (OSError, ValueError) as exc:
        raise CliError(
            "asset_bundle_unavailable",
            f"could not initialize {label} renderer from {bundle_root}: {exc}",
            ExitCode.INPUT_ERROR,
        ) from exc


def _publish_extract_result(result: PngExtractionResult, output_path: Path) -> None:
    assets_target = output_path.parent / ASSET_DIRECTORY_NAME
    created_files: list[Path] = []
    assets_created = False
    try:
        assets_target.mkdir()
        assets_created = True
        for source in result.bundle.asset_paths:
            target = assets_target / source.name
            _copy_file_exclusive(source, target)
            created_files.append(target)
        _copy_file_exclusive(result.bundle.document_path, output_path)
        created_files.append(output_path)
    except CliError:
        _rollback_created_output(
            created_files, assets_target if assets_created else None
        )
        raise
    except FileExistsError as exc:
        _rollback_created_output(
            created_files, assets_target if assets_created else None
        )
        raise CliError(
            "output_exists",
            f"output appeared during publication; nothing was overwritten: {exc}",
            ExitCode.OUTPUT_CONFLICT,
        ) from exc
    except OSError as exc:
        _rollback_created_output(
            created_files, assets_target if assets_created else None
        )
        raise CliError(
            "output_publish_failed",
            f"could not publish extracted bundle: {exc}",
            ExitCode.OPERATIONAL_ERROR,
        ) from exc


def _copy_file_exclusive(source: Path, target: Path) -> None:
    target_created = False
    try:
        data = source.read_bytes()
        with target.open("xb") as output_file:
            target_created = True
            output_file.write(data)
            output_file.flush()
            os.fsync(output_file.fileno())
    except FileExistsError as exc:
        raise CliError(
            "output_exists",
            f"output appeared during publication; nothing was overwritten: {target}",
            ExitCode.OUTPUT_CONFLICT,
        ) from exc
    except OSError as exc:
        if target_created:
            target.unlink(missing_ok=True)
        raise CliError(
            "output_publish_failed",
            f"could not publish output {target}: {exc}",
            ExitCode.OPERATIONAL_ERROR,
        ) from exc


def _copy_directory_exclusive(source: Path, target: Path) -> None:
    created_files: list[Path] = []
    created_directories: list[Path] = []
    try:
        target.mkdir()
        created_directories.append(target)
        for source_path in sorted(source.rglob("*"), key=lambda path: path.parts):
            relative_path = source_path.relative_to(source)
            target_path = target / relative_path
            if source_path.is_dir():
                target_path.mkdir()
                created_directories.append(target_path)
            elif source_path.is_file():
                _copy_file_exclusive(source_path, target_path)
                created_files.append(target_path)
            else:
                raise OSError(f"unsupported staged output entry: {source_path}")
    except FileExistsError as exc:
        _rollback_directory_copy(created_files, created_directories)
        raise CliError(
            "output_exists",
            f"roundtrip output appeared during publication: {target}",
            ExitCode.OUTPUT_CONFLICT,
        ) from exc
    except CliError:
        _rollback_directory_copy(created_files, created_directories)
        raise
    except OSError as exc:
        _rollback_directory_copy(created_files, created_directories)
        raise CliError(
            "output_publish_failed",
            f"could not publish roundtrip output {target}: {exc}",
            ExitCode.OPERATIONAL_ERROR,
        ) from exc


def _rollback_directory_copy(
    files: Sequence[Path],
    directories: Sequence[Path],
) -> None:
    for path in reversed(files):
        path.unlink(missing_ok=True)
    for path in reversed(directories):
        try:
            path.rmdir()
        except OSError:
            pass


def _rollback_created_output(files: Sequence[Path], directory: Path | None) -> None:
    for path in reversed(files):
        path.unlink(missing_ok=True)
    if directory is not None:
        try:
            directory.rmdir()
        except OSError:
            pass


def _print_extraction_diagnostics(
    result: PngExtractionResult,
    stderr: TextIO,
) -> None:
    for diagnostic in result.diagnostics:
        print(
            f"aiteqno: warning [{diagnostic.code}]: {diagnostic.message}",
            file=stderr,
        )


def _print_report_warnings(
    warnings: Sequence[object],
    stage: str,
    stderr: TextIO,
) -> None:
    for warning in warnings:
        code = getattr(warning, "code", "render_warning")
        message = getattr(warning, "message", str(warning))
        print(f"aiteqno: warning [{stage}.{code}]: {message}", file=stderr)


def _print_error(error: CliError, stderr: TextIO) -> None:
    print(f"aiteqno: error [{error.code}]: {error}", file=stderr)


__all__ = [
    "ASSET_DIRECTORY_NAME",
    "CliError",
    "CliRuntime",
    "DOCUMENT_IR_FILENAME",
    "ExitCode",
    "RECONSTRUCTED_DOCX_FILENAME",
    "RECONSTRUCTED_PREVIEW_FILENAME",
    "build_parser",
    "default_runtime",
    "main",
]
