# Repository MVH

- 真実のソースはコード、型、テスト、リンター、CI 設定
- 変更前に `docs/adr/` と repo の検証入口を確認する
- 完了前にこのファイルの検証コマンドを実行する
- 検証入口が不足している場合は勝手に補わず不足として報告する

## Commands

- setup: `mise exec -- uv sync`
- format: `mise exec -- uv run ruff format --check .`
- lint: `mise exec -- uv run ruff check .`
- typecheck: `mise exec -- uv run mypy .`
- test: `mise exec -- uv run pytest -q`
- e2e: `未定義`

