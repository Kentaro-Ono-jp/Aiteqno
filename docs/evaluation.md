# Restoration evaluation

Aiteqno's restoration score measures the generated DOCX, not OCR accuracy and
not the diagnostic PNG preview. A result near 70 is deliberately acceptable
when its important content remains readable.

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
