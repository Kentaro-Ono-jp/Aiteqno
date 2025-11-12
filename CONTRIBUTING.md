# Contributing

本プロジェクトへの貢献には **CLA.md** への同意が必要です。
Pull Request を作成する際は、PR テンプレートの **「I agree to the CLA」** チェックを有効にしてください。

## 開発フロー（最小）

- Fork → branch 作成 → 変更 → PR（小さく、テストが通ること）
- コミットは Conventional Commits 推奨（feat:, fix:, docs:, chore:, ci: など）

### PR チェックリスト

- [ ] `feat|fix|docs|chore|ci` のいずれかでコミットを整理
- [ ] 影響範囲の簡単な説明（UI/互換性/依存パッケージ）
- [ ] 破壊的変更があれば**代替手順と移行ガイド**を記載
- [ ] セキュリティ影響（入力検証/外部I/O/権限）を自己レビュー
- [ ] **CLA 同意**済み（未同意はマージ不可）

### ブランチ/リリースの目安

- `main`: 安定版、タグ付けして配布  
- `develop`: 次期リリース候補  
- `feat/*` / `fix/*`: 機能・修正単位で小さくPR
