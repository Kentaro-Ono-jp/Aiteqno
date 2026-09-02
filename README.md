# Aiteqno

[![CI](https://github.com/Kentaro-Ono-jp/Aiteqno/actions/workflows/ci.yml/badge.svg)](https://github.com/Kentaro-Ono-jp/Aiteqno/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11--3.14-informational)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Aiteqno is a local, source-independent document round-trip tool. V1 extracts
text and visual structure from one single-page PNG into a versioned Document
IR, then attempts to reconstruct a readable DOCX and comparison PNG from that
IR alone. The current realistic Japanese real-runtime baseline is an explicit
failure; the repository records that gap instead of presenting the diagnostic
preview as proof of success.

```text
single-page PNG
       |
       v
    extract --------> document.ir.json + assets/
                         |                 |
                         +---- render ----> reconstructed.docx
                         |
                         +---- preview ---> reconstructed.png
```

The reconstructed document is an approximation, not a pixel-perfect copy. The
intended formal result is a DOCX that preserves the important text and document
relationships well enough to read and use. The full source page is never kept
as a background shortcut.

## V1 scope

| Capability | V1 contract |
| --- | --- |
| Input | One single-page PNG |
| Intermediate format | `document.ir.json` validated by the published JSON Schema, plus content-addressed image assets |
| Formal reconstruction | `reconstructed.docx` |
| Comparison artifact | `reconstructed.png` |
| OCR | Local Tesseract 5.x; `jpn` is the default and ordered multilingual profiles remain explicit |
| Quality target | A score around 70 is acceptable only when readability hard gates also pass |

PDF input, DOCX input, multi-page extraction, form semantics, HTTP APIs, and a
GUI are outside V1. See the [roadmap](ROADMAP.md) for the intended order of
future work.

## Downloadable Windows demo

The [GitHub Releases page](https://github.com/Kentaro-Ono-jp/Aiteqno/releases)
provides a prerelease Windows demo ZIP. Extract it, then drag a single-page PNG
onto `run-demo.cmd`. The first run creates a versioned runtime under
`%LOCALAPPDATA%`; it does not create a repository `.venv`.

The demo adds the formal JSON Schema and a SHA-256 manifest to the normal
round-trip output:

```text
<input-name>-aiteqno-output/
|-- document.ir.json
|-- document-ir.schema.json
|-- demo.manifest.json
|-- assets/
|-- reconstructed.docx
`-- reconstructed.png
```

Python 3.11 through 3.14 and Tesseract 5.x with `jpn` / `eng` data remain local
prerequisites. Python dependencies are downloaded on the first run. See the
[demo and release guide](docs/demo-release.md) for usage, limitations, archive
verification, and maintainer commands.

## Windows Quick Start

Prerequisites:

- Git
- Python 3.11 through 3.14
- Tesseract 5.x with `jpn` language data (`eng` is optional for explicit multilingual runs)

Clone the repository and create an isolated environment from PowerShell. These
commands do not require activation of the virtual environment:

```powershell
git clone https://github.com/Kentaro-Ono-jp/Aiteqno.git
Set-Location .\Aiteqno

py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\aiteqno.exe --help
```

If Tesseract is not on `PATH`, point Aiteqno at the executable and language
directory for the current PowerShell session:

```powershell
$env:AITEQNO_TESSERACT_EXECUTABLE = "C:\Program Files\Tesseract-OCR\tesseract.exe"
$env:AITEQNO_TESSDATA_PREFIX = "C:\Program Files\Tesseract-OCR\tessdata"
```

Copy a PNG into a local input directory and run the complete round trip. The
destination directory must not already exist:

```powershell
New-Item -ItemType Directory -Force .\input | Out-Null
Copy-Item "C:\path\to\form.png" ".\input\form.png"

.\.venv\Scripts\aiteqno.exe roundtrip `
  ".\input\form.png" `
  -o ".\output\form-roundtrip"
```

The result is a portable bundle:

```text
output/form-roundtrip/
|-- document.ir.json
|-- assets/
|-- reconstructed.docx
`-- reconstructed.png
```

`render` and `preview` read only `document.ir.json` and its sibling `assets`
directory. The original PNG can be moved elsewhere before those commands run.

```powershell
.\.venv\Scripts\aiteqno.exe extract `
  ".\input\form.png" `
  -o ".\work\document.ir.json"

.\.venv\Scripts\aiteqno.exe render `
  ".\work\document.ir.json" `
  -o ".\work\reconstructed.docx"

.\.venv\Scripts\aiteqno.exe preview `
  ".\work\document.ir.json" `
  -o ".\work\reconstructed.png"
```

For Tesseract installation, language selection, path handling, overwrite rules,
and exit codes, see the [OCR runtime guide](docs/ocr-runtime.md) and the
[complete CLI reference](docs/cli.md).

## Contracts and quality evidence

- [V1 architecture](docs/architecture.md) defines boundaries, coordinate
  systems, source independence, and reconstruction rules.
- [Document IR JSON Schema](schemas/document-ir-v0.1.schema.json) is the formal
  machine-readable contract.
- [Evaluation contract](docs/evaluation.md) defines the weighted score and the
  readability hard gates.
- [Golden E2E guide](docs/e2e.md) documents the source-free round trip across
  Windows and Linux.
- [Real-runtime failure baseline](docs/real-runtime-baseline.md) records the
  reviewed Japanese source, a same-runtime no-upscale/300-DPI OCR comparison,
  a separate zero/2px white crop-padding comparison, and a fixed
  `jpn,eng`/`jpn` language-profile comparison before deterministic table
  topology, actual LibreOffice pages, and the current expected end-to-end failure.
- [Windows demo and release guide](docs/demo-release.md) documents the
  downloadable package and its reproducible archive contract.
- [Golden fixture manifest](tests/fixtures/e2e/manifest.json) records fixture
  provenance, hashes, reviewed content, and the accepted score.

The deterministic representative golden scores `78.79 / 100` against a
threshold of `70` after fixed fake OCR observations. That number measures
candidate IR preservation in DOCX; it is not real OCR accuracy. The separate
real-runtime Japanese baseline records control and 300 DPI candidate OCR
immediately after Tesseract, before DOCX generation. Although the 300 DPI trial
improves aggregate OCR metrics in the current Ubuntu lane, it loses a logical
block recovered by the no-upscale control and is therefore rejected. The
independent two-source-pixel white-padding trial keeps the same 96-DPI runtime,
source crop bboxes, language order, and OCR options. It clears the fixed +1pp
and no-loss adoption gates, so that padded candidate becomes the downstream
OCR input while the 300-DPI candidate remains diagnostic only. On that fixed
2px input, the Japanese-only profile passes the +1pp, recovered-set, protected
literal, trained-data, topology, and multilingual-smoke gates. It is therefore
the production default and downstream profile; ordered `jpn,eng` remains an
explicit option. Quality 80 Focus 1 composes that language decision with a
four-pixel white border, PSM 8, and separator-safe same-row grouping derived
only from source geometry. Quality 80 Focus 2 additionally recovers short grid
ticks, closed cells, and two-row internal boundaries from source geometry. Its
active set is explicitly pinned to the existing baseline followed by
questionnaire 01; no directory scan or glob can add later questionnaires. The
rendered-visible evaluator remains fixed at PSM 6. The selected IR's validated
topology is rendered as editable native Word tables. A numeric pass is never
sufficient by itself: package and source-independence integrity remain separate
gates.

## Development

Install the development tools and run the same deterministic checks used by CI:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src scripts tests
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe scripts\verify_distribution.py
```

CI runs deterministic tests on Windows and Linux with Python 3.11 and 3.14
without depending on machine-global document runtimes. A dedicated Ubuntu 24.04
job exercises real Tesseract, LibreOffice, Poppler, and Japanese fonts, then
runs only the current Quality 80 Focus 2 descriptor in its quality gate. It
processes the two explicitly listed fixtures sequentially and uploads their
complete evidence without generating or scoring questionnaires 02 through 10.

## Migrating from the pre-V1 prototype

The experimental layout scripts, guide-line/red-box renderers, and their custom
JSON are no longer part of the active tree. They remain available in Git
history. Read the [V1 migration note](docs/migration-v1.md) before replacing an
older workflow; pre-V1 layout JSON must be regenerated from its source PNG.

## Project policies

- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [Maintainers](MAINTAINERS.md)
- [Trademark policy](TRADEMARKS.md)
- [Licensing policy](LICENSING_POLICY.md)

## License

Aiteqno v0.2.0 and later is released under the [MIT License](LICENSE). Versions
v0.1.0 through v0.1.1 were published under AGPL-3.0; existing grants for those
copies remain valid. See [LICENSING_POLICY.md](LICENSING_POLICY.md).
