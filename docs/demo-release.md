# Windows demo package and GitHub Release

Issue #40 adds a small Windows distribution for people who want to exercise the
V1 round trip without cloning the repository or creating a development virtual
environment. GitHub Releases is the publication surface because GitHub Packages
does not provide a Python/PyPI registry. See GitHub's official lists of
[supported package registries](https://docs.github.com/en/packages/learn-github-packages/introduction-to-github-packages)
and [Release capabilities](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

## User workflow

Download the `Aiteqno-demo-*-windows.zip` asset from the
[GitHub Releases page](https://github.com/Kentaro-Ono-jp/Aiteqno/releases),
extract the entire archive, and drag a single-page PNG onto `run-demo.cmd`.

The launcher:

1. finds Python 3.11, 3.12, 3.13, or 3.14;
2. verifies Tesseract 5 or newer and the selected OCR languages;
3. creates or reuses a wheel-hash-specific runtime below
   `%LOCALAPPDATA%\Aiteqno\demo-runtimes`;
4. runs the existing `aiteqno roundtrip` application boundary;
5. copies the canonical Document IR schema and records SHA-256 evidence; and
6. opens the completed result directory in Explorer.

The launcher never writes a `.venv` into the extracted archive or repository.
It never overwrites an existing result directory. The first run needs internet
access because third-party Python dependencies are resolved from PyPI; Python
and Tesseract themselves are intentionally not bundled.

Use PowerShell for explicit settings:

```powershell
.\run-demo.ps1 "C:\input\form.png" "C:\result\form" `
  -Language jpn,eng `
  -Dpi 144 `
  -NoOpen
```

## Result contract

The result contains three JSON documents with distinct responsibilities:

| Path | Responsibility |
| --- | --- |
| `document.ir.json` | extracted document content, geometry, style, provenance, and asset references |
| `document-ir.schema.json` | canonical Draft 2020-12 machine-readable IR contract |
| `demo.manifest.json` | source name/hash, Aiteqno version, OCR settings, artifact paths, sizes, and hashes |

The remaining results are `assets/`, `reconstructed.docx`, and
`reconstructed.png`. The DOCX is the formal reconstructed layout. Reconstruction
is approximate: a score around 70 is acceptable only while the separate
readability gates pass.

V1 does not implement form semantics, so the demo does not invent
`form.schema.json` or `form.data.json`. It also does not accept PDF, DOCX, or
multi-page input.

## Reproducible archive build

Build and verify the normal Python distributions first:

```powershell
python -m build
python scripts\verify_distribution.py
```

Build the demo ZIP from the one wheel in `dist`:

```powershell
python scripts\build_demo_package.py `
  --wheel dist `
  --output dist\Aiteqno-demo-v0.3.0-demo.1-windows.zip `
  --release-tag v0.3.0-demo.1

python scripts\verify_demo_package.py `
  dist\Aiteqno-demo-v0.3.0-demo.1-windows.zip `
  --release-tag v0.3.0-demo.1 `
  --package-version 0.3.0.dev0
```

The builder fixes ZIP timestamps, entry ordering, compression settings, and
permissions. `package.manifest.json` records every embedded file's SHA-256 and
size. Rebuilding from identical source and wheel bytes therefore produces an
identical archive.

CI builds and verifies a `ci` archive on Windows and Linux after validating the
wheel and sdist.

## Publishing a prerelease

The release tag must point to the tested `main` commit. Create a draft first,
attach the wheel, sdist, demo ZIP, and `SHA256SUMS.txt`, verify the uploaded
assets, then publish it as a prerelease. The initial V1 demo tag is
`v0.3.0-demo.1`; the embedded package version remains `0.3.0.dev0`.

Example with the authenticated GitHub CLI:

```powershell
gh release create v0.3.0-demo.1 `
  --repo Kentaro-Ono-jp/Aiteqno `
  --target main `
  --title "Aiteqno v0.3.0 demo 1" `
  --notes-file release-notes.md `
  --draft `
  --prerelease

gh release upload v0.3.0-demo.1 `
  dist\aiteqno-0.3.0.dev0-py3-none-any.whl `
  dist\aiteqno-0.3.0.dev0.tar.gz `
  dist\Aiteqno-demo-v0.3.0-demo.1-windows.zip `
  dist\SHA256SUMS.txt `
  --repo Kentaro-Ono-jp/Aiteqno
```

Do not publish the draft until a freshly downloaded demo ZIP passes
`verify_demo_package.py` and its SHA-256 matches `SHA256SUMS.txt`.
