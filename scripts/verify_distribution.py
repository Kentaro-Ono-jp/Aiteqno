"""Verify the built wheel/sdist contract on every supported CI shell."""

import argparse
from collections.abc import Sequence
from pathlib import Path
from tarfile import open as open_tar
from zipfile import ZipFile


DIST_DIRECTORY = Path("dist")
REQUIRED_WHEEL_FILES = {
    "aiteqno/__init__.py",
    "aiteqno/_version.py",
    "aiteqno/py.typed",
    "aiteqno/domain/__init__.py",
    "aiteqno/domain/codec.py",
    "aiteqno/domain/errors.py",
    "aiteqno/domain/model.py",
    "aiteqno/ports/__init__.py",
    "aiteqno/ports/assets.py",
    "aiteqno/ports/baseline.py",
    "aiteqno/ports/docx.py",
    "aiteqno/ports/evaluation.py",
    "aiteqno/ports/extraction.py",
    "aiteqno/ports/ocr.py",
    "aiteqno/ports/ocr_experiment.py",
    "aiteqno/ports/ocr_quality.py",
    "aiteqno/ports/ocr_resolution.py",
    "aiteqno/ports/preview.py",
    "aiteqno/ports/structure.py",
    "aiteqno/application/__init__.py",
    "aiteqno/application/baseline.py",
    "aiteqno/application/extract.py",
    "aiteqno/application/evaluate.py",
    "aiteqno/application/ocr_experiment.py",
    "aiteqno/application/ocr_padding.py",
    "aiteqno/application/ocr_quality.py",
    "aiteqno/application/ocr_resolution.py",
    "aiteqno/application/preview.py",
    "aiteqno/application/render.py",
    "aiteqno/adapters/__init__.py",
    "aiteqno/adapters/assets.py",
    "aiteqno/adapters/docx.py",
    "aiteqno/adapters/evaluation.py",
    "aiteqno/adapters/extraction.py",
    "aiteqno/adapters/json_schema.py",
    "aiteqno/adapters/libreoffice.py",
    "aiteqno/adapters/ocr_fake.py",
    "aiteqno/adapters/preview.py",
    "aiteqno/adapters/structure.py",
    "aiteqno/adapters/tesseract.py",
    "aiteqno/cli/__init__.py",
    "aiteqno/cli/__main__.py",
    "aiteqno/cli/main.py",
}
FORBIDDEN_SDIST_PATHS = {
    "SchemaBridge/",
    "tests/test_layout_extractor_smoke.py",
}
FORBIDDEN_RUNTIME_REQUIREMENTS = {"reportlab"}
FORBIDDEN_REPOSITORY_PATHS = {
    Path("output/layout_a4_portrait.json"),
}
REQUIRED_SDIST_PATHS = {
    "MANIFEST.in",
    "demo/windows/README.md",
    "demo/windows/run-demo.cmd",
    "demo/windows/run-demo.ps1",
    "docs/demo-release.md",
    "docs/real-runtime-baseline.md",
    "scripts/build_real_baseline_fixture.py",
    "scripts/build_demo_package.py",
    "scripts/demo_runner.py",
    "scripts/run_real_baseline.py",
    "scripts/verify_demo_package.py",
    "tests/fixtures/baseline/excluded-sources.json",
    "tests/fixtures/baseline/synthetic-dense-japanese-form-v1/generation.json",
    "tests/fixtures/baseline/synthetic-dense-japanese-form-v1/manifest.json",
    "tests/fixtures/baseline/synthetic-dense-japanese-form-v1/reference.json",
    "tests/fixtures/baseline/synthetic-dense-japanese-form-v1/source.png.b64",
}


def verify_distribution(dist_directory: Path = DIST_DIRECTORY) -> None:
    legacy_repository_entries = {
        path.as_posix() for path in FORBIDDEN_REPOSITORY_PATHS if path.exists()
    }
    if legacy_repository_entries:
        raise AssertionError(
            "repository retains removed pre-V1 files: "
            f"{sorted(legacy_repository_entries)}"
        )

    wheels = list(dist_directory.glob("aiteqno-*.whl"))
    sdists = list(dist_directory.glob("aiteqno-*.tar.gz"))
    if len(wheels) != 1:
        raise AssertionError(f"expected one wheel, received: {wheels}")
    if len(sdists) != 1:
        raise AssertionError(f"expected one sdist, received: {sdists}")

    with ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        entry_points = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        if len(entry_points) != 1:
            raise AssertionError(f"unexpected entry point files: {entry_points}")
        entry_point_text = archive.read(entry_points[0]).decode("utf-8")
        metadata_entries = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_entries) != 1:
            raise AssertionError(f"unexpected metadata files: {metadata_entries}")
        metadata_text = archive.read(metadata_entries[0]).decode("utf-8")

    missing = REQUIRED_WHEEL_FILES - names
    if missing:
        raise AssertionError(f"wheel is missing files: {sorted(missing)}")
    schema_suffix = ".data/data/share/aiteqno/schemas/document-ir-v0.1.schema.json"
    schema_entries = [name for name in names if name.endswith(schema_suffix)]
    if len(schema_entries) != 1:
        raise AssertionError(f"unexpected schema entries: {schema_entries}")
    if "aiteqno = aiteqno.cli:main" not in entry_point_text:
        raise AssertionError("console entry point is missing")

    lowered_metadata = metadata_text.casefold()
    obsolete_requirements = {
        dependency
        for dependency in FORBIDDEN_RUNTIME_REQUIREMENTS
        if f"requires-dist: {dependency}" in lowered_metadata
    }
    if obsolete_requirements:
        raise AssertionError(
            f"wheel retains obsolete dependencies: {sorted(obsolete_requirements)}"
        )

    with open_tar(sdists[0], mode="r:gz") as archive:
        sdist_names = {member.name.replace("\\", "/") for member in archive}
    missing_sdist_paths = {
        required
        for required in REQUIRED_SDIST_PATHS
        if not any(name.endswith(f"/{required}") for name in sdist_names)
    }
    if missing_sdist_paths:
        raise AssertionError(
            f"sdist is missing release sources: {sorted(missing_sdist_paths)}"
        )
    legacy_entries = {
        name
        for name in sdist_names
        if any(
            name.endswith(forbidden.rstrip("/")) or f"/{forbidden}" in f"/{name}"
            for forbidden in FORBIDDEN_SDIST_PATHS
        )
    }
    if legacy_entries:
        raise AssertionError(
            f"sdist retains removed pre-V1 files: {sorted(legacy_entries)}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist-directory",
        type=Path,
        default=DIST_DIRECTORY,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    verify_distribution(arguments.dist_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
