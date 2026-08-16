"""Verify the contents and hashes of an Aiteqno Windows demo ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile


REQUIRED_PATHS = {
    "README.md",
    "LICENSE",
    "run-demo.cmd",
    "run-demo.ps1",
    "runner/demo_runner.py",
    "schema/document-ir-v0.1.schema.json",
    "package.manifest.json",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AssertionError(f"{label} must be a JSON object")
    return value


def verify_demo_package(
    archive_path: str | Path,
    *,
    expected_release_tag: str | None = None,
    expected_package_version: str | None = None,
) -> Mapping[str, Any]:
    """Verify the archive contract and return its parsed package manifest."""

    path = Path(archive_path)
    if not path.is_file():
        raise FileNotFoundError(f"demo archive does not exist: {path}")

    with ZipFile(path) as archive:
        names = archive.namelist()
        if not names or any(name.startswith("/") or "\\" in name for name in names):
            raise AssertionError("archive paths must be relative POSIX paths")
        roots = {PurePosixPath(name).parts[0] for name in names}
        if len(roots) != 1:
            raise AssertionError(f"archive must have one root directory: {sorted(roots)}")
        archive_root = roots.pop()
        relative_names = {
            PurePosixPath(name).relative_to(archive_root).as_posix() for name in names
        }
        missing = REQUIRED_PATHS - relative_names
        if missing:
            raise AssertionError(f"demo archive is missing files: {sorted(missing)}")

        manifest_path = f"{archive_root}/package.manifest.json"
        manifest = _object(
            json.loads(archive.read(manifest_path).decode("utf-8")),
            "package manifest",
        )
        release_tag = manifest.get("release_tag")
        package_version = manifest.get("package_version")
        if expected_release_tag is not None and release_tag != expected_release_tag:
            raise AssertionError(
                f"release tag mismatch: expected {expected_release_tag!r}, received {release_tag!r}"
            )
        if expected_package_version is not None and package_version != expected_package_version:
            raise AssertionError(
                "package version mismatch: "
                f"expected {expected_package_version!r}, received {package_version!r}"
            )
        if archive_root != f"Aiteqno-demo-{release_tag}":
            raise AssertionError(
                f"archive root does not match release tag: {archive_root!r}"
            )

        declared_files = _object(manifest.get("files"), "manifest files")
        expected_names = set(declared_files) | {"package.manifest.json"}
        if relative_names != expected_names:
            raise AssertionError(
                "archive and manifest file sets differ: "
                f"archive={sorted(relative_names)}, manifest={sorted(expected_names)}"
            )
        for relative_path, raw_record in declared_files.items():
            record = _object(raw_record, f"record for {relative_path}")
            data = archive.read(f"{archive_root}/{relative_path}")
            if record.get("sha256") != _sha256(data):
                raise AssertionError(f"SHA-256 mismatch for {relative_path}")
            if record.get("size_bytes") != len(data):
                raise AssertionError(f"size mismatch for {relative_path}")

        wheel_paths = [name for name in declared_files if name.startswith("wheel/")]
        if len(wheel_paths) != 1 or not wheel_paths[0].endswith(".whl"):
            raise AssertionError(f"expected one bundled wheel: {wheel_paths}")
        wheel_bytes = archive.read(f"{archive_root}/{wheel_paths[0]}")
        with ZipFile(BytesIO(wheel_bytes)) as wheel:
            metadata = [
                name
                for name in wheel.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata) != 1:
                raise AssertionError(f"bundled wheel metadata is invalid: {metadata}")

        schema = json.loads(
            archive.read(
                f"{archive_root}/schema/document-ir-v0.1.schema.json"
            ).decode("utf-8")
        )
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise AssertionError("bundled Document IR schema is not Draft 2020-12")

        powershell = archive.read(f"{archive_root}/run-demo.ps1").decode("utf-8")
        if "$env:LOCALAPPDATA" not in powershell or "Get-Sha256Hex" not in powershell:
            raise AssertionError("PowerShell launcher lacks the per-user runtime contract")
        if "\\.venv" in powershell or "/.venv" in powershell:
            raise AssertionError("PowerShell launcher must not create a repository .venv")

        command = archive.read(f"{archive_root}/run-demo.cmd").decode("utf-8")
        if "%~1" not in command or "run-demo.ps1" not in command:
            raise AssertionError("CMD launcher lacks drag-and-drop forwarding")
        readme = archive.read(f"{archive_root}/README.md").decode("utf-8")
        if "{{" in readme or "}}" in readme:
            raise AssertionError("demo README retains template placeholders")

    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--release-tag")
    parser.add_argument("--package-version")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    manifest = verify_demo_package(
        arguments.archive,
        expected_release_tag=arguments.release_tag,
        expected_package_version=arguments.package_version,
    )
    print(
        f"verified {arguments.archive}: "
        f"{manifest['release_tag']} / aiteqno {manifest['package_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
