# V1 golden round-trip

Issue #23 fixes one deterministic vertical slice across Windows and Linux:

```text
representative PNG
  -> extract
  -> schema-valid Document IR + cropped assets
  -> render DOCX after deleting the PNG
  -> preview PNG after deleting the PNG
  -> DOCX read-back + LibreOffice headless open
  -> restoration evaluation
```

## Fixture policy

`tests/fixtures/e2e/manifest.json` is the reviewable source of truth. It records
two original MIT-licensed synthetic fixtures:

- a small Japanese/English OCR runtime smoke image;
- a patient-form-like representative image used for the scored round trip.

The form is realistic in structure but is not copied from a real document and
contains no personal data. The manifest pins each source SHA-256, a normalized
Document IR semantic SHA-256, the cropped asset's decoded RGB24 SHA-256,
expected element counts and text, essential content, the expected DOCX
topology, and the quality threshold/result. The normalization replaces
content-addressed asset IDs, paths, and encoded-byte hashes with stable indexes
and decoded-pixel hashes. This excludes OS-specific PNG compression bytes while
keeping document semantics, geometry, style, provenance, and pixel content
reviewable. A fixture or extraction change must update those values in a
deliberate review diff.

OCR observations are fixed by `FakeOcrBackend` in the cross-platform golden
test. Everything after that boundary is real: PNG decoding, OpenCV structure
extraction, schema validation, bundle publication, DOCX/PNG rendering, DOCX
read-back, and evaluation. Separate opt-in integration tests exercise the real
Tesseract backend.

Consequently, this golden route is a deterministic IR-to-DOCX regression test,
not a claim about real OCR or source-image restoration quality. The dedicated
[real-runtime failure baseline](real-runtime-baseline.md) records a same-runtime
no-upscale/300-DPI OCR comparison before DOCX generation, then independently
covers actual LibreOffice pages, visible-text OCR, and source-grounded
evaluation.

## Quality result

With the fixed fake OCR observations, the representative DOCX scores `78.79`
against the inclusive threshold `70`.
LibreOffice V1 evidence proves repair-free opening by converting the DOCX to a
temporary PDF through an isolated headless profile. It does not invent page
coordinates, so the golden evaluation deliberately receives zero geometry
credit. Text, recovered elements, and the reviewed DOCX structure still clear
the threshold, and every hard gate passes.

## Local verification

Run the deterministic suite on any supported platform:

```powershell
python -m unittest tests.test_golden_roundtrip -v
```

Run the real LibreOffice gate when `soffice` or `libreoffice` is installed:

```powershell
$env:AITEQNO_RUN_LIBREOFFICE_INTEGRATION = "1"
python -m unittest tests.test_golden_roundtrip.RealLibreOfficeGoldenIntegrationTest -v
```

If LibreOffice is outside `PATH`, point directly to it:

```powershell
$env:AITEQNO_LIBREOFFICE_EXECUTABLE = "C:\Program Files\LibreOffice\program\soffice.exe"
```

CI runs the full deterministic suite on `ubuntu-latest` and `windows-latest`
with Python 3.11 and 3.14 without machine-global document runtimes. A dedicated
Ubuntu 24.04 job installs Tesseract, LibreOffice, Poppler, and Japanese fonts,
runs both OCR inputs consecutively, records the same-run comparison, keeps the
no-upscale control selected for downstream DOCX work, then uploads all retained
evidence. The lane currently asserts the truthful `regressed` 300 DPI decision;
a changed decision requires review instead of silently adopting the candidate.

## Updating the golden contract

Do not regenerate expected values merely to make a failing test green. First
inspect the IR, DOCX read-back, preview, and evaluation difference. If the new
behavior is intentional, update the manifest hashes/properties and topology in
the same PR, state why the output changed, and keep the source/license record
intact. The golden manifest is a product behavior contract, not a cache.
