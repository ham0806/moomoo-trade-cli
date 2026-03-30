# ADR 0001: Repository Harness Baseline

- Status: Accepted
- Date: 2026-03-30

## Context

このリポジトリを Codex で安定して扱うために、最小実行可能ハーネスを定義する必要がある。

## Decision

- 指示の入口は `AGENTS.md` を優先し、既存 `CLAUDE.md` がある場合は必要に応じて併用する
- ADR は `docs/adr/` に保存する
- 検証入口は repo ルートから実行できる 1 コマンドに揃える
- 既存の git hook runner を優先し、存在しない場合のみ `pre-commit` を新設する
- Windows では Codex hooks を前提にしない

## Command Entry Points

- setup: `mise exec -- uv sync`
- format: `mise exec -- uv run ruff format --check .`
- lint: `mise exec -- uv run ruff check .`
- typecheck: `mise exec -- uv run mypy .`
- test: `mise exec -- uv run pytest -q`
- e2e: `未定義`

## Consequences

- Codex はこの ADR と repo ルートの指示ファイルを入口として検証コマンドを発見できる
- 記述ドキュメントより実行可能な設定とテストを優先する
- 将来 hooks を導入する場合も、まずこの入口を正として保守する

