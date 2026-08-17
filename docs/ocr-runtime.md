# OCR runtime setup

Issue #19 implements a replaceable OCR port and selects local Tesseract 5.x
through `pytesseract` as the V1 standard backend. OCR runs locally and does not
send document pixels or recognized text to a network service.

## Supported versions

| Component | V1 requirement | Version checked for Issue #19 |
| --- | --- | --- |
| `pytesseract` | Repository-pinned Python wrapper | `0.3.13` |
| Tesseract engine | `5.x` or newer | upstream `5.5.3` |
| Language data | `jpn` and `eng` | both required by the default backend |

`pytesseract` is installed with the Python package. The Tesseract executable
and trained-data files are external runtime prerequisites and are not bundled
in the Aiteqno wheel.

Authoritative references:

- [Tesseract installation](https://tesseract-ocr.github.io/tessdoc/Installation.html)
- [Tesseract supported operating systems](https://tesseract-ocr.github.io/tessdoc/supported-operating-systems.html)
- [Tesseract release notes](https://github.com/tesseract-ocr/tessdoc/blob/main/ReleaseNotes.md)
- [`pytesseract` usage and output API](https://github.com/madmaze/pytesseract)

## Windows 10 / 11

1. Install a current Tesseract 5.x build. The upstream installation guide
   points Windows users to the maintained UB Mannheim installers.
2. Include both English and Japanese trained data. Confirm that
   `eng.traineddata` and `jpn.traineddata` exist in the selected `tessdata`
   directory.
3. Either put `tesseract.exe` on `PATH`, or pass its full path to the adapter.
4. If trained data is outside the executable's default location, pass the
   directory through `tessdata_prefix` or set `TESSDATA_PREFIX`.

Verify the runtime in PowerShell:

```powershell
tesseract --version
tesseract --list-langs
```

The version must start with `5.` or newer, and the language list must contain
both `jpn` and `eng`.

Explicit configuration avoids machine-specific hard-coding in application
code:

```python
from aiteqno.adapters import TesseractOcrBackend

backend = TesseractOcrBackend(
    executable_path=r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    tessdata_prefix=r"C:\Program Files\Tesseract-OCR\tessdata",
    required_languages=("jpn", "eng"),
)
capabilities = backend.healthcheck()
```

`healthcheck()` does not recognize document content. It verifies the executable,
major version, and requested trained data before OCR begins.

## OCR working raster

The production Tesseract path keeps the decoded source resolution. Passing
`target_dpi=300` explicitly enables the experimental working raster when the
decoded source metadata is below 300 DPI. Only each OCR crop (or the full-page
OCR target when there are no regions) is enlarged: the decoded source,
structure coordinates, assets, and Document IR page metadata remain untouched.
The working raster stays RGB, uses Pillow LANCZOS resampling, is limited to
40,000,000 pixels per crop, and is never downscaled when the source is already
300 DPI or higher.

Tesseract TSV geometry is clipped in working coordinates and mapped back to
source pixels using the actual integer source/working dimension ratios. The
adapter floors left/top, ceils right/bottom, clamps to the source crop, then
adds its source offset. Consequently, public token and provenance bboxes remain
positive source-pixel rectangles. The transform observer exposes the exact
dimensions, scales, raster digests, effective DPI, and inverse-mapping policy
used by a completed recognition call.

`target_dpi=None` is the production default and disables the working-raster
resize. The real-runtime A/B runner invokes both settings explicitly. The
current 300 DPI result is `regressed`, so the candidate is evidence only and is
not a production default. OCR input resolution is adapter-specific and is not
added to the portable `OcrOptions` contract.

## Ubuntu and GitHub Actions

The repository CI installs the distro-provided Tesseract 5.x runtime and
Japanese data before running tests:

```bash
sudo apt-get update
sudo apt-get install --yes --no-install-recommends tesseract-ocr tesseract-ocr-jpn
tesseract --list-langs
```

CI sets `AITEQNO_RUN_TESSERACT_INTEGRATION=1` for the test step. This enables the
tracked Japanese/English fixture test. Local unit tests use `FakeOcrBackend` and
mocked process responses, so they remain deterministic and require neither the
engine nor a network connection.

To run the real integration test locally after installing the runtime:

```powershell
$env:AITEQNO_RUN_TESSERACT_INTEGRATION = "1"
python -m unittest tests.test_ocr_backend.TesseractOcrBackendIntegrationTest -v
```

When the executable or data is not on the default search path, set
`AITEQNO_TESSERACT_EXECUTABLE` and `AITEQNO_TESSDATA_PREFIX` for this test only.

## Diagnostics

The adapter raises `OcrBackendError` with a stable `code`:

| Code | Meaning | Typical action |
| --- | --- | --- |
| `ocr_executable_missing` | executable path or `PATH` lookup failed | install Tesseract or configure `executable_path` |
| `ocr_unsupported_version` | detected engine is older than 5.x | upgrade the engine |
| `ocr_language_missing` | requested trained data is absent | install `jpn` / `eng` or configure `TESSDATA_PREFIX` |
| `ocr_unreadable_input` | normalized pixel input cannot be opened | validate the PNG decoding boundary |
| `ocr_timeout` | recognition exceeded `OcrOptions.timeout_seconds` | inspect the input or adjust the bounded timeout |
| `ocr_engine_failure` | Tesseract process returned an error | inspect runtime installation and trained data |
| `ocr_working_raster_limit` | one resized crop would exceed 40,000,000 pixels | reduce the source/crop size before recognition |
| `ocr_working_raster_failure` | Pillow could not allocate, resize, or hash the OCR working raster | inspect host memory and input dimensions |
| `ocr_invalid_response` | TSV output violated the adapter contract | verify the engine/wrapper combination |

Recognized document text is deliberately excluded from diagnostic messages and
provenance notes.

## Licenses

- Aiteqno is MIT licensed.
- Tesseract OCR is licensed under Apache License 2.0.
- `pytesseract` is licensed under Apache License 2.0.
- Language data is an external runtime asset. Preserve the notices supplied by
  the package or trained-data distribution used on the target machine.

See [LICENSING_POLICY.md](../LICENSING_POLICY.md) for the repository policy.
