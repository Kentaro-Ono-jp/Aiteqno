# Aiteqno

Schema-first OCR pipeline for structured documents.

The current V1 round-trip CLI contract and Windows PowerShell examples are in
[docs/cli.md](docs/cli.md).

Aiteqno converts structured forms (DOCX / PDF) into reliable JSON  
by detecting document structure **before running OCR**.

This approach dramatically improves accuracy for form-like documents  
such as questionnaires, medical records, and administrative forms.

---

# The Problem

Traditional OCR pipelines work like this:

image → OCR → guess document structure → messy data

This approach struggles with:

- questionnaires
- medical forms
- administrative documents
- structured PDFs

Because OCR tries to understand structure **after** recognition.

---

# The Aiteqno Approach

Aiteqno flips the pipeline:

schema → layout detection → OCR → validation → JSON

By defining document structure first, Aiteqno can:

- identify fields before OCR
- validate OCR results using schemas
- produce structured JSON reliably

This makes form processing significantly more robust.

---

# Example

Input

DOCX / PDF questionnaire

Output


{
"patient_name": "Taro Yamada",
"age": 42,
"symptoms": ["headache", "fatigue"]
}


---

# Use Cases

Aiteqno is designed for structured document processing.

Examples include:

- medical questionnaires
- insurance forms
- administrative documents
- enterprise form pipelines
- EHR integration

---

# Current Status

Early research and prototype stage.

The project currently focuses on building the core pipeline:

DOCX → schema → layout detection → OCR → JSON

---

# Roadmap

## Next milestones

- EHR integration templates (CSV / XML mapping)
- sample form datasets (input/*.pdf → output/*.json)
- minimal audit log implementation (CLI flag)
- API compatibility policy (90-day notice for breaking changes)
- security fixes and dependency maintenance
- conversion engine improvements (table / vertical line detection)
- reproducible builds and artifact verification
- documentation (Quick Start / FAQ)
- benchmark publication (CPU / memory / processing time)
- real-world use case collection

These roadmap items are derived from the current development plan. :contentReference[oaicite:0]{index=0}

---

# Timeline (rough estimate)

2028 Q2
- initial EHR templates
- minimal audit log
- FAQ documentation

2028 Q3
- benchmark publication
- signed builds
- expanded case documentation

Breaking changes will be announced **90 days in advance**.

---

# License

Aiteqno is released under the [MIT License](LICENSE).

Versions v0.1.0 through v0.1.1 were published under AGPL-3.0. Existing license
grants for those copies remain valid; v0.2.0 and later are published under MIT.

---

# Contributing

Issues, discussion, and pull requests are welcome. Contributions are accepted
under the MIT License; see [CONTRIBUTING.md](CONTRIBUTING.md).

If you have real-world form datasets or use cases, please open an issue.

---

# License

MIT

bellow In Japanese .
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/Python-3.x-informational)
![CI](https://github.com/Kentaro-Ono-jp/Aiteqno/actions/workflows/ci.yml/badge.svg)

# 問診票電子化及び電子カルテ入力支援システム

紙の問診票を構造化データに変換し、院内EHR/基幹へ取り込むためのバックエンド（Flask）。
ライセンスはMIT License（詳細はLICENSE参照）。商標の扱いはTRADEMARKS.mdを参照。

## Quick Start

環境作成:
    python -m venv .venv
    # macOS/Linux:
    source .venv/bin/activate
    # Windows (Git Bash/CMD):
    .venv\Scripts\activate

依存関係:
    pip install -r requirements.txt

起動:
    # macOS/Linux:
    export FLASK_APP=app.py
    # Windows (CMD/Powershell):
    set FLASK_APP=app.py
    flask run --host=0.0.0.0 --port=5000

## API (最小)

- GET  /api/mode
- GET/POST /api/form
- GET  /api/blob/

## License

本リポジトリはMIT Licenseで公開しています。v0.1.0からv0.1.1までは
AGPL-3.0で公開されていました。詳細はLICENSEとLICENSING_POLICY.mdを参照してください。

## Security

脆弱性は公開Issueではなく、メールまたは GitHub Security Advisories でご連絡ください。

## Naming

本リポジトリ名「Aiteqno」はプロジェクト名に由来します。ReactorFrontが
オープンソースプロジェクトとして公開・維持しています。

## Maintainers

- Kentaro Ono（ReactorFront）
  contact: <swordy.battle.axe@gmail.com>
