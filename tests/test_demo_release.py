import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZIP_DEFLATED, ZipFile

from aiteqno.cli import ExitCode
from scripts.build_demo_package import build_demo_package
from scripts.demo_runner import run_demo
from scripts.verify_demo_package import verify_demo_package


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _fake_wheel(path: Path) -> None:
    with ZipFile(path, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("aiteqno/__init__.py", "__version__ = '0.4.0.dev0'\n")
        archive.writestr(
            "aiteqno-0.4.0.dev0.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: aiteqno\nVersion: 0.4.0.dev0\n\n",
        )


def _fake_roundtrip(arguments, *, stdout, stderr):
    del stderr
    output = Path(arguments[arguments.index("-o") + 1])
    output.mkdir(parents=True)
    (output / "document.ir.json").write_text("{}\n", encoding="utf-8")
    assets = output / "assets"
    assets.mkdir()
    (assets / "sha256-test.png").write_bytes(b"asset")
    (output / "reconstructed.docx").write_bytes(b"docx")
    (output / "reconstructed.png").write_bytes(b"png")
    print(f"bundle={output}", file=stdout)
    return int(ExitCode.SUCCESS)


class DemoPackageTest(unittest.TestCase):
    def test_builder_is_deterministic_and_archive_is_self_verifying(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wheel = root / "aiteqno-0.4.0.dev0-py3-none-any.whl"
            _fake_wheel(wheel)
            first = root / "first.zip"
            second = root / "second.zip"

            build_demo_package(
                repository_root=REPOSITORY_ROOT,
                wheel_path=wheel,
                output_path=first,
                release_tag="v0.4.0-demo.1",
            )
            build_demo_package(
                repository_root=REPOSITORY_ROOT,
                wheel_path=wheel,
                output_path=second,
                release_tag="v0.4.0-demo.1",
            )

            self.assertEqual(
                hashlib.sha256(first.read_bytes()).hexdigest(),
                hashlib.sha256(second.read_bytes()).hexdigest(),
            )
            manifest = verify_demo_package(
                first,
                expected_release_tag="v0.4.0-demo.1",
                expected_package_version="0.4.0.dev0",
            )
            self.assertEqual(manifest["entrypoint"], "run-demo.cmd")
            self.assertTrue(manifest["prerequisites"]["first_run_internet"])
            self.assertEqual(
                manifest["prerequisites"]["tesseract"],
                ">=5 with jpn language data; eng is optional",
            )

    @unittest.skipUnless(os.name == "nt", "PowerShell launcher is Windows-only")
    def test_powershell_launcher_has_valid_syntax(self):
        script = REPOSITORY_ROOT / "demo" / "windows" / "run-demo.ps1"
        escaped = str(script).replace("'", "''")
        command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{escaped}', [ref]$tokens, [ref]$errors) > $null; "
            "if ($errors.Count -gt 0) { $errors | Out-String | Write-Error; exit 1 }"
        )

        completed = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        launcher = script.read_text(encoding="utf-8-sig")
        self.assertIn('[string[]] $Language = @("jpn")', launcher)


    def test_runner_defaults_to_adopted_japanese_only_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.png"
            source.write_bytes(b"png")
            schema = root / "schema.json"
            schema.write_text("{}", encoding="utf-8")
            output = root / "output"
            captured_arguments = []

            def capture_roundtrip(arguments, *, stdout, stderr):
                captured_arguments.extend(arguments)
                return _fake_roundtrip(arguments, stdout=stdout, stderr=stderr)

            exit_code = run_demo(
                source,
                output,
                schema,
                cli_main=capture_roundtrip,
            )

            self.assertEqual(exit_code, ExitCode.SUCCESS)
            manifest = json.loads(
                (output / "demo.manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["settings"]["ocr_languages"], ["jpn"])
            language_values = [
                captured_arguments[index + 1]
                for index, value in enumerate(captured_arguments)
                if value == "--language"
            ]
            self.assertEqual(language_values, ["jpn"])


class DemoRunnerTest(unittest.TestCase):
    def test_runner_adds_schema_manifest_and_publishes_complete_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "日本語 デモ"
            root.mkdir()
            source = root / "問診票.png"
            source.write_bytes(b"source-png")
            schema = root / "schema.json"
            schema.write_text('{"$schema":"test"}\n', encoding="utf-8")
            output = root / "復元 結果"
            stdout = StringIO()
            stderr = StringIO()

            exit_code = run_demo(
                source,
                output,
                schema,
                languages=("eng",),
                dpi=96.0,
                cli_main=_fake_roundtrip,
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, ExitCode.SUCCESS, stderr.getvalue())
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {
                    "document.ir.json",
                    "document-ir.schema.json",
                    "demo.manifest.json",
                    "assets",
                    "reconstructed.docx",
                    "reconstructed.png",
                },
            )
            manifest = json.loads(
                (output / "demo.manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["source"]["filename"], source.name)
            self.assertEqual(
                manifest["source"]["sha256"],
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )
            self.assertEqual(manifest["settings"]["ocr_languages"], ["eng"])
            self.assertEqual(manifest["settings"]["preview_dpi"], 96.0)
            self.assertEqual(len(manifest["assets"]), 1)
            self.assertIn(f"bundle={output.resolve()}", stdout.getvalue())
            self.assertEqual(list(root.glob(".aiteqno-demo-*")), [])

    def test_runner_preserves_existing_output_without_calling_core(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.png"
            source.write_bytes(b"png")
            schema = root / "schema.json"
            schema.write_text("{}", encoding="utf-8")
            output = root / "output"
            output.mkdir()
            marker = output / "user.txt"
            marker.write_text("preserve", encoding="utf-8")
            cli_main = Mock()
            stderr = StringIO()

            exit_code = run_demo(
                source,
                output,
                schema,
                cli_main=cli_main,
                stderr=stderr,
            )

            self.assertEqual(exit_code, ExitCode.OUTPUT_CONFLICT)
            self.assertIn("output_exists", stderr.getvalue())
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            cli_main.assert_not_called()

    def test_runner_summarizes_repeated_success_warnings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.png"
            source.write_bytes(b"png")
            schema = root / "schema.json"
            schema.write_text("{}", encoding="utf-8")
            output = root / "output"
            stdout = StringIO()
            stderr = StringIO()

            def noisy_success(arguments, *, stdout, stderr):
                exit_code = _fake_roundtrip(
                    arguments,
                    stdout=stdout,
                    stderr=stderr,
                )
                for _ in range(3):
                    print(
                        "aiteqno: warning [ocr_region_empty]: empty region",
                        file=stderr,
                    )
                return exit_code

            exit_code = run_demo(
                source,
                output,
                schema,
                cli_main=noisy_success,
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, ExitCode.SUCCESS)
            diagnostics = stderr.getvalue()
            self.assertEqual(diagnostics.count("ocr_region_empty"), 1)
            self.assertIn("(3 occurrences)", diagnostics)

    def test_runner_cleans_staging_after_core_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.png"
            source.write_bytes(b"png")
            schema = root / "schema.json"
            schema.write_text("{}", encoding="utf-8")
            output = root / "output"

            def fail(arguments, *, stdout, stderr):
                del arguments, stdout
                print("simulated failure", file=stderr)
                return int(ExitCode.DEPENDENCY_ERROR)

            stderr = StringIO()
            exit_code = run_demo(
                source,
                output,
                schema,
                cli_main=fail,
                stderr=stderr,
            )

            self.assertEqual(exit_code, ExitCode.DEPENDENCY_ERROR)
            self.assertIn("simulated failure", stderr.getvalue())
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".aiteqno-demo-*")), [])


if __name__ == "__main__":
    unittest.main()
