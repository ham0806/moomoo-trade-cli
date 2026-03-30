import argparse
import json
import sys
from typing import Any, Optional, Sequence

from .accounts import list_accounts, summarize_account
from .balance import fetch_balance
from .config import load_opend_config
from .deals import fetch_deals
from .engine import TradingEngine
from .errors import MoomooTraderError
from .orders import fetch_orders
from .positions import fetch_positions
from .trade_api import modify_order, place_order, query_max_trade_qtys, query_order_fees
from .trading_config import load_trading_config


def _write_json(payload: Any) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _handle_error(exc: Exception) -> int:
    print(str(exc), file=sys.stderr)
    return 1


def _build_accounts_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="moomoo-accounts",
        description="OpenD から口座一覧を取得します。",
    )


def _build_balance_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moomoo-balance",
        description="OpenD から口座残高を取得します。",
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=("real", "simulate"),
        help="取得対象の取引環境",
    )
    parser.add_argument(
        "--account-id",
        help="対象口座の acc_id。複数候補がある場合は必須です。",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="OpenD のキャッシュを使わずにサーバーへ再取得します。",
    )
    return parser


def _build_orders_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moomoo-orders",
        description="OpenD から現在の注文一覧を取得します。",
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=("real", "simulate"),
        help="取得対象の取引環境",
    )
    parser.add_argument(
        "--account-id",
        help="対象口座の acc_id。複数候補がある場合は必須です。",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="OpenD のキャッシュを使わずにサーバーへ再取得します。",
    )
    return parser


def _build_positions_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moomoo-positions",
        description="OpenD から現在のポジション一覧を取得します。",
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=("real", "simulate"),
        help="取得対象の取引環境",
    )
    parser.add_argument(
        "--account-id",
        help="対象口座の acc_id。複数候補がある場合は必須です。",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="OpenD のキャッシュを使わずにサーバーへ再取得します。",
    )
    return parser


def _build_trade_engine_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moomoo-trade-engine",
        description="moomoo OpenD を使った自動売買エンジンを起動します。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command_name in ("start", "once"):
        subparser = subparsers.add_parser(command_name)
        subparser.add_argument(
            "--config",
            required=True,
            help="trading.toml のパス",
        )
        subparser.add_argument(
            "--env",
            required=True,
            choices=("real", "simulate"),
            help="実行対象の取引環境",
        )

    return parser


def _build_deals_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moomoo-deals",
        description="OpenD から約定履歴を取得します。",
    )
    parser.add_argument("--env", required=True, choices=("real", "simulate"))
    parser.add_argument("--account-id", help="対象口座の acc_id。")
    parser.add_argument("--start", help="開始日。YYYY-MM-DD 形式。省略時は当日です。")
    parser.add_argument("--end", help="終了日。YYYY-MM-DD 形式。省略時は当日です。")
    parser.add_argument("--symbol", help="市場付きコード。例: JP.7203")
    return parser


def _build_place_order_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moomoo-place-order",
        description="OpenD へ直接注文を発行します。",
    )
    parser.add_argument("--env", required=True, choices=("real", "simulate"))
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--symbol", required=True, help="市場付きコード。例: JP.7203")
    parser.add_argument(
        "--side",
        required=True,
        choices=("BUY", "SELL", "SELL_SHORT", "BUY_BACK"),
    )
    parser.add_argument("--qty", required=True, type=float)
    parser.add_argument("--price", required=True, type=float)
    parser.add_argument("--order-type", default="NORMAL", choices=("NORMAL", "MARKET"))
    parser.add_argument("--time-in-force", default="DAY", choices=("DAY", "GTC"))
    parser.add_argument("--session")
    parser.add_argument("--jp-acc-type")
    parser.add_argument("--position-id")
    parser.add_argument("--remark")
    parser.add_argument("--credential-name")
    return parser


def _build_modify_order_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moomoo-modify-order",
        description="OpenD の既存注文を訂正または取消します。",
    )
    parser.add_argument("--env", required=True, choices=("real", "simulate"))
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--order-id", required=True)
    parser.add_argument(
        "--operation",
        required=True,
        choices=("NORMAL", "CANCEL", "DISABLE", "ENABLE", "DELETE"),
    )
    parser.add_argument("--qty", type=float, default=0.0)
    parser.add_argument("--price", type=float, default=0.0)
    parser.add_argument("--credential-name")
    return parser


def _build_max_trade_qty_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moomoo-max-trade-qty",
        description="最大取引数量を照会します。",
    )
    parser.add_argument("--env", required=True, choices=("real", "simulate"))
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--symbol", required=True, help="市場付きコード。例: JP.7203")
    parser.add_argument(
        "--side",
        required=True,
        choices=("BUY", "SELL", "SELL_SHORT", "BUY_BACK"),
    )
    parser.add_argument("--price", required=True, type=float)
    parser.add_argument("--order-type", default="NORMAL", choices=("NORMAL", "MARKET"))
    parser.add_argument("--session")
    parser.add_argument("--jp-acc-type")
    parser.add_argument("--position-id")
    return parser


def _build_order_fees_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moomoo-order-fees",
        description="注文手数料を照会します。",
    )
    parser.add_argument("--env", required=True, choices=("real", "simulate"))
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--order-id", dest="order_ids", required=True, action="append")
    return parser


def main_accounts(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_accounts_parser()
    parser.parse_args(argv)

    try:
        config = load_opend_config()
        accounts = list_accounts(config)
        _write_json([summarize_account(account) for account in accounts])
        return 0
    except MoomooTraderError as exc:
        return _handle_error(exc)
    except Exception as exc:  # pragma: no cover - 予期しない障害の保険
        return _handle_error(RuntimeError("予期しないエラーが発生しました: {}".format(exc)))


def main_balance(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_balance_parser()
    args = parser.parse_args(argv)

    try:
        config = load_opend_config()
        payload = fetch_balance(
            config=config,
            env_name=args.env,
            account_id=args.account_id,
            refresh=args.refresh,
        )
        _write_json(payload)
        return 0
    except MoomooTraderError as exc:
        return _handle_error(exc)
    except Exception as exc:  # pragma: no cover - 予期しない障害の保険
        return _handle_error(RuntimeError("予期しないエラーが発生しました: {}".format(exc)))


def main_orders(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_orders_parser()
    args = parser.parse_args(argv)

    try:
        config = load_opend_config()
        payload = fetch_orders(
            config=config,
            env_name=args.env,
            account_id=args.account_id,
            refresh=args.refresh,
        )
        _write_json(payload)
        return 0
    except MoomooTraderError as exc:
        return _handle_error(exc)
    except Exception as exc:  # pragma: no cover - 予期しない障害の保険
        return _handle_error(RuntimeError("予期しないエラーが発生しました: {}".format(exc)))


def main_positions(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_positions_parser()
    args = parser.parse_args(argv)

    try:
        config = load_opend_config()
        payload = fetch_positions(
            config=config,
            env_name=args.env,
            account_id=args.account_id,
            refresh=args.refresh,
        )
        _write_json(payload)
        return 0
    except MoomooTraderError as exc:
        return _handle_error(exc)
    except Exception as exc:  # pragma: no cover - 予期しない障害の保険
        return _handle_error(RuntimeError("予期しないエラーが発生しました: {}".format(exc)))


def main_trade_engine(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_trade_engine_parser()
    args = parser.parse_args(argv)

    try:
        opend_config = load_opend_config()
        trading_config = load_trading_config(args.config)
        engine = TradingEngine(
            opend_config=opend_config,
            trading_config=trading_config,
            env_name=args.env,
        )
        if args.command == "once":
            try:
                engine.run_once()
                return 0
            finally:
                engine.close()

        engine.start()
        return 0
    except MoomooTraderError as exc:
        return _handle_error(exc)
    except Exception as exc:  # pragma: no cover - 予期しない障害の保険
        return _handle_error(RuntimeError("予期しないエラーが発生しました: {}".format(exc)))


def main_deals(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_deals_parser()
    args = parser.parse_args(argv)

    try:
        config = load_opend_config()
        payload = fetch_deals(
            config=config,
            env_name=args.env,
            account_id=args.account_id,
            start=args.start,
            end=args.end,
            symbol=args.symbol,
        )
        _write_json(payload)
        return 0
    except MoomooTraderError as exc:
        return _handle_error(exc)
    except Exception as exc:  # pragma: no cover
        return _handle_error(RuntimeError("予期しないエラーが発生しました: {}".format(exc)))


def main_place_order(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_place_order_parser()
    args = parser.parse_args(argv)

    try:
        config = load_opend_config()
        payload = place_order(
            config=config,
            env_name=args.env,
            account_id=args.account_id,
            symbol=args.symbol,
            side=args.side,
            qty=args.qty,
            price=args.price,
            order_type=args.order_type,
            time_in_force=args.time_in_force,
            session=args.session,
            jp_acc_type=args.jp_acc_type,
            position_id=args.position_id,
            remark=args.remark,
            credential_name=args.credential_name,
        )
        _write_json(payload)
        return 0
    except MoomooTraderError as exc:
        return _handle_error(exc)
    except Exception as exc:  # pragma: no cover
        return _handle_error(RuntimeError("予期しないエラーが発生しました: {}".format(exc)))


def main_modify_order(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_modify_order_parser()
    args = parser.parse_args(argv)

    try:
        config = load_opend_config()
        payload = modify_order(
            config=config,
            env_name=args.env,
            account_id=args.account_id,
            order_id=args.order_id,
            operation=args.operation,
            qty=args.qty,
            price=args.price,
            credential_name=args.credential_name,
        )
        _write_json(payload)
        return 0
    except MoomooTraderError as exc:
        return _handle_error(exc)
    except Exception as exc:  # pragma: no cover
        return _handle_error(RuntimeError("予期しないエラーが発生しました: {}".format(exc)))


def main_max_trade_qty(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_max_trade_qty_parser()
    args = parser.parse_args(argv)

    try:
        config = load_opend_config()
        payload = query_max_trade_qtys(
            config=config,
            env_name=args.env,
            account_id=args.account_id,
            symbol=args.symbol,
            side=args.side,
            order_type=args.order_type,
            price=args.price,
            session=args.session,
            jp_acc_type=args.jp_acc_type,
            position_id=args.position_id,
        )
        _write_json(payload)
        return 0
    except MoomooTraderError as exc:
        return _handle_error(exc)
    except Exception as exc:  # pragma: no cover
        return _handle_error(RuntimeError("予期しないエラーが発生しました: {}".format(exc)))


def main_order_fees(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_order_fees_parser()
    args = parser.parse_args(argv)

    try:
        config = load_opend_config()
        payload = query_order_fees(
            config=config,
            env_name=args.env,
            account_id=args.account_id,
            order_ids=args.order_ids,
        )
        _write_json(payload)
        return 0
    except MoomooTraderError as exc:
        return _handle_error(exc)
    except Exception as exc:  # pragma: no cover
        return _handle_error(RuntimeError("予期しないエラーが発生しました: {}".format(exc)))


def accounts_entrypoint() -> None:
    raise SystemExit(main_accounts())


def balance_entrypoint() -> None:
    raise SystemExit(main_balance())


def orders_entrypoint() -> None:
    raise SystemExit(main_orders())


def positions_entrypoint() -> None:
    raise SystemExit(main_positions())


def trade_engine_entrypoint() -> None:
    raise SystemExit(main_trade_engine())


def deals_entrypoint() -> None:
    raise SystemExit(main_deals())


def place_order_entrypoint() -> None:
    raise SystemExit(main_place_order())


def modify_order_entrypoint() -> None:
    raise SystemExit(main_modify_order())


def max_trade_qty_entrypoint() -> None:
    raise SystemExit(main_max_trade_qty())


def order_fees_entrypoint() -> None:
    raise SystemExit(main_order_fees())
