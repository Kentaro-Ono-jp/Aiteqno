# OCR runtime setup

Issue #19 implements a replaceable OCR port and selects local Tesseract 5.x
through `pytesseract` as the V1 standard backend. OCR runs locally and does not
send document pixels or recognized text to a network service.

## Supported versions

| Component | V1 requirement | Version checked for Issue #19 |
| --- | --- | --- |
| `pytesseract` | Repository-pinned Python wrapper | `0.3.13` |
| Tesseract engine | `5.x` or newer | upstream `5.5.3` |
| Language data | `jpn`; optional `eng` | `jpn` is required by the default backend; `eng` is used only when explicitly selected and by comparison fixtures |

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
2. Include Japanese trained data and confirm that `jpn.traineddata` exists in
   the selected `tessdata` directory. Also install `eng.traineddata` when using
   an explicit multilingual profile or reproducing the real-runtime A/B runner.
3. Either put `tesseract.exe` on `PATH`, or pass its full path to the adapter.
4. If trained data is outside the executable's default location, pass the
   directory through `tessdata_prefix` or set `TESSDATA_PREFIX`.

Verify the runtime in PowerShell:

```powershell
tesseract --version
tesseract --list-langs
```

The version must start with `5.` or newer, and the language list must contain
`jpn`. It must also contain `eng` before an explicit `jpn,eng` run or the
real-runtime comparison runner.

Explicit configuration avoids machine-specific hard-coding in application
code:

```python
from aiteqno.adapters import TesseractOcrBackend

backend = TesseractOcrBackend(
    executable_path=r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    tessdata_prefix=r"C:\Program Files\Tesseract-OCR\tessdata",
    required_languages=("jpn",),
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

## OCR region crop padding

The production Tesseract adapter adds an artificial two-source-pixel white RGB
border around each structure-provided OCR region crop immediately before the
engine call. It does not expand the crop's source bbox, change decoded source
pixels, affect structure extraction, or pad full-page OCR. Tesseract TSV bboxes
are first clipped in the padded raster, the two-pixel border is subtracted, and
the result is clamped to the original crop before its source offset is added.
Tokens that fall wholly inside the artificial border are discarded. Public
token bboxes and OCR provenance therefore remain in original PNG pixels.

`region_padding_px=0` is the same-runtime control and
`region_padding_px=2` is the adopted production candidate. The adapter rejects
combining crop padding with the separate 300-DPI experiment so one comparison
never changes two OCR-input variables. Immutable backend evidence records the
padding version, exact white color, operation order, source/pre-padding/working
dimensions, Pillow version, raster hashes, and inverse-mapping policy. The
padding setting is also part of each OCR provenance parameter digest.

## OCR region grouping

The public `extract` and `roundtrip` paths merge adjacent source-detected text
regions before OCR when they overlap vertically by at least 45 percent and the
horizontal gap is no greater than twice the taller region. Source-detected
vertical separators block a merge, so table columns remain independent. This
rule reads only source geometry: OCR text, confidence, fixture identity,
filenames, hashes, and fixed page coordinates are not inputs.

The wider same-row crop gives Tesseract enough neighboring context to preserve
short Japanese labels and logical blocks. Tokens are still mapped back to
original PNG coordinates, and the existing two-source-pixel crop-padding and
`jpn`-only production contracts remain unchanged.

## OCR language profile

The production default is ordered `jpn` only. Issue #57 measured it against the
previous ordered `jpn,eng` control after fixing every other input and runtime
field: exact 2px region padding, no upscale, PSM 6, OEM 3, source regions,
reference, thresholds, executable/version, and the common Japanese trained-data
file. The backend invocation observer records the ordered tuple and SHA-256/size
of only the trained-data files actually passed to Tesseract; the comparison
runner does not infer them from installation state.

The candidate is selected only when text accuracy improves by at least one
percentage point, block and anchor metrics do not fall, every control-recovered
block/anchor and protected `PNG`/`PDF`/`DOCX`/`JSON`/`30`/`90`/`70` literal is
retained, essential misses do not increase, all geometry/provenance/non-text
integrity gates pass, and the unchanged mixed-language smoke fixture retains
`AITEQNO`, `2026`, and Japanese content. These gates classify the candidate as
`supported`, so the portable backend, extraction service, and CLI now default
to `jpn`.

Ordered explicit selection remains available:

```powershell
aiteqno extract input.png -o output\document.ir.json `
  --language jpn --language eng
```

The real-runtime runner keeps `jpn,eng` installed for its immutable control and
for actual-DOCX visible-text diagnosis. A default change therefore does not
rewrite historical experiment evidence or remove multilingual capability.

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
| `ocr_traineddata_evidence_unavailable` | an observed invocation's actual trained-data file could not be resolved or hashed | verify `TESSDATA_PREFIX`, file readability, and the selected installation |
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
