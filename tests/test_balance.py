import unittest
from unittest.mock import patch

import pandas as pd

from moomoo_trader.balance import _query_balance_with_currency_fallback, normalize_balance_frame


class NormalizeBalanceFrameTest(unittest.TestCase):
    def test_主要項目を先頭に並べて_rawを保持する(self):
        frame = pd.DataFrame(
            [
                {
                    "currency": "JPY",
                    "total_assets": 100000.0,
                    "cash": 50000.0,
                    "market_val": 50000.0,
                    "avl_withdrawal_cash": 45000.0,
                    "power": 200000.0,
                    "custom_field": "kept",
                }
            ]
        )
        account = {"acc_id": "281756479345015383", "trd_env": "REAL"}

        payload = normalize_balance_frame(frame, account)

        self.assertEqual(
            list(payload.keys()),
            [
                "acc_id",
                "trd_env",
                "currency",
                "total_assets",
                "cash",
                "market_val",
                "avl_withdrawal_cash",
                "power",
                "raw",
            ],
        )
        self.assertEqual(payload["acc_id"], "281756479345015383")
        self.assertEqual(payload["raw"]["custom_field"], "kept")

    def test_nanをnull相当に正規化する(self):
        frame = pd.DataFrame(
            [
                {
                    "currency": "JPY",
                    "total_assets": float("nan"),
                    "cash": None,
                    "market_val": 0.0,
                    "avl_withdrawal_cash": float("nan"),
                    "power": 1.0,
                }
            ]
        )
        account = {"acc_id": "1", "trd_env": "SIMULATE"}

        payload = normalize_balance_frame(frame, account)

        self.assertIsNone(payload["total_assets"])
        self.assertIsNone(payload["avl_withdrawal_cash"])
        self.assertIsNone(payload["raw"]["cash"])


class QueryBalanceWithCurrencyFallbackTest(unittest.TestCase):
    def test_変換非対応なら次の通貨で再試行する(self):
        class FakeContext:
            def __init__(self):
                self.calls = []

            def accinfo_query(self, **kwargs):
                self.calls.append(kwargs)
                if "currency" not in kwargs:
                    return -1, "This account does not support converting to this currency"
                if kwargs["currency"] == "JPY":
                    return 0, "ok"
                return -1, "This account does not support converting to this currency"

        context = FakeContext()
        with patch("moomoo_trader.balance.get_currency_candidates", return_value=["JPY", "USD"]):
            ret, data = _query_balance_with_currency_fallback(
                context=context,
                sdk_trd_env="REAL",
                account_id=1,
                refresh=False,
            )

        self.assertEqual(ret, 0)
        self.assertEqual(data, "ok")
        self.assertEqual(len(context.calls), 2)
        self.assertEqual(context.calls[1]["currency"], "JPY")


if __name__ == "__main__":
    unittest.main()
