"""LibreOffice headless evidence for repair-free DOCX opening.

The adapter deliberately treats LibreOffice as an optional external runtime.
It converts one immutable DOCX input to a temporary PDF using an isolated user
profile.  A successful, structurally plausible PDF proves that LibreOffice
opened the package without an interactive repair flow.  Page-region extraction
is intentionally left empty in V1, so the evaluator reports zero geometry
credit instead of inventing coordinates.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from os import PathLike
from pathlib import Path

from aiteqno.ports import SnapshotObservation


DEFAULT_LIBREOFFICE_TIMEOUT_SECONDS = 90.0
LIBREOFFICE_RENDERER_NAME = "libreoffice-headless"

_REPAIR_MARKERS = (
    "corrupt",
    "damaged",
    "recover",
    "recovery",
    "repair",
)


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
    "LIBREOFFICE_RENDERER_NAME",
    "LibreOfficeSnapshotRenderer",
]
