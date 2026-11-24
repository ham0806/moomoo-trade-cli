import os
import shutil
import threading
import time
import unittest
import uuid
from pathlib import Path

from moomoo_trader.errors import MoomooTraderError
from moomoo_trader.models import OrderLedgerEntry
from moomoo_trader.reservations import OrderIntentStore

_STATE_ROOT = Path(__file__).resolve().parents[1] / ".test-state"


def _order(
    signature: str,
    *,
    status: str = "SUBMITTED",
    order_id: str = "order-1",
) -> OrderLedgerEntry:
    return OrderLedgerEntry(
        order_id=order_id,
        code="7203",
        market="JP",
        full_code="JP.7203",
        side="BUY",
        qty=10.0,
        price=1000.0,
        dealt_qty=0.0,
        dealt_avg_price=None,
        status=status,
        order_type="NORMAL",
        time_in_force="DAY",
        create_time=None,
        updated_time=None,
        remark="moomoo-trade-cli|strategy=test|intent={}".format(signature),
        currency="JPY",
        session=None,
        raw={},
    )


class OrderIntentStoreTest(unittest.TestCase):
    def setUp(self):
        _STATE_ROOT.mkdir(parents=True, exist_ok=True)
        self.state_dir = _STATE_ROOT / ("reservation-" + uuid.uuid4().hex)
        self.state_dir.mkdir()
        self.store = OrderIntentStore(str(self.state_dir))

    def tearDown(self):
        shutil.rmtree(str(self.state_dir), ignore_errors=True)
        try:
            os.rmdir(str(_STATE_ROOT))
        except OSError:
            pass

    def _reserve(self, store=None, signature="sig-1", owner_id="owner-a"):
        target = store or self.store
        return target.reserve(
            intent_signature=signature,
            full_code="JP.7203",
            side="BUY",
            qty=10,
            effective_price=1000,
            owner_id=owner_id,
        )

    def test_同じ注文意図を二重に予約しない(self):
        self.assertTrue(self._reserve())
        self.assertFalse(self._reserve(owner_id="owner-b"))
        self.assertTrue(self.store.is_blocked("sig-1"))

    def test_照会に注文が無くても予約を解放しない(self):
        self.assertTrue(self._reserve())
        self.store.mark_unknown("sig-1", "owner-a", "切断")

        remaining = self.store.reconcile([], owner_id="owner-a")

        self.assertEqual(["sig-1"], [entry.intent_signature for entry in remaining])
        self.assertTrue(self.store.is_blocked("sig-1"))

    def test_別プロセスは予約を解放できない(self):
        self.assertTrue(self._reserve())
        self.store.mark_sent("sig-1", "owner-a")
        self.store.mark_placed("sig-1", "owner-a", "order-1")

        self.store.release("sig-1", "owner-b")
        self.store.reconcile([], owner_id="owner-b")

        self.assertTrue(self.store.is_blocked("sig-1"))
        entry = self.store.active_entries()[0]
        self.assertEqual("owner-a", entry.owner_id)
        self.assertEqual("PLACED", entry.status)

    def test_別プロセスの進行中予約を削除できない(self):
        # A が発注済み、B の古い注文一覧にはまだ現れない状況を再現する。
        self.assertTrue(self._reserve(owner_id="owner-a"))
        self.store.mark_sent("sig-1", "owner-a")
        self.store.mark_placed("sig-1", "owner-a", "order-1")

        self.store.reconcile([], owner_id="owner-b")
        allowed = self._reserve(store=self.store, owner_id="owner-b")

        self.assertFalse(allowed)
        self.assertTrue(self.store.is_blocked("sig-1"))

    def test_照合で注文が見つかれば予約を維持する(self):
        self.assertTrue(self._reserve())
        self.store.mark_sent("sig-1", "owner-a")
        self.store.mark_placed("sig-1", "owner-a", "order-1")

        remaining = self.store.reconcile([_order("sig-1")], owner_id="owner-a")

        self.assertEqual("PLACED", remaining[0].status)
        self.assertEqual("order-1", remaining[0].order_id)
        self.assertTrue(self.store.is_blocked("sig-1"))

    def test_過去の同条件注文では結果不明の予約を解放しない(self):
        # 新しい発注は応答不明、同じ署名の過去注文は約定済みという状況。
        self.assertTrue(self._reserve())
        self.store.mark_sent("sig-1", "owner-a")
        self.store.mark_unknown("sig-1", "owner-a", "応答なし")

        remaining = self.store.reconcile(
            [_order("sig-1", status="FILLED_ALL", order_id="past-order")],
            owner_id="owner-a",
        )

        self.assertEqual(["sig-1"], [entry.intent_signature for entry in remaining])
        self.assertTrue(self.store.is_blocked("sig-1"))

    def test_注文IDが異なる完了注文では予約を解放しない(self):
        self.assertTrue(self._reserve())
        self.store.mark_sent("sig-1", "owner-a")
        self.store.mark_placed("sig-1", "owner-a", "current-order")

        remaining = self.store.reconcile(
            [_order("sig-1", status="FILLED_ALL", order_id="past-order")],
            owner_id="owner-a",
        )

        self.assertEqual(["sig-1"], [entry.intent_signature for entry in remaining])
        self.assertTrue(self.store.is_blocked("sig-1"))

    def test_トランザクション中は他プロセスが同じ予約を取得できない(self):
        started = threading.Event()
        errors = []

        def holder():
            try:
                with self.store.transaction("owner-a") as transaction:
                    transaction.reserve(
                        intent_signature="sig-1",
                        full_code="JP.7203",
                        side="BUY",
                        qty=10,
                        effective_price=1000,
                    )
                    started.set()
                    time.sleep(0.3)
            except Exception as exc:  # pragma: no cover - 失敗時の情報保持
                errors.append(exc)

        def contender():
            started.wait(2)
            with self.store.transaction("owner-b") as transaction:
                errors.append(("seen", transaction.is_blocked("sig-1")))

        first = threading.Thread(target=holder)
        second = threading.Thread(target=contender)
        first.start()
        second.start()
        first.join(5)
        second.join(5)

        self.assertEqual([], [error for error in errors if not isinstance(error, tuple)])
        self.assertIn(("seen", True), errors)

    def test_約定済みの注文は予約を解放する(self):
        self.assertTrue(self._reserve())
        self.store.mark_placed("sig-1", "owner-a", "order-1")

        remaining = self.store.reconcile(
            [_order("sig-1", status="FILLED_ALL")],
            owner_id="owner-a",
        )

        self.assertEqual([], remaining)
        self.assertFalse(self.store.is_blocked("sig-1"))

    def test_自分が作成した未送信の予約は解放できる(self):
        self.assertTrue(self._reserve())
        self.store.release("sig-1", "owner-a")

        self.assertFalse(self.store.is_blocked("sig-1"))

    def test_結果不明の予約は自分でも解放できない(self):
        self.assertTrue(self._reserve())
        self.store.mark_sent("sig-1", "owner-a")
        self.store.mark_unknown("sig-1", "owner-a", "切断")

        self.store.release("sig-1", "owner-a")

        self.assertTrue(self.store.is_blocked("sig-1"))

    def test_別の口座や環境の予約ストアとは共有しない(self):
        other = OrderIntentStore(str(self.state_dir), namespace="REAL|999|127.0.0.1:11111")
        self.assertTrue(self._reserve(store=other, owner_id="owner-b"))

        # 既定名前空間のストアは別予約として扱う。
        self.assertTrue(self._reserve())
        self.assertTrue(self.store.is_blocked("sig-1"))
        self.assertTrue(other.is_blocked("sig-1"))
        self.assertNotEqual(self.store.path, other.path)

    def test_同じ名前空間なら別インスタンスでも予約を共有する(self):
        other = OrderIntentStore(str(self.state_dir))
        self.assertTrue(self._reserve())
        self.assertTrue(other.is_blocked("sig-1"))

    def test_予約ファイルが壊れていたら停止する(self):
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text("{broken", encoding="utf-8")

        with self.assertRaises(MoomooTraderError):
            self.store.is_blocked("sig-1")


if __name__ == "__main__":
    unittest.main()
