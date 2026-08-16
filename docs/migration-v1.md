# Migrating from the pre-V1 prototype

Aiteqno V1 replaces the experimental layout-extractor tree with one installable
package, one versioned intermediate representation, and four public commands.
This note is for local workflows or scripts that used a checkout from before
the V1 cleanup.

## What was removed

- `SchemaBridge/backEnd/layout_extractor/` and its directly imported Python
  scripts
- the layout-extractor smoke test and its PDF preview dependency
- guide-line, red-box, source-background, and debug-label renderers
- the prototype layout JSON and its implicit `input` / `output` directory rules
- README instructions for a Flask application and HTTP endpoints that are not
  part of Aiteqno V1

The removed implementation remains available in Git history. It is not shipped
in the wheel or source distribution and is not imported by V1 runtime or tests.

## Command replacements

| Pre-V1 workflow | V1 replacement |
| --- | --- |
| Run `simple_pipeline.py` from the layout-extractor directory | `aiteqno roundtrip input.png -o output-directory` |
| Generate prototype layout JSON | `aiteqno extract input.png -o document.ir.json` |
| Draw a guide-line/red-box PNG | `aiteqno preview document.ir.json -o reconstructed.png` |
| Draw a debug PDF | No direct replacement; DOCX is formal output and PNG is the comparison artifact |
| Reconstruct while reading the source image | `aiteqno render document.ir.json -o reconstructed.docx` using only the IR bundle |

The complete command behavior is documented in [the CLI reference](cli.md).

## Data migration

Pre-V1 layout JSON is not Document IR and has no automatic converter. It lacks
the versioned coordinate, provenance, asset, style, confidence, and validation
contracts needed for source-independent reconstruction. Re-run `extract` from
the original single-page PNG to create a V1 bundle:

```powershell
aiteqno extract ".\input\form.png" -o ".\work\document.ir.json"
```

Validate the resulting structure against
[`document-ir-v0.1.schema.json`](../schemas/document-ir-v0.1.schema.json), then
use only the JSON and sibling `assets` directory for rendering and previewing.
The full source PNG is deliberately not copied into the bundle as a background.

## Runtime and dependency changes

- Supported Python versions are 3.11 through 3.14.
- Tesseract 5.x is the OCR runtime used by `extract` and `roundtrip`.
- `render` writes DOCX through `python-docx`; ReportLab is no longer a runtime
  dependency because V1 does not create the removed debug PDF.
- The public console entry point is `aiteqno`; importing files by modifying
  `sys.path` is unsupported.

For a clean installation, discard any old virtual environment and create a new
one from `pyproject.toml` as shown in the [README](../README.md#windows-quick-start).
