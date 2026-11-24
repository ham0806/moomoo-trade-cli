import unittest

from moomoo_trader.errors import InvalidConfigError
from moomoo_trader.trading_config import parse_trading_config


class TradingConfigTest(unittest.TestCase):
    def _base(self):
        return {
            "account_id": "123",
            "strategy": "noop",
            "poll_interval_seconds": 10,
            "allow_live": False,
            "symbols": [
                {
                    "code": "AAPL",
                    "market": "US",
                    "max_position_qty": 100,
                    "max_order_qty": 50,
                    "allowed_sides": ["BUY", "SELL", "SELL_SHORT", "BUY_BACK"],
                    "allowed_order_types": ["NORMAL", "MARKET"],
                    "allowed_time_in_force": ["DAY", "GTC"],
                    "allowed_sessions": ["RTH", "ETH"],
                }
            ],
            "risk_limits": {
                "max_daily_orders": 5,
                "max_daily_notional": 100000,
                "max_open_orders_per_symbol": 1,
                "cancel_after_seconds": 60,
            },
        }

    def test_新しい売買制約を読み込める(self):
        config = parse_trading_config(
            {
                "account_id": "123",
                "strategy": "noop",
                "poll_interval_seconds": 10,
                "allow_live": False,
                "symbols": [
                    {
                        "code": "AAPL",
                        "market": "US",
                        "max_position_qty": 100,
                        "max_order_qty": 50,
                        "allowed_sides": ["BUY", "SELL", "SELL_SHORT", "BUY_BACK"],
                        "allowed_order_types": ["NORMAL", "MARKET"],
                        "allowed_time_in_force": ["DAY", "GTC"],
                        "allowed_sessions": ["RTH", "ETH"],
                    }
                ],
                "risk_limits": {
                    "max_daily_orders": 5,
                    "max_daily_notional": 100000,
                    "max_open_orders_per_symbol": 1,
                    "cancel_after_seconds": 60,
                },
            }
        )

        symbol = config.symbols[0]
        self.assertEqual(["BUY", "SELL", "SELL_SHORT", "BUY_BACK"], symbol.allowed_sides)
        self.assertEqual(["NORMAL", "MARKET"], symbol.allowed_order_types)
        self.assertEqual(["DAY", "GTC"], symbol.allowed_time_in_force)

    def test_不正な売買方向を拒否する(self):
        with self.assertRaises(InvalidConfigError):
            parse_trading_config(
                {
                    "account_id": "123",
                    "strategy": "noop",
                    "poll_interval_seconds": 10,
                    "allow_live": False,
                    "symbols": [
                        {
                            "code": "AAPL",
                            "market": "US",
                            "max_position_qty": 100,
                            "max_order_qty": 50,
                            "allowed_sides": ["HOLD"],
                            "allowed_order_types": ["NORMAL"],
                            "allowed_time_in_force": ["DAY"],
                            "allowed_sessions": ["RTH"],
                        }
                    ],
                    "risk_limits": {
                        "max_daily_orders": 5,
                        "max_daily_notional": 100000,
                        "max_open_orders_per_symbol": 1,
                        "cancel_after_seconds": 60,
                    },
                }
            )

    def test_NaNの日次上限を拒否する(self):
        raw = self._base()
        raw["risk_limits"]["max_daily_notional"] = float("nan")

        with self.assertRaises(InvalidConfigError):
            parse_trading_config(raw)

    def test_無限大の保有上限を拒否する(self):
        raw = self._base()
        raw["symbols"][0]["max_position_qty"] = float("inf")

        with self.assertRaises(InvalidConfigError):
            parse_trading_config(raw)

    def test_新しいリスク設定の既定値(self):
        config = parse_trading_config(self._base())

        self.assertEqual(0.05, config.risk_limits.market_order_slippage_ratio)
        self.assertEqual(300, config.risk_limits.max_quote_age_seconds)
        self.assertEqual(30, config.state_sync_interval_seconds)
        self.assertIsNone(config.state_dir)

    def test_新しいリスク設定を読み込める(self):
        raw = self._base()
        raw["risk_limits"]["market_order_slippage_ratio"] = 0.01
        raw["risk_limits"]["max_quote_age_seconds"] = 0
        raw["state_sync_interval_seconds"] = 5
        raw["state_dir"] = "C:/state"

        config = parse_trading_config(raw)

        self.assertEqual(0.01, config.risk_limits.market_order_slippage_ratio)
        self.assertEqual(0, config.risk_limits.max_quote_age_seconds)
        self.assertEqual(5, config.state_sync_interval_seconds)
        self.assertEqual("C:/state", config.state_dir)


if __name__ == "__main__":
    unittest.main()
