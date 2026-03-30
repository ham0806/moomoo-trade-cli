import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from moomoo_trader.cli import (
    main_accounts,
    main_balance,
    main_deals,
    main_max_trade_qty,
    main_modify_order,
    main_order_fees,
    main_orders,
    main_place_order,
    main_positions,
    main_trade_engine,
)
from moomoo_trader.errors import AccountSelectionError, OpenDConnectionError


class CliTest(unittest.TestCase):
    def test_accountsコマンドがjsonを出力する(self):
        fake_accounts = [
            {
                "acc_id": "100",
                "trd_env": "REAL",
                "acc_type": "MARGIN",
                "trdmarket_auth": ["JP"],
                "uni_card_num": "200",
                "card_num": "300",
                "security_firm": "FUTUINC",
                "jp_acc_type": [],
            }
        ]

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("moomoo_trader.cli.load_opend_config"), patch(
            "moomoo_trader.cli.list_accounts", return_value=fake_accounts
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main_accounts([])

        self.assertEqual(exit_code, 0)
        self.assertIn('"acc_id": "100"', stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_balanceコマンドでOpenDエラーを標準エラーへ出す(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("moomoo_trader.cli.load_opend_config"), patch(
            "moomoo_trader.cli.fetch_balance",
            side_effect=OpenDConnectionError("OpenD に接続できません。"),
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main_balance(["--env", "real", "--account-id", "1"])

        self.assertEqual(exit_code, 1)
        self.assertIn("OpenD に接続できません。", stderr.getvalue())

    def test_balanceコマンドで口座曖昧エラーを標準エラーへ出す(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("moomoo_trader.cli.load_opend_config"), patch(
            "moomoo_trader.cli.fetch_balance",
            side_effect=AccountSelectionError(
                "対象口座を一意に決められません。`--account-id` を指定してください。"
            ),
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main_balance(["--env", "real"])

        self.assertEqual(exit_code, 1)
        self.assertIn("--account-id", stderr.getvalue())

    def test_ordersコマンドがjsonを出力する(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("moomoo_trader.cli.load_opend_config"), patch(
            "moomoo_trader.cli.fetch_orders",
            return_value=[{"order_id": "1", "full_code": "JP.7203"}],
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main_orders(["--env", "real", "--account-id", "1"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"order_id": "1"', stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_positionsコマンドがjsonを出力する(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("moomoo_trader.cli.load_opend_config"), patch(
            "moomoo_trader.cli.fetch_positions",
            return_value=[{"full_code": "JP.7203", "qty": 10}],
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main_positions(["--env", "real", "--account-id", "1"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"full_code": "JP.7203"', stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_trade_engine_onceがengineを実行する(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        fake_engine = type(
            "FakeEngine",
            (),
            {
                "run_once": lambda self: None,
                "close": lambda self: None,
                "start": lambda self: None,
            },
        )()
        with patch("moomoo_trader.cli.load_opend_config"), patch(
            "moomoo_trader.cli.load_trading_config"
        ), patch("moomoo_trader.cli.TradingEngine", return_value=fake_engine):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main_trade_engine(
                    ["once", "--config", ".\\trading.toml", "--env", "simulate"]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual("", stderr.getvalue())

    def test_dealsコマンドがjsonを出力する(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("moomoo_trader.cli.load_opend_config"), patch(
            "moomoo_trader.cli.fetch_deals",
            return_value=[{"deal_id": "1", "full_code": "JP.7203"}],
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main_deals(["--env", "real", "--account-id", "1"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"deal_id": "1"', stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_place_orderコマンドがjsonを出力する(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("moomoo_trader.cli.load_opend_config"), patch(
            "moomoo_trader.cli.place_order",
            return_value={"order_id": "1", "full_code": "JP.7203"},
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main_place_order(
                    [
                        "--env",
                        "simulate",
                        "--account-id",
                        "1",
                        "--symbol",
                        "JP.7203",
                        "--side",
                        "BUY",
                        "--qty",
                        "10",
                        "--price",
                        "1000",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn('"order_id": "1"', stdout.getvalue())

    def test_modify_orderコマンドがjsonを出力する(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("moomoo_trader.cli.load_opend_config"), patch(
            "moomoo_trader.cli.modify_order",
            return_value={"order_id": "1", "operation": "CANCEL"},
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main_modify_order(
                    [
                        "--env",
                        "simulate",
                        "--account-id",
                        "1",
                        "--order-id",
                        "1",
                        "--operation",
                        "CANCEL",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn('"operation": "CANCEL"', stdout.getvalue())

    def test_max_trade_qtyコマンドがjsonを出力する(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("moomoo_trader.cli.load_opend_config"), patch(
            "moomoo_trader.cli.query_max_trade_qtys",
            return_value={"max_cash_buy": 10.0},
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main_max_trade_qty(
                    [
                        "--env",
                        "simulate",
                        "--account-id",
                        "1",
                        "--symbol",
                        "JP.7203",
                        "--side",
                        "BUY",
                        "--price",
                        "1000",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn('"max_cash_buy": 10.0', stdout.getvalue())

    def test_order_feesコマンドがjsonを出力する(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("moomoo_trader.cli.load_opend_config"), patch(
            "moomoo_trader.cli.query_order_fees",
            return_value=[{"order_id": "1", "fee_amount": 12.3}],
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main_order_fees(
                    [
                        "--env",
                        "real",
                        "--account-id",
                        "1",
                        "--order-id",
                        "1",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn('"fee_amount": 12.3', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
