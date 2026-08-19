import base64
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from io import StringIO
from pathlib import Path

from docx import Document as open_docx
from PIL import Image

from aiteqno.adapters import (
    BundleAssetResolver,
    FakeOcrBackend,
    FilesystemDocumentBundleWriter,
    JsonSchemaDocumentIRValidator,
    OpenCvStructureExtractor,
    PillowPngAssetEncoder,
    PillowPngDecoder,
    PillowPreviewRenderer,
    PythonDocxRenderer,
)
from aiteqno.adapters.json_schema import document_ir_from_file
from aiteqno.cli import CliRuntime, ExitCode, main
from aiteqno.ports import DEFAULT_OCR_LANGUAGES, OcrBackendError, OcrOptions


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "structure"
IR_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "document_ir"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _png_data():
    return base64.b64decode(
        (FIXTURE_ROOT / "structured-page.png.b64").read_text(encoding="ascii")
    )


def _runtime(ocr_backend=None):
    return CliRuntime(
        decoder=PillowPngDecoder(),
        structure_extractor=OpenCvStructureExtractor(),
        ocr_backend=ocr_backend
        or FakeOcrBackend((), available_languages=("jpn", "eng")),
        asset_encoder=PillowPngAssetEncoder(),
        validator=JsonSchemaDocumentIRValidator(),
        bundle_writer=FilesystemDocumentBundleWriter(),
        docx_renderer_factory=lambda root: PythonDocxRenderer(
            asset_resolver=BundleAssetResolver(root)
        ),
        preview_renderer_factory=lambda root: PillowPreviewRenderer(
            asset_resolver=BundleAssetResolver(root)
        ),
    )


def _run(arguments, runtime=None):
    stdout = StringIO()
    stderr = StringIO()
    exit_code = main(
        arguments,
        runtime=runtime or _runtime(),
        stdout=stdout,
        stderr=stderr,
    )
    return exit_code, stdout.getvalue(), stderr.getvalue()


class CliContractTest(unittest.TestCase):
    def test_help_version_module_entrypoint_and_console_metadata(self):
        exit_code, stdout, stderr = _run(["--help"])

        self.assertEqual(exit_code, ExitCode.SUCCESS)
        self.assertEqual(stderr, "")
        for command in ("extract", "render", "preview", "roundtrip"):
            self.assertIn(command, stdout)
            command_code, command_stdout, command_stderr = _run([command, "--help"])
            self.assertEqual(command_code, ExitCode.SUCCESS)
            self.assertIn("--output", command_stdout)
            self.assertEqual(command_stderr, "")

        version_code, version_stdout, version_stderr = _run(["--version"])
        self.assertEqual(version_code, ExitCode.SUCCESS)
        self.assertIn("aiteqno 0.4.0.dev0", version_stdout)
        self.assertEqual(version_stderr, "")

        completed = subprocess.run(
            [sys.executable, "-m", "aiteqno.cli", "--help"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, ExitCode.SUCCESS, completed.stderr)
        self.assertIn("roundtrip", completed.stdout)

        project = tomllib.loads(
            (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(project["project"]["scripts"]["aiteqno"], "aiteqno.cli:main")

    def test_extract_render_and_preview_support_unicode_without_source_png(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "日本語 フォルダー"
            root.mkdir()
            input_path = root / "元の問診票.png"
            input_path.write_bytes(_png_data())
            bundle_root = root / "抽出結果"
            ir_path = bundle_root / "文書.ir.json"

            extract_code, extract_stdout, extract_stderr = _run(
                ["extract", str(input_path), "-o", str(ir_path), "--language", "eng"]
            )

            self.assertEqual(extract_code, ExitCode.SUCCESS, extract_stderr)
            self.assertTrue(ir_path.is_file())
            self.assertTrue((bundle_root / "assets").is_dir())
            self.assertIn(f"document_ir={ir_path.resolve()}", extract_stdout)
            self.assertNotIn("warning", extract_stdout)
            self.assertIn("aiteqno: warning", extract_stderr)
            document_ir_from_file(ir_path)

            input_path.unlink()
            docx_path = bundle_root / "復元結果.docx"
            preview_path = bundle_root / "復元結果.png"
            render_code, render_stdout, render_stderr = _run(
                ["render", str(ir_path), "-o", str(docx_path)]
            )
            preview_code, preview_stdout, preview_stderr = _run(
                ["preview", str(ir_path), "-o", str(preview_path), "--dpi", "96"]
            )

            self.assertEqual(render_code, ExitCode.SUCCESS, render_stderr)
            self.assertEqual(preview_code, ExitCode.SUCCESS, preview_stderr)
            self.assertIn(f"docx={docx_path.resolve()}", render_stdout)
            self.assertIn(f"preview={preview_path.resolve()}", preview_stdout)
            self.assertGreater(len(open_docx(docx_path).sections), 0)
            with Image.open(preview_path) as preview:
                preview.verify()

            original_docx = docx_path.read_bytes()
            conflict_code, _, conflict_stderr = _run(
                ["render", str(ir_path), "-o", str(docx_path)]
            )
            self.assertEqual(conflict_code, ExitCode.OUTPUT_CONFLICT)
            self.assertIn("output_exists", conflict_stderr)
            self.assertEqual(docx_path.read_bytes(), original_docx)
            self.assertEqual(list(root.rglob(".aiteqno-*-*")), [])

    def test_default_japanese_profile_and_explicit_multilingual_order(self):
        self.assertEqual(DEFAULT_OCR_LANGUAGES, ("jpn",))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.png"
            input_path.write_bytes(_png_data())
            backend = _RecordingOcrBackend()

            default_code, _, default_stderr = _run(
                [
                    "extract",
                    str(input_path),
                    "-o",
                    str(root / "default" / "document.ir.json"),
                ],
                runtime=_runtime(backend),
            )
            explicit_code, _, explicit_stderr = _run(
                [
                    "extract",
                    str(input_path),
                    "-o",
                    str(root / "explicit" / "document.ir.json"),
                    "--language",
                    "jpn",
                    "--language",
                    "eng",
                ],
                runtime=_runtime(backend),
            )

        self.assertEqual(default_code, ExitCode.SUCCESS, default_stderr)
        self.assertEqual(explicit_code, ExitCode.SUCCESS, explicit_stderr)
        self.assertEqual(backend.language_calls, [("jpn",), ("jpn", "eng")])

    def test_roundtrip_publishes_fixed_self_contained_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "問診票 入力.png"
            input_path.write_bytes(_png_data())
            output = root / "往復 成果物"

            exit_code, stdout, stderr = _run(
                [
                    "roundtrip",
                    str(input_path),
                    "-o",
                    str(output),
                    "--language",
                    "eng",
                    "--dpi",
                    "96",
                ]
            )

            self.assertEqual(exit_code, ExitCode.SUCCESS, stderr)
            expected = {
                "document.ir.json",
                "assets",
                "reconstructed.docx",
                "reconstructed.png",
            }
            self.assertEqual({path.name for path in output.iterdir()}, expected)
            document = document_ir_from_file(output / "document.ir.json")
            for asset in document.assets:
                BundleAssetResolver(output).resolve(asset)
            open_docx(output / "reconstructed.docx")
            with Image.open(output / "reconstructed.png") as preview:
                preview.verify()
            self.assertIn(f"bundle={output.resolve()}", stdout)
            self.assertNotIn("warning", stdout)

            marker = output / "user-marker.txt"
            marker.write_text("preserve", encoding="utf-8")
            conflict_code, _, conflict_stderr = _run(
                ["roundtrip", str(input_path), "-o", str(output)]
            )
            self.assertEqual(conflict_code, ExitCode.OUTPUT_CONFLICT)
            self.assertIn("output_exists", conflict_stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_failure_classes_have_stable_exit_codes_and_stderr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing = root / "missing.png"
            input_error, input_stdout, input_stderr = _run(
                ["extract", str(missing), "-o", str(root / "input.ir.json")]
            )
            self.assertEqual(input_error, ExitCode.INPUT_ERROR)
            self.assertEqual(input_stdout, "")
            self.assertIn("input_not_found", input_stderr)
            self.assertNotIn("Traceback", input_stderr)

            bad_png = root / "invalid.png"
            bad_png.write_bytes(b"not a PNG")
            decode_error, _, decode_stderr = _run(
                ["extract", str(bad_png), "-o", str(root / "bad.ir.json")]
            )
            self.assertEqual(decode_error, ExitCode.INPUT_ERROR)
            self.assertIn("invalid_png", decode_stderr)

            invalid_ir = IR_FIXTURE_ROOT / "invalid-version.document.ir.json"
            invalid_ir_error, _, invalid_ir_stderr = _run(
                ["render", str(invalid_ir), "-o", str(root / "invalid.docx")]
            )
            self.assertEqual(invalid_ir_error, ExitCode.INPUT_ERROR)
            self.assertIn("invalid_document_ir", invalid_ir_stderr)

            valid_png = root / "valid.png"
            valid_png.write_bytes(_png_data())
            dependency_error, _, dependency_stderr = _run(
                [
                    "extract",
                    str(valid_png),
                    "-o",
                    str(root / "dependency" / "document.ir.json"),
                    "--language",
                    "eng",
                ],
                runtime=_runtime(_MissingOcrBackend()),
            )
            self.assertEqual(dependency_error, ExitCode.DEPENDENCY_ERROR)
            self.assertIn("ocr_executable_missing", dependency_stderr)

            usage_error, _, usage_stderr = _run(
                [
                    "preview",
                    str(IR_FIXTURE_ROOT / "canonical.document.ir.json"),
                    "-o",
                    str(root / "x.jpg"),
                ]
            )
            self.assertEqual(usage_error, ExitCode.USAGE_ERROR)
            self.assertIn("invalid_output_extension", usage_stderr)

            parser_stdout = StringIO()
            parser_stderr = StringIO()
            parser_error = main(
                ["extract"],
                runtime=_runtime(),
                stdout=parser_stdout,
                stderr=parser_stderr,
            )
            self.assertEqual(parser_error, ExitCode.USAGE_ERROR)
            self.assertEqual(parser_stdout.getvalue(), "")
            self.assertIn("required", parser_stderr.getvalue())

    def test_extract_refuses_existing_sibling_asset_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.png"
            input_path.write_bytes(_png_data())
            output_parent = root / "existing-bundle"
            assets = output_parent / "assets"
            assets.mkdir(parents=True)
            marker = assets / "owned-by-user.txt"
            marker.write_text("preserve", encoding="utf-8")

            exit_code, _, stderr = _run(
                [
                    "extract",
                    str(input_path),
                    "-o",
                    str(output_parent / "document.ir.json"),
                ]
            )

            self.assertEqual(exit_code, ExitCode.OUTPUT_CONFLICT)
            self.assertIn("output_exists", stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertFalse((output_parent / "document.ir.json").exists())


class RealCliRoundtripIntegrationTest(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("AITEQNO_RUN_TESSERACT_INTEGRATION") == "1",
        "set AITEQNO_RUN_TESSERACT_INTEGRATION=1 with Tesseract installed",
    )
    def test_default_cli_runtime_completes_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.png"
            input_path.write_bytes(_png_data())
            output = root / "output"
            stdout = StringIO()
            stderr = StringIO()

            exit_code = main(
                [
                    "roundtrip",
                    str(input_path),
                    "-o",
                    str(output),
                    "--language",
                    "eng",
                    "--dpi",
                    "96",
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, ExitCode.SUCCESS, stderr.getvalue())
            self.assertTrue((output / "document.ir.json").is_file())
            self.assertTrue((output / "reconstructed.docx").is_file())
            self.assertTrue((output / "reconstructed.png").is_file())


class _MissingOcrBackend:
    def healthcheck(self):
        raise AssertionError("CLI extraction does not require an eager healthcheck")

    def recognize(self, image, regions=(), languages=("eng",), options=OcrOptions()):
        raise OcrBackendError(
            "ocr_executable_missing",
            "simulated missing Tesseract executable",
            provider="test",
        )


class _RecordingOcrBackend(FakeOcrBackend):
    def __init__(self):
        super().__init__((), available_languages=("jpn", "eng"))
        self.language_calls = []

    def recognize(
        self,
        image,
        regions=(),
        languages=DEFAULT_OCR_LANGUAGES,
        options=OcrOptions(),
    ):
        self.language_calls.append(tuple(languages))
        return super().recognize(image, regions, languages, options)


if __name__ == "__main__":
    unittest.main()
