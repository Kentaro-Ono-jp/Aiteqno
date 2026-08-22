"""LibreOffice headless evidence for repair-free DOCX opening.

The adapter deliberately treats LibreOffice as an optional external runtime.
It converts one immutable DOCX input to a temporary PDF using an isolated user
profile.  A successful, structurally plausible PDF proves that LibreOffice
opened the package without an interactive repair flow.  Page-region extraction
is intentionally left empty in V1, so the evaluator reports zero geometry
credit instead of inventing coordinates.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from aiteqno.ports import SnapshotObservation


DEFAULT_LIBREOFFICE_TIMEOUT_SECONDS = 90.0
DEFAULT_SNAPSHOT_DPI = 144
LIBREOFFICE_RENDERER_NAME = "libreoffice-headless"
PDFTOPPM_RASTERIZER_NAME = "pdftoppm"

_PDFTOPPM_ENVIRONMENT_VARIABLE = "AITEQNO_PDFTOPPM_EXECUTABLE"
_RASTERIZED_PAGE_PATTERN = re.compile(r"^raw-page-(\d+)\.png$")
_CLEANUP_RETRY_ATTEMPTS = 100
_CLEANUP_RETRY_DELAY_SECONDS = 0.1

_REPAIR_MARKERS = (
    "corrupt",
    "damaged",
    "recover",
    "recovery",
    "repair",
)


def _remove_tree_with_retry(path: Path) -> bool:
    """Remove a temporary tree after late LibreOffice profile writes settle.

    On Windows, the headless launcher can return just before its profile
    registry finishes one final write.  A bounded retry keeps that runtime
    race from turning an otherwise repeatable snapshot operation into a
    partially published result.
    """

    for attempt in range(_CLEANUP_RETRY_ATTEMPTS):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt == _CLEANUP_RETRY_ATTEMPTS - 1:
                return False
            time.sleep(_CLEANUP_RETRY_DELAY_SECONDS)
    return False


@dataclass(frozen=True, slots=True)
class LibreOfficeSnapshotPage:
    """One retained, rasterized page from an actual LibreOffice PDF export."""

    page_number: int
    relative_path: str
    sha256: str
    width_px: int
    height_px: int
    dpi: int

    def to_dict(self) -> dict[str, int | str]:
        """Return a JSON-serializable evidence record."""

        return {
            "page_number": self.page_number,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "dpi": self.dpi,
        }


@dataclass(frozen=True, slots=True)
class LibreOfficeSnapshotEvidence:
    """Persisted evidence from LibreOffice plus Poppler page rasterization."""

    renderer_name: str
    renderer_version: str
    rasterizer_name: str
    rasterizer_version: str
    pdf_relative_path: str
    pdf_sha256: str
    page_count: int
    pages: tuple[LibreOfficeSnapshotPage, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable evidence record."""

        return {
            "renderer_name": self.renderer_name,
            "renderer_version": self.renderer_version,
            "rasterizer_name": self.rasterizer_name,
            "rasterizer_version": self.rasterizer_version,
            "pdf": {
                "relative_path": self.pdf_relative_path,
                "sha256": self.pdf_sha256,
            },
            "page_count": self.page_count,
            "pages": [page.to_dict() for page in self.pages],
        }


class LibreOfficeSnapshotRenderer:
    """Open a DOCX with LibreOffice and normalize the compatibility evidence."""

    def __init__(
        self,
        *,
        executable_path: str | PathLike[str] | None = None,
        timeout_seconds: float = DEFAULT_LIBREOFFICE_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._explicit_executable = (
            None if executable_path is None else Path(executable_path).expanduser()
        )
        self._timeout_seconds = float(timeout_seconds)

    def observe(self, docx_path: str | PathLike[str]) -> SnapshotObservation:
        """Return repair-free opening evidence without retaining converted files."""

        target = Path(docx_path).expanduser()
        if target.suffix.lower() != ".docx":
            raise ValueError("docx_path must use the .docx extension")
        if not target.is_file():
            raise FileNotFoundError(f"DOCX input does not exist: {target}")

        executable = self._resolve_executable()
        if executable is None:
            return SnapshotObservation(
                renderer_name=LIBREOFFICE_RENDERER_NAME,
                renderer_version="unavailable",
                available=False,
                opened_without_repair=None,
            )

        version = self._read_version(executable)
        with tempfile.TemporaryDirectory(prefix="aiteqno-libreoffice-") as raw_root:
            root = Path(raw_root)
            output_directory = root / "output"
            profile_directory = root / "profile"
            output_directory.mkdir()
            profile_directory.mkdir()
            command = [
                str(executable),
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile_directory.resolve().as_uri()}",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(output_directory),
                str(target.resolve()),
            ]
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self._timeout_seconds,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return self._failed(version, f"LibreOffice execution failed: {exc}")

            diagnostics = "\n".join(
                part.strip()
                for part in (completed.stdout, completed.stderr)
                if part.strip()
            )
            pdf_path = output_directory / f"{target.stem}.pdf"
            error = self._conversion_error(
                completed.returncode,
                diagnostics,
                pdf_path,
            )
            if error is not None:
                return self._failed(version, error)

        return SnapshotObservation(
            renderer_name=LIBREOFFICE_RENDERER_NAME,
            renderer_version=version,
            available=True,
            opened_without_repair=True,
        )

    def render_evidence(
        self,
        docx_path: str | PathLike[str],
        output_directory: str | PathLike[str],
        *,
        rasterizer_executable_path: str | PathLike[str] | None = None,
        dpi: int = DEFAULT_SNAPSHOT_DPI,
    ) -> LibreOfficeSnapshotEvidence:
        """Create and retain actual PDF and PNG evidence without overwriting files.

        The output directory must not already exist. Work happens in a sibling
        staging directory, so callers either receive a complete evidence set or
        an exception without a partially published output directory.
        """

        target = Path(docx_path).expanduser()
        if target.suffix.lower() != ".docx":
            raise ValueError("docx_path must use the .docx extension")
        if not target.is_file():
            raise FileNotFoundError(f"DOCX input does not exist: {target}")
        if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
            raise ValueError("dpi must be a positive integer")

        evidence_output = Path(output_directory).expanduser().resolve()
        if evidence_output.exists():
            raise FileExistsError(
                "snapshot evidence output_directory already exists; "
                f"choose a new path: {evidence_output}"
            )

        executable = self._resolve_executable()
        if executable is None:
            raise RuntimeError(
                "LibreOffice executable was not found; install LibreOffice or set "
                "AITEQNO_LIBREOFFICE_EXECUTABLE to the soffice executable"
            )
        rasterizer = self._resolve_rasterizer(rasterizer_executable_path)
        if rasterizer is None:
            raise RuntimeError(
                "pdftoppm executable was not found; install Poppler or set "
                f"{_PDFTOPPM_ENVIRONMENT_VARIABLE} to the pdftoppm executable"
            )

        renderer_version = self._read_version(executable)
        rasterizer_version = self._read_rasterizer_version(rasterizer)
        evidence_output.parent.mkdir(parents=True, exist_ok=True)

        work_root = Path(
            tempfile.mkdtemp(
                prefix=f".{evidence_output.name}-work-",
                dir=evidence_output.parent,
            )
        )
        staged_evidence: Path | None = None
        try:
            staged_evidence = Path(
                tempfile.mkdtemp(
                    prefix=f".{evidence_output.name}-staging-",
                    dir=evidence_output.parent,
                )
            )
            conversion_directory = work_root / "conversion"
            profile_directory = work_root / "profile"
            conversion_directory.mkdir()
            profile_directory.mkdir()

            command = [
                str(executable),
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile_directory.resolve().as_uri()}",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(conversion_directory),
                str(target.resolve()),
            ]
            completed = self._run_or_raise(command, "LibreOffice conversion")
            diagnostics = self._diagnostics(completed)
            converted_pdf = conversion_directory / f"{target.stem}.pdf"
            conversion_error = self._conversion_error(
                completed.returncode,
                diagnostics,
                converted_pdf,
            )
            if conversion_error is not None:
                raise RuntimeError(conversion_error)

            snapshot_pdf = staged_evidence / "snapshot.pdf"
            shutil.copyfile(converted_pdf, snapshot_pdf)
            raster_prefix = staged_evidence / "raw-page"
            raster_command = [
                str(rasterizer),
                "-png",
                "-r",
                str(dpi),
                str(snapshot_pdf),
                str(raster_prefix),
            ]
            rasterized = self._run_or_raise(raster_command, "pdftoppm rasterization")
            if rasterized.returncode != 0:
                detail = self._diagnostics(rasterized)
                suffix = f": {detail}" if detail else ""
                raise RuntimeError(
                    "pdftoppm rasterization exited with status "
                    f"{rasterized.returncode}{suffix}"
                )

            raw_pages = self._rasterized_pages(staged_evidence)
            if not raw_pages:
                raise RuntimeError("pdftoppm did not produce any PNG page snapshots")

            pages: list[LibreOfficeSnapshotPage] = []
            for page_number, (_, raw_page) in enumerate(raw_pages, start=1):
                page_path = staged_evidence / f"page-{page_number:03d}.png"
                raw_page.replace(page_path)
                width_px, height_px = self._validate_png_page(page_path)
                pages.append(
                    LibreOfficeSnapshotPage(
                        page_number=page_number,
                        relative_path=page_path.name,
                        sha256=self._sha256(page_path),
                        width_px=width_px,
                        height_px=height_px,
                        dpi=dpi,
                    )
                )

            evidence = LibreOfficeSnapshotEvidence(
                renderer_name=LIBREOFFICE_RENDERER_NAME,
                renderer_version=renderer_version,
                rasterizer_name=PDFTOPPM_RASTERIZER_NAME,
                rasterizer_version=rasterizer_version,
                pdf_relative_path=snapshot_pdf.name,
                pdf_sha256=self._sha256(snapshot_pdf),
                page_count=len(pages),
                pages=tuple(pages),
            )

            try:
                staged_evidence.replace(evidence_output)
            except FileExistsError as exc:
                raise FileExistsError(
                    "snapshot evidence output_directory was created concurrently; "
                    f"no files were overwritten: {evidence_output}"
                ) from exc
        finally:
            # Cleanup is deliberately post-publication.  The profile lives in
            # a separate tree and cannot mutate the validated PDF/PNG set, so
            # a late Windows profile writer is resource hygiene rather than a
            # reason to invalidate or strand immutable evidence.
            _remove_tree_with_retry(work_root)
            if staged_evidence is not None:
                _remove_tree_with_retry(staged_evidence)

        return evidence

    def _resolve_executable(self) -> Path | None:
        if self._explicit_executable is not None:
            candidate = self._explicit_executable.resolve()
            return candidate if candidate.is_file() else None

        configured = os.environ.get("AITEQNO_LIBREOFFICE_EXECUTABLE")
        if configured:
            candidate = Path(configured).expanduser().resolve()
            return candidate if candidate.is_file() else None

        for command in ("soffice", "libreoffice"):
            discovered = shutil.which(command)
            if discovered:
                return Path(discovered).resolve()

        program_roots = tuple(
            value
            for value in (
                os.environ.get("ProgramFiles"),
                os.environ.get("ProgramFiles(x86)"),
            )
            if value
        )
        for root in program_roots:
            candidate = Path(root) / "LibreOffice" / "program" / "soffice.exe"
            if candidate.is_file():
                return candidate.resolve()
        return None

    @staticmethod
    def _resolve_rasterizer(
        executable_path: str | PathLike[str] | None,
    ) -> Path | None:
        if executable_path is not None:
            candidate = Path(executable_path).expanduser().resolve()
            return candidate if candidate.is_file() else None

        configured = os.environ.get(_PDFTOPPM_ENVIRONMENT_VARIABLE)
        if configured:
            candidate = Path(configured).expanduser().resolve()
            return candidate if candidate.is_file() else None

        discovered = shutil.which(PDFTOPPM_RASTERIZER_NAME)
        return None if discovered is None else Path(discovered).resolve()

    def _read_version(self, executable: Path) -> str:
        try:
            completed = subprocess.run(
                [str(executable), "--version"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(self._timeout_seconds, 15.0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        output = (completed.stdout or completed.stderr).strip()
        if completed.returncode != 0 or not output:
            return "unknown"
        return output.splitlines()[0][:200]

    def _read_rasterizer_version(self, executable: Path) -> str:
        try:
            completed = subprocess.run(
                [str(executable), "-v"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(self._timeout_seconds, 15.0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        output = (completed.stdout or completed.stderr).strip()
        if completed.returncode != 0 or not output:
            return "unknown"
        return output.splitlines()[0][:200]

    def _run_or_raise(
        self,
        command: list[str],
        operation: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"{operation} timed out after {self._timeout_seconds:g} seconds"
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"{operation} could not start: {exc}") from exc

    @staticmethod
    def _diagnostics(completed: subprocess.CompletedProcess[str]) -> str:
        return "\n".join(
            part.strip()
            for part in (completed.stdout, completed.stderr)
            if part.strip()
        )[:2000]

    @staticmethod
    def _rasterized_pages(directory: Path) -> list[tuple[int, Path]]:
        pages: list[tuple[int, Path]] = []
        for candidate in directory.glob("raw-page-*.png"):
            match = _RASTERIZED_PAGE_PATTERN.fullmatch(candidate.name)
            if match is not None:
                pages.append((int(match.group(1)), candidate))
        pages.sort(key=lambda item: item[0])
        return pages

    @staticmethod
    def _validate_png_page(path: Path) -> tuple[int, int]:
        try:
            with Image.open(path) as image:
                image.load()
                if image.format != "PNG":
                    raise RuntimeError(
                        f"rasterized snapshot is not a PNG image: {path.name}"
                    )
                width_px, height_px = image.size
        except (OSError, UnidentifiedImageError) as exc:
            raise RuntimeError(
                f"rasterized snapshot is not a readable PNG image: {path.name}"
            ) from exc
        if width_px <= 0 or height_px <= 0:
            raise RuntimeError(
                f"rasterized snapshot has invalid dimensions: {path.name}"
            )
        return width_px, height_px

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _conversion_error(
        returncode: int,
        diagnostics: str,
        pdf_path: Path,
    ) -> str | None:
        if returncode != 0:
            return f"LibreOffice conversion exited with status {returncode}"
        lowered = diagnostics.casefold()
        marker = next((item for item in _REPAIR_MARKERS if item in lowered), None)
        if marker is not None:
            return f"LibreOffice reported possible repair/recovery marker: {marker}"
        try:
            payload = pdf_path.read_bytes()
        except OSError:
            return "LibreOffice did not produce a readable PDF snapshot"
        if not payload.startswith(b"%PDF-") or not payload.rstrip().endswith(b"%%EOF"):
            return "LibreOffice produced an invalid PDF snapshot"
        return None

    @staticmethod
    def _failed(version: str, error: str) -> SnapshotObservation:
        return SnapshotObservation(
            renderer_name=LIBREOFFICE_RENDERER_NAME,
            renderer_version=version,
            available=True,
            opened_without_repair=False,
            errors=(error,),
        )


__all__ = [
    "DEFAULT_LIBREOFFICE_TIMEOUT_SECONDS",
    "DEFAULT_SNAPSHOT_DPI",
    "LIBREOFFICE_RENDERER_NAME",
    "PDFTOPPM_RASTERIZER_NAME",
    "LibreOfficeSnapshotEvidence",
    "LibreOfficeSnapshotPage",
    "LibreOfficeSnapshotRenderer",
]
