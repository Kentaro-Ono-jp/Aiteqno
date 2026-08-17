# Licensing Policy

## Current license

Aiteqno v0.2.0以降は [MIT License](LICENSE) で提供します。
ソフトウェアの利用、複製、変更、結合、公開、配布、再許諾、販売は、
MIT License本文に定める条件の下で許可されます。

外部からの貢献もMIT Licenseで受け入れます。個別のContributor License
Agreement（CLA）への同意は求めません。Pull Requestを送信する前に、投稿者が
その貢献物を提供する権利を持つことを確認してください。

## License history

- v0.1.0からv0.1.1まではAGPL-3.0で公開されました。
- v0.2.0からMIT Licenseへ変更しました。

過去にAGPL-3.0で取得されたコピーに対する既存の許諾は取り消されません。
現在のソースコードを利用する場合は、ルートの [LICENSE](LICENSE) を参照してください。

## Third-party OCR runtime

V1の標準OCR backendは、外部runtimeとしてTesseract 5.xを、Python wrapper
として`pytesseract`を利用します。両者はApache License 2.0で提供されます。
Tesseract実行ファイルとtrained-dataはAiteqnoのwheelへ同梱せず、利用環境で
別途導入します。導入元が提供するライセンス表示とnoticesを保持してください。

対応版、Windows / CI導入手順、公式参照先は
[docs/ocr-runtime.md](docs/ocr-runtime.md)に記録します。

## Test fixture provenance

公開テストfixtureは、Aiteqno向けに独自作成したMIT素材、または再配布条件を
明示的に確認できる素材だけを使用します。出所が分かっても再配布許諾を確認
できない文書は、画像そのものを現在のツリーへ保持しません。診断上必要な場合
も、外部URL、SHA-256、寸法、除外理由だけを記録します。

実ランタイムbaselineのfixture、確認者、source hash、除外記録は
[docs/real-runtime-baseline.md](docs/real-runtime-baseline.md)を参照してください。
