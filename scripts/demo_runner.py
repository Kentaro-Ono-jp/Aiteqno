"""Run the downloadable Windows demo and publish its evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import TextIO

from aiteqno import __version__
from aiteqno.cli import ExitCode, main as aiteqno_main


DOCUMENT_IR_FILENAME = "document.ir.json"
DOCUMENT_IR_SCHEMA_FILENAME = "document-ir.schema.json"
DEMO_MANIFEST_FILENAME = "demo.manifest.json"
RECONSTRUCTED_DOCX_FILENAME = "reconstructed.docx"
RECONSTRUCTED_PREVIEW_FILENAME = "reconstructed.png"
ASSET_DIRECTORY_NAME = "assets"

_FIXED_ARTIFACTS = {
    "document_ir": DOCUMENT_IR_FILENAME,
    "document_ir_schema": DOCUMENT_IR_SCHEMA_FILENAME,
    "reconstructed_docx": RECONSTRUCTED_DOCX_FILENAME,
    "reconstructed_preview": RECONSTRUCTED_PREVIEW_FILENAME,
}
CliMain = Callable[..., int]
_WARNING_PATTERN = re.compile(r"^aiteqno: warning \[([^]]+)](?:: )?(.*)$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_record(bundle: Path, relative_path: str) -> dict[str, str | int]:
    path = bundle / relative_path
    return {
        "path": relative_path,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _build_manifest(
    *,
    input_png: Path,
    bundle: Path,
    languages: tuple[str, ...],
    dpi: float,
) -> dict[str, object]:
    artifacts = {
        name: _artifact_record(bundle, relative_path)
        for name, relative_path in _FIXED_ARTIFACTS.items()
    }
    assets = [
        _artifact_record(bundle, path.relative_to(bundle).as_posix())
        for path in sorted((bundle / ASSET_DIRECTORY_NAME).rglob("*"))
        if path.is_file()
    ]
    return {
        "manifest_version": "1.0",
        "created_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "aiteqno_version": __version__,
        "source": {
            "filename": input_png.name,
            "sha256": _sha256(input_png),
            "size_bytes": input_png.stat().st_size,
        },
        "settings": {
            "ocr_languages": list(languages),
            "preview_dpi": dpi,
        },
        "artifacts": artifacts,
        "assets": assets,
    }


def _error(code: str, message: str, exit_code: ExitCode, stderr: TextIO) -> int:
    print(f"aiteqno-demo: error [{code}]: {message}", file=stderr)
    return int(exit_code)


def _relay_cli_diagnostics(text: str, exit_code: int, stderr: TextIO) -> None:
    if not text:
        return
    if exit_code != int(ExitCode.SUCCESS):
        print(text, file=stderr, end="" if text.endswith("\n") else "\n")
        return

    warning_counts: dict[tuple[str, str], int] = {}
    unclassified: list[str] = []
    for line in text.splitlines():
        match = _WARNING_PATTERN.fullmatch(line)
        if match is None:
            unclassified.append(line)
            continue
        key = (match.group(1), match.group(2))
        warning_counts[key] = warning_counts.get(key, 0) + 1

    for (code, message), count in warning_counts.items():
        occurrences = f" ({count} occurrences)" if count > 1 else ""
        print(
            f"aiteqno-demo: warning [{code}]{occurrences}: {message}",
            file=stderr,
        )
    for line in unclassified:
        print(line, file=stderr)


def run_demo(
    input_png: str | Path,
    output_directory: str | Path,
    schema_path: str | Path,
    *,
    languages: Sequence[str] = ("jpn", "eng"),
    dpi: float = 144.0,
    cli_main: CliMain = aiteqno_main,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Create the complete demo bundle without overwriting an existing path."""

    output_stream = stdout if stdout is not None else sys.stdout
    error_stream = stderr if stderr is not None else sys.stderr
    source = Path(input_png).expanduser().resolve(strict=False)
    destination = Path(output_directory).expanduser().resolve(strict=False)
    schema = Path(schema_path).expanduser().resolve(strict=False)
    selected_languages = tuple(languages)

    if destination.exists():
        return _error(
            "output_exists",
            f"output directory already exists and was preserved: {destination}",
            ExitCode.OUTPUT_CONFLICT,
            error_stream,
        )
    if not schema.is_file():
        return _error(
            "schema_missing",
            f"bundled Document IR schema is missing: {schema}",
            ExitCode.DEPENDENCY_ERROR,
            error_stream,
        )
    if not selected_languages or any(not language for language in selected_languages):
        return _error(
            "invalid_languages",
            "at least one non-empty OCR language is required",
            ExitCode.USAGE_ERROR,
            error_stream,
        )
    if dpi <= 0:
        return _error(
            "invalid_dpi",
            "preview DPI must be greater than zero",
            ExitCode.USAGE_ERROR,
            error_stream,
        )

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".aiteqno-demo-", dir=destination.parent
        ) as temporary_directory:
            staged_bundle = Path(temporary_directory) / "bundle"
            cli_stdout = StringIO()
            cli_stderr = StringIO()
            arguments = [
                "roundtrip",
                str(source),
                "-o",
                str(staged_bundle),
                "--dpi",
                str(dpi),
            ]
            for language in selected_languages:
                arguments.extend(("--language", language))

            exit_code = int(
                cli_main(arguments, stdout=cli_stdout, stderr=cli_stderr)
            )
            _relay_cli_diagnostics(cli_stderr.getvalue(), exit_code, error_stream)
            if exit_code != int(ExitCode.SUCCESS):
                return exit_code

            expected_paths = [
                staged_bundle / relative_path
                for relative_path in _FIXED_ARTIFACTS.values()
                if relative_path != DOCUMENT_IR_SCHEMA_FILENAME
            ]
            expected_paths.append(staged_bundle / ASSET_DIRECTORY_NAME)
            missing = [path.name for path in expected_paths if not path.exists()]
            if missing:
                return _error(
                    "incomplete_roundtrip",
                    f"roundtrip did not produce required artifacts: {sorted(missing)}",
                    ExitCode.OPERATIONAL_ERROR,
                    error_stream,
                )

            shutil.copyfile(schema, staged_bundle / DOCUMENT_IR_SCHEMA_FILENAME)
            manifest = _build_manifest(
                input_png=source,
                bundle=staged_bundle,
                languages=selected_languages,
                dpi=dpi,
            )
            (staged_bundle / DEMO_MANIFEST_FILENAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )

            destination.mkdir()
            published_by_runner = True
            try:
                for child in staged_bundle.iterdir():
                    target = destination / child.name
                    if child.is_dir():
                        shutil.copytree(child, target)
                    else:
                        shutil.copy2(child, target)
            except Exception:
                if published_by_runner:
                    shutil.rmtree(destination, ignore_errors=True)
                raise
    except (OSError, ValueError) as exc:
        return _error(
            "demo_publication_failed",
            str(exc),
            ExitCode.OPERATIONAL_ERROR,
            error_stream,
        )

    print(f"bundle={destination}", file=output_stream)
    print(f"document_ir={destination / DOCUMENT_IR_FILENAME}", file=output_stream)
    print(
        f"document_ir_schema={destination / DOCUMENT_IR_SCHEMA_FILENAME}",
        file=output_stream,
    )
    print(f"manifest={destination / DEMO_MANIFEST_FILENAME}", file=output_stream)
    print(f"docx={destination / RECONSTRUCTED_DOCX_FILENAME}", file=output_stream)
    print(f"preview={destination / RECONSTRUCTED_PREVIEW_FILENAME}", file=output_stream)
    return int(ExitCode.SUCCESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Aiteqno PNG demo and publish a reviewable bundle."
    )
    parser.add_argument("input", metavar="INPUT_PNG")
    parser.add_argument("-o", "--output", required=True, metavar="DIRECTORY")
    parser.add_argument("--schema", required=True, metavar="JSON_SCHEMA")
    parser.add_argument(
        "--language",
        action="append",
        dest="languages",
        metavar="LANGUAGE",
        help="repeat to set OCR languages (default: jpn, eng)",
    )
    parser.add_argument("--dpi", type=float, default=144.0, metavar="DPI")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    return run_demo(
        arguments.input,
        arguments.output,
        arguments.schema,
        languages=arguments.languages or ("jpn", "eng"),
        dpi=arguments.dpi,
    )


if __name__ == "__main__":
    raise SystemExit(main())
