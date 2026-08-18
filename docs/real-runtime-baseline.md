# Real-runtime failure baseline

Issue #43 establishes a truthful starting point for improving Aiteqno. It does
not claim that the current implementation restores a realistic Japanese form
at 70% quality. It proves the opposite in a repeatable way: a reviewed source
image is processed by real Tesseract, reconstructed as DOCX, rendered by real
LibreOffice, rasterized by Poppler, OCRed again from the actual pages, and then
reported as an expected `fail`. Issue #45 adds an earlier OCR-only checkpoint so
that recognition quality can be measured independently of every DOCX and
rendering stage. Issue #47 adds a same-process OCR input-resolution A/B check:
the original-resolution control and the 300 DPI working-raster candidate are
measured with the same source, reference, regions, Tesseract runtime, and
trained data before downstream selection. The current candidate is rejected,
so it remains diagnostic only. Issue #53 independently compares the same
no-upscale control against an exact two-source-pixel artificial white border on
each OCR region crop. That candidate satisfies the fixed no-loss adoption gate
and becomes the next control. Issue #57 then holds that 2px path fixed and
changes only the ordered Tesseract language profile from `jpn,eng` to `jpn`.
The Japanese-only candidate satisfies the quality, no-loss, protected-literal,
trained-data, topology, and multilingual-smoke gates and becomes the selected
profile for downstream DOCX work and the production default.

```text
reviewed MIT source PNG + deterministic structure extraction
  -> control: real Tesseract without OCR-raster upscaling
  -> candidate: real Tesseract with 300 DPI OCR-only working rasters
  -> OCR reports + transform evidence + same-runtime A/B decision
  -> padding control: 96 DPI region crops without artificial border
  -> padding candidate: the same crops with a 2px white border
  -> OCR reports + padding evidence + same-runtime A/B decision
  -> language control: 2px padding + ordered jpn,eng
  -> language candidate: the same input/runtime + ordered jpn only
  -> backend invocation hashes + protected literals + multilingual smoke
  -> OCR reports + same-runtime language decision
  -> atomically published selected bundle (2px padding + jpn today)
  -> current DOCX renderer
  -> actual LibreOffice PDF
  -> Poppler page PNGs
  -> real Tesseract visible-text evidence
  -> source-quality FAIL (expected today)
```

Only the raster supplied inside the Tesseract adapter changes in the resolution
and padding experiments. The language experiment changes only ordered
`languages`, the matching trained-data set, and their derived parameters digest.
The decoded source, structure extraction, source coordinates, Document IR
schema, and DOCX reconstruction remain unchanged. Every returned token bbox is
mapped back to the original source pixels. A future improvement must move the
same measurement without weakening the reference or silently accepting a
changed runtime.

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

## Evidence layers and the OCR A/B checkpoint

The reports must not be collapsed into one ambiguous score. Before the four
quality layers, `ocr-quality-control-evaluation.json` and the existing
`ocr-quality-evaluation.json` score the no-upscale control and 300 DPI candidate
respectively. `ocr-input-transform.json` records the transform actually used by
the backend, and `ocr-resolution-comparison.json` decides whether the candidate
is eligible as a supported improvement. Separately, `ocr-padding/control/` and
`ocr-padding/candidate/` retain independent no-padding and 2px-padding
observations, `ocr-padding/crop-padding-evidence.json` records the backend-owned
border contract, and `ocr-padding/comparison.json` decides the Issue #53
candidate. Finally, `ocr-language/control/` and `ocr-language/candidate/` retain
fresh 2px observations for ordered `jpn,eng` and `jpn`. Their runtime evidence is
emitted by the backend and hashes the trained-data files actually used.
`ocr-language/comparison.json` gates the profile, protected-literal diagnostics,
normalized table topology, and the unchanged multilingual smoke fixture. The
runner does not infer dimensions, padding, scales, effective DPI, languages, or
trained-data identity.

1. `ocr-quality-evaluation.json` compares the reviewed source text directly
   with OCR text in the candidate IR. It is written immediately after real
   Tesseract extraction, before any DOCX or preview work. It does not read DOCX,
   LibreOffice, Poppler, or rendered-page OCR evidence, so a later rendering
   failure cannot erase or contaminate the completed OCR observation.
2. `source-quality-evaluation.json` compares reviewed source truth with the
   selected IR and text OCRed from the actual rendered DOCX pages. The selected
   IR is the supported 2px + `jpn` language candidate; the rejected 300-DPI
   candidate and the earlier `jpn,eng` language control never enter this layer.
   Matching is independent of candidate element IDs and OCR token segmentation.
3. `ir-to-docx-restoration-evaluation.json` is the existing evaluator. It asks
   only how much of the selected IR survived DOCX generation. It cannot measure
   OCR accuracy because its expected content comes from that same selected IR.
4. `actual-docx-snapshot/` contains the LibreOffice PDF, every Poppler page PNG,
   page hashes/dimensions, and visible OCR tokens. The diagnostic
   `reconstructed.png` is retained separately and is never accepted as proof of
   the DOCX's actual appearance.

For artifact compatibility, `baseline-summary.json` retains the historical
`source_to_candidate_ir_ocr` and `candidate_ir_to_docx` layer names. In those
keys, "candidate IR" means the IR selected for the source/restoration baseline,
not necessarily the 300 DPI experiment. Their `selected_input` field is
authoritative; `candidate_300_dpi_experiment` reports the experiment separately.
`ocr_crop_padding_comparison` and `candidate_two_pixel_padding_experiment`
report the independent padding decision and observation.
`ocr_language_profile_comparison` and `candidate_jpn_only_language_experiment`
report the profile decision and observation. `selected_profile` is authoritative
for the downstream layers.

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
DPI, and trained-data hashes used for the observation. The candidate effective
OCR DPI is 300 while the fixed PNG metadata remains approximately 96 DPI. Exact
runtime-dependent scores are not pinned; the dedicated real-runtime test
asserts the same-run direction, integrity checks, recovery identities, and
comparison decision instead.

The 300 DPI path is supported only when full-text character accuracy improves
by at least 1.0 percentage point, block coverage and anchor recall do not fall,
no previously recovered anchor or block is lost, essential-block misses do not
increase, and runtime, source/reference, geometry, and non-text IR integrity all
match. A candidate may remain an OCR-quality `fail` against 70/60/100 and still
be eligible as a supported, narrowly measured improvement. Confidence and token
counts are diagnostics, not adoption criteria. A `supported` decision does not
change production by itself; adoption requires a separate issue and review.

The current Ubuntu observation improves text-character accuracy, logical-block
coverage, and essential-anchor recall, but loses the control-recovered
`request-language-label` logical block. It is therefore truthfully classified
as `regressed`, not averaged into a win. Exact scores are deliberately not
pinned. Other runtimes may produce different diagnostics, but the fixed Ubuntu
lane must surface any decision change for review.

The later visible-page OCR remains on its original no-upscale path. The 300 DPI
experiment therefore affects only its separately retained candidate IR; it does
not silently change the source-to-actual-DOCX measurement in the same issue.

The crop-padding experiment changes one different variable. Both sides use the
same approximately 96-DPI source raster, `jpn,eng`, PSM 6, OEM 3, Tesseract
binary, and trained data. The control sends each exact source region crop; the
candidate surrounds it with two white RGB pixels without expanding the source
bbox. Candidate working dimensions are exactly `source width + 4` by `source
height + 4`. Returned TSV coordinates have the artificial border subtracted
before source-coordinate restoration. Full-page and later visible-page OCR are
not padded.

The candidate must improve full-text accuracy by at least 1.0 percentage point,
must not reduce block coverage or anchor recall, must retain every recovered
control block and anchor, and must not increase essential misses. Every common
runtime, reference, geometry, provenance, non-text IR, asset, and topology check
must also pass. `supported` selects the 2px observation; `regressed` and
`inconclusive` retain control; `invalid` stops before canonical bundle
publication. The current same-runtime observations classify it as `supported`
while the overall source-to-DOCX baseline remains an expected `fail`.

The language-profile experiment changes one final variable on top of that
adopted padding control. Both sides use the same approximately 96-DPI source,
exact 2px white padding, PSM 6, OEM 3, Tesseract executable/version, source
regions, and Japanese trained-data bytes. Control uses ordered `jpn,eng` and
candidate uses ordered `jpn`. Candidate runtime evidence must contain no English
trained-data record, and the common `jpn.traineddata` size and SHA-256 must match
exactly. Parameters digests must differ only because the ordered language tuple
differs.

In the Windows/Tesseract 5.5.3 reconnaissance run, kept outside tracked files,
the fixed candidate moved text accuracy from `68.939394` to `76.893939`, block
coverage from `56.250000` to `70.833333`, and held anchor recall at `66.666667`.
It lost no control-recovered block, anchor, or protected literal; the remaining
essential blocks were `title`, `phone-label`, and `content-structure`. These
exact values select and explain the hypothesis but are not cross-runtime pins.
The dedicated Ubuntu 24.04 same-process artifact is the adoption authority.

`supported` requires at least +1.0 text point, nondecreasing block/anchor
metrics, no lost recovered block/anchor/protected literal, no increase in
essential misses, identical source/reference/threshold/geometry/provenance and
normalized non-text IR/assets/table topology, plus the fixed mixed-language
fixture retaining `AITEQNO`, `2026`, and Japanese text under `jpn` alone.
`regressed` or `inconclusive` retains `jpn,eng`; `invalid` stops canonical
publication. The current result is `supported`, so `jpn` is the selected bundle
and portable production default. Explicit ordered language selection remains
available, and actual-DOCX diagnostic OCR remains independently fixed at
`jpn,eng`.

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
|-- ocr-quality-control-evaluation.json
|-- ocr-quality-evaluation.json
|-- ocr-input-transform.json
|-- ocr-resolution-comparison.json
|-- ocr-padding/
|   |-- crop-padding-evidence.json
|   |-- comparison.json
|   |-- control/
|   |   |-- ocr-quality-evaluation.json
|   |   `-- bundle/document.ir.json
|   `-- candidate/
|       |-- ocr-quality-evaluation.json
|       `-- bundle/document.ir.json
|-- ocr-language/
|   |-- comparison.json
|   |-- protected-literal-diagnostics.json
|   |-- multilingual-smoke.json
|   |-- multilingual-smoke-evidence.json
|   |-- environment-evidence.json
|   |-- control/
|   |   |-- runtime-config-evidence.json
|   |   |-- ocr-quality-evaluation.json
|   |   `-- bundle/document.ir.json
|   `-- candidate/
|       |-- runtime-config-evidence.json
|       |-- ocr-quality-evaluation.json
|       `-- bundle/document.ir.json
|-- source-quality-evaluation.json
|-- ir-to-docx-restoration-evaluation.json
|-- extraction-diagnostics.json
|-- docx-observation.json
|-- docx-render-report.json
|-- preview-render-report.json
|-- control-bundle/
|   `-- document.ir.json
|-- candidate-bundle/
|   `-- document.ir.json
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

All OCR A/B observations are written create-only before DOCX or preview generation. A
later rendering, LibreOffice, or Poppler failure therefore leaves the completed
comparisons available beside `operational-error.json`. `control-bundle/` and
`candidate-bundle/` remain the immutable resolution observations; the two
`ocr-padding/*/bundle/` directories remain the padding observations, and the two
`ocr-language/*/bundle/` directories remain the language observations. After
valid comparisons, deterministic table topology is inferred only from the
language decision's selected IR. The observations remain immutable; the
enriched selection is written through a same-directory staging path and atomically
published as `bundle/`. Its
`document.ir.json`, assets, reconstructed DOCX, and preview therefore describe
one side consistently. The topology step does not change any OCR text, element,
source metadata, or asset. The 300-DPI experiment always leaves production on
its control. The reviewed padding change adopts only `supported`; `regressed`
and `inconclusive` select padding control. The reviewed language change likewise
adopts only `supported`; otherwise it selects the 2px `jpn,eng` control. Any
`invalid` comparison stops before topology/publication because its evidence
cannot be trusted.

The DOCX renderer consumes the validated extension only after the selected
bundle is published. Each of the five topology tables becomes one
identifiable, editable native Word table. Cell text is kept as source-tagged
editable runs, so the read-back observer retains every selected OCR text element while
Word and LibreOffice can lay out tokens naturally inside 45 physical cells.
Supporting table primitives remain unchanged in the IR and are accounted for
once in `native_table_consumed_element_ids`; duplicate border evidence is not
drawn again. Pages without topology continue through the legacy renderer.

The IR-to-DOCX reference includes only structure explicitly encoded in this
DOCX: table-to-cell containment, cell-to-text containment, physical-cell
adjacency, and source reading order. The evaluator formula and threshold are
unchanged. Geometry remains `0` until actual rendered regions can be observed;
the retained PDF/PNG and the one-page gate remain the visual authority.

`environment.json` records the OS, Python, Aiteqno, installed Python packages,
git revision, options, executable versions, `jpn`/`eng` trained-data hashes,
Ubuntu package versions, locale/timezone, and fontconfig mappings. Exact OCR
scores may move when those runtimes move; CI does not pin those scores. It
verifies the fixed integrity contract, the truthful `regressed` 300-DPI
decision, the `supported` 2px-padding and `jpn`-only decisions, backend-owned
trained-data identity, protected literals, multilingual smoke, fixed
five-table/45-cell structure, native-table consumption, and an IR-to-DOCX score
of at least 70 while the combined selected-source decision remains the expected
`fail`.

## CI lanes and intentional updates

The normal Windows/Linux Python matrix runs deterministic tests without
machine-global document runtimes. A dedicated Ubuntu 24.04/Python 3.14 job
installs real Tesseract, LibreOffice, Poppler, Noto CJK, and Liberation fonts.
Its test succeeds only when all five scored OCR observations and the smoke run
complete, the 300-DPI comparison remains `regressed` for the documented lost
block, the 2px-padding comparison is `supported` with no lost recovery, the
`jpn`-only comparison is `supported` with no protected or multilingual loss,
the 2px + `jpn` IR is selected, the remaining process completes, and the
combined quality decision is the expected `fail`. Aggregate gains are asserted
only directionally; exact scores are not pinned. Evidence is uploaded on every
run, including operational failures.

When the baseline eventually becomes `pass`, do not merely change
`expected_current_state`. Inspect every actual page, complete the human checks,
review component and gate changes, and update the reference or threshold only
when the product contract itself intentionally changes.
