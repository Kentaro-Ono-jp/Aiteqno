# Aiteqno roadmap

The V1 vertical slice is complete: one single-page PNG can be extracted to a
validated Document IR bundle and reconstructed as a readable DOCX and comparison
PNG without reading the original page image.

The active product surface is the local `aiteqno` CLI. An HTTP API, GUI, and EHR
integration are not implemented and are not current commitments.

## Completed V1 foundation

- versioned Document IR model and JSON Schema
- source-independent, content-addressed asset bundle
- replaceable structure extraction and OCR ports
- local Tesseract adapter with Japanese and English support
- DOCX reconstruction and deterministic PNG preview
- weighted restoration evaluation with readability hard gates
- golden round-trip fixtures on Windows and Linux CI
- removal of the pre-V1 prototype and debug-overlay pipeline

The design and acceptance evidence are recorded in
[the V1 architecture](docs/architecture.md),
[the evaluation contract](docs/evaluation.md), and
[the golden E2E guide](docs/e2e.md).

## Candidate next phases

These phases describe dependency order, not promised dates.

1. **Broaden input adapters**
   - rasterized multi-page input
   - PDF input
   - DOCX input
2. **Expand Document IR deliberately**
   - semantic tables and repeated regions
   - an explicit migration path beyond IR `0.1.x`
   - additional writing systems and reading-order strategies
3. **Add form semantics**
   - `form.schema.json` for inferred logical fields
   - `form.data.json` for extracted values
   - human review and correction workflow
4. **Strengthen quality evidence**
   - more licensed representative fixtures
   - Microsoft Word release smoke checks
   - performance and memory benchmarks
5. **Add delivery surfaces only after the core contracts remain stable**
   - optional library API
   - optional HTTP service or GUI
   - downstream mappings such as CSV, XML, or EHR-specific adapters

Each phase should be decomposed into independently reviewable Issues and PRs.
New input formats or delivery surfaces must reuse the Document IR boundary
rather than bypassing it.
