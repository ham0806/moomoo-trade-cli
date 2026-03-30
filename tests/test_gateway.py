import unittest
from unittest.mock import patch

from moomoo_trader.config import OpenDConfig
from moomoo_trader.gateway import MoomooGateway


class FakeTradeContext:
    def __init__(self):
        self.place_order_kwargs = None
        self.acctradinginfo_kwargs = None
        self.modify_order_kwargs = None
        self.order_fee_query_kwargs = None

    def place_order(self, **kwargs):
        self.place_order_kwargs = kwargs
        return 0, [
            {
                "order_id": "1",
                "code": kwargs["code"],
                "trd_side": kwargs["trd_side"],
                "qty": kwargs["qty"],
                "price": kwargs["price"],
                "dealt_qty": 0,
                "order_status": "SUBMITTED",
                "order_type": "NORMAL",
                "time_in_force": "DAY",
                "remark": kwargs["remark"],
                "session": kwargs["session"],
            }
        ]

    def acctradinginfo_query(self, **kwargs):
        self.acctradinginfo_kwargs = kwargs
        return 0, [
            {
                "max_cash_buy": 10,
                "max_position_sell": 5,
                "max_sell_short": 3,
                "max_buy_back": 2,
                "session": "RTH",
            }
        ]

    def modify_order(self, **kwargs):
        self.modify_order_kwargs = kwargs
        return 0, [
            {
                "order_id": kwargs["order_id"],
                "code": "US.AAPL",
                "trd_side": "BUY",
                "qty": kwargs["qty"],
                "price": kwargs["price"],
                "dealt_qty": 0,
                "order_status": "SUBMITTED",
                "order_type": "NORMAL",
                "time_in_force": "DAY",
            }
        ]

    def order_fee_query(self, **kwargs):
        self.order_fee_query_kwargs = kwargs
        return 0, [
            {
                "order_id": "1",
                "fee_amount": 12.3,
                "currency": "USD",
            }
        ]


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.gateway = MoomooGateway(
            config=OpenDConfig(host="127.0.0.1", port=11111),
            env_name="real",
            account_id="123",
        )
        self.gateway.trade_context = FakeTradeContext()
        self.gateway.quote_context = object()

    def test_place_orderに注文パラメータを渡す(self):
        with patch("moomoo_trader.gateway.parse_trd_side", return_value="SELL_SHORT"), patch(
            "moomoo_trader.gateway.parse_order_type",
            return_value="MARKET",
        ), patch("moomoo_trader.gateway.parse_time_in_force", return_value="GTC"), patch(
            "moomoo_trader.gateway.parse_session",
            return_value="ETH",
        ), patch("moomoo_trader.gateway.parse_jp_acc_type", return_value="JP_GENERAL_SHORT"):
            order = self.gateway.place_order(
                full_code="US.AAPL",
                side="SELL_SHORT",
                order_type_name="MARKET",
                time_in_force_name="GTC",
                qty=2,
                price=123.4,
                remark="remark",
                session_name="ETH",
                jp_acc_type_name="JP_GENERAL_SHORT",
                position_id="p-1",
            )

        self.assertEqual("US.AAPL", self.gateway.trade_context.place_order_kwargs["code"])
        self.assertEqual("MARKET", self.gateway.trade_context.place_order_kwargs["order_type"])
        self.assertEqual("GTC", self.gateway.trade_context.place_order_kwargs["time_in_force"])
        self.assertEqual("ETH", self.gateway.trade_context.place_order_kwargs["session"])
        self.assertEqual("p-1", self.gateway.trade_context.place_order_kwargs["position_id"])
        self.assertEqual("1", order.order_id)

    def test_query_max_trade_qtysを正規化する(self):
        with patch("moomoo_trader.gateway.parse_order_type", return_value="NORMAL"), patch(
            "moomoo_trader.gateway.parse_session",
            return_value="RTH",
        ), patch("moomoo_trader.gateway.parse_jp_acc_type", return_value="JP_GENERAL"):
            snapshot = self.gateway.query_max_trade_qtys(
                full_code="US.AAPL",
                order_type_name="NORMAL",
                price=123.4,
                session_name="RTH",
                jp_acc_type_name="JP_GENERAL",
                position_id=None,
            )

        self.assertEqual(10.0, snapshot.max_cash_buy)
        self.assertEqual(3.0, snapshot.max_sell_short)
        self.assertEqual("RTH", snapshot.session)

    def test_modify_orderに訂正パラメータを渡す(self):
        with patch("moomoo_trader.gateway.parse_modify_order_op", return_value="NORMAL"):
            payload = self.gateway.modify_order(
                order_id="1",
                operation_name="NORMAL",
                qty=3,
                price=234.5,
            )

        self.assertEqual("1", self.gateway.trade_context.modify_order_kwargs["order_id"])
        self.assertEqual(3, self.gateway.trade_context.modify_order_kwargs["qty"])
        self.assertEqual(234.5, self.gateway.trade_context.modify_order_kwargs["price"])
        self.assertEqual("1", payload["order_id"])

    def test_query_order_feesを正規化する(self):
        payload = self.gateway.query_order_fees(["1"])

        self.assertEqual(["1"], self.gateway.trade_context.order_fee_query_kwargs["order_id_list"])
        self.assertEqual("1", payload[0]["order_id"])
        self.assertEqual(12.3, payload[0]["fee_amount"])


if __name__ == "__main__":
    unittest.main()
