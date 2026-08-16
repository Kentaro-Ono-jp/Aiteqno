"""Verify the built wheel/sdist contract on every supported CI shell."""

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
    "aiteqno/ports/docx.py",
    "aiteqno/ports/evaluation.py",
    "aiteqno/ports/extraction.py",
    "aiteqno/ports/ocr.py",
    "aiteqno/ports/preview.py",
    "aiteqno/ports/structure.py",
    "aiteqno/application/__init__.py",
    "aiteqno/application/extract.py",
    "aiteqno/application/evaluate.py",
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


def main() -> None:
    wheels = list(DIST_DIRECTORY.glob("aiteqno-*.whl"))
    sdists = list(DIST_DIRECTORY.glob("aiteqno-*.tar.gz"))
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


if __name__ == "__main__":
    main()
