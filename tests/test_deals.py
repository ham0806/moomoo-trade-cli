import unittest
from unittest.mock import patch

from moomoo_trader.config import OpenDConfig
from moomoo_trader.deals import fetch_deals


class FakeGateway:
    last_instance = None

    def __init__(self, config, env_name, account_id, event_queue=None):
        self.config = config
        self.env_name = env_name
        self.account_id = account_id
        self.event_queue = event_queue
        self.connected = False
        self.closed = False
        self.deal_queries = []
        FakeGateway.last_instance = self

    def connect(self):
        self.connected = True

    def close(self):
        self.closed = True

    def query_deal_records(self, *, start_date, end_date, full_code=""):
        self.deal_queries.append(
            {"start_date": start_date, "end_date": end_date, "full_code": full_code}
        )
        return []


class FetchDealsTest(unittest.TestCase):
    def test_account_id省略時はNoneのままgatewayへ渡す(self):
        with patch("moomoo_trader.deals.MoomooGateway", FakeGateway):
            result = fetch_deals(
                config=OpenDConfig(host="127.0.0.1", port=11111),
                env_name="real",
            )

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertIsNone(gateway.account_id)
        self.assertTrue(gateway.connected)
        self.assertTrue(gateway.closed)
        self.assertEqual([], result)

    def test_account_id指定時はそのままgatewayへ渡す(self):
        with patch("moomoo_trader.deals.MoomooGateway", FakeGateway):
            fetch_deals(
                config=OpenDConfig(host="127.0.0.1", port=11111),
                env_name="real",
                account_id="123",
            )

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertEqual("123", gateway.account_id)

    def test_シンボル指定時は市場の日付を既定にする(self):
        with patch("moomoo_trader.deals.MoomooGateway", FakeGateway), patch(
            "moomoo_trader.deals.market_today_text",
            return_value="2026-01-14",
        ):
            fetch_deals(
                config=OpenDConfig(host="127.0.0.1", port=11111),
                env_name="real",
                account_id="123",
                symbol="US.AAPL",
            )

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertEqual("2026-01-14", gateway.deal_queries[0]["start_date"])
        self.assertEqual("2026-01-14", gateway.deal_queries[0]["end_date"])

    def test_開始日のみ指定時は終了日を当日にする(self):
        with patch("moomoo_trader.deals.MoomooGateway", FakeGateway), patch(
            "moomoo_trader.deals.market_today_text",
            return_value="2026-11-11",
        ):
            fetch_deals(
                config=OpenDConfig(host="127.0.0.1", port=11111),
                env_name="real",
                account_id="123",
                symbol="US.AAPL",
                start="2026-11-01",
            )

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertEqual("2026-11-01", gateway.deal_queries[0]["start_date"])
        self.assertEqual("2026-11-11", gateway.deal_queries[0]["end_date"])

    def test_開始日と終了日を指定した場合はそのまま使う(self):
        with patch("moomoo_trader.deals.MoomooGateway", FakeGateway), patch(
            "moomoo_trader.deals.market_today_text",
            return_value="2026-11-11",
        ):
            fetch_deals(
                config=OpenDConfig(host="127.0.0.1", port=11111),
                env_name="real",
                account_id="123",
                symbol="US.AAPL",
                start="2026-11-01",
                end="2026-11-05",
            )

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertEqual("2026-11-01", gateway.deal_queries[0]["start_date"])
        self.assertEqual("2026-11-05", gateway.deal_queries[0]["end_date"])


if __name__ == "__main__":
    unittest.main()
