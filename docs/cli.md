# Aiteqno V1 command-line interface

The V1 CLI exposes the single-page PNG round trip implemented by the
application services. It is a local command-line tool: OCR uses the installed
Tesseract runtime, and no document pixels or recognized text are sent over the
network.

## Setup on Windows

Install the package and its Python dependencies from PowerShell:

```powershell
python -m pip install -e .
aiteqno --help
```

Extraction also requires Tesseract 5.x and the language data selected on the
command line. The default is `jpn` followed by `eng`. See
[OCR runtime setup](ocr-runtime.md) for installation details.

When Tesseract is not on `PATH`, configure it for the current PowerShell
session:

```powershell
$env:AITEQNO_TESSERACT_EXECUTABLE = "C:\Program Files\Tesseract-OCR\tesseract.exe"
$env:AITEQNO_TESSDATA_PREFIX = "C:\Program Files\Tesseract-OCR\tessdata"
```

## Commands

Extract a PNG to a Document IR file and a sibling `assets` directory:

```powershell
aiteqno extract ".\input\問診票.png" -o ".\work\document.ir.json"
```

Select OCR languages explicitly by repeating `--language` in priority order:

```powershell
aiteqno extract input.png -o ".\work\document.ir.json" --language jpn --language eng
```

Render a DOCX using only the IR file and its sibling assets:

```powershell
aiteqno render ".\work\document.ir.json" -o ".\work\reconstructed.docx"
```

Render a comparison PNG using the same self-contained bundle:

```powershell
aiteqno preview ".\work\document.ir.json" -o ".\work\reconstructed.png"
aiteqno preview ".\work\document.ir.json" -o ".\work\reconstructed-192dpi.png" --dpi 192
```

Run the full vertical slice into one new directory:

```powershell
aiteqno roundtrip ".\input\問診票.png" -o ".\output\問診票-roundtrip"
```

The round-trip directory is fixed for V1:

```text
問診票-roundtrip/
├── document.ir.json
├── assets/
├── reconstructed.docx
└── reconstructed.png
```

`render` and `preview` resolve assets relative to `document.ir.json`. They do
not read or require the original PNG. The output directory can therefore be
copied to another machine and rendered there as a self-contained bundle.

## Paths and overwrite policy

- Relative paths are resolved from the current working directory.
- `~` is expanded to the current user's home directory.
- Spaces, Windows drive paths, and Japanese file or directory names are
  supported. Quote paths containing spaces in PowerShell.
- No command overwrites an existing file or directory.
- `extract` reserves the `assets` directory beside its JSON output. Choose a
  dedicated output directory when an unrelated `assets` directory already
  exists.
- `roundtrip` requires a destination directory that does not yet exist.

## stdout, stderr, and exit codes

Successful artifact paths are written to stdout as `name=absolute-path` lines.
Non-fatal extraction and rendering diagnostics are written to stderr as
warnings. Fatal diagnostics are also written to stderr and never include a
Python traceback during normal CLI operation.

| Exit code | Meaning |
| ---: | --- |
| `0` | success or requested help/version |
| `1` | operational extraction or rendering failure |
| `2` | invalid command syntax or output extension |
| `3` | missing, unreadable, or invalid input |
| `4` | output conflict; an existing artifact was preserved |
| `5` | missing or unsupported runtime dependency |
| `130` | interrupted by the user |

Examples for scripts:

```powershell
aiteqno roundtrip input.png -o output
if ($LASTEXITCODE -ne 0) {
    throw "Aiteqno failed with exit code $LASTEXITCODE"
}
```
