# Aiteqno

Schema-first OCR pipeline for structured documents.

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
- security fixes prioritized for commercial SLA
- conversion engine improvements (table / vertical line detection)
- signed builds for enterprise usage
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

# Why AGPL?

Aiteqno is released under AGPL to ensure that improvements made in SaaS environments remain open.

This prevents proprietary forks of the core pipeline while allowing collaborative development.

---

# Contributing

Issues and discussion are welcome.

If you have real-world form datasets or use cases, please open an issue.

---

# License

AGPL-3.0

bellow In Japanese .
![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)
![Python](https://img.shields.io/badge/Python-3.x-informational)
![CI](https://github.com/Kentaro-Ono-jp/Aiteqno/actions/workflows/ci.yml/badge.svg)

# 問診票電子化及び電子カルテ入力支援システム

紙の問診票を構造化データに変換し、院内EHR/基幹へ取り込むためのバックエンド（Flask）。
ライセンスは AGPL-3.0（詳細は LICENSE 参照）。商標の扱いは TRADEMARKS.md を参照。

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

本リポジトリは AGPL-3.0。改変やネット提供時はソース公開義務が発生します。

## Commercial Support

有償サポート（SLA/LTS/監査支援など）提供可。連絡先: <swordy.battle.axe@gmail.com>

## Security

脆弱性は公開Issueではなく、メールまたは GitHub Security Advisories でご連絡ください。

## Naming

本リポジトリ名「Aiteqno」は、約2年半後（2028年4月予定）に設立予定の法人名に由来します。現時点では個人事業主（屋号：ReactorFront）によるOSSコア公開であり、商用/OEM対応は別契約にて提供します。現在参画中の取引先の社名や案件詳細は公開しません。

## Maintainers

- Kentaro Ono（Findyフリーランス参画 / 個人事業主 ReactorFront）  
  contact: <swordy.battle.axe@gmail.com>

## Commercial & OEM (Quick Guide)

- **無料（AGPL-3.0）**: 研究・検証・OSS用途。ネット提供時は**改変含むソース公開義務**があります。
- **商用/クローズド利用**: デュアルライセンス（有償）を提供します。要件・規模に応じた見積。
- **OES（部品として組込み）**: コアはそのまま組込み可。導入テンプレ/監査ログ/LTSは商用拡張で対応。

**問い合わせ**: <swordy.battle.axe@gmail.com>  
件名例: `[Commercial Inquiry] Aiteqno QR/PDF/OCR`

### FAQ（抜粋）

- Q. AGPLだと社内利用でもコード公開が必要？  
  A. **社内専用（第三者提供なし）**なら公開義務はありません。SaaS等で第三者に提供する場合に公開義務が発生します。
- Q. クローズドで使いたい  
  A. 商用ライセンスをご検討ください（価格表あり / ボリューム割引可）。
- Q. 既存EHR/業務と連携したい  
  A. EHR連携テンプレ（CSV/XML）と導入プレイブックを提供予定。お気軽に相談ください。
  