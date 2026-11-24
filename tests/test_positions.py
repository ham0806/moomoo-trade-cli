import unittest

from moomoo_trader.positions import build_position_snapshot


def _record(**overrides):
    payload = {
        "code": "7203",
        "market": "JP",
        "full_code": "JP.7203",
        "qty": 10,
        "can_sell_qty": 10,
        "position_side": "SHORT",
        "position_id": 12345,
        "cost_price": 1000,
        "market_val": 10000,
        "pl_val": 0,
        "currency": "JPY",
    }
    payload.update(overrides)
    return payload


class BuildPositionSnapshotTest(unittest.TestCase):
    def test_position_idを整数として保持する(self):
        snapshot = build_position_snapshot(_record(position_id="12345"))

        self.assertEqual(12345, snapshot.position_id)
        self.assertIsInstance(snapshot.position_id, int)

    def test_小数表記のposition_idも整数へ変換する(self):
        snapshot = build_position_snapshot(_record(position_id="12345.0"))

        self.assertEqual(12345, snapshot.position_id)

    def test_position_idなしはNoneにする(self):
        snapshot = build_position_snapshot(_record(position_id="N/A"))

        self.assertIsNone(snapshot.position_id)

    def test_整数でないposition_idは拒否する(self):
        with self.assertRaises(ValueError):
            build_position_snapshot(_record(position_id="short-1"))


if __name__ == "__main__":
    unittest.main()
