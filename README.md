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
- GET  /api/blob/<path>

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
  contact: swordy.battle.axe@gmail.com