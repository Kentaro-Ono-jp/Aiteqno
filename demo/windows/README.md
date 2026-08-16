# Aiteqno Windows demo — {{RELEASE_TAG}}

この ZIP は、1ページの PNG を Aiteqno の Document IR に変換し、その IR
だけから Word 文書と確認用 PNG を復元する最小デモです。

同梱されている Python パッケージのバージョンは `{{PACKAGE_VERSION}}` です。

## 事前に必要なもの

- Windows 10 / 11
- Python 3.11〜3.14（Microsoft Store版ではなく python.org 版を推奨）
- Tesseract OCR 5.x 以降
- Tesseract の `jpn` と `eng` 言語データ
- 初回のみ、Python依存パッケージを取得するためのインターネット接続

Python本体とTesseract本体はZIPには含まれません。画像とOCRテキストは
ローカルで処理され、Aiteqnoから外部サービスへ送信されません。

## いちばん簡単な使い方

1. ZIPを任意の新しいフォルダーへ展開します。
2. 解析したいPNGを `run-demo.cmd` にドラッグ＆ドロップします。
3. 初回セットアップと処理が終わると、結果フォルダーがExplorerで開きます。

コマンドプロンプトから出力先を指定することもできます。

```bat
run-demo.cmd "C:\path\to\input.png" "C:\path\to\result"
```

既定の出力先は入力PNGと同じ場所の `<元ファイル名>-aiteqno-output` です。
既存ファイルを守るため、同名の出力先が存在する場合は上書きしません。

## 出力されるもの

```text
result/
|-- document.ir.json          OCR・構造・座標・スタイルを保持するDocument IR
|-- document-ir.schema.json  Document IRの正式なJSON Schema
|-- demo.manifest.json       入力・設定・全成果物のSHA-256記録
|-- assets/                  IRから参照される切り出し画像
|-- reconstructed.docx       Wordで開く復元文書
`-- reconstructed.png        復元レイアウトの確認用画像
```

`document.ir.json` と `assets/` は元PNGから独立しています。復元はピクセル完全一致を
目標にしておらず、可読性と約70%の評価基準を許容ラインとしています。レイアウトの
正本は `reconstructed.docx` です。

`form.schema.json` と `form.data.json` は現行V1には存在しません。このデモが出力する
JSONは、Document IR本体、正式Schema、実行証跡Manifestの3種類です。

## OCR言語やDPIを変える

PowerShellから直接起動します。

```powershell
.\run-demo.ps1 "C:\path\to\input.png" `
  "C:\path\to\result" `
  -Language eng `
  -Dpi 192 `
  -NoOpen
```

## 初回ランタイム

初回だけ `%LOCALAPPDATA%\Aiteqno\demo-runtimes` に専用Pythonランタイムを作ります。
ZIPの中やリポジトリに `.venv` は作りません。同じwheelとPython版の組み合わせは
次回から再利用します。

## 制約

- 入力は1ページPNGだけです。PDF、DOCX、複数ページ画像は未対応です。
- OCR品質はTesseract、言語データ、入力解像度に依存します。
- 復元物が読めない場合は `document.ir.json` と `demo.manifest.json` を添えて
  GitHub Issueへ報告してください。

ライセンスは同梱の `LICENSE`（MIT）を参照してください。
