import unittest

from moomoo_trader.errors import InvalidConfigError
from moomoo_trader.trading_config import parse_trading_config


class TradingConfigTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
