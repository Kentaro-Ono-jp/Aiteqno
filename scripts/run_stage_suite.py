"""Run a data-driven source-to-actual-DOCX cumulative fixture stage."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from docx import Document as open_docx
from PIL import Image

from aiteqno import __version__
from aiteqno.adapters import (
    FilesystemDocumentBundleWriter,
    JsonSchemaDocumentIRValidator,
    OpenCvStructureExtractor,
    PillowPngAssetEncoder,
    PillowPngDecoder,
    TesseractOcrBackend,
)
from aiteqno.adapters.evaluation import PythonDocxObserver
from aiteqno.adapters.json_schema import document_ir_from_file
from aiteqno.adapters.libreoffice import LibreOfficeSnapshotRenderer
from aiteqno.application import (
    SOURCE_BASELINE_EVALUATOR_NAME,
    SOURCE_BASELINE_EVALUATOR_VERSION,
    SourceBaselineConfig,
    StageFixtureMeasurement,
    evaluate_source_baseline,
    evaluate_stage_gate,
    extract_png,
)
from aiteqno.cli.main import default_runtime, main as cli_main
from aiteqno.domain import ElementType, TextElement
from aiteqno.ports import OcrOptions, SourceBaselineObservation, SourceBaselineReference


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE_PATH = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "stages" / "questionnaire-stage-1.json"
)
STAGE_RUNNER_NAME = "aiteqno-cumulative-fixture-stage-runner"
STAGE_RUNNER_VERSION = "1.0"

_WORDPROCESSINGML_NAMESPACE = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
)
_WORD_RUN = f"{{{_WORDPROCESSINGML_NAMESPACE}}}r"
_WORD_RUN_PROPERTIES = f"{{{_WORDPROCESSINGML_NAMESPACE}}}rPr"
_WORD_VANISH = f"{{{_WORDPROCESSINGML_NAMESPACE}}}vanish"
_WORD_WEB_HIDDEN = f"{{{_WORDPROCESSINGML_NAMESPACE}}}webHidden"
_WORD_TEXT_TAGS = frozenset(
    {
        f"{{{_WORDPROCESSINGML_NAMESPACE}}}t",
        f"{{{_WORDPROCESSINGML_NAMESPACE}}}delText",
        f"{{{_WORDPROCESSINGML_NAMESPACE}}}instrText",
    }
)
_LAYOUT_ONLY_ZERO_WIDTH_CHARACTERS = frozenset(
    {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def _write_json_new(path: Path, value: object) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_bytes_new(path, payload)


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise RuntimeError(f"{label} must be a JSON array")
    return value


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{label} must be a non-empty string")
    return value


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RuntimeError(f"{label} must be a positive number")
    return float(value)


def _relative_path(root: Path, raw: object, label: str) -> Path:
    value = _non_empty_string(raw, label)
    portable = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    if (
        portable.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or not portable.parts
        or ".." in portable.parts
    ):
        raise RuntimeError(f"{label} must be a relative path without traversal")
    resolved_root = root.resolve()
    resolved = resolved_root.joinpath(*portable.parts).resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(f"{label} resolves outside its allowed root") from exc
    return resolved


def _repository_path(raw: object, label: str) -> Path:
    return _relative_path(REPOSITORY_ROOT, raw, label)


@dataclass(frozen=True, slots=True)
class FixtureContract:
    fixture_id: str
    manifest_path: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    source_path: Path
    source_encoding: str
    source_data: bytes
    source_sha256: str
    source_width: int
    source_height: int
    source_dpi: float
    reference_path: Path
    reference_sha256: str
    reference: SourceBaselineReference


@dataclass(frozen=True, slots=True)
class SuiteContract:
    stage_id: str
    threshold: float
    production_languages: tuple[str, ...]
    preview_dpi: float
    snapshot_dpi: int
    visible_languages: tuple[str, ...]
    visible_options: OcrOptions
    fixtures: tuple[FixtureContract, ...]
    suite_path: Path
    suite_sha256: str


def _read_fixture(manifest_path: Path, expected_fixture_id: str) -> FixtureContract:
    if not manifest_path.is_file():
        raise RuntimeError(f"fixture manifest does not exist: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = _object(json.loads(manifest_bytes), "fixture manifest")
    fixture_id = _non_empty_string(manifest.get("fixture_id"), "fixture_id")
    if fixture_id != expected_fixture_id:
        raise RuntimeError(
            "suite/manifest fixture ID mismatch: "
            f"expected {expected_fixture_id!r}, observed {fixture_id!r}"
        )

    source = _object(manifest.get("source"), f"{fixture_id} source")
    source_path = _relative_path(
        manifest_path.parent,
        source.get("path"),
        f"{fixture_id} source.path",
    )
    if not source_path.is_file():
        raise RuntimeError(f"fixture source does not exist: {source_path}")
    encoding = _non_empty_string(
        source.get("encoding"),
        f"{fixture_id} source.encoding",
    )
    raw_source = source_path.read_bytes()
    if encoding == "raw":
        source_data = raw_source
    elif encoding == "base64":
        try:
            source_data = base64.b64decode(raw_source.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError(f"{fixture_id} source is not valid base64") from exc
    else:
        raise RuntimeError(
            f"{fixture_id} source.encoding must be 'raw' or 'base64', got {encoding!r}"
        )
    source_digest = _sha256(source_data)
    if source.get("sha256") != source_digest:
        raise RuntimeError(f"{fixture_id} source SHA-256 mismatch")

    width = source.get("pixel_width")
    height = source.get("pixel_height")
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise RuntimeError(f"{fixture_id} source.pixel_width must be positive")
    if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
        raise RuntimeError(f"{fixture_id} source.pixel_height must be positive")
    declared_dpi = _positive_number(source.get("dpi"), f"{fixture_id} source.dpi")
    decoded = PillowPngDecoder(fallback_dpi=declared_dpi).decode(source_data)
    observed_dimensions = (
        decoded.source.pixel_width,
        decoded.source.pixel_height,
    )
    if observed_dimensions != (width, height):
        raise RuntimeError(
            f"{fixture_id} source dimensions mismatch: "
            f"declared {(width, height)}, observed {observed_dimensions}"
        )
    effective_dpi = round((decoded.source.dpi_x + decoded.source.dpi_y) / 2)
    if effective_dpi != round(declared_dpi):
        raise RuntimeError(
            f"{fixture_id} source DPI mismatch: "
            f"declared {declared_dpi:g}, observed {effective_dpi:g}"
        )
    color_mode = source.get("color_mode")
    if color_mode is not None:
        with Image.open(io.BytesIO(source_data)) as source_image:
            if source_image.mode != color_mode:
                raise RuntimeError(
                    f"{fixture_id} source color mode mismatch: "
                    f"declared {color_mode!r}, observed {source_image.mode!r}"
                )

    reference_contract = _object(
        manifest.get("reference"),
        f"{fixture_id} reference",
    )
    reference_path = _relative_path(
        manifest_path.parent,
        reference_contract.get("path"),
        f"{fixture_id} reference.path",
    )
    if not reference_path.is_file():
        raise RuntimeError(f"fixture reference does not exist: {reference_path}")
    reference_bytes = reference_path.read_bytes()
    reference_digest = _sha256(reference_bytes)
    if reference_contract.get("sha256") != reference_digest:
        raise RuntimeError(f"{fixture_id} reference SHA-256 mismatch")
    reference = SourceBaselineReference.from_json(reference_bytes)
    if reference.reference_id != fixture_id:
        raise RuntimeError(f"{fixture_id} reference_id mismatch")
    if reference.source_sha256 != source_digest:
        raise RuntimeError(f"{fixture_id} reference source identity mismatch")
    review = _object(manifest.get("review"), f"{fixture_id} review")
    if review.get("status") != "reviewed" or not reference.reviewed:
        raise RuntimeError(f"{fixture_id} reference is not human-reviewed")
    _non_empty_string(review.get("reviewer"), f"{fixture_id} review.reviewer")
    _non_empty_string(review.get("reviewed_at"), f"{fixture_id} review.reviewed_at")

    return FixtureContract(
        fixture_id=fixture_id,
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_sha256=_sha256(manifest_bytes),
        source_path=source_path,
        source_encoding=encoding,
        source_data=source_data,
        source_sha256=source_digest,
        source_width=width,
        source_height=height,
        source_dpi=declared_dpi,
        reference_path=reference_path,
        reference_sha256=reference_digest,
        reference=reference,
    )


def _language_tuple(value: object, label: str) -> tuple[str, ...]:
    result = tuple(_non_empty_string(item, f"{label} item") for item in _array(value, label))
    if not result or len(result) != len(set(result)):
        raise RuntimeError(f"{label} must contain unique languages")
    return result


def _load_suite(suite_path: Path) -> SuiteContract:
    resolved_suite = suite_path.resolve()
    if not resolved_suite.is_file():
        raise RuntimeError(f"stage suite does not exist: {resolved_suite}")
    suite_bytes = resolved_suite.read_bytes()
    suite = _object(json.loads(suite_bytes), "stage suite")
    if suite.get("schema_version") != 1:
        raise RuntimeError("stage suite schema_version must be 1")
    stage_id = _non_empty_string(suite.get("stage_id"), "stage_id")
    threshold = _positive_number(suite.get("threshold"), "threshold")
    if threshold > 100:
        raise RuntimeError("threshold must not exceed 100")
    pipeline = _object(suite.get("pipeline"), "pipeline")
    if pipeline.get("entrypoint") != "aiteqno roundtrip":
        raise RuntimeError("pipeline.entrypoint must be 'aiteqno roundtrip'")
    production_languages = _language_tuple(
        pipeline.get("languages"),
        "pipeline.languages",
    )
    preview_dpi = _positive_number(pipeline.get("preview_dpi"), "preview_dpi")
    evaluation = _object(suite.get("evaluation"), "evaluation")
    if evaluation.get("evaluator_name") != SOURCE_BASELINE_EVALUATOR_NAME:
        raise RuntimeError("suite evaluator_name does not match source evaluator")
    if evaluation.get("text_evidence") != "rendered_visible":
        raise RuntimeError("suite text_evidence must be rendered_visible")
    if evaluation.get("structure_aware_visible_ocr") is not True:
        raise RuntimeError("visible OCR must be structure-aware")
    snapshot_dpi_value = _positive_number(
        evaluation.get("snapshot_dpi"),
        "snapshot_dpi",
    )
    if not snapshot_dpi_value.is_integer():
        raise RuntimeError("snapshot_dpi must be an integer")
    snapshot_dpi = int(snapshot_dpi_value)
    visible_languages = _language_tuple(
        evaluation.get("visible_ocr_languages"),
        "evaluation.visible_ocr_languages",
    )
    visible_options = OcrOptions(
        page_segmentation_mode=int(
            _positive_number(
                evaluation.get("page_segmentation_mode"),
                "evaluation.page_segmentation_mode",
            )
        ),
        engine_mode=int(
            _positive_number(
                evaluation.get("engine_mode"),
                "evaluation.engine_mode",
            )
        ),
    )

    fixture_entries = _array(suite.get("fixtures"), "fixtures")
    fixtures: list[FixtureContract] = []
    fixture_ids: set[str] = set()
    for index, raw_entry in enumerate(fixture_entries):
        entry = _object(raw_entry, f"fixtures[{index}]")
        fixture_id = _non_empty_string(
            entry.get("fixture_id"),
            f"fixtures[{index}].fixture_id",
        )
        if fixture_id in fixture_ids:
            raise RuntimeError(f"duplicate fixture ID in suite: {fixture_id}")
        fixture_ids.add(fixture_id)
        manifest_path = _repository_path(
            entry.get("manifest"),
            f"fixtures[{index}].manifest",
        )
        fixtures.append(_read_fixture(manifest_path, fixture_id))
    if not fixtures:
        raise RuntimeError("stage suite must contain at least one fixture")
    return SuiteContract(
        stage_id=stage_id,
        threshold=threshold,
        production_languages=production_languages,
        preview_dpi=preview_dpi,
        snapshot_dpi=snapshot_dpi,
        visible_languages=visible_languages,
        visible_options=visible_options,
        fixtures=tuple(fixtures),
        suite_path=resolved_suite,
        suite_sha256=_sha256(suite_bytes),
    )


def _document_text(observation: Any) -> str:
    elements = sorted(
        (
            item
            for item in observation.elements
            if item.element_type is ElementType.TEXT and item.text is not None
        ),
        key=lambda item: (
            item.page_number,
            item.reading_order if item.reading_order is not None else 2**31 - 1,
            item.id,
        ),
    )
    return "\n".join(item.text or "" for item in elements)


def _ir_text(document: Any) -> str:
    lines: list[str] = []
    for page in sorted(document.pages, key=lambda item: item.number):
        elements = sorted(
            (item for item in page.elements if isinstance(item, TextElement)),
            key=lambda item: (item.reading_order, item.id),
        )
        lines.extend(item.text for item in elements)
    return "\n".join(lines)


def _visible_snapshot_ocr(
    fixture_output: Path,
    snapshot: Any,
    *,
    snapshot_dpi: int,
    languages: tuple[str, ...],
    options: OcrOptions,
) -> tuple[str, list[dict[str, object]]]:
    snapshot_directory = fixture_output / "actual-docx-snapshot"
    visible_root = fixture_output / "rendered-visible-ocr"
    decoder = PillowPngDecoder(fallback_dpi=snapshot_dpi)
    backend = TesseractOcrBackend(required_languages=languages)
    all_text: list[str] = []
    pages: list[dict[str, object]] = []
    for page in snapshot.pages:
        page_path = snapshot_directory / page.relative_path
        bundle_path = visible_root / f"page-{page.page_number:03d}-bundle"
        extraction = extract_png(
            page_path.read_bytes(),
            bundle_path,
            decoder=decoder,
            structure_extractor=OpenCvStructureExtractor(),
            ocr_backend=backend,
            asset_encoder=PillowPngAssetEncoder(),
            validator=JsonSchemaDocumentIRValidator(),
            bundle_writer=FilesystemDocumentBundleWriter(),
            languages=languages,
            ocr_options=options,
        )
        page_text = _ir_text(extraction.document)
        all_text.append(page_text)
        pages.append(
            {
                "page_number": page.page_number,
                "snapshot_relative_path": (
                    f"actual-docx-snapshot/{page.relative_path}"
                ),
                "bundle_relative_path": (
                    f"rendered-visible-ocr/page-{page.page_number:03d}-bundle"
                ),
                "text": page_text,
                "text_element_count": sum(
                    isinstance(item, TextElement)
                    for candidate_page in extraction.document.pages
                    for item in candidate_page.elements
                ),
                "diagnostics": [
                    {
                        "code": item.code,
                        "stage": item.stage,
                        "message": item.message,
                        "source_ref": item.source_ref,
                    }
                    for item in extraction.diagnostics
                ],
            }
        )
    visible_text = "\n\n".join(all_text)
    _write_json_new(
        visible_root / "visible-ocr.json",
        {
            "evidence": "rendered_visible",
            "method": "OpenCV structure regions followed by Tesseract OCR",
            "languages": list(languages),
            "page_segmentation_mode": options.page_segmentation_mode,
            "engine_mode": options.engine_mode,
            "snapshot_dpi": snapshot_dpi,
            "pages": pages,
        },
    )
    return visible_text, pages


def _reopen_edit_save(docx_path: Path) -> tuple[bool, str]:
    try:
        with tempfile.TemporaryDirectory(prefix="aiteqno-stage-docx-") as root:
            saved = Path(root) / "roundtrip-save.docx"
            document = open_docx(docx_path)
            document.save(saved)
            open_docx(saved)
    except Exception as exc:
        return False, f"DOCX edit/save/reopen failed: {type(exc).__name__}: {exc}"
    return True, "python-docx opened, saved, and reopened the DOCX"


def _docx_media_evidence(
    docx_path: Path,
    *,
    source_sha256: str,
    source_dimensions: tuple[int, int],
) -> tuple[bool, str, list[dict[str, object]]]:
    records: list[dict[str, object]] = []
    try:
        with ZipFile(docx_path) as package:
            for name in sorted(package.namelist()):
                if not name.startswith("word/media/") or name.endswith("/"):
                    continue
                payload = package.read(name)
                record: dict[str, object] = {
                    "part": name,
                    "sha256": _sha256(payload),
                    "size": len(payload),
                }
                try:
                    with Image.open(io.BytesIO(payload)) as image:
                        record["pixel_width"] = image.width
                        record["pixel_height"] = image.height
                except Exception:
                    record["image_dimensions_unavailable"] = True
                records.append(record)
    except (BadZipFile, OSError) as exc:
        return False, f"DOCX media inspection failed: {exc}", records
    source_width, source_height = source_dimensions
    forbidden = [
        item
        for item in records
        if item["sha256"] == source_sha256
        or (
            item.get("pixel_width") == source_width
            and item.get("pixel_height") == source_height
        )
    ]
    if forbidden:
        return False, "source-sized or byte-identical page image is embedded", records
    return True, "no source-sized or byte-identical page image is embedded", records


def _hidden_text_check(docx_path: Path) -> tuple[bool, str]:
    try:
        with ZipFile(docx_path) as package:
            xml_parts = [
                (name, package.read(name))
                for name in package.namelist()
                if name.startswith("word/") and name.endswith(".xml")
            ]
    except (BadZipFile, OSError) as exc:
        return False, f"DOCX hidden-text inspection failed: {exc}"

    hidden_semantic_runs: list[str] = []
    for name, payload in xml_parts:
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            return False, f"DOCX hidden-text inspection failed in {name}: {exc}"
        for run in root.iter(_WORD_RUN):
            properties = run.find(_WORD_RUN_PROPERTIES)
            if properties is None or (
                properties.find(_WORD_VANISH) is None
                and properties.find(_WORD_WEB_HIDDEN) is None
            ):
                continue
            value = "".join(
                node.text or "" for node in run.iter() if node.tag in _WORD_TEXT_TAGS
            )
            if any(
                not character.isspace()
                and character not in _LAYOUT_ONLY_ZERO_WIDTH_CHARACTERS
                for character in value
            ):
                hidden_semantic_runs.append(name)

    if hidden_semantic_runs:
        parts = ", ".join(sorted(set(hidden_semantic_runs)))
        return False, f"DOCX contains hidden-text semantic content in: {parts}"
    return True, "DOCX contains no hidden semantic text"


def _integrity_report(
    fixture: FixtureContract,
    *,
    docx_path: Path,
    observation: Any,
    snapshot: Any,
    visible_text: str,
) -> dict[str, object]:
    checks: list[dict[str, object]] = []

    def record(name: str, passed: bool, reason: str, **details: object) -> None:
        checks.append(
            {
                "name": name,
                "passed": passed,
                "reason": reason,
                **details,
            }
        )

    record(
        "source_reference_identity",
        fixture.reference.source_sha256 == fixture.source_sha256,
        "source and reviewed reference SHA-256 identities match",
    )
    record(
        "docx_package_readable",
        bool(observation.package_readable),
        "DOCX observer parsed the OPC package",
    )
    record(
        "python_docx_reopenable",
        bool(observation.python_docx_reopenable),
        "python-docx reopened the generated package",
    )
    record(
        "docx_observer_errors",
        not observation.errors,
        "DOCX observer reported no missing or malformed required content",
        errors=list(observation.errors),
    )
    record(
        "external_relationships",
        not observation.external_relationships,
        "DOCX contains no external relationships",
        relationships=list(observation.external_relationships),
    )
    record(
        "actual_render",
        snapshot.page_count >= 1 and bool(snapshot.pages),
        "LibreOffice opened the DOCX without repair and Poppler rasterized pages",
        renderer=snapshot.renderer_name,
        renderer_version=snapshot.renderer_version,
        rasterizer=snapshot.rasterizer_name,
        rasterizer_version=snapshot.rasterizer_version,
        page_count=snapshot.page_count,
    )
    record(
        "rendered_visible_text",
        bool(visible_text.strip()),
        "structure-aware OCR observed visible text on actual rendered pages",
    )
    reopen_passed, reopen_reason = _reopen_edit_save(docx_path)
    record("edit_save_reopen", reopen_passed, reopen_reason)
    media_passed, media_reason, media = _docx_media_evidence(
        docx_path,
        source_sha256=fixture.source_sha256,
        source_dimensions=(fixture.source_width, fixture.source_height),
    )
    record("source_background_absent", media_passed, media_reason, media=media)
    hidden_passed, hidden_reason = _hidden_text_check(docx_path)
    record("hidden_truth_absent", hidden_passed, hidden_reason)
    return {
        "passed": all(bool(item["passed"]) for item in checks),
        "checks": checks,
    }


def _git_output(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def _runtime_record(suite: SuiteContract) -> dict[str, object]:
    required_languages = tuple(
        dict.fromkeys((*suite.production_languages, *suite.visible_languages))
    )
    capabilities = TesseractOcrBackend(
        required_languages=required_languages
    ).healthcheck()
    return {
        "runner": {"name": STAGE_RUNNER_NAME, "version": STAGE_RUNNER_VERSION},
        "aiteqno_version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "git": {
            "commit": _git_output("rev-parse", "HEAD"),
            "working_tree_dirty": bool(_git_output("status", "--porcelain")),
        },
        "tesseract": {
            "provider": capabilities.provider,
            "version": capabilities.provider_version,
            "executable": capabilities.executable,
            "required_languages": list(required_languages),
            "available_languages": list(capabilities.available_languages),
        },
        "production_pipeline": {
            "entrypoint": "aiteqno roundtrip",
            "languages": list(suite.production_languages),
            "preview_dpi": suite.preview_dpi,
        },
        "evaluation": {
            "evaluator_name": SOURCE_BASELINE_EVALUATOR_NAME,
            "evaluator_version": SOURCE_BASELINE_EVALUATOR_VERSION,
            "text_evidence": "rendered_visible",
            "visible_ocr_languages": list(suite.visible_languages),
            "page_segmentation_mode": suite.visible_options.page_segmentation_mode,
            "engine_mode": suite.visible_options.engine_mode,
            "snapshot_dpi": suite.snapshot_dpi,
        },
    }


def _previous_scores(path: Path | None) -> dict[str, float]:
    if path is None:
        return {}
    value = _object(json.loads(path.read_bytes()), "previous stage summary")
    fixtures = _array(value.get("fixtures"), "previous stage fixtures")
    result: dict[str, float] = {}
    for raw in fixtures:
        item = _object(raw, "previous stage fixture")
        fixture_id = _non_empty_string(item.get("fixture_id"), "previous fixture_id")
        score = item.get("overall_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise RuntimeError("previous overall_score must be numeric")
        result[fixture_id] = float(score)
    return result


def _run_fixture(
    fixture: FixtureContract,
    suite: SuiteContract,
    fixture_output: Path,
    *,
    previous_overall: float | None,
) -> tuple[StageFixtureMeasurement, dict[str, object]]:
    fixture_output.mkdir(parents=True)
    source_output = fixture_output / "source.png"
    _write_bytes_new(source_output, fixture.source_data)
    _write_bytes_new(fixture_output / "manifest.json", fixture.manifest_path.read_bytes())
    _write_bytes_new(fixture_output / "reference.json", fixture.reference_path.read_bytes())

    roundtrip_output = fixture_output / "public-roundtrip"
    arguments = [
        "roundtrip",
        str(source_output),
        "-o",
        str(roundtrip_output),
        "--dpi",
        str(suite.preview_dpi),
    ]
    for language in suite.production_languages:
        arguments.extend(("--language", language))
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = cli_main(
        arguments,
        runtime=default_runtime(),
        stdout=stdout,
        stderr=stderr,
    )
    _write_bytes_new(
        fixture_output / "roundtrip-stdout.txt",
        stdout.getvalue().encode("utf-8"),
    )
    _write_bytes_new(
        fixture_output / "roundtrip-stderr.txt",
        stderr.getvalue().encode("utf-8"),
    )
    if exit_code != 0:
        raise RuntimeError(
            f"public aiteqno roundtrip failed for {fixture.fixture_id}: "
            f"exit code {exit_code}"
        )

    document_path = roundtrip_output / "document.ir.json"
    docx_path = roundtrip_output / "reconstructed.docx"
    document = document_ir_from_file(document_path)
    observation = PythonDocxObserver().observe(docx_path)
    _write_json_new(
        fixture_output / "docx-observation.json",
        observation.to_dict(),
    )
    snapshot_directory = fixture_output / "actual-docx-snapshot"
    snapshot = LibreOfficeSnapshotRenderer().render_evidence(
        docx_path,
        snapshot_directory,
        rasterizer_executable_path=(
            os.environ.get("AITEQNO_PDFTOPPM_EXECUTABLE") or None
        ),
        dpi=suite.snapshot_dpi,
    )
    _write_json_new(
        snapshot_directory / "snapshot-evidence.json",
        snapshot.to_dict(),
    )
    visible_text, visible_pages = _visible_snapshot_ocr(
        fixture_output,
        snapshot,
        snapshot_dpi=suite.snapshot_dpi,
        languages=suite.visible_languages,
        options=suite.visible_options,
    )
    result = evaluate_source_baseline(
        fixture.reference,
        SourceBaselineObservation(
            source_sha256=fixture.source_sha256,
            candidate_ir=document,
            final_docx_text=_document_text(observation),
            visible_rendered_text=visible_text,
            rendered_page_count=snapshot.page_count,
        ),
        config=SourceBaselineConfig(threshold=suite.threshold),
    )
    _write_bytes_new(
        fixture_output / "source-quality-evaluation.json",
        (result.to_json(indent=2) + "\n").encode("utf-8"),
    )
    integrity = _integrity_report(
        fixture,
        docx_path=docx_path,
        observation=observation,
        snapshot=snapshot,
        visible_text=visible_text,
    )
    _write_json_new(fixture_output / "integrity.json", integrity)

    components = {item.name: item.score for item in result.components}
    fixture_summary: dict[str, object] = {
        "fixture_id": fixture.fixture_id,
        "source": {
            "repository_path": fixture.source_path.relative_to(REPOSITORY_ROOT).as_posix(),
            "encoding": fixture.source_encoding,
            "sha256": fixture.source_sha256,
            "pixel_width": fixture.source_width,
            "pixel_height": fixture.source_height,
            "dpi": fixture.source_dpi,
        },
        "reference": {
            "repository_path": fixture.reference_path.relative_to(REPOSITORY_ROOT).as_posix(),
            "sha256": fixture.reference_sha256,
            "reviewed": fixture.reference.reviewed,
        },
        "manifest_sha256": fixture.manifest_sha256,
        "evaluator_name": result.evaluator_name,
        "evaluator_version": result.evaluator_version,
        "text_evidence": result.text_evidence,
        "overall_score": result.overall_score,
        "threshold": suite.threshold,
        "legacy_evaluator_state_diagnostic": result.state.value,
        "components": components,
        "hard_gates_diagnostic": [item.to_dict() for item in result.hard_gates],
        "manual_checks_diagnostic": [item.to_dict() for item in result.manual_checks],
        "integrity_passed": integrity["passed"],
        "rendered_page_count_diagnostic": snapshot.page_count,
        "visible_page_count": len(visible_pages),
        "artifacts": {
            "document_ir": "public-roundtrip/document.ir.json",
            "docx": "public-roundtrip/reconstructed.docx",
            "preview": "public-roundtrip/reconstructed.png",
            "actual_snapshot": "actual-docx-snapshot/snapshot-evidence.json",
            "visible_ocr": "rendered-visible-ocr/visible-ocr.json",
            "evaluation": "source-quality-evaluation.json",
            "integrity": "integrity.json",
        },
        "artifact_hashes": {
            "document_ir": _path_sha256(document_path),
            "docx": _path_sha256(docx_path),
            "evaluation": _path_sha256(
                fixture_output / "source-quality-evaluation.json"
            ),
        },
    }
    if previous_overall is not None:
        fixture_summary["previous_overall_score_diagnostic"] = previous_overall
        fixture_summary["score_delta_diagnostic"] = round(
            result.overall_score - previous_overall,
            6,
        )
    _write_json_new(fixture_output / "fixture-summary.json", fixture_summary)
    measurement = StageFixtureMeasurement(
        fixture_id=fixture.fixture_id,
        overall_score=result.overall_score,
        integrity_passed=bool(integrity["passed"]),
        artifact_path=f"fixtures/{fixture.fixture_id}",
        previous_overall_score=previous_overall,
    )
    return measurement, fixture_summary


def run(
    suite_path: Path,
    output_directory: Path,
    *,
    previous_summary: Path | None = None,
) -> dict[str, object]:
    suite = _load_suite(suite_path)
    output = output_directory.resolve(strict=False)
    output.mkdir(parents=True)
    try:
        runtime = _runtime_record(suite)
        _write_json_new(output / "runtime.json", runtime)
        _write_bytes_new(output / "suite.json", suite.suite_path.read_bytes())
        previous = _previous_scores(previous_summary)
        unknown_previous = sorted(set(previous) - {item.fixture_id for item in suite.fixtures})
        if unknown_previous:
            raise RuntimeError(
                "previous summary contains fixtures outside this stage: "
                + ", ".join(unknown_previous)
            )
        measurements: list[StageFixtureMeasurement] = []
        summaries: list[dict[str, object]] = []
        for fixture in suite.fixtures:
            measurement, summary = _run_fixture(
                fixture,
                suite,
                output / "fixtures" / fixture.fixture_id,
                previous_overall=previous.get(fixture.fixture_id),
            )
            measurements.append(measurement)
            summaries.append(summary)
        gate = evaluate_stage_gate(measurements, threshold=suite.threshold)
        by_id = {item["fixture_id"]: item for item in summaries}
        stage_summary = {
            "stage_id": suite.stage_id,
            "suite_sha256": suite.suite_sha256,
            "runner": {"name": STAGE_RUNNER_NAME, "version": STAGE_RUNNER_VERSION},
            "runtime": "runtime.json",
            "evaluator": {
                "name": SOURCE_BASELINE_EVALUATOR_NAME,
                "version": SOURCE_BASELINE_EVALUATOR_VERSION,
                "text_evidence": "rendered_visible",
            },
            "threshold": suite.threshold,
            "minimum_overall": gate.minimum_overall,
            "average_overall_diagnostic": gate.average_overall_diagnostic,
            "average_used_for_decision": False,
            "state": gate.state,
            "fixtures": [
                {
                    "fixture_id": decision.measurement.fixture_id,
                    "source_sha256": by_id[decision.measurement.fixture_id]["source"]["sha256"],
                    "reference_sha256": by_id[decision.measurement.fixture_id]["reference"]["sha256"],
                    "overall_score": decision.measurement.overall_score,
                    "integrity_passed": decision.measurement.integrity_passed,
                    "passed": decision.passed,
                    "reasons": list(decision.reasons),
                    "legacy_evaluator_state_diagnostic": by_id[decision.measurement.fixture_id]["legacy_evaluator_state_diagnostic"],
                    "components": by_id[decision.measurement.fixture_id]["components"],
                    "artifact": decision.measurement.artifact_path,
                    **(
                        {
                            "previous_overall_score_diagnostic": decision.measurement.previous_overall_score,
                            "score_delta_diagnostic": round(
                                decision.measurement.overall_score
                                - decision.measurement.previous_overall_score,
                                6,
                            ),
                        }
                        if decision.measurement.previous_overall_score is not None
                        else {}
                    ),
                }
                for decision in gate.fixtures
            ],
        }
        _write_json_new(output / "stage-summary.json", stage_summary)
        return stage_summary
    except Exception as exc:
        try:
            _write_json_new(
                output / "operational-error.json",
                {"error_type": type(exc).__name__, "message": str(exc)},
            )
        except Exception:
            pass
        raise


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous-summary", type=Path)
    parser.add_argument("--expect-state", choices=("pass", "fail"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    try:
        summary = run(
            arguments.suite,
            arguments.output,
            previous_summary=arguments.previous_summary,
        )
    except Exception as exc:
        print(f"stage run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"stage_id={summary['stage_id']}")
    print(f"state={summary['state']}")
    print(f"minimum_overall={summary['minimum_overall']}")
    print(f"artifacts={arguments.output.resolve(strict=False)}")
    if arguments.expect_state is not None and summary["state"] != arguments.expect_state:
        print(
            "stage state mismatch: "
            f"expected {arguments.expect_state}, observed {summary['state']}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
