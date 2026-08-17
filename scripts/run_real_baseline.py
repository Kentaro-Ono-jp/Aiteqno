"""Run the licensed Japanese failure baseline through every real runtime layer.

This command is intentionally a measurement runner, not a product-quality
shortcut.  It keeps the source-grounded score separate from the existing
IR-to-DOCX restoration score, checkpoints OCR-only quality before DOCX work,
and retains the actual LibreOffice PDF/pages used for the decision.  A known
quality ``fail`` is a successful baseline run when ``--expect-state fail`` is
supplied.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import locale
import os
import platform
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from aiteqno import __version__
from aiteqno.adapters import (
    BundleAssetResolver,
    FilesystemDocumentBundleWriter,
    JsonSchemaDocumentIRValidator,
    LibreOfficeSnapshotRenderer,
    OpenCvStructureExtractor,
    PillowPngAssetEncoder,
    PillowPngDecoder,
    PillowPreviewRenderer,
    PythonDocxObserver,
    PythonDocxRenderer,
    TesseractOcrBackend,
)
from aiteqno.application import (
    OcrQualityConfig,
    SourceBaselineConfig,
    build_evaluation_reference,
    evaluate_ocr_quality,
    evaluate_restoration,
    evaluate_source_baseline,
    extract_png,
    render_docx,
    render_preview,
)
from aiteqno.domain import ElementType
from aiteqno.ports import (
    EvaluationState,
    OcrOptions,
    OcrQualityObservation,
    OcrRuntimeEvidence,
    OcrTrainedDataEvidence,
    SnapshotObservation,
    SourceBaselineObservation,
    SourceBaselineReference,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_DIRECTORY = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "baseline"
    / "synthetic-dense-japanese-form-v1"
)
LANGUAGES = ("jpn", "eng")
OCR_OPTIONS = OcrOptions(page_segmentation_mode=6, engine_mode=3)
OCR_PROVIDER = "tesseract"
MINIMUM_TESSERACT_MAJOR_VERSION = 5
SOURCE_DPI = 96


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def _write_json_new(path: Path, value: Any) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_bytes_new(path, payload)


def _fixture_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"fixture contract mismatch: {label} must be an object")
    return value


def _require_contract_equal(label: str, observed: Any, expected: Any) -> None:
    if observed != expected:
        raise RuntimeError(
            f"fixture contract mismatch for {label}: "
            f"expected {expected!r}, observed {observed!r}"
        )


def _fixture_source_path(fixture_directory: Path, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise RuntimeError(
            "fixture contract mismatch: source.path must be a non-empty relative path"
        )
    portable_path = PurePosixPath(raw_path.replace("\\", "/"))
    windows_path = PureWindowsPath(raw_path)
    if (
        portable_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or not portable_path.parts
        or ".." in portable_path.parts
    ):
        raise RuntimeError(
            "fixture contract mismatch: source.path must remain inside the fixture "
            "directory"
        )

    fixture_root = fixture_directory.resolve()
    source_path = fixture_root.joinpath(*portable_path.parts).resolve(strict=False)
    try:
        source_path.relative_to(fixture_root)
    except ValueError as exc:
        raise RuntimeError(
            "fixture contract mismatch: source.path resolves outside the fixture "
            "directory"
        ) from exc
    if not source_path.is_file():
        raise RuntimeError(
            f"fixture contract mismatch: source.path is not a file: {raw_path!r}"
        )
    return source_path


def _read_fixture(
    fixture_directory: Path,
) -> tuple[bytes, dict[str, Any], SourceBaselineReference]:
    manifest_path = fixture_directory / "manifest.json"
    reference_path = fixture_directory / "reference.json"
    manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
    reference_value = json.loads(reference_path.read_text(encoding="utf-8"))
    manifest = dict(_fixture_object(manifest_value, "manifest"))
    reference_data = _fixture_object(reference_value, "reference")
    source_contract = _fixture_object(manifest.get("source"), "manifest source")
    source_dimensions = _fixture_object(
        reference_data.get("source_dimensions"),
        "reference source_dimensions",
    )
    ocr_contract = _fixture_object(
        manifest.get("ocr_contract"),
        "manifest ocr_contract",
    )
    quality_contract = _fixture_object(
        manifest.get("quality_contract"),
        "manifest quality_contract",
    )

    _require_contract_equal(
        "source.encoding",
        source_contract.get("encoding"),
        "base64",
    )
    source_path = _fixture_source_path(
        fixture_directory,
        source_contract.get("path"),
    )
    try:
        source_data = base64.b64decode(source_path.read_bytes().strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(
            "fixture contract mismatch: source is not valid base64"
        ) from exc

    reference = SourceBaselineReference.from_dict(reference_data)
    _require_contract_equal(
        "fixture_id/reference_id",
        manifest.get("fixture_id"),
        reference.reference_id,
    )
    observed_digest = _sha256(source_data)
    for label, declared_digest in (
        ("manifest source.sha256", source_contract.get("sha256")),
        ("reference source_sha256", reference.source_sha256),
        ("reference source_dimensions.sha256", source_dimensions.get("sha256")),
    ):
        _require_contract_equal(label, declared_digest, observed_digest)

    decoded_source = PillowPngDecoder(fallback_dpi=SOURCE_DPI).decode(source_data)
    actual_dimensions = (
        decoded_source.source.pixel_width,
        decoded_source.source.pixel_height,
    )
    _require_contract_equal(
        "manifest source dimensions",
        (source_contract.get("pixel_width"), source_contract.get("pixel_height")),
        actual_dimensions,
    )
    _require_contract_equal(
        "reference source dimensions",
        (
            source_dimensions.get("pixel_width"),
            source_dimensions.get("pixel_height"),
        ),
        actual_dimensions,
    )

    _require_contract_equal(
        "ocr provider",
        ocr_contract.get("provider"),
        OCR_PROVIDER,
    )
    _require_contract_equal(
        "minimum Tesseract major version",
        ocr_contract.get("minimum_major_version"),
        MINIMUM_TESSERACT_MAJOR_VERSION,
    )
    _require_contract_equal(
        "ocr languages",
        ocr_contract.get("languages"),
        list(LANGUAGES),
    )
    _require_contract_equal(
        "ocr page segmentation mode",
        ocr_contract.get("page_segmentation_mode"),
        OCR_OPTIONS.page_segmentation_mode,
    )
    _require_contract_equal(
        "ocr engine mode",
        ocr_contract.get("engine_mode"),
        OCR_OPTIONS.engine_mode,
    )
    _require_contract_equal(
        "manifest source DPI",
        source_contract.get("dpi"),
        SOURCE_DPI,
    )
    _require_contract_equal(
        "ocr source DPI",
        ocr_contract.get("source_dpi"),
        SOURCE_DPI,
    )
    effective_source_dpi = round(
        (decoded_source.source.dpi_x + decoded_source.source.dpi_y) / 2
    )
    _require_contract_equal(
        "decoded source DPI used by runner",
        effective_source_dpi,
        SOURCE_DPI,
    )

    _require_contract_equal(
        "quality/reference expected_page_count",
        quality_contract.get("expected_page_count"),
        reference.expected_page_count,
    )
    _require_contract_equal(
        "quality/reference normalization",
        quality_contract.get("normalization"),
        reference_data.get("normalization"),
    )
    ocr_defaults = OcrQualityConfig()
    component_minimums = _fixture_object(
        quality_contract.get("component_minimums"),
        "quality component_minimums",
    )
    _require_contract_equal(
        "OCR text character minimum",
        component_minimums.get("text_character_accuracy"),
        ocr_defaults.minimum_text_accuracy,
    )
    _require_contract_equal(
        "OCR logical block coverage minimum",
        component_minimums.get("logical_block_coverage"),
        ocr_defaults.minimum_logical_block_coverage,
    )
    _require_contract_equal(
        "OCR essential anchor recall",
        quality_contract.get("essential_anchor_recall"),
        ocr_defaults.required_anchor_recall,
    )
    if not reference.reviewed or manifest["review"]["status"] != "reviewed":
        raise RuntimeError("real baseline source reference has not been human-reviewed")
    return source_data, manifest, reference


def _runtime() -> tuple[PillowPngDecoder, TesseractOcrBackend]:
    decoder = PillowPngDecoder(fallback_dpi=SOURCE_DPI)
    backend = TesseractOcrBackend(
        executable_path=os.environ.get("AITEQNO_TESSERACT_EXECUTABLE") or None,
        tessdata_prefix=os.environ.get("AITEQNO_TESSDATA_PREFIX") or None,
        required_languages=LANGUAGES,
    )
    backend.healthcheck()
    return decoder, backend


def _docx_text(observation: Any) -> str:
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


def _visible_snapshot_ocr(
    snapshot_directory: Path,
    pages: Iterable[Any],
    *,
    decoder: PillowPngDecoder,
    backend: TesseractOcrBackend,
) -> tuple[str, list[dict[str, Any]]]:
    all_text: list[str] = []
    page_evidence: list[dict[str, Any]] = []
    for page in pages:
        page_path = snapshot_directory / page.relative_path
        image = decoder.decode(page_path.read_bytes())
        tokens = sorted(
            backend.recognize(
                image,
                languages=LANGUAGES,
                options=OCR_OPTIONS,
            ),
            key=lambda item: (
                item.bbox.y,
                item.bbox.x,
                item.bbox.height,
                item.bbox.width,
                item.text,
            ),
        )
        page_text = "\n".join(item.text for item in tokens)
        all_text.append(page_text)
        page_evidence.append(
            {
                "page_number": page.page_number,
                "source_relative_path": page.relative_path,
                "text": page_text,
                "tokens": [
                    {
                        "text": item.text,
                        "confidence": item.confidence,
                        "bbox": {
                            "x": item.bbox.x,
                            "y": item.bbox.y,
                            "width": item.bbox.width,
                            "height": item.bbox.height,
                        },
                        "provider": item.provider,
                        "provider_version": item.provider_version,
                        "model": item.model,
                        "parent_region_ref": item.parent_region_ref,
                    }
                    for item in tokens
                ],
            }
        )
    return "\n\n".join(all_text), page_evidence


def _command_output(command: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc)}
    return {
        "available": True,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _tessdata_records(executable: str) -> list[dict[str, Any]]:
    configured = os.environ.get("AITEQNO_TESSDATA_PREFIX")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(executable).resolve().parent / "tessdata",
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/share/tessdata"),
    ]
    directory = next(
        (
            candidate.resolve()
            for candidate in candidates
            if candidate is not None
            and all((candidate / f"{lang}.traineddata").is_file() for lang in LANGUAGES)
        ),
        None,
    )
    if directory is None:
        return [
            {
                "language": language,
                "path": None,
                "sha256": None,
                "size": None,
            }
            for language in LANGUAGES
        ]
    return [
        {
            "language": language,
            "path": str((directory / f"{language}.traineddata").resolve()),
            "sha256": _path_sha256(directory / f"{language}.traineddata"),
            "size": (directory / f"{language}.traineddata").stat().st_size,
        }
        for language in LANGUAGES
    ]


def _ocr_runtime_evidence(
    source_data: bytes,
    *,
    decoder: PillowPngDecoder,
    backend: TesseractOcrBackend,
) -> OcrRuntimeEvidence:
    capabilities = backend.healthcheck()
    source_image = decoder.decode(source_data)
    records_by_language = {
        record["language"]: record
        for record in _tessdata_records(capabilities.executable)
    }
    traineddata: list[OcrTrainedDataEvidence] = []
    for language in LANGUAGES:
        record = records_by_language.get(language)
        if record is None or record.get("sha256") is None or record.get("size") is None:
            raise RuntimeError(
                "OCR runtime evidence is incomplete: could not hash traineddata "
                f"for {language!r}"
            )
        traineddata.append(
            OcrTrainedDataEvidence(
                language=language,
                size_bytes=record["size"],
                sha256=record["sha256"],
            )
        )
    return OcrRuntimeEvidence(
        provider=capabilities.provider,
        provider_version=capabilities.provider_version,
        executable=capabilities.executable,
        languages=LANGUAGES,
        page_segmentation_mode=OCR_OPTIONS.page_segmentation_mode,
        engine_mode=OCR_OPTIONS.engine_mode,
        effective_ocr_dpi=round(
            (source_image.source.dpi_x + source_image.source.dpi_y) / 2
        ),
        source_dpi_x=source_image.source.dpi_x,
        source_dpi_y=source_image.source.dpi_y,
        traineddata=tuple(traineddata),
        operating_system=platform.platform(),
        python_version=platform.python_version(),
    )


def _environment_record(
    *,
    fixture_directory: Path,
    manifest: dict[str, Any],
    source_data: bytes,
    reference_path: Path,
    backend: TesseractOcrBackend,
    snapshot: Any | None,
) -> dict[str, Any]:
    capabilities = backend.healthcheck()
    pip_freeze = _command_output([sys.executable, "-m", "pip", "freeze", "--all"])
    git_revision = _command_output(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"]
    )
    package_versions = _command_output(
        [
            "dpkg-query",
            "-W",
            "-f=${Package}=${Version}\\n",
            "tesseract-ocr",
            "tesseract-ocr-jpn",
            "libreoffice-writer",
            "poppler-utils",
            "fonts-noto-cjk",
            "fonts-liberation2",
        ]
    )
    font_matches = {
        font: _command_output(["fc-match", font])
        for font in ("Arial", "Noto Sans CJK JP", "Yu Gothic")
    }
    libreoffice_command = _command_output(
        [
            os.environ.get("AITEQNO_LIBREOFFICE_EXECUTABLE") or "libreoffice",
            "--version",
        ]
    )
    poppler_command = _command_output(
        [
            os.environ.get("AITEQNO_PDFTOPPM_EXECUTABLE") or "pdftoppm",
            "-v",
        ]
    )
    return {
        "fixture": {
            "id": manifest["fixture_id"],
            "directory": str(fixture_directory),
            "source_sha256": _sha256(source_data),
            "reference_sha256": _path_sha256(reference_path),
        },
        "options": {
            "languages": list(LANGUAGES),
            "page_segmentation_mode": OCR_OPTIONS.page_segmentation_mode,
            "engine_mode": OCR_OPTIONS.engine_mode,
            "source_dpi": manifest["source"]["dpi"],
            "snapshot_dpi": 144 if snapshot is None else snapshot.pages[0].dpi,
        },
        "system": {
            "platform": platform.platform(),
            "os_name": os.name,
            "python": sys.version,
            "python_executable": sys.executable,
            "locale": locale.setlocale(locale.LC_ALL, None),
            "timezone": os.environ.get("TZ"),
            "aiteqno_version": __version__,
        },
        "git": git_revision,
        "python_packages": pip_freeze,
        "tesseract": {
            "provider": capabilities.provider,
            "version": capabilities.provider_version,
            "executable": capabilities.executable,
            "available_languages": list(capabilities.available_languages),
            "traineddata": _tessdata_records(capabilities.executable),
        },
        "libreoffice": {
            "renderer": None if snapshot is None else snapshot.renderer_name,
            "version": None if snapshot is None else snapshot.renderer_version,
            "preflight": libreoffice_command,
        },
        "poppler": {
            "rasterizer": None if snapshot is None else snapshot.rasterizer_name,
            "version": None if snapshot is None else snapshot.rasterizer_version,
            "preflight": poppler_command,
        },
        "linux_packages": package_versions,
        "fontconfig": font_matches,
    }


def _final_state(
    ocr_state: EvaluationState,
    source_state: EvaluationState,
    restoration_state: EvaluationState,
) -> str:
    states = (ocr_state, source_state, restoration_state)
    if EvaluationState.FAIL in states:
        return EvaluationState.FAIL.value
    if EvaluationState.REQUIRES_HUMAN_REVIEW in states:
        return EvaluationState.REQUIRES_HUMAN_REVIEW.value
    return EvaluationState.PASS.value


def run(
    fixture_directory: Path,
    output_directory: Path,
    *,
    expect_state: str | None,
) -> str:
    fixture_directory = fixture_directory.resolve()
    output_directory = output_directory.resolve(strict=False)
    try:
        output_directory.mkdir(parents=True)
    except FileExistsError as exc:
        raise FileExistsError(
            f"baseline output already exists; no files were overwritten: {output_directory}"
        ) from exc

    try:
        return _run_in_created_output(
            fixture_directory,
            output_directory,
            expect_state=expect_state,
        )
    except Exception as exc:
        try:
            _write_json_new(
                output_directory / "operational-error.json",
                {
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        except FileExistsError:
            pass
        except OSError as evidence_error:
            exc.add_note(f"could not retain operational-error.json: {evidence_error}")
        raise


def _run_in_created_output(
    fixture_directory: Path,
    output_directory: Path,
    *,
    expect_state: str | None,
) -> str:

    source_data, manifest, reference = _read_fixture(fixture_directory)
    source_path = output_directory / "source.png"
    _write_bytes_new(source_path, source_data)

    decoder, backend = _runtime()
    _write_json_new(
        output_directory / "preflight-environment.json",
        _environment_record(
            fixture_directory=fixture_directory,
            manifest=manifest,
            source_data=source_data,
            reference_path=fixture_directory / "reference.json",
            backend=backend,
            snapshot=None,
        ),
    )
    bundle_directory = output_directory / "bundle"
    extraction = extract_png(
        source_data,
        bundle_directory,
        decoder=decoder,
        structure_extractor=OpenCvStructureExtractor(),
        ocr_backend=backend,
        asset_encoder=PillowPngAssetEncoder(),
        validator=JsonSchemaDocumentIRValidator(),
        bundle_writer=FilesystemDocumentBundleWriter(),
        languages=LANGUAGES,
        ocr_options=OCR_OPTIONS,
    )
    document = extraction.document
    quality = manifest["quality_contract"]
    minima = quality["component_minimums"]
    ocr_result = evaluate_ocr_quality(
        reference,
        OcrQualityObservation(
            source_sha256=_sha256(source_data),
            candidate_ir=document,
            runtime=_ocr_runtime_evidence(
                source_data,
                decoder=decoder,
                backend=backend,
            ),
        ),
        config=OcrQualityConfig(
            minimum_text_accuracy=minima["text_character_accuracy"],
            minimum_logical_block_coverage=minima["logical_block_coverage"],
            required_anchor_recall=quality["essential_anchor_recall"],
        ),
    )
    _write_bytes_new(
        output_directory / "ocr-quality-evaluation.json",
        (ocr_result.to_json(indent=2) + "\n").encode("utf-8"),
    )

    docx_path = bundle_directory / "reconstructed.docx"
    preview_path = bundle_directory / "reconstructed.png"
    docx_render = render_docx(
        document,
        docx_path,
        renderer=PythonDocxRenderer(
            asset_resolver=BundleAssetResolver(bundle_directory)
        ),
    )
    preview_render = render_preview(
        document,
        preview_path,
        renderer=PillowPreviewRenderer(
            asset_resolver=BundleAssetResolver(bundle_directory)
        ),
        dpi=144,
    )

    _write_json_new(
        output_directory / "extraction-diagnostics.json",
        {
            "count": len(extraction.diagnostics),
            "by_code": dict(
                sorted(Counter(item.code for item in extraction.diagnostics).items())
            ),
            "items": [
                {
                    "code": item.code,
                    "stage": item.stage,
                    "message": item.message,
                    "source_ref": item.source_ref,
                }
                for item in extraction.diagnostics
            ],
        },
    )
    _write_json_new(
        output_directory / "docx-render-report.json", docx_render.report.to_dict()
    )
    _write_json_new(
        output_directory / "preview-render-report.json", preview_render.report.to_dict()
    )

    docx_observer = PythonDocxObserver()
    docx_observation = docx_observer.observe(docx_path)
    _write_json_new(
        output_directory / "docx-observation.json", docx_observation.to_dict()
    )

    snapshot_directory = output_directory / "actual-docx-snapshot"
    snapshot = LibreOfficeSnapshotRenderer().render_evidence(
        docx_path,
        snapshot_directory,
        rasterizer_executable_path=(
            os.environ.get("AITEQNO_PDFTOPPM_EXECUTABLE") or None
        ),
        dpi=144,
    )
    _write_json_new(snapshot_directory / "snapshot-evidence.json", snapshot.to_dict())
    visible_text, visible_pages = _visible_snapshot_ocr(
        snapshot_directory,
        snapshot.pages,
        decoder=decoder,
        backend=backend,
    )
    _write_json_new(
        snapshot_directory / "visible-ocr.json",
        {
            "languages": list(LANGUAGES),
            "page_segmentation_mode": OCR_OPTIONS.page_segmentation_mode,
            "engine_mode": OCR_OPTIONS.engine_mode,
            "pages": visible_pages,
        },
    )

    source_config = SourceBaselineConfig(
        threshold=quality["overall_minimum"],
        minimum_text_accuracy=minima["text_character_accuracy"],
        minimum_logical_block_coverage=minima["logical_block_coverage"],
        minimum_structure_similarity=minima["structure_similarity"],
        minimum_geometry_similarity=minima["geometry_similarity"],
    )
    source_observation = SourceBaselineObservation(
        source_sha256=_sha256(source_data),
        candidate_ir=document,
        final_docx_text=_docx_text(docx_observation),
        visible_rendered_text=visible_text,
        rendered_page_count=snapshot.page_count,
    )
    source_result = evaluate_source_baseline(
        reference,
        source_observation,
        config=source_config,
    )
    _write_bytes_new(
        output_directory / "source-quality-evaluation.json",
        (source_result.to_json(indent=2) + "\n").encode("utf-8"),
    )

    restoration_reference = build_evaluation_reference(
        document,
        reference_id=f"{reference.reference_id}-candidate-ir",
        reviewed=True,
    )
    restoration_result = evaluate_restoration(
        document,
        restoration_reference,
        docx_path,
        docx_render.report,
        observer=docx_observer,
        snapshot=SnapshotObservation(
            renderer_name=snapshot.renderer_name,
            renderer_version=snapshot.renderer_version,
            available=True,
            opened_without_repair=True,
        ),
    )
    _write_bytes_new(
        output_directory / "ir-to-docx-restoration-evaluation.json",
        (restoration_result.to_json(indent=2) + "\n").encode("utf-8"),
    )

    final_state = _final_state(
        ocr_result.state,
        source_result.state,
        restoration_result.state,
    )
    summary = {
        "fixture_id": manifest["fixture_id"],
        "expected_current_state": quality["expected_current_state"],
        "final_state": final_state,
        "layers": {
            "source_to_candidate_ir_ocr": {
                "state": ocr_result.state.value,
                "text_character_accuracy": (ocr_result.text_character_accuracy.score),
                "logical_block_coverage": (ocr_result.logical_block_coverage.score),
                "essential_anchor_recall": (ocr_result.essential_anchor_recall.score),
                "text_evidence": "candidate_ir",
                "report": "ocr-quality-evaluation.json",
            },
            "source_to_actual_docx": {
                "state": source_result.state.value,
                "overall_score": source_result.overall_score,
                "text_evidence": source_result.text_evidence,
            },
            "candidate_ir_to_docx": {
                "state": restoration_result.state.value,
                "overall_score": restoration_result.overall_score,
                "scope": "IR preservation only; not OCR or source-image accuracy",
            },
            "actual_docx_snapshot": {
                "page_count": snapshot.page_count,
                "expected_page_count": reference.expected_page_count,
                "evidence": "actual-docx-snapshot/snapshot-evidence.json",
            },
        },
    }
    _write_json_new(output_directory / "baseline-summary.json", summary)
    environment = _environment_record(
        fixture_directory=fixture_directory,
        manifest=manifest,
        source_data=source_data,
        reference_path=fixture_directory / "reference.json",
        backend=backend,
        snapshot=snapshot,
    )
    _write_json_new(output_directory / "environment.json", environment)

    if expect_state is not None and final_state != expect_state:
        raise RuntimeError(
            f"baseline decision changed: expected {expect_state}, observed {final_state}; "
            "review the retained artifacts before updating the contract"
        )
    return final_state


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture-directory",
        type=Path,
        default=DEFAULT_FIXTURE_DIRECTORY,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--expect-state",
        choices=("pass", "fail", "requires_human_review"),
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    try:
        final_state = run(
            arguments.fixture_directory,
            arguments.output,
            expect_state=arguments.expect_state,
        )
    except Exception as exc:
        print(f"baseline operational error: {exc}", file=sys.stderr)
        return 1
    print(f"baseline_state={final_state}")
    print(f"artifacts={arguments.output.resolve(strict=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
