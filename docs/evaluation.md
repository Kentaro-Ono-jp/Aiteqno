# Restoration evaluation

Aiteqno has three deliberately separate quality measurements. The OCR-only
baseline compares human-reviewed source text with OCR tokens in the candidate
Document IR before DOCX generation. The existing restoration score measures
how much of that candidate IR survives in the generated DOCX; it does not
measure OCR accuracy or inspect the source image. The end-to-end source baseline
compares reviewed source truth with candidate IR and text observed on actual
rendered DOCX pages. None of these evaluators uses the diagnostic PNG preview as
proof of the DOCX appearance.

The representative `78.79` result belongs only to the second measurement, the
IR-to-DOCX restoration score, and uses fixed `FakeOcrBackend` observations. It
is not evidence of real Tesseract accuracy or realistic Japanese-form
restoration. The real-runtime source baseline is intentionally `fail`; see
[Real-runtime failure baseline](real-runtime-baseline.md).

## Inputs

The evaluator combines four independent sources of evidence:

1. a reviewed reference with expected elements, essential text anchors, and
   structural relationships;
2. text, borders, media, and structure read back from the generated DOCX;
3. the render report produced for that exact DOCX;
4. repair-free opening and optional normalized regions from a LibreOffice or
   equivalent DOCX page snapshot.

`PythonDocxObserver` supplies item 2 without reading the source PNG. Snapshot
production belongs to the golden E2E adapter; a missing snapshot does not crash
the evaluator, but it prevents an automatic pass. The V1 LibreOffice adapter
converts DOCX to a temporary PDF to establish repair-free opening and reports no
regions, rather than inferring geometry it did not measure. The representative
golden still passes at 78.79 with zero geometry credit.

See [V1 golden round-trip](e2e.md) for fixture provenance, CI coverage, and the
intentional-update policy.

## Decision model

### IR-to-DOCX restoration

The fixed V1 score is:

```text
0.45 × text similarity
+ 0.20 × element coverage
+ 0.20 × structure similarity
+ 0.15 × geometry similarity
```

Each component is reported on a 0–100 scale. The default inclusive threshold is
70. The threshold is configurable, while component weights and matching
tolerances are contractual and covered by tests.

Numeric score alone cannot pass a document. Essential text, essential elements,
essential structure, DOCX/package integrity, repair-free snapshot opening,
required assets, the no-source-background rule, and the no-external-relationship
rule are hard gates.

For a topology-aware DOCX, the generated restoration reference supplies the
source-addressable relationships that the OOXML actually encodes: table/cell
containment, cell/text containment, physical-cell adjacency, and reading order.
The native renderer embeds source element IDs in editable text controls and
table IDs in standard table captions. This changes neither the weighted formula
nor its threshold; it prevents a visually compact cell line from collapsing
multiple OCR elements into one anonymous read-back paragraph.

The final states are:

- `pass`: score meets the threshold and every requirement is established;
- `fail`: score is below threshold or a hard gate fails;
- `requires_human_review`: no known failure exists, but review or machine
  evidence is incomplete.

An arbitrary reference created with `reviewed=False` can therefore be scored for
diagnosis, but it cannot become `pass`.

## Python API

```python
from aiteqno.adapters import FilesystemEvaluationWriter, PythonDocxObserver
from aiteqno.application import EvaluationConfig, evaluate_restoration

result = evaluate_restoration(
    document,
    reference,
    "reconstructed.docx",
    render_result.report,
    observer=PythonDocxObserver(),
    snapshot=libreoffice_snapshot,
    config=EvaluationConfig(threshold=70),
)

FilesystemEvaluationWriter().write(result, "evaluation.json")
```

`build_evaluation_reference()` converts validated Document IR geometry to
page-normalized expectations and attaches reviewed annotations. For tests and
external adapters that already have normalized evidence,
`evaluate_restoration_input()` evaluates `RestorationEvaluationInput` directly.

## `evaluation.json`

The create-only artifact records:

- evaluator, IR, and reference versions/IDs;
- overall score and threshold;
- component scores, weights, and weighted contributions;
- matched, missing, and unexpected elements;
- every hard gate with `pass`, `fail`, or `unknown` status and a reason;
- final state, decision reasons, and pending human checks.

Serialization is deterministic UTF-8 JSON. Re-evaluating identical evidence
produces identical JSON.

## Source-grounded baseline

`evaluate_source_baseline()` consumes a `SourceBaselineReference` whose text,
logical regions, structures, source digest, and review status are independent
of candidate IDs. It evaluates NFKC/whitespace-normalized character accuracy,
logical block coverage, structure similarity, and geometry similarity with
component-specific minimums. Reviewed reading-order, containment, and adjacency
relationships are evaluated from candidate order and geometry without assuming
candidate IDs. Text OCRed from actual LibreOffice page PNGs takes
precedence over OOXML read-back text, so invisible, clipped, or severely
overlapped XML text cannot earn readability credit merely by existing in the
package.

Manual evidence uses explicit `pending`, `passed`, and `failed` states. Pending
evidence prevents an otherwise clean automatic pass; a failed human check is a
hard failure. See the baseline guide for weights, thresholds, artifacts, and CI
runtime recording.

## OCR-only baseline

`evaluate_ocr_quality()` stops at the candidate Document IR. It reconstructs
text from page geometry and reading order, matches reviewed logical source
regions without trusting candidate IDs or token boundaries, and reports full
character accuracy, block coverage and per-block accuracy, exact essential
anchor recall, missing and extra text, and confidence diagnostics. Confidence
never substitutes for a text comparison.

This layer has independent minimums of 70 for full character accuracy and 60
for logical-block coverage. Exact recall of every essential anchor, the source
digest, the reviewed reference, candidate page count, and essential logical
blocks are hard gates. Its create-only `ocr-quality-evaluation.json` checkpoint
is written immediately after extraction, so a later DOCX or LibreOffice failure
cannot erase the completed OCR evidence. See the real-runtime guide for the
fixed input, runtime record, and intentional-failure CI policy.

`compare_ocr_experiment()` applies the common same-runtime adoption policy to
two already-scored OCR observations. An immutable experiment contract declares
the only runtime fields that may differ and the exact hypothesis-specific
integrity checks that must be present. Reference, thresholds, normalization,
source geometry, OCR provenance, non-text IR, assets, and table topology remain
common hard checks. Confidence and token counts remain diagnostics. The legacy
`compare_ocr_resolution()` API is a compatibility wrapper that adds the fixed
300 DPI transform check before using this common decision engine; its artifact
schema and Issue #47 decision remain unchanged.

`compare_ocr_padding()` uses the same common engine for the independent Issue
#53 hypothesis. Its runtime allows no differences: both sides remain at the
decoded source DPI with `jpn,eng`, PSM 6, and OEM 3. The hypothesis check
requires zero artificial pixels on control, exactly two white RGB pixels on
every candidate region edge, unchanged source crop bboxes, working dimensions
of `source + 4` on each axis, backend raster hashes, and the fixed inverse
mapping policy. Full-page OCR, 300-DPI resizing, fixture truth, and thresholds
cannot enter this comparison.

The real-runtime runner evaluates this unchanged contract twice in one process:
first against the production no-upscale control, then against an experimental
300 DPI OCR-working-raster path. `ocr-resolution-comparison.json` compares the two
results and separately gates runtime identity, source/reference identity,
source-coordinate integrity, and non-text IR identity. It does not change the
OCR evaluator, its 70/60/100 thresholds, or its normalization. Exact scores are
runtime observations. The current candidate improves aggregate metrics but
loses a control-recovered logical block, so its decision is `regressed` and the
control remains the resolution experiment's selection. The separate 0px/2px
crop-padding comparison applies the same +1pp, aggregate non-regression,
recovered-set superset, essential-miss, geometry, provenance, and non-text IR
gates. A `supported` padding result selects the 2px observation for downstream
DOCX work; `regressed` or `inconclusive` selects control, and `invalid` stops
before publication.
