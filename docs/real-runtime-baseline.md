# Real-runtime failure baseline

Issue #43 establishes a truthful starting point for improving Aiteqno. It does
not claim that the current implementation restores a realistic Japanese form
at 70% quality. It proves the opposite in a repeatable way: a reviewed source
image is processed by real Tesseract, reconstructed as DOCX, rendered by real
LibreOffice, rasterized by Poppler, OCRed again from the actual pages, and then
reported as an expected `fail`. Issue #45 adds an earlier OCR-only checkpoint so
that recognition quality can be measured independently of every DOCX and
rendering stage.

```text
reviewed MIT source PNG
  -> real Tesseract + current structure extraction
  -> candidate Document IR
  -> OCR-quality FAIL checkpoint (expected today)
  -> current DOCX renderer
  -> actual LibreOffice PDF
  -> Poppler page PNGs
  -> real Tesseract visible-text evidence
  -> source-quality FAIL (expected today)
```

No production OCR, structure extraction, Document IR, or DOCX reconstruction
algorithm is changed by this baseline. A future improvement must move the same
measurement without weakening the reference or silently accepting a changed
runtime.

## Licensed fixture and review

The public fixture is
`tests/fixtures/baseline/synthetic-dense-japanese-form-v1/`. It is original,
MIT-licensed synthetic artwork created for Aiteqno, contains no personal data,
and was visually reviewed by Kentaro Ono on 2026-08-17. The fixture deliberately
has realistic Japanese text density, small type, rules, grids, checkboxes, and
several logical sections. Its source SHA-256 is:

```text
df0b724d8fcc1b5d5e0483a60401c2cb3882675f71d1e37ecdbcff9e687ffc25
```

The repository previously tracked `input/form_blank_testClinic_v1.png`. Its
pixels are effectively the same as an image published in a
[Flexi Reads article](https://flexireads.wordpress.com/2020/03/17/how-to-fill-out-a-medical-form-in-japan/),
and no permission for MIT redistribution was established. The current tree no
longer contains that image. `tests/fixtures/baseline/excluded-sources.json`
retains only its URL, SHA-256, dimensions, and exclusion reason. Historical Git
objects are not rewritten by this issue.

## Four evidence layers

The reports must not be collapsed into one ambiguous score.

1. `ocr-quality-evaluation.json` compares the reviewed source text directly
   with OCR text in the candidate IR. It is written immediately after real
   Tesseract extraction, before any DOCX or preview work. It does not read DOCX,
   LibreOffice, Poppler, or rendered-page OCR evidence, so a later rendering
   failure cannot erase or contaminate the completed OCR observation.
2. `source-quality-evaluation.json` compares reviewed source truth with the
   candidate IR and text OCRed from the actual rendered DOCX pages. Matching is
   independent of candidate element IDs and OCR token segmentation.
3. `ir-to-docx-restoration-evaluation.json` is the existing evaluator. It asks
   only how much of the candidate IR survived DOCX generation. It cannot measure
   OCR accuracy because its expected content comes from that same candidate IR.
4. `actual-docx-snapshot/` contains the LibreOffice PDF, every Poppler page PNG,
   page hashes/dimensions, and visible OCR tokens. The diagnostic
   `reconstructed.png` is retained separately and is never accepted as proof of
   the DOCX's actual appearance.

The combined state fails if any scored layer fails. An unavailable human check
remains `pending`; an explicit human rejection is `failed`. A known machine
failure remains `fail` even while human checks are pending.

## OCR-only quality contract

The OCR-only layer reuses the reviewed source reference and normalization from
the source-quality contract, but its observation ends at the candidate IR. Text
tokens are reconstructed by source page and coordinates rather than candidate
element IDs or token boundaries. It requires at least 70 text-character
accuracy, at least 60 logical-block coverage, and exact recall of every
essential phrase. Source digest and reviewed-reference mismatches are hard
failures. OCR recognition-confidence values and low-confidence tokens are
retained for diagnosis, but confidence never substitutes for text correctness.

The report also records the actual Tesseract provider/version, language order,
PSM, OEM, the effective integer OCR DPI separately from decoded PNG metadata
DPI, and trained-data hashes used for the observation. Exact runtime-dependent
scores are not pinned; the dedicated real-runtime test independently asserts
that the current OCR-only layer remains an expected `fail`.

## Source-quality contract

Text is normalized with NFKC and all Unicode whitespace removed. Essential
anchors use exact normalized substring matching; fuzzy matching cannot make an
essential phrase pass.

| Component | Weight | Minimum |
| --- | ---: | ---: |
| Text character accuracy | 45% | 70 |
| Logical block coverage | 20% | 60 |
| Structure similarity | 20% | 60 |
| Geometry similarity | 15% | 50 |

The weighted overall minimum is 70. Component minimums prevent strong rule or
geometry detection from hiding unreadable text. The source digest, reviewed
reference, one-page candidate IR, one-page actual DOCX, every essential anchor,
essential logical blocks, essential structures, essential reading-order /
containment / adjacency relationships, and manual checks are separate hard
gates.

Required manual checks are:

- no fatal text overlap;
- no clipped text;
- human-usable layout;
- open, edit, and save in Word or an equivalent editor.

## Reproducing the run

The runner requires Tesseract 5.x with `jpn` and `eng`, LibreOffice Writer,
Poppler `pdftoppm`, and Japanese fonts. Its output directory is create-only.

```powershell
$env:AITEQNO_TESSERACT_EXECUTABLE = "C:\Program Files\Tesseract-OCR\tesseract.exe"
$env:AITEQNO_TESSDATA_PREFIX = "C:\Program Files\Tesseract-OCR\tessdata"
$env:AITEQNO_LIBREOFFICE_EXECUTABLE = "C:\Program Files\LibreOffice\program\soffice.exe"
$env:AITEQNO_PDFTOPPM_EXECUTABLE = "C:\path\to\pdftoppm.exe"

python scripts/run_real_baseline.py `
  --output build/real-runtime-baseline `
  --expect-state fail
```

The retained output is:

```text
real-runtime-baseline/
|-- source.png
|-- preflight-environment.json
|-- environment.json
|-- baseline-summary.json
|-- ocr-quality-evaluation.json
|-- source-quality-evaluation.json
|-- ir-to-docx-restoration-evaluation.json
|-- extraction-diagnostics.json
|-- docx-observation.json
|-- docx-render-report.json
|-- preview-render-report.json
|-- bundle/
|   |-- document.ir.json
|   |-- reconstructed.docx
|   `-- reconstructed.png
`-- actual-docx-snapshot/
    |-- snapshot.pdf
    |-- snapshot-evidence.json
    |-- visible-ocr.json
    `-- page-001.png ...
```

`environment.json` records the OS, Python, Aiteqno, installed Python packages,
git revision, options, executable versions, `jpn`/`eng` trained-data hashes,
Ubuntu package versions, locale/timezone, and fontconfig mappings. Exact OCR
scores may move when those runtimes move; CI asserts that the current decision
is `fail`, not that runtime-dependent bytes are identical.

## CI lanes and intentional updates

The normal Windows/Linux Python matrix runs deterministic tests without
machine-global document runtimes. A dedicated Ubuntu 24.04/Python 3.14 job
installs real Tesseract, LibreOffice, Poppler, Noto CJK, and Liberation fonts.
Its test succeeds only when the whole process completes and the quality decision
is the expected `fail`. Evidence is uploaded on every run, including operational
failures.

When the baseline eventually becomes `pass`, do not merely change
`expected_current_state`. Inspect every actual page, complete the human checks,
review component and gate changes, and update the reference or threshold only
when the product contract itself intentionally changes.
