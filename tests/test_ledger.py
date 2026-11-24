import unittest
from datetime import datetime, timedelta, timezone

from moomoo_trader.ledger import OrderLedger, PositionLedger
from moomoo_trader.models import DealLedgerEntry, OrderLedgerEntry, PositionSnapshot
from moomoo_trader.orders import build_order_entry, normalize_order_record


def _order(**overrides) -> OrderLedgerEntry:
    payload = {
        "order_id": "1",
        "code": "7203",
        "market": "JP",
        "full_code": "JP.7203",
        "side": "BUY",
        "qty": 100.0,
        "price": 1000.0,
        "dealt_qty": 0.0,
        "dealt_avg_price": None,
        "status": "SUBMITTED",
        "order_type": "NORMAL",
        "time_in_force": "DAY",
        "create_time": datetime(2026, 3, 30, 0, 0, tzinfo=timezone.utc),
        "updated_time": None,
        "remark": "moomoo-trade-cli|strategy=test|intent=abc",
        "currency": "JPY",
        "session": None,
        "raw": {},
    }
    payload.update(overrides)
    return OrderLedgerEntry(**payload)


def _deal(**overrides) -> DealLedgerEntry:
    payload = {
        "deal_id": "d-1",
        "order_id": "1",
        "code": "7203",
        "market": "JP",
        "full_code": "JP.7203",
        "side": "BUY",
        "qty": 10.0,
        "price": 1000.0,
        "create_time": None,
        "status": "FILLED_ALL",
        "raw": {},
    }
    payload.update(overrides)
    return DealLedgerEntry(**payload)


def _position(**overrides) -> PositionSnapshot:
    payload = {
        "code": "7203",
        "market": "JP",
        "full_code": "JP.7203",
        "qty": 10.0,
        "can_sell_qty": 10.0,
        "position_side": "LONG",
        "position_id": 1,
        "cost_price": 100.0,
        "market_val": 1000.0,
        "pl_val": 0.0,
        "currency": "JPY",
        "raw": {},
    }
    payload.update(overrides)
    return PositionSnapshot(**payload)


class OrderLedgerTest(unittest.TestCase):
    def test_古い注文だけを取消候補にする(self):
        now = datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc)
        ledger = OrderLedger()
        ledger.replace_orders(
            [
                _order(order_id="old", create_time=now - timedelta(minutes=10)),
                _order(order_id="new", create_time=now - timedelta(seconds=5)),
            ]
        )

        stale = ledger.stale_orders(now, cancel_after_seconds=300)

        self.assertEqual(["old"], [order.order_id for order in stale])

    def test_結果不明の注文は取消候補にしない(self):
        now = datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc)
        ledger = OrderLedger()
        ledger.replace_orders([_order(status="TIMEOUT", create_time=now - timedelta(minutes=10))])

        self.assertEqual([], ledger.stale_orders(now, cancel_after_seconds=300))
        self.assertEqual(1, len(ledger.open_orders()))

    def test_米国市場の注文時刻を日本時間と直接比較しない(self):
        # 夏時間の米東部 10:00 は UTC 14:00。日本時間 23:00:30 時点で経過 30 秒。
        normalized = normalize_order_record(
            {
                "order_id": "1",
                "code": "US.AAPL",
                "trd_side": "BUY",
                "qty": 1,
                "price": 100,
                "dealt_qty": 0,
                "order_status": "SUBMITTED",
                "create_time": "2026-07-15 10:00:00",
            }
        )
        order = build_order_entry(normalized)
        ledger = OrderLedger()
        ledger.replace_orders([order])
        now = datetime(2026, 7, 15, 23, 0, 30, tzinfo=timezone(timedelta(hours=9)))

        self.assertEqual([], ledger.stale_orders(now, cancel_after_seconds=300))
        self.assertIsNotNone(order.create_time)
        self.assertIsNotNone(order.create_time.tzinfo)

    def test_未約定と未反映の約定数量を区別する(self):
        order = _order(qty=100, dealt_qty=40, applied_dealt_qty=10)

        self.assertEqual(90.0, order.pending_qty)
        self.assertEqual(30.0, order.unapplied_dealt_qty)

    def test_全約定後の未反映数量を保持する(self):
        now = datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc)
        ledger = OrderLedger()
        ledger.replace_orders([_order(status="FILLED_ALL", dealt_qty=100, create_time=now)])

        self.assertEqual([], ledger.open_orders())
        self.assertEqual(100.0, ledger.all_orders()[0].unapplied_dealt_qty)

    def test_注文通知より先に届いた約定数量を保持する(self):
        ledger = OrderLedger()
        ledger.record_applied_deal("1", 5)

        ledger.upsert_order(_order(order_id="1", qty=5, dealt_qty=5, status="FILLED_ALL"))

        order = ledger.all_orders()[0]
        self.assertEqual(5.0, order.applied_dealt_qty)
        self.assertEqual(0.0, order.unapplied_dealt_qty)

    def test_同期後も前日約定の適用済み数量を維持する(self):
        ledger = OrderLedger()
        ledger.upsert_order(_order(order_id="1", qty=10, dealt_qty=5, status="FILLED_PART"))

        ledger.reset_applied_deals({"1": 5})

        self.assertEqual(5.0, ledger.all_orders()[0].applied_dealt_qty)


class PositionLedgerTest(unittest.TestCase):
    def test_買い増しで取得単価を加重平均する(self):
        ledger = PositionLedger()
        ledger.replace_positions([_position(qty=10, can_sell_qty=10, cost_price=100)])
        ledger.apply_deal(_deal(qty=10, price=200))

        position = ledger.get("JP.7203")

        assert position is not None
        self.assertEqual(20.0, position.qty)
        self.assertEqual(150.0, position.cost_price)
        self.assertEqual(4000.0, position.market_val)

    def test_売却で保有数量と売却可能数量を更新する(self):
        ledger = PositionLedger()
        ledger.replace_positions([_position(qty=10, can_sell_qty=10, cost_price=100)])
        ledger.apply_deal(_deal(side="SELL", qty=4, price=150))

        position = ledger.get("JP.7203")

        assert position is not None
        self.assertEqual(6.0, position.qty)
        self.assertEqual(6.0, position.can_sell_qty)


if __name__ == "__main__":
    unittest.main()
