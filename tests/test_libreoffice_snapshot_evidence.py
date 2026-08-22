from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from aiteqno.adapters import LibreOfficeSnapshotRenderer


class LibreOfficeSnapshotEvidenceTest(unittest.TestCase):
    def test_persists_pdf_and_numbered_png_pages_with_runtime_evidence(self):
        commands: list[list[str]] = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            if command[-1] == "--version":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "LibreOffice 25.2.4.2\n",
                    "",
                )
            if command[-1] == "-v":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "",
                    "pdftoppm version 25.07.0\n",
                )
            if "--convert-to" in command:
                output_directory = Path(command[command.index("--outdir") + 1])
                source = Path(command[-1])
                (output_directory / f"{source.stem}.pdf").write_bytes(
                    b"%PDF-1.7\nfixture\n%%EOF\n"
                )
                return subprocess.CompletedProcess(command, 0, "convert complete", "")

            prefix = Path(command[-1])
            Image.new("RGB", (120, 180), "white").save(f"{prefix}-1.png")
            Image.new("RGB", (240, 360), "white").save(f"{prefix}-2.png")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docx_path = root / "fixture.docx"
            docx_path.write_bytes(b"fixture")
            output_directory = root / "snapshot-evidence"
            renderer = LibreOfficeSnapshotRenderer(executable_path=sys.executable)
            with patch(
                "aiteqno.adapters.libreoffice.subprocess.run",
                side_effect=fake_run,
            ):
                evidence = renderer.render_evidence(
                    docx_path,
                    output_directory,
                    rasterizer_executable_path=sys.executable,
                    dpi=144,
                )

            self.assertTrue((output_directory / "snapshot.pdf").is_file())
            self.assertTrue((output_directory / "page-001.png").is_file())
            self.assertTrue((output_directory / "page-002.png").is_file())
            self.assertEqual(list(output_directory.glob("raw-page-*.png")), [])
            self.assertEqual(evidence.renderer_version, "LibreOffice 25.2.4.2")
            self.assertEqual(evidence.rasterizer_version, "pdftoppm version 25.07.0")
            self.assertEqual(evidence.page_count, 2)
            self.assertEqual(
                [(page.width_px, page.height_px) for page in evidence.pages],
                [(120, 180), (240, 360)],
            )
            self.assertTrue(all(page.dpi == 144 for page in evidence.pages))
            self.assertEqual(
                evidence.pdf_sha256,
                hashlib.sha256(
                    (output_directory / "snapshot.pdf").read_bytes()
                ).hexdigest(),
            )
            self.assertEqual(
                evidence.pages[0].sha256,
                hashlib.sha256(
                    (output_directory / "page-001.png").read_bytes()
                ).hexdigest(),
            )
            self.assertEqual(
                evidence.to_dict()["pages"][1]["relative_path"],
                "page-002.png",
            )

        conversion_command = next(
            command for command in commands if "--convert-to" in command
        )
        self.assertTrue(
            any(
                part.startswith("-env:UserInstallation=file:")
                for part in conversion_command
            )
        )

    def test_existing_output_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docx_path = root / "fixture.docx"
            docx_path.write_bytes(b"fixture")
            output_directory = root / "existing"
            output_directory.mkdir()
            sentinel = output_directory / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            renderer = LibreOfficeSnapshotRenderer(executable_path=sys.executable)

            with patch("aiteqno.adapters.libreoffice.subprocess.run") as run:
                with self.assertRaisesRegex(FileExistsError, "already exists"):
                    renderer.render_evidence(
                        docx_path,
                        output_directory,
                        rasterizer_executable_path=sys.executable,
                    )

            run.assert_not_called()
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_missing_rasterizer_has_actionable_error_without_partial_output(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docx_path = root / "fixture.docx"
            docx_path.write_bytes(b"fixture")
            output_directory = root / "snapshot-evidence"
            renderer = LibreOfficeSnapshotRenderer(executable_path=sys.executable)

            with self.assertRaisesRegex(
                RuntimeError,
                "AITEQNO_PDFTOPPM_EXECUTABLE",
            ):
                renderer.render_evidence(
                    docx_path,
                    output_directory,
                    rasterizer_executable_path=root / "missing-pdftoppm",
                )

            self.assertFalse(output_directory.exists())

    def test_failed_rasterization_does_not_publish_partial_output(self):
        def fake_run(command, **_kwargs):
            if command[-1] == "--version":
                return subprocess.CompletedProcess(command, 0, "LibreOffice 25.2", "")
            if command[-1] == "-v":
                return subprocess.CompletedProcess(command, 0, "", "pdftoppm 25.07")
            if "--convert-to" in command:
                output_directory = Path(command[command.index("--outdir") + 1])
                source = Path(command[-1])
                (output_directory / f"{source.stem}.pdf").write_bytes(
                    b"%PDF-1.7\nfixture\n%%EOF\n"
                )
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 1, "", "bad PDF")

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docx_path = root / "fixture.docx"
            docx_path.write_bytes(b"fixture")
            output_directory = root / "snapshot-evidence"
            renderer = LibreOfficeSnapshotRenderer(executable_path=sys.executable)

            with patch(
                "aiteqno.adapters.libreoffice.subprocess.run",
                side_effect=fake_run,
            ):
                with self.assertRaisesRegex(RuntimeError, "status 1: bad PDF"):
                    renderer.render_evidence(
                        docx_path,
                        output_directory,
                        rasterizer_executable_path=sys.executable,
                    )

            self.assertFalse(output_directory.exists())
            self.assertEqual(list(root.glob(".snapshot-evidence-staging-*")), [])
            self.assertEqual(list(root.glob(".snapshot-evidence-work-*")), [])

    def test_retries_transient_windows_cleanup_without_invalidating_evidence(self):
        def fake_run(command, **_kwargs):
            if command[-1] == "--version":
                return subprocess.CompletedProcess(command, 0, "LibreOffice 25.2", "")
            if command[-1] == "-v":
                return subprocess.CompletedProcess(command, 0, "", "pdftoppm 25.07")
            if "--convert-to" in command:
                output_directory = Path(command[command.index("--outdir") + 1])
                source = Path(command[-1])
                (output_directory / f"{source.stem}.pdf").write_bytes(
                    b"%PDF-1.7\nfixture\n%%EOF\n"
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            prefix = Path(command[-1])
            Image.new("RGB", (120, 180), "white").save(f"{prefix}-1.png")
            return subprocess.CompletedProcess(command, 0, "", "")

        real_rmtree = shutil.rmtree
        cleanup_calls = 0

        def flaky_rmtree(path, *args, **kwargs):
            nonlocal cleanup_calls
            cleanup_calls += 1
            if cleanup_calls == 1:
                raise OSError(145, "The directory is not empty")
            return real_rmtree(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docx_path = root / "fixture.docx"
            docx_path.write_bytes(b"fixture")
            output_directory = root / "snapshot-evidence"
            renderer = LibreOfficeSnapshotRenderer(executable_path=sys.executable)

            with (
                patch(
                    "aiteqno.adapters.libreoffice.subprocess.run",
                    side_effect=fake_run,
                ),
                patch(
                    "aiteqno.adapters.libreoffice.shutil.rmtree",
                    side_effect=flaky_rmtree,
                ),
                patch("aiteqno.adapters.libreoffice.time.sleep") as sleep,
            ):
                evidence = renderer.render_evidence(
                    docx_path,
                    output_directory,
                    rasterizer_executable_path=sys.executable,
                )

            self.assertEqual(evidence.page_count, 1)
            self.assertTrue((output_directory / "page-001.png").is_file())
            self.assertGreaterEqual(cleanup_calls, 2)
            sleep.assert_called_once_with(0.1)
            self.assertEqual(list(root.glob(".snapshot-evidence-staging-*")), [])
            self.assertEqual(list(root.glob(".snapshot-evidence-work-*")), [])

    def test_late_profile_cleanup_cannot_invalidate_published_evidence(self):
        def fake_run(command, **_kwargs):
            if command[-1] == "--version":
                return subprocess.CompletedProcess(command, 0, "LibreOffice 25.2", "")
            if command[-1] == "-v":
                return subprocess.CompletedProcess(command, 0, "", "pdftoppm 25.07")
            if "--convert-to" in command:
                output_directory = Path(command[command.index("--outdir") + 1])
                source = Path(command[-1])
                (output_directory / f"{source.stem}.pdf").write_bytes(
                    b"%PDF-1.7\nfixture\n%%EOF\n"
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            prefix = Path(command[-1])
            Image.new("RGB", (120, 180), "white").save(f"{prefix}-1.png")
            return subprocess.CompletedProcess(command, 0, "", "")

        real_rmtree = shutil.rmtree

        def locked_profile_rmtree(path, *args, **kwargs):
            if "-work-" in Path(path).name:
                raise OSError(145, "The directory is not empty")
            return real_rmtree(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docx_path = root / "fixture.docx"
            docx_path.write_bytes(b"fixture")
            output_directory = root / "snapshot-evidence"
            renderer = LibreOfficeSnapshotRenderer(executable_path=sys.executable)

            with (
                patch(
                    "aiteqno.adapters.libreoffice.subprocess.run",
                    side_effect=fake_run,
                ),
                patch(
                    "aiteqno.adapters.libreoffice.shutil.rmtree",
                    side_effect=locked_profile_rmtree,
                ),
                patch("aiteqno.adapters.libreoffice.time.sleep"),
            ):
                evidence = renderer.render_evidence(
                    docx_path,
                    output_directory,
                    rasterizer_executable_path=sys.executable,
                )

            self.assertEqual(evidence.page_count, 1)
            self.assertTrue((output_directory / "snapshot.pdf").is_file())
            self.assertTrue((output_directory / "page-001.png").is_file())
            for work_directory in root.glob(".snapshot-evidence-work-*"):
                real_rmtree(work_directory)


if __name__ == "__main__":
    unittest.main()
