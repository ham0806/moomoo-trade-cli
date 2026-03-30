# moomoo-trade-cli

`mise + Python 3.8 + uv` で moomoo OpenD に接続し、口座一覧、口座残高、注文一覧、ポジション一覧、直接発注、注文訂正、自動売買エンジンの最小基盤を提供する CLI です。

## 前提

- OpenD は別途起動し、ログインと同意確認を済ませておく
- この実装は TCP API を使うため、OpenD の `127.0.0.1:11111` が有効であること
- Python は `mise.toml` で `3.8.20` に固定
- 自動売買の実口座利用時は、事前に moomoo 側の免責事項同意を完了し、OpenD を再起動しておく

## セットアップ

1. リポジトリ直下で `mise` 設定を trust する

```powershell
mise trust .\mise.toml
```

`mise` が `Config files are not trusted.` と表示した場合に必要です。

2. ツールをインストールする

```powershell
mise install
```

3. 依存関係を同期する

```powershell
mise exec -- uv sync
```

4. 検証用ツールを含む開発依存も同期されるので、以降の検証は以下で実行できます

```powershell
mise exec -- uv run ruff format --check .
mise exec -- uv run ruff check .
mise exec -- uv run mypy .
mise exec -- uv run pytest -q
```

## 環境変数

- `MOOMOO_OPEND_HOST`
  - 省略時は `127.0.0.1`
- `MOOMOO_OPEND_PORT`
  - 省略時は `11111`

## 使い方

### 口座一覧を取得する

```powershell
mise exec -- uv run moomoo-accounts
```

### 残高を取得する

まず口座一覧から `acc_id` を確認し、その値を `--account-id` に渡します。`acc_id` は桁数が大きいため、JSON では文字列として出力します。

```powershell
mise exec -- uv run moomoo-balance --env real --account-id <acc_id>
```

シミュレート口座の場合は以下です。

```powershell
mise exec -- uv run moomoo-balance --env simulate --account-id <acc_id>
```

OpenD のキャッシュを使わずに最新値を取りに行きたい場合は `--refresh` を付けます。

```powershell
mise exec -- uv run moomoo-balance --env real --account-id <acc_id> --refresh
```

### 注文一覧を取得する

```powershell
mise exec -- uv run moomoo-orders --env real --account-id <acc_id>
```

### ポジション一覧を取得する

```powershell
mise exec -- uv run moomoo-positions --env real --account-id <acc_id>
```

### 約定履歴を取得する

省略時は当日分を返します。必要なら `--start` と `--end` で日付範囲を指定できます。

```powershell
mise exec -- uv run moomoo-deals --env real --account-id <acc_id>
```

### 戦略プログラムから直接発注する

戦略ロジックを外部プログラムに置き、この CLI を発注アダプタとして使えます。

```powershell
mise exec -- uv run moomoo-place-order --env simulate --account-id <acc_id> --symbol JP.7203 --side BUY --qty 10 --price 1000 --order-type NORMAL --time-in-force DAY
```

実口座で OpenD の取引ロック解除が必要な場合は、`--credential-name` で Windows 資格情報名を渡します。

```powershell
mise exec -- uv run moomoo-place-order --env real --account-id <acc_id> --symbol JP.7203 --side BUY --qty 10 --price 1000 --credential-name main-live
```

### 既存注文を訂正または取消する

```powershell
mise exec -- uv run moomoo-modify-order --env simulate --account-id <acc_id> --order-id <order_id> --operation CANCEL
```

価格や数量の訂正は `--operation NORMAL --qty ... --price ...` を使います。

### 最大取引数量を照会する

```powershell
mise exec -- uv run moomoo-max-trade-qty --env real --account-id <acc_id> --symbol JP.7203 --side BUY --price 1000 --order-type NORMAL
```

### 注文手数料を照会する

```powershell
mise exec -- uv run moomoo-order-fees --env real --account-id <acc_id> --order-id <order_id>
```

### 自動売買エンジンの設定を用意する

`trading.example.toml` をコピーして `trading.toml` を作成します。

```powershell
Copy-Item .\trading.example.toml .\trading.toml
```

設定項目は次の通りです。

- `account_id`
  - 発注対象の口座 ID
- `strategy`
  - 同梱戦略は `noop` のみ。独自戦略を使う場合は `module.path:ClassName`
- `poll_interval_seconds`
  - 常駐時の評価間隔
- `allow_live`
  - `true` のときだけ `--env real` で実注文を許可
- `credential_name`
  - Windows 資格情報ストアに保存した取引暗証番号の論理名
- `risk_limits`
  - 注文数、想定約定代金、未完了注文数、失効注文の自動キャンセル秒数
- `symbols`
  - 監視対象の銘柄群。`code`, `market`, `max_position_qty`, `max_order_qty`, `allowed_sides`, `allowed_order_types`, `allowed_time_in_force`, `allowed_sessions` を指定

### Windows 資格情報ストアに取引暗証番号を登録する

実口座で `allow_live = true` を使う場合だけ必要です。

```powershell
mise exec -- uv run python -c "import keyring; keyring.set_password('moomoo-trade-cli', 'main-live', 'YOUR_TRADE_PASSWORD')"
```

取得確認は以下です。

```powershell
mise exec -- uv run python -c "import keyring; print(bool(keyring.get_password('moomoo-trade-cli', 'main-live')))"
```

### 自動売買エンジンを 1 回だけ実行する

まずは `simulate` と `allow_live = false` で動作確認します。

```powershell
mise exec -- uv run moomoo-trade-engine once --config .\trading.toml --env simulate
```

標準出力には JSON Lines 形式のイベントログが流れます。標準エラーには人間向けメッセージだけを出します。

### 自動売買エンジンを常駐起動する

```powershell
mise exec -- uv run moomoo-trade-engine start --config .\trading.toml --env simulate
```

実口座で動かす場合は `trading.toml` 側で `allow_live = true` にしたうえで、明示的に `--env real` を指定してください。

```powershell
mise exec -- uv run moomoo-trade-engine once --config .\trading.toml --env real
```

## 検証

```powershell
mise exec -- uv run ruff format --check .
mise exec -- uv run ruff check .
mise exec -- uv run mypy .
mise exec -- uv run pytest -q
```

## トラブルシュート

- `免責事項への同意が未完了です` と出る場合
  - moomoo 側の案内ページで免責事項への同意を完了してから OpenD を再起動してください
- `対象口座を一意に決められません` と出る場合
  - `mise exec -- uv run moomoo-accounts` で `acc_id` を確認し、`--account-id` を明示してください
- `Windows 資格情報ストアに <name> が見つかりません` と出る場合
  - `keyring.set_password('moomoo-trade-cli', '<name>', '<trade_password>')` で資格情報を登録してください
- `allow_live=false のため実口座注文を拒否しました` と出る場合
  - `--env real` に加えて `trading.toml` 側も `allow_live = true` に変更してください
- OpenD を再起動した直後に push が来なくなった場合
  - エンジンを再起動してください。起動時に注文・約定・ポジションを再同期します

## 実装方針

- Python SDK は `moomoo` import を優先し、互換用に `futu` import も受け付けます
- `get_acc_list()` で対象口座を確定してから `accinfo_query()` を呼びます
- 口座候補が複数ある場合は安全のため自動選択せず、`--account-id` を要求します
- 自動売買エンジンは `Strategy.evaluate()` が返した `TradeIntent` をリスクゲートに通し、発注と push 追跡を行います
- 実口座アンロックは Windows 資格情報ストアから自動取得した秘密情報で行います

## 法令・規約メモ

- 実装時の注意点は [docs/legal-compliance-notes.md](./docs/legal-compliance-notes.md) にまとめています
- とくに `real`、`SELL_SHORT`、第三者向け提供、VPS 常駐化の前に確認してください

## 参考資料

- [Environment Setup](https://openapi.moomoo.com/moomoo-api-doc/en/quick/env.html)
- [Get the List of Trading Accounts](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-acc-list.html)
- [Get Account Funds](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-funds.html)
- [moomoo-api on PyPI](https://pypi.org/project/moomoo-api/)
