import unittest

from moomoo_trader.models import (
    OrderLedgerEntry,
    TradeIntent,
    build_order_remark,
    strategy_token,
)


def _intent(strategy_id: str) -> TradeIntent:
    return TradeIntent(
        code="7203",
        market="JP",
        side="BUY",
        qty=10,
        limit_price=1000,
        strategy_id=strategy_id,
    )


class OrderRemarkTest(unittest.TestCase):
    def test_戦略IDは固定長トークンで保存する(self):
        intent = _intent("noop")

        remark = intent.remark

        self.assertIn("strategy={}".format(strategy_token("noop")), remark)
        self.assertNotIn("strategy=noop", remark)
        self.assertIn("intent={}".format(intent.intent_signature), remark)

    def test_前方一致する別戦略でもトークンが一致しない(self):
        alpha = _intent("alpha")
        alpha_live = _intent("alpha-live")

        self.assertNotEqual(strategy_token("alpha"), strategy_token("alpha-live"))
        self.assertNotEqual(alpha.remark, alpha_live.remark)

    def test_長いASCII戦略IDでも64バイト以内に収める(self):
        intent = _intent("very-long-strategy-name-0123456789")

        remark = intent.remark

        self.assertLessEqual(len(remark.encode("utf-8")), 64)
        self.assertIn("intent={}".format(intent.intent_signature), remark)

    def test_日本語の戦略IDでも文字境界を壊さず64バイト以内にする(self):
        intent = _intent("日本語の非常に長い戦略識別子です")

        remark = intent.remark

        encoded = remark.encode("utf-8")
        self.assertLessEqual(len(encoded), 64)
        self.assertEqual(encoded.decode("utf-8"), remark)
        self.assertIn("intent={}".format(intent.intent_signature), remark)

    def test_備考から注文意図を取り出せる(self):
        intent = _intent("日本語の非常に長い戦略識別子です")
        order = OrderLedgerEntry(
            order_id="1",
            code="7203",
            market="JP",
            full_code="JP.7203",
            side="BUY",
            qty=10.0,
            price=1000.0,
            dealt_qty=0.0,
            dealt_avg_price=None,
            status="SUBMITTED",
            order_type="NORMAL",
            time_in_force="DAY",
            create_time=None,
            updated_time=None,
            remark=intent.remark,
            currency="JPY",
            session=None,
            raw={},
        )

        self.assertEqual(intent.intent_signature, order.intent_signature)

    def test_備考生成関数でも64バイトを超えない(self):
        remark = build_order_remark("x" * 200, "0123456789abcdef")

        self.assertLessEqual(len(remark.encode("utf-8")), 64)


if __name__ == "__main__":
    unittest.main()
