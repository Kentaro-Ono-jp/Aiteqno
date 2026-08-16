# Contributing

Aiteqnoへの貢献を歓迎します。

Pull Requestを送信することで、貢献物を本リポジトリの
[MIT License](LICENSE) の下で提供することに同意したものとします。
投稿者自身がその貢献物を提供する権利を持つことを確認してください。

## 開発フロー（最小）

- Fork → branch 作成 → 変更 → PR（小さく、テストが通ること）
- コミットは Conventional Commits 推奨（feat:, fix:, docs:, chore:, ci: など）

### PR チェックリスト

- [ ] `feat|fix|docs|chore|ci` のいずれかでコミットを整理
- [ ] 影響範囲の簡単な説明（UI/互換性/依存パッケージ）
- [ ] 破壊的変更があれば**代替手順と移行ガイド**を記載
- [ ] セキュリティ影響（入力検証/外部I/O/権限）を自己レビュー
- [ ] 貢献物をMIT Licenseで提供できる権利がある

### ブランチ/リリースの目安

- `main`: 安定版、タグ付けして配布  
- `develop`: 次期リリース候補  
- `feat/*` / `fix/*`: 機能・修正単位で小さくPR
