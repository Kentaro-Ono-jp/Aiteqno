"""Build the deterministic Windows demo ZIP attached to GitHub Releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from collections.abc import Sequence
from email.parser import Parser
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ARCHIVE_FORMAT_VERSION = "1.0"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
RELEASE_TAG_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_wheel(path: Path) -> Path:
    if path.is_dir():
        wheels = sorted(path.glob("aiteqno-*.whl"))
        if len(wheels) != 1:
            raise ValueError(f"expected one Aiteqno wheel in {path}, received {wheels}")
        return wheels[0]
    if not path.is_file() or path.suffix != ".whl":
        raise ValueError(f"Aiteqno wheel does not exist: {path}")
    return path


def _wheel_identity(wheel: Path) -> tuple[str, str]:
    with ZipFile(wheel) as archive:
        metadata_paths = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_paths) != 1:
            raise ValueError(f"wheel has unexpected METADATA entries: {metadata_paths}")
        metadata = Parser().parsestr(
            archive.read(metadata_paths[0]).decode("utf-8")
        )
    name = metadata.get("Name", "")
    version = metadata.get("Version", "")
    if name.casefold().replace("_", "-") != "aiteqno" or not version:
        raise ValueError(f"wheel identity is not Aiteqno: name={name!r}, version={version!r}")
    return name, version


def _required_source(repository_root: Path, relative_path: str) -> bytes:
    path = repository_root / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"demo package source is missing: {path}")
    return path.read_bytes()


def _render_readme(template: bytes, release_tag: str, package_version: str) -> bytes:
    text = template.decode("utf-8")
    rendered = text.replace("{{RELEASE_TAG}}", release_tag).replace(
        "{{PACKAGE_VERSION}}", package_version
    )
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("demo README retains an unresolved template placeholder")
    return rendered.encode("utf-8")


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(name, FIXED_ZIP_TIMESTAMP)
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = ZIP_DEFLATED
    return info


def build_demo_package(
    *,
    repository_root: str | Path,
    wheel_path: str | Path,
    output_path: str | Path,
    release_tag: str,
) -> Path:
    """Build one deterministic, self-describing Windows demo archive."""

    if not RELEASE_TAG_PATTERN.fullmatch(release_tag):
        raise ValueError(f"release tag is not archive-safe: {release_tag!r}")

    root = Path(repository_root).resolve()
    wheel = _resolve_wheel(Path(wheel_path).resolve())
    output = Path(output_path).resolve()
    if output.exists():
        raise FileExistsError(f"demo archive already exists: {output}")
    _, package_version = _wheel_identity(wheel)

    files = {
        "README.md": _render_readme(
            _required_source(root, "demo/windows/README.md"),
            release_tag,
            package_version,
        ),
        "LICENSE": _required_source(root, "LICENSE"),
        "run-demo.cmd": _required_source(root, "demo/windows/run-demo.cmd"),
        "run-demo.ps1": _required_source(root, "demo/windows/run-demo.ps1"),
        "runner/demo_runner.py": _required_source(root, "scripts/demo_runner.py"),
        "schema/document-ir-v0.1.schema.json": _required_source(
            root, "schemas/document-ir-v0.1.schema.json"
        ),
        f"wheel/{wheel.name}": wheel.read_bytes(),
    }
    manifest = {
        "archive_format_version": ARCHIVE_FORMAT_VERSION,
        "release_tag": release_tag,
        "package_name": "aiteqno",
        "package_version": package_version,
        "entrypoint": "run-demo.cmd",
        "prerequisites": {
            "operating_system": "Windows 10 or 11",
            "python": ">=3.11,<3.15",
            "tesseract": ">=5 with jpn language data; eng is optional",
            "first_run_internet": True,
        },
        "files": {
            path: {"sha256": _sha256_bytes(data), "size_bytes": len(data)}
            for path, data in sorted(files.items())
        },
    }
    files["package.manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    archive_root = f"Aiteqno-demo-{release_tag}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
    try:
        with ZipFile(temporary_path, mode="w") as archive:
            for relative_path, data in sorted(files.items()):
                archive.writestr(
                    _zip_info(f"{archive_root}/{relative_path}"),
                    data,
                    compress_type=ZIP_DEFLATED,
                    compresslevel=9,
                )
        temporary_path.replace(output)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    archive = build_demo_package(
        repository_root=arguments.repository_root,
        wheel_path=arguments.wheel,
        output_path=arguments.output,
        release_tag=arguments.release_tag,
    )
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
