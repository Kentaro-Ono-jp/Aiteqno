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
final `ocr-region-grouping/control/` and `candidate/` observations keep that
adopted 2px + `jpn` runtime fixed. Their only declared geometry difference is a
deterministic same-row crop plan. `region-plan-evidence.json` records every
source bbox, vertical separator, adjacency decision, union bbox, singleton, and
configuration/plan digest. The runner does not infer dimensions, padding,
scales, effective DPI, languages, trained-data identity, or grouping from OCR
text.

1. `ocr-quality-evaluation.json` compares the reviewed source text directly
   with OCR text in the candidate IR. It is written immediately after real
   Tesseract extraction, before any DOCX or preview work. It does not read DOCX,
   LibreOffice, Poppler, or rendered-page OCR evidence, so a later rendering
   failure cannot erase or contaminate the completed OCR observation.
2. `source-quality-evaluation.json` compares reviewed source truth with the
   selected IR and text OCRed from the actual rendered DOCX pages. The selected
   IR is the valid selection after the supported 2px + `jpn` language candidate
   and region-grouping checkpoint; the rejected 300-DPI candidate and the
   earlier `jpn,eng` language control never enter this layer.
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
for the downstream layers. `ocr_region_grouping_comparison` and
`candidate_geometry_line_grouping_experiment` report the final crop-plan
decision; `selected_grouping` is authoritative.

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

The formal Ubuntu 24.04/Tesseract 5.3.4 observation classifies the candidate as
`supported`. Independent push and pull-request artifacts emitted byte-identical
`ocr-language/comparison.json` evidence for this observation:

| Metric | `jpn,eng` control | `jpn` candidate | Delta (percentage points) |
|---|---:|---:|---:|
| Text-character accuracy | 66.856061 | 76.136364 | +9.280303 |
| Logical-block coverage | 58.333333 | 70.833333 | +12.500000 |
| Essential-anchor recall | 66.666667 | 66.666667 | 0.000000 |

No control-recovered logical block, anchor, or protected literal was lost. The
candidate reduced the essential-block misses by four; `title`, `phone-label`,
and `content-structure` remain. The mixed-language smoke observed Japanese text,
`AITEQNO`, and `2026`. Both profiles used the same 2,471,260-byte
`jpn.traineddata` with SHA-256
`1f5de9236d2e85f5fdf4b3c500f2d4926f8d9449f28f5394472d9e8d83b91b4d`,
while the candidate runtime evidence contained no `eng.traineddata`. These
numbers record the adoption observation; CI continues to assert the fixed gates
rather than cross-runtime score constants.

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

The region-grouping experiment is the third and final Tesseract micro-hypothesis.
It changes no OCR runtime field. Control and candidate both use the adopted
approximately 96-DPI/no-upscale raster, exact 2px white crop padding, ordered
`jpn`, PSM 6, OEM 3, executable/version, and `jpn.traineddata` bytes. Control
keeps every detector region as one crop. Candidate sorts the same source bboxes
deterministically and joins adjacent members only when their vertical overlap is
at least `0.45`, their horizontal gap is no greater than the larger member
height, and no detected vertical separator crosses that gap and shared row.
The union remains in original source pixels. OCR text, OCR confidence, fixture
truth, expected strings, and dictionaries cannot participate in the plan.

Every source region must occur exactly once in the candidate partition. Group
evidence retains ordered members, member bboxes, union bbox, gap, maximum gap,
overlap ratio, blocking separators, algorithm version, configuration digest,
and plan digest. Regions outside groups keep the original reference and bbox.
Their normalized OCR observations—including text, source/point bbox,
recognition confidence, provider/model/languages, and provenance other than the
declared experiment digest—must be byte-equivalent. This makes the isolated
`phone-label` a negative control; grouping may not dictionary-correct it.

The fixed candidate is `supported` only with at least +1.0 full-text point,
nondecreasing block/anchor metrics, no lost recovered block/anchor/protected
literal, no increase in essential misses, a newly recovered `title` or
`content-structure` block, unchanged singleton observations, passing mixed-
language smoke, and all common geometry/non-text/topology gates. `regressed` or
`inconclusive` retains singleton crops; `invalid` stops publication.

The untracked Windows/Tesseract 5.5.3 reconnaissance observation planned 79
source regions as 50 crops with 11 groups. It changed text accuracy from
`76.893939` to `77.651515` (+`0.757576`), block coverage from `70.833333` to
`75.000000`, and anchor recall from `66.666667` to `75.000000`. `title` changed
from `文妻吾解析評価ント` (55.555556%) to exact `文書解析評価シート` (100%), while
the singleton phone observation remained `二話` and every singleton/protected/
smoke/topology check passed. Because +1.0 was not reached, that runtime correctly
classified the candidate as `inconclusive`; it is reconnaissance, not the
cross-runtime adoption authority.

The formal Ubuntu 24.04 / Tesseract 5.3.4 observation is recorded by
[CI run 32144917106](https://github.com/Kentaro-Ono-jp/Aiteqno/actions/runs/32144917106)
and artifact
`real-runtime-baseline-4b233cd18edbb93d7012ffda51eb20b802748bff`.
It reproduced the same fixed plan shape: 79 source regions became 50 candidate
crops containing 11 groups and 39 unchanged singletons. Exact OCR-only results
were:

| Metric | singleton control | grouped candidate | Delta |
| --- | ---: | ---: | ---: |
| Text character accuracy | 76.136364 | 76.893939 | +0.757575 |
| Logical block coverage | 70.833333 | 75.000000 | +4.166667 |
| Essential anchor recall | 66.666667 | 75.000000 | +8.333333 |

The candidate newly recovered logical blocks `title` and `created-date`, with
`title` recovered exactly. It lost no control-recovered block or anchor,
reduced essential misses from three to two, and passed every region-plan,
runtime, trained-data, source geometry,
provenance, non-text IR, table-topology, singleton, protected-literal, and
multilingual-smoke check. `phone-label` remained the unchanged singleton `二話`.
`content-structure` remained unrecovered and its character accuracy changed
from `33.333333` to `26.315789`.

The comparison's fixed machine decision remains `inconclusive`, so
`selected_grouping` remains `single-regions` and the canonical downstream DOCX
continues from the 2px + ordered `jpn` control. At project level, however, the
text, block, and anchor improvements with zero lost recovery are the desired
successful result and complete the approximately-70% OCR goal. The remaining
misses are retained as diagnostics, not as authority for another OCR chase.
Issue #61 closed that proposed fourth pursuit as not planned; Issue #62 starts
the DOCX text-flow work from the fixed selected IR.

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
|-- ocr-region-grouping/
|   |-- comparison.json
|   |-- region-plan-evidence.json
|   |-- protected-literal-diagnostics.json
|   |-- singleton-observations.json
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
`ocr-language/*/bundle/` directories remain the language observations. The two
`ocr-region-grouping/*/bundle/` directories remain the fresh fixed-runtime crop
plan observations. After valid comparisons, deterministic table topology is
inferred only from the profile/grouping decision's selected IR. The observations remain immutable; the
enriched selection is written through a same-directory staging path and atomically
published as `bundle/`. Its
`document.ir.json`, assets, reconstructed DOCX, and preview therefore describe
one side consistently. The topology step does not change any OCR text, element,
source metadata, or asset. The 300-DPI experiment always leaves production on
its control. The reviewed padding change adopts only `supported`; `regressed`
and `inconclusive` select padding control. The reviewed language change likewise
adopts only `supported`; otherwise it selects the 2px `jpn,eng` control. A
supported language candidate then reaches the grouping checkpoint, which adopts
groups only for `supported` and otherwise retains fresh singleton control. Any
`invalid` comparison stops before topology/publication because its evidence
cannot be trusted.

The DOCX renderer consumes the validated extension only after the selected
bundle is published. Each of the five topology tables becomes one
identifiable, editable native Word table. Cell text is kept as source-tagged
editable runs, so the read-back observer retains every selected OCR text element while
Word and LibreOffice can lay out tokens naturally inside 45 physical cells.
Topology cells and outside text bands share one geometry-first line plan. It
orders same-line fragments by source `x`, uses deterministic tie-breakers,
stabilizes fragment font sizes within the measured line span, omits artificial
ASCII spaces between CJK fragments, retains single Latin/number word
separators, and represents large visual gaps with native tab stops. Every
source fragment remains its own tagged editable run; text is not corrected from
fixture truth or merged into an untraceable replacement run.
For short one-fragment cell labels only, a fixed geometry-only readability
floor may raise the run to at most 10.5pt when the glyph box is at least 8.5pt
high and the content occupies no more than two advance units. Cell height and
remaining width still cap the result; OCR confidence and text truth are not
inputs.

### Issue #62 DOCX text-flow result

The formal Ubuntu 24.04 observation for Issue #62 is
[CI run 32261042508](https://github.com/Kentaro-Ono-jp/Aiteqno/actions/runs/32261042508),
artifact
`real-runtime-baseline-4ddf46fdd6f434e69be8fffe2e519566d8368810`.
It compares the renderer change with the fixed main baseline at `0b53ebb`:

| Source-to-actual-DOCX measure | Main baseline | Issue #62 | Delta |
|---|---:|---:|---:|
| Overall score | 39.85 | 45.22 | +5.37 |
| Rendered-visible text accuracy | 9.280303 | 21.212121 | +11.931818 |
| Logical-block coverage | 70.833333 | 70.833333 | 0 |
| Structure similarity | 46.969697 | 46.969697 | 0 |
| Geometry similarity | 80.754604 | 80.754604 | 0 |
| IR-to-DOCX restoration overall | 78.73 | 78.73 | 0 |
| IR-to-DOCX text similarity | 100.0 | 100.0 | 0 |
| Full-page visible OCR tokens | 114 | 202 | +88 |

The actual snapshot newly recovers the exact essential anchor `住所`. The short
label remains a source-tagged editable run and wraps onto two visible lines
inside its tall native cell; neither glyph is clipped. Human review found the
headings and table text substantially more readable than the baseline, with no
fatal overlap or clipping.

The source SHA-256 remains
`df0b724d8fcc1b5d5e0483a60401c2cb3882675f71d1e37ecdbcff9e687ffc25`, and
the selected IR SHA-256 remains
`5e0e90a43490362916e56e88cd5a46ce30fc19acd77b78db813e3456ce09c32e`.
The artifact retains one page, five native tables, 45 cells, 390 consumed
native-table element IDs, all 374 source text elements, and IR-to-DOCX text
similarity 100. It has zero renderer omissions or errors, zero external
relationships, a readable OPC package, successful python-docx reopen, and
repair-free LibreOffice rendering.

The combined score remains below the approximate 70% project target. The
largest renderer-side residual is still visible text, and all 374 text runs
still report `Noto Sans CJK JP` to `Arial` substitution. Issue #64 addresses
that bounded font-policy residual below; OCR settings and the selected IR stay
fixed.

### Issue #64 Japanese DOCX font result

The formal Ubuntu 24.04 observation for Issue #64 is
[CI run 32267181737](https://github.com/Kentaro-Ono-jp/Aiteqno/actions/runs/32267181737),
artifact
`real-runtime-baseline-41514059668fafcb9272f13bd6d5b532bacefbd9`.
It compares the static default-font policy change with the fixed Issue #62 main
baseline at `ddda668`:

| Source-to-actual-DOCX measure | Issue #62 | Issue #64 | Delta |
|---|---:|---:|---:|
| Overall score | 45.22 | 45.90 | +0.68 |
| Rendered-visible text accuracy | 21.212121 | 22.727273 | +1.515152 |
| Logical-block coverage | 70.833333 | 70.833333 | 0 |
| Structure similarity | 46.969697 | 46.969697 | 0 |
| Geometry similarity | 80.754604 | 80.754604 | 0 |
| IR-to-DOCX restoration overall | 78.73 | 78.73 | 0 |
| IR-to-DOCX text similarity | 100.0 | 100.0 | 0 |
| Full-page visible OCR tokens | 202 | 226 | +24 |
| Renderer font substitutions | 374 | 0 | -374 |

All 374 source-tagged runs now resolve `Noto Sans CJK JP` without renderer
fallback and write the same family to the `ascii`, `hAnsi`, `eastAsia`, and
`cs` `w:rFonts` channels. The formal integration also inspects the rendered
PDF: it contains `NotoSansCJKjp-Regular` and no `LiberationSans`. The remaining
`DejaVuSerif` glyphs are four generated heading-separator spaces, not Japanese
source-run fallback. Applying Noto to those generated separators was measured
separately and reduced visible-text accuracy, so separator policy remains
unchanged.

The previously recovered exact essential anchor `住所` remains covered. No new
essential anchor or logical block was recovered: `title`, `phone-label`, and
`content-structure` remain missing. Font-family-only probes did not improve
that gate; the only probes that recovered another anchor changed font size or
paragraph flow, both explicitly outside Issue #64 and the fixed Issue #62
boundary. With the product owner's explicit approval, this one acceptance item
is recorded as a bounded-scope exception rather than hidden or satisfied by a
geometry change.

The selected IR SHA-256 remains
`5e0e90a43490362916e56e88cd5a46ce30fc19acd77b78db813e3456ce09c32e`.
The artifact retains one page, five native tables, 45 cells, 390 consumed
native-table element IDs, all 374 source text elements, and IR-to-DOCX text
similarity 100. It has zero renderer omissions or errors, zero external
relationships, a readable OPC package, successful python-docx reopen, and
repair-free LibreOffice rendering. Human review of the actual snapshot found
no fatal text overlap or clipping and confirmed that the native tables remain
editable.

The combined score remains below the approximate 70% project target. With the
font-policy defect closed, the remaining bounded DOCX work is native Word table
layout and structure; OCR, the evaluator, the selected IR, and the Issue #62
text-flow rules remain fixed.

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
scores may move when those runtimes move. CI does not require exact equality,
but it enforces the reviewed Issue #64 directional floors: source-to-actual
overall and visible-text accuracy must stay strictly above the Issue #62
baseline, logical-block, structure, and geometry scores may not fall, and
IR-to-DOCX restoration may not regress. It also verifies the fixed integrity
contract, the truthful `regressed` 300-DPI
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
