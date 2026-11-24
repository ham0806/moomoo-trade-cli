import io
import json
import os
import shutil
import threading
import time
import unittest
import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from moomoo_trader.config import OpenDConfig
from moomoo_trader.engine import TradingEngine
from moomoo_trader.errors import (
    ApiCallError,
    DataNotReadyError,
    OrderResultUnknownError,
)
from moomoo_trader.models import (
    BalanceSnapshot,
    DealLedgerEntry,
    MarketSnapshot,
    MaxTradeQtySnapshot,
    OrderLedgerEntry,
    PositionSnapshot,
    TradeIntent,
    build_order_remark,
)
from moomoo_trader.time_utils import now_utc
from moomoo_trader.trading_config import RiskLimits, SymbolConfig, TradingConfig

# 注文予約は実ファイルへ書き込むため、リポジトリ内の作業用ディレクトリを使う。
_STATE_ROOT = Path(__file__).resolve().parents[1] / ".test-state"


def _make_state_dir() -> str:
    _STATE_ROOT.mkdir(parents=True, exist_ok=True)
    path = _STATE_ROOT / ("engine-" + uuid.uuid4().hex)
    path.mkdir()
    return str(path)


def _make_order(**overrides) -> OrderLedgerEntry:
    payload = {
        "order_id": "existing-1",
        "code": "7203",
        "market": "JP",
        "full_code": "JP.7203",
        "side": "BUY",
        "qty": 10.0,
        "price": 1000.0,
        "dealt_qty": 0.0,
        "dealt_avg_price": None,
        "status": "SUBMITTED",
        "order_type": "NORMAL",
        "time_in_force": "DAY",
        "create_time": now_utc() - timedelta(hours=1),
        "updated_time": None,
        "remark": None,
        "currency": "JPY",
        "session": None,
        "raw": {},
    }
    payload.update(overrides)
    return OrderLedgerEntry(**payload)


class FakeStrategy:
    def __init__(self, intents):
        self.strategy_id = "fake-strategy"
        self._intents = intents

    def evaluate(self, snapshots, portfolio_state, clock):
        return list(self._intents)


class FakeGateway:
    last_instance = None
    initial_positions = []
    initial_jp_acc_types = ["JP_GENERAL"]
    place_order_errors = []
    query_positions_error = None
    query_max_trade_qty_delay = 0.0
    deal_history_available = True
    positions_provider = None
    today_orders_provider = None
    today_deals_provider = None
    initial_open_orders = []
    query_max_trade_qty_hook = None

    def __init__(self, config, env_name, account_id, event_queue=None):
        self.config = config
        self.env_name = env_name
        self.account_id = account_id
        self.event_queue = event_queue
        self.connected = False
        self.closed = False
        self.unlocked_password = None
        self.placed_orders = []
        self.cancelled_orders = []
        self.max_trade_qty_queries = []
        self.place_calls = 0
        self.position_query_calls = 0
        self.deal_history_available = FakeGateway.deal_history_available
        self._open_orders = list(self.initial_open_orders)
        self._today_orders = []
        self._today_deals = []
        self._positions = list(self.initial_positions)
        self._jp_acc_types = list(self.initial_jp_acc_types)
        FakeGateway.last_instance = self

    def connect(self):
        self.connected = True

    def close(self):
        self.closed = True

    def unlock_trade(self, password):
        self.unlocked_password = password

    def query_balance(self, refresh=False):
        return BalanceSnapshot(
            acc_id=self.account_id,
            trd_env=self.env_name.upper(),
            currency="JPY",
            total_assets=100000.0,
            cash=100000.0,
            market_val=0.0,
            avl_withdrawal_cash=100000.0,
            power=100000.0,
            raw={},
        )

    def query_positions(self, refresh=False):
        self.position_query_calls += 1
        if FakeGateway.query_positions_error is not None:
            raise FakeGateway.query_positions_error
        if FakeGateway.positions_provider is not None:
            self._positions = list(FakeGateway.positions_provider(self))
        return list(self._positions)

    def query_open_orders(self, refresh=False):
        return list(self._open_orders)

    def query_today_orders(self, now=None, start_date=None, end_date=None):
        if FakeGateway.today_orders_provider is not None:
            return list(FakeGateway.today_orders_provider(self))
        return list(self._today_orders)

    def query_today_deals(self, now=None, start_date=None, end_date=None):
        if FakeGateway.today_deals_provider is not None:
            return list(FakeGateway.today_deals_provider(self))
        return list(self._today_deals)

    def get_market_snapshots(self, full_codes):
        snapshots = {}
        for full_code in full_codes:
            market, code = full_code.split(".", 1)
            snapshots[full_code] = MarketSnapshot(
                code=code,
                market=market,
                full_code=full_code,
                last_price=1000.0,
                bid_price=999.0,
                ask_price=1001.0,
                update_time=now_utc().isoformat(),
                raw={},
            )
        return snapshots

    def query_max_trade_qtys(self, **kwargs):
        if FakeGateway.query_max_trade_qty_delay:
            time.sleep(FakeGateway.query_max_trade_qty_delay)
        if FakeGateway.query_max_trade_qty_hook is not None:
            FakeGateway.query_max_trade_qty_hook(self, kwargs)
        self.max_trade_qty_queries.append(dict(kwargs))
        return MaxTradeQtySnapshot(
            max_cash_buy=100.0,
            max_position_sell=100.0,
            max_sell_short=100.0,
            max_buy_back=100.0,
            session=kwargs.get("session_name"),
            raw={},
        )

    def place_order(self, **kwargs):
        self.place_calls += 1
        if FakeGateway.place_order_errors:
            raise FakeGateway.place_order_errors.pop(0)
        order = OrderLedgerEntry(
            order_id=str(len(self.placed_orders) + 1),
            code=kwargs["full_code"].split(".", 1)[1],
            market=kwargs["full_code"].split(".", 1)[0],
            full_code=kwargs["full_code"],
            side=kwargs["side"],
            qty=float(kwargs["qty"]),
            price=float(kwargs["price"]),
            dealt_qty=0.0,
            dealt_avg_price=None,
            status="SUBMITTED",
            order_type=kwargs["order_type_name"],
            time_in_force=kwargs["time_in_force_name"],
            create_time=None,
            updated_time=None,
            remark=kwargs["remark"],
            currency="JPY",
            session=kwargs["session_name"],
            raw={},
        )
        self.placed_orders.append(order)
        self._open_orders.append(order)
        return order

    def cancel_order(self, order_id):
        self.cancelled_orders.append(order_id)

    def market_authorities(self):
        return {"JP": True, "US": True}

    def available_jp_acc_types(self):
        return list(self._jp_acc_types)

    def resolve_jp_acc_type(self, side):
        if side in {"SELL_SHORT", "BUY_BACK"}:
            for value in self._jp_acc_types:
                if value.endswith("_SHORT"):
                    return value
        return self._jp_acc_types[0]


class TradingEngineTest(unittest.TestCase):
    def setUp(self):
        self.state_dir = _make_state_dir()
        FakeGateway.initial_positions = []
        FakeGateway.initial_jp_acc_types = ["JP_GENERAL"]
        FakeGateway.place_order_errors = []
        FakeGateway.query_positions_error = None
        FakeGateway.query_max_trade_qty_delay = 0.0
        FakeGateway.deal_history_available = True
        FakeGateway.positions_provider = None
        FakeGateway.today_orders_provider = None
        FakeGateway.today_deals_provider = None
        FakeGateway.initial_open_orders = []
        FakeGateway.query_max_trade_qty_hook = None
        self.opend_config = OpenDConfig(host="127.0.0.1", port=11111)
        self.trading_config = TradingConfig(
            account_id="123",
            symbols=[
                SymbolConfig(
                    code="7203",
                    market="JP",
                    max_position_qty=100,
                    max_order_qty=100,
                    allowed_sides=["BUY", "SELL", "SELL_SHORT", "BUY_BACK"],
                    allowed_order_types=["NORMAL", "MARKET"],
                    allowed_time_in_force=["DAY", "GTC"],
                    allowed_sessions=["NONE"],
                )
            ],
            strategy="noop",
            poll_interval_seconds=1,
            allow_live=False,
            credential_name=None,
            risk_limits=RiskLimits(
                max_daily_orders=5,
                max_daily_notional=100000.0,
                max_open_orders_per_symbol=1,
                cancel_after_seconds=300,
            ),
            strategy_options={},
        )

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)
        try:
            os.rmdir(str(_STATE_ROOT))
        except OSError:
            pass

    def test_once実行で注文を発行できる(self):
        strategy = FakeStrategy(
            [
                TradeIntent(
                    code="7203",
                    market="JP",
                    side="BUY",
                    qty=10,
                    limit_price=1000,
                    strategy_id="fake-strategy",
                    order_type="MARKET",
                    time_in_force="GTC",
                )
            ]
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = TradingEngine(
                opend_config=self.opend_config,
                trading_config=self.trading_config,
                env_name="simulate",
                gateway_cls=FakeGateway,
                stdout=stdout,
                stderr=stderr,
                state_dir=self.state_dir,
            )
            summary = engine.run_once()
            engine.close()

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertEqual(["1"], summary["placed_orders"])
        self.assertEqual(1, len(gateway.placed_orders))
        self.assertEqual("MARKET", gateway.placed_orders[0].order_type)
        self.assertEqual("GTC", gateway.placed_orders[0].time_in_force)
        self.assertEqual("JP_GENERAL", gateway.max_trade_qty_queries[0]["jp_acc_type_name"])
        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertTrue(any(event["event"] == "order_submitted" for event in events))

    def test_short建玉の買戻しでposition_idとshort口座を渡す(self):
        FakeGateway.initial_positions = [
            PositionSnapshot(
                code="7203",
                market="JP",
                full_code="JP.7203",
                qty=10.0,
                can_sell_qty=0.0,
                position_side="SHORT",
                position_id=12345,
                cost_price=1000.0,
                market_val=10000.0,
                pl_val=0.0,
                currency="JPY",
                raw={},
            )
        ]
        FakeGateway.initial_jp_acc_types = ["JP_GENERAL_SHORT"]
        strategy = FakeStrategy(
            [
                TradeIntent(
                    code="7203",
                    market="JP",
                    side="BUY_BACK",
                    qty=5,
                    limit_price=1000,
                    strategy_id="fake-strategy",
                    order_type="NORMAL",
                    time_in_force="DAY",
                )
            ]
        )

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = TradingEngine(
                opend_config=self.opend_config,
                trading_config=self.trading_config,
                env_name="simulate",
                gateway_cls=FakeGateway,
                state_dir=self.state_dir,
            )
            summary = engine.run_once()
            engine.close()

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertEqual(["1"], summary["placed_orders"])
        self.assertEqual("JP_GENERAL_SHORT", gateway.max_trade_qty_queries[0]["jp_acc_type_name"])
        self.assertEqual(12345, gateway.max_trade_qty_queries[0]["position_id"])
        self.assertEqual("BUY_BACK", gateway.placed_orders[0].side)

    def test_real_allow_live_trueならアンロックする(self):
        strategy = FakeStrategy([])
        stdout = io.StringIO()
        stderr = io.StringIO()
        trading_config = TradingConfig(
            account_id="123",
            symbols=self.trading_config.symbols,
            strategy="noop",
            poll_interval_seconds=1,
            allow_live=True,
            credential_name="main-live",
            risk_limits=self.trading_config.risk_limits,
            strategy_options={},
        )

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy), patch(
            "moomoo_trader.engine.get_trade_password",
            return_value="mock-password",
        ):
            engine = TradingEngine(
                opend_config=self.opend_config,
                trading_config=trading_config,
                env_name="real",
                gateway_cls=FakeGateway,
                stdout=stdout,
                stderr=stderr,
                state_dir=self.state_dir,
            )
            engine.bootstrap()
            engine.close()

        self.assertEqual("mock-password", FakeGateway.last_instance.unlocked_password)

    def _build_engine(self, strategy, stdout=None, stderr=None, sleep_fn=None):
        kwargs = {
            "opend_config": self.opend_config,
            "trading_config": self.trading_config,
            "env_name": "simulate",
            "gateway_cls": FakeGateway,
            "state_dir": self.state_dir,
        }
        if stdout is not None:
            kwargs["stdout"] = stdout
        if stderr is not None:
            kwargs["stderr"] = stderr
        if sleep_fn is not None:
            kwargs["sleep_fn"] = sleep_fn
        return TradingEngine(**kwargs)

    def test_設定にない銘柄のintentはAPIを呼ばずに拒否する(self):
        strategy = FakeStrategy(
            [
                TradeIntent(
                    code="AAPL",
                    market="US",
                    side="BUY",
                    qty=10,
                    limit_price=100,
                    strategy_id="fake-strategy",
                )
            ]
        )
        stdout = io.StringIO()

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy, stdout=stdout)
            summary = engine.run_once()
            engine.close()

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertEqual([], summary["placed_orders"])
        self.assertEqual(1, len(summary["rejected_orders"]))
        self.assertIn("設定ファイルに存在しない", summary["rejected_orders"][0]["reason"])
        self.assertEqual([], gateway.max_trade_qty_queries)
        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertTrue(any(event["event"] == "intent_rejected" for event in events))

    def test_limit_priceなしのintentは拒否して残りを処理する(self):
        strategy = FakeStrategy(
            [
                TradeIntent(
                    code="7203",
                    market="JP",
                    side="BUY",
                    qty=10,
                    limit_price=None,
                    strategy_id="fake-strategy",
                ),
                TradeIntent(
                    code="7203",
                    market="JP",
                    side="BUY",
                    qty=10,
                    limit_price=1000,
                    strategy_id="fake-strategy",
                    order_type="MARKET",
                    time_in_force="GTC",
                ),
            ]
        )

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            summary = engine.run_once()
            engine.close()

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertEqual(["1"], summary["placed_orders"])
        self.assertEqual(1, len(summary["rejected_orders"]))
        self.assertEqual(1, len(gateway.max_trade_qty_queries))

    def test_数量照会が失敗しても残りのintentを処理する(self):
        strategy = FakeStrategy(
            [
                TradeIntent(
                    code="7203",
                    market="JP",
                    side="BUY",
                    qty=10,
                    limit_price=1000,
                    strategy_id="fake-strategy",
                    order_type="MARKET",
                    time_in_force="GTC",
                ),
                TradeIntent(
                    code="7203",
                    market="JP",
                    side="BUY",
                    qty=20,
                    limit_price=1000,
                    strategy_id="fake-strategy",
                    order_type="MARKET",
                    time_in_force="GTC",
                ),
            ]
        )
        original = FakeGateway.query_max_trade_qtys
        calls = []

        def flaky(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise ApiCallError("最大取引数量取得に失敗しました")
            return original(self, **kwargs)

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy), patch.object(
            FakeGateway, "query_max_trade_qtys", flaky
        ):
            engine = self._build_engine(strategy)
            summary = engine.run_once()
            engine.close()

        self.assertEqual(2, len(calls))
        self.assertEqual(["1"], summary["placed_orders"])
        self.assertEqual(1, len(summary["rejected_orders"]))
        self.assertIn("最大取引数量取得", summary["rejected_orders"][0]["reason"])

    def test_常駐ループは一時エラーを記録して継続する(self):
        strategy = FakeStrategy([])
        stdout = io.StringIO()
        stderr = io.StringIO()
        sleeps = []
        outcomes = [
            ApiCallError("一時的な API エラー"),
            ApiCallError("一時的な API エラー"),
            {"placed_orders": [], "rejected_orders": []},
            KeyboardInterrupt(),
        ]

        def fake_run_once():
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(
                strategy,
                stdout=stdout,
                stderr=stderr,
                sleep_fn=sleeps.append,
            )
            engine.run_once = fake_run_once
            engine.start()

        self.assertEqual([], outcomes)
        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        errors = [event for event in events if event["event"] == "engine_cycle_error"]
        self.assertEqual(2, len(errors))
        self.assertEqual(2, errors[-1]["consecutive_failures"])
        self.assertEqual([1, 2, 1], sleeps)
        self.assertIn("停止要求", stderr.getvalue())
        self.assertTrue(FakeGateway.last_instance.closed)

    def test_連続失敗の上限で常駐を終了する(self):
        strategy = FakeStrategy([])
        stdout = io.StringIO()
        stderr = io.StringIO()

        def fake_run_once():
            raise ApiCallError("永続的な API エラー")

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(
                strategy,
                stdout=stdout,
                stderr=stderr,
                sleep_fn=lambda seconds: None,
            )
            engine.run_once = fake_run_once
            with self.assertRaises(ApiCallError):
                engine.start()

        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        errors = [event for event in events if event["event"] == "engine_cycle_error"]
        self.assertEqual(engine.MAX_CONSECUTIVE_CYCLE_FAILURES, len(errors))
        self.assertIn("連続", stderr.getvalue())
        self.assertTrue(FakeGateway.last_instance.closed)

    def _build_engine_with(self, trading_config=None, env_name="simulate"):
        return TradingEngine(
            opend_config=self.opend_config,
            trading_config=trading_config or self.trading_config,
            env_name=env_name,
            gateway_cls=FakeGateway,
            state_dir=self.state_dir,
        )

    def _buy_intent(self, qty, **overrides):
        payload = {
            "code": "7203",
            "market": "JP",
            "side": "BUY",
            "qty": qty,
            "limit_price": 1000,
            "strategy_id": "fake-strategy",
            "order_type": "NORMAL",
            "time_in_force": "DAY",
        }
        payload.update(overrides)
        return TradeIntent(**payload)

    def test_allow_live_falseでは古い注文を取り消さない(self):
        strategy = FakeStrategy([])
        trading_config = replace(self.trading_config, allow_live=False)
        managed_remark = build_order_remark("fake-strategy", "deadbeefdeadbeef")

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine_with(trading_config, env_name="real")
            engine.bootstrap()
            engine.order_ledger.upsert_order(_make_order(order_id="managed", remark=managed_remark))
            engine.order_ledger.upsert_order(_make_order(order_id="manual", side="SELL"))
            engine._cancel_stale_orders(now_utc())
            engine.close()

        self.assertEqual([], FakeGateway.last_instance.cancelled_orders)

    def test_allow_live_trueでは管理下の古い注文だけを取り消す(self):
        strategy = FakeStrategy([])
        trading_config = replace(
            self.trading_config,
            allow_live=True,
            credential_name="main-live",
        )
        managed_remark = build_order_remark("fake-strategy", "deadbeefdeadbeef")

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy), patch(
            "moomoo_trader.engine.get_trade_password",
            return_value="mock-password",
        ):
            engine = self._build_engine_with(trading_config, env_name="real")
            engine.bootstrap()
            engine.order_ledger.upsert_order(_make_order(order_id="managed", remark=managed_remark))
            engine.order_ledger.upsert_order(_make_order(order_id="manual", side="SELL"))
            engine.order_ledger.upsert_order(
                _make_order(
                    order_id="other-symbol",
                    code="AAPL",
                    market="US",
                    full_code="US.AAPL",
                    remark=managed_remark,
                )
            )
            engine._cancel_stale_orders(now_utc())
            engine.close()

        self.assertEqual(["managed"], FakeGateway.last_instance.cancelled_orders)

    def test_発注結果不明の注文は照合まで再送しない(self):
        strategy = FakeStrategy([self._buy_intent(10)])
        FakeGateway.place_order_errors = [OrderResultUnknownError("応答なし")]

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            with self.assertRaises(OrderResultUnknownError):
                engine.run_once()
            second = engine.run_once()
            engine.close()

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertEqual(1, gateway.place_calls)
        self.assertEqual([], second["placed_orders"])
        self.assertIn("再発注", second["rejected_orders"][0]["reason"])

    def test_発注結果不明の予約は別プロセスでも再送を止める(self):
        strategy = FakeStrategy([self._buy_intent(10)])
        FakeGateway.place_order_errors = [OrderResultUnknownError("応答なし")]

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            first_engine = self._build_engine(strategy)
            with self.assertRaises(OrderResultUnknownError):
                first_engine.run_once()
            first_gateway = FakeGateway.last_instance
            first_engine.close()

            second_engine = self._build_engine(strategy)
            summary = second_engine.run_once()
            second_engine.close()

        gateway = FakeGateway.last_instance
        assert gateway is not None
        assert first_gateway is not None
        self.assertEqual(1, first_gateway.place_calls)
        self.assertEqual(0, gateway.place_calls)
        self.assertEqual([], summary["placed_orders"])
        self.assertIn("照合中", summary["rejected_orders"][0]["reason"])

    def test_別エンジンは受理済みの予約を古い注文一覧で削除できない(self):
        strategy = FakeStrategy([self._buy_intent(10)])

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            first_engine = self._build_engine(strategy)
            first_summary = first_engine.run_once()
            first_engine.close()

            second_engine = self._build_engine(strategy)
            second_summary = second_engine.run_once()
            second_engine.close()

        self.assertEqual(["1"], first_summary["placed_orders"])
        self.assertEqual([], second_summary["placed_orders"])
        self.assertEqual(0, FakeGateway.last_instance.place_calls)

    def test_別口座の予約は自口座の数量金額判定へ混入しない(self):
        strategy = FakeStrategy([self._buy_intent(10)])

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            other_account = replace(self.trading_config, account_id="999")
            first_engine = self._build_engine_with(other_account)
            second_engine = self._build_engine_with(self.trading_config)
            self.assertNotEqual(
                first_engine.intent_store.path,
                second_engine.intent_store.path,
            )
            first_engine.run_once()
            first_engine.close()

            summary = second_engine.run_once()
            second_engine.close()

        self.assertEqual(["1"], summary["placed_orders"])

    def test_前方一致する別戦略の注文を取り消さない(self):
        strategy = FakeStrategy([])
        strategy.strategy_id = "alpha-live"
        legacy_remark = "moomoo-trade-cli|strategy=alpha|intent=deadbeefdeadbeef"
        new_remark = build_order_remark("alpha", "deadbeefdeadbeef")

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            engine.bootstrap()
            legacy_order = _make_order(order_id="legacy", remark=legacy_remark)
            new_order = _make_order(order_id="new", remark=new_remark)
            engine.close()

        self.assertFalse(engine._is_engine_managed_order(legacy_order))
        self.assertFalse(engine._is_engine_managed_order(new_order))
        self.assertTrue(
            engine._is_engine_managed_order(
                _make_order(
                    order_id="own",
                    remark=build_order_remark("alpha-live", "deadbeefdeadbeef"),
                )
            )
        )

    def test_照会済みの未約定売りを売却可能数量から二重控除しない(self):
        strategy = FakeStrategy(
            [
                TradeIntent(
                    code="7203",
                    market="JP",
                    side="SELL",
                    qty=40,
                    limit_price=1000,
                    strategy_id="fake-strategy",
                )
            ]
        )
        FakeGateway.initial_positions = [
            PositionSnapshot(
                code="7203",
                market="JP",
                full_code="JP.7203",
                qty=100.0,
                can_sell_qty=40.0,
                position_side="LONG",
                position_id=1,
                cost_price=1000.0,
                market_val=100000.0,
                pl_val=0.0,
                currency="JPY",
                raw={},
            )
        ]
        FakeGateway.initial_open_orders = [
            _make_order(
                order_id="open-sell",
                side="SELL",
                qty=60.0,
                create_time=now_utc() - timedelta(hours=1),
            )
        ]

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            summary = engine.run_once()
            engine.close()

        self.assertEqual(["1"], summary["placed_orders"])
        self.assertEqual(1, FakeGateway.last_instance.place_calls)

    def test_訂正後の注文価格で日次金額を再評価する(self):
        strategy = FakeStrategy([self._buy_intent(10)])
        trading_config = replace(
            self.trading_config,
            risk_limits=replace(
                self.trading_config.risk_limits,
                max_open_orders_per_symbol=5,
            ),
        )

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine_with(trading_config)
            engine.run_once()
            engine.event_queue.put(
                {
                    "type": "order",
                    "payload": {
                        "order_id": "1",
                        "code": "7203",
                        "market": "JP",
                        "full_code": "JP.7203",
                        "trd_side": "BUY",
                        "qty": 10,
                        "price": 20000,
                        "dealt_qty": 0,
                        "order_status": "SUBMITTED",
                        "remark": build_order_remark(
                            "fake-strategy",
                            self._buy_intent(10).intent_signature,
                        ),
                    },
                }
            )
            strategy._intents = [self._buy_intent(60)]
            summary = engine.run_once()
            daily_notional = engine._daily_notional(engine.intent_store.active_entries())
            engine.close()

        self.assertGreaterEqual(daily_notional, 200000.0)
        self.assertEqual([], summary["placed_orders"])
        self.assertIn("当日想定約定代金", summary["rejected_orders"][0]["reason"])

    def test_ポジション照会後の約定を未反映数量として予約する(self):
        strategy = FakeStrategy([self._buy_intent(100)])
        trading_config = replace(
            self.trading_config,
            risk_limits=replace(
                self.trading_config.risk_limits,
                max_daily_notional=1000000.0,
            ),
        )

        def filled_order(now=None):
            return [
                _make_order(
                    order_id="filled-1",
                    qty=100.0,
                    dealt_qty=100.0,
                    status="FILLED_ALL",
                    create_time=now_utc(),
                )
            ]

        def filled_deal(now=None):
            return [
                DealLedgerEntry(
                    deal_id="deal-1",
                    order_id="filled-1",
                    code="7203",
                    market="JP",
                    full_code="JP.7203",
                    side="BUY",
                    qty=100.0,
                    price=1000.0,
                    create_time=now_utc(),
                    status="FILLED_ALL",
                    raw={},
                )
            ]

        FakeGateway.positions_provider = lambda gateway: []
        FakeGateway.today_orders_provider = filled_order
        FakeGateway.today_deals_provider = filled_deal

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine_with(trading_config)
            summary = engine.run_once()
            engine.close()

        self.assertEqual([], summary["placed_orders"])
        self.assertIn("保有上限", summary["rejected_orders"][0]["reason"])

    def test_全約定通知だけ届いても保有上限を超える買いを拒否する(self):
        strategy = FakeStrategy([self._buy_intent(100)])
        trading_config = replace(
            self.trading_config,
            risk_limits=replace(
                self.trading_config.risk_limits,
                max_daily_notional=1000000.0,
            ),
        )

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine_with(trading_config)
            engine.bootstrap()
            engine.event_queue.put(
                {
                    "type": "order",
                    "payload": {
                        "order_id": "old-order",
                        "code": "7203",
                        "market": "JP",
                        "full_code": "JP.7203",
                        "trd_side": "BUY",
                        "qty": 100,
                        "price": 1000,
                        "dealt_qty": 100,
                        "order_status": "FILLED_ALL",
                        "remark": build_order_remark("fake-strategy", "aaaabbbbccccdddd"),
                    },
                }
            )
            summary = engine.run_once()
            engine.close()

        self.assertEqual([], summary["placed_orders"])
        self.assertIn("保有上限", summary["rejected_orders"][0]["reason"])
        self.assertEqual(0, FakeGateway.last_instance.place_calls)

    def test_別口座や別環境の約定通知を台帳へ混入させない(self):
        strategy = FakeStrategy([])

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            engine.bootstrap()
            payload = {
                "deal_id": "deal-1",
                "order_id": "order-1",
                "code": "7203",
                "market": "JP",
                "full_code": "JP.7203",
                "trd_side": "BUY",
                "qty": 10,
                "price": 1000,
                "create_time": now_utc().isoformat(),
                "status": "FILLED_ALL",
            }
            engine.event_queue.put(
                {"type": "deal", "payload": dict(payload, acc_id="999", trd_env="SIMULATE")}
            )
            engine.event_queue.put(
                {"type": "deal", "payload": dict(payload, acc_id="123", trd_env="REAL")}
            )
            engine.run_once()
            self.assertEqual({}, engine.position_ledger.all())

            engine.event_queue.put(
                {"type": "deal", "payload": dict(payload, acc_id="123", trd_env="SIMULATE")}
            )
            engine.run_once()
            position = engine.position_ledger.get("JP.7203")
            engine.close()

        assert position is not None
        self.assertEqual(10.0, position.qty)

    def test_データ未準備なら起動せず発注しない(self):
        strategy = FakeStrategy([self._buy_intent(10)])
        FakeGateway.query_positions_error = DataNotReadyError("This data is not ready yet")

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            with self.assertRaises(DataNotReadyError):
                engine.run_once()
            engine.close()

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertEqual(0, gateway.place_calls)
        self.assertEqual([], gateway.placed_orders)

    def test_起動後も定期照会して古い保有状態を使わない(self):
        strategy = FakeStrategy([self._buy_intent(10)])
        FakeGateway.initial_positions = [
            PositionSnapshot(
                code="7203",
                market="JP",
                full_code="JP.7203",
                qty=100.0,
                can_sell_qty=100.0,
                position_side="LONG",
                position_id=1,
                cost_price=1000.0,
                market_val=100000.0,
                pl_val=0.0,
                currency="JPY",
                raw={},
            )
        ]
        trading_config = replace(self.trading_config, state_sync_interval_seconds=0)

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine_with(trading_config)
            summary = engine.run_once()
            engine.close()

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertGreaterEqual(gateway.position_query_calls, 2)
        self.assertEqual([], summary["placed_orders"])
        self.assertIn("保有上限", summary["rejected_orders"][0]["reason"])

    def test_数量照会中に有効期限が切れた注文は発注しない(self):
        expires_at = now_utc() + timedelta(seconds=1)
        strategy = FakeStrategy([self._buy_intent(10, expires_at=expires_at)])
        FakeGateway.query_max_trade_qty_delay = 1.1

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            summary = engine.run_once()
            engine.close()

        self.assertEqual([], summary["placed_orders"])
        self.assertIn("有効期限切れ", summary["rejected_orders"][0]["reason"])
        self.assertEqual(0, FakeGateway.last_instance.place_calls)

    def test_注文通知の訂正で当日注文の金額を更新する(self):
        strategy = FakeStrategy([])

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            engine.bootstrap()
            for price in (1000, 20000):
                engine.event_queue.put(
                    {
                        "type": "order",
                        "payload": {
                            "order_id": "order-1",
                            "code": "7203",
                            "market": "JP",
                            "full_code": "JP.7203",
                            "trd_side": "BUY",
                            "qty": 10,
                            "price": price,
                            "dealt_qty": 0,
                            "order_status": "SUBMITTED",
                        },
                    }
                )
            engine.run_once()
            engine.close()

        self.assertEqual(1, len(engine.today_orders))
        self.assertEqual(20000.0, engine.today_orders[0].price)

    def test_別エンジンの同時発注を古い数量で通過させない(self):
        strategy_a = FakeStrategy([self._buy_intent(60)])
        strategy_b = FakeStrategy([self._buy_intent(70)])
        trading_config = replace(
            self.trading_config,
            risk_limits=replace(
                self.trading_config.risk_limits,
                max_open_orders_per_symbol=5,
                max_daily_notional=1000000.0,
            ),
        )
        result = {}

        with patch(
            "moomoo_trader.engine.load_strategy",
            side_effect=[strategy_a, strategy_b],
        ):
            engine_a = self._build_engine_with(trading_config)
            engine_b = self._build_engine_with(trading_config)
            engine_b.bootstrap()

            def run_b():
                result["summary"] = engine_b.run_once()

            def hook(gateway, kwargs):
                if gateway is not engine_a.gateway:
                    return
                if result.get("started"):
                    return
                result["started"] = True
                thread = threading.Thread(target=run_b)
                result["thread"] = thread
                thread.start()
                time.sleep(0.5)

            FakeGateway.query_max_trade_qty_hook = hook
            summary_a = engine_a.run_once()
            result["thread"].join(5)
            engine_a.close()
            engine_b.close()

        self.assertEqual(["1"], summary_a["placed_orders"])
        self.assertEqual([], result["summary"]["placed_orders"])
        self.assertIn("保有上限", result["summary"]["rejected_orders"][0]["reason"])

    def test_時計差があっても照会中の約定を未反映として予約する(self):
        strategy = FakeStrategy([self._buy_intent(100)])
        trading_config = replace(
            self.trading_config,
            risk_limits=replace(
                self.trading_config.risk_limits,
                max_daily_notional=1000000.0,
            ),
        )

        FakeGateway.positions_provider = lambda gateway: []
        FakeGateway.today_orders_provider = lambda now=None: [
            _make_order(
                order_id="filled-1",
                qty=100.0,
                dealt_qty=100.0,
                status="FILLED_ALL",
                create_time=now_utc(),
            )
        ]
        FakeGateway.today_deals_provider = lambda now=None: [
            DealLedgerEntry(
                deal_id="deal-1",
                order_id="filled-1",
                code="7203",
                market="JP",
                full_code="JP.7203",
                side="BUY",
                qty=100.0,
                price=1000.0,
                # 時計差・時刻精度で照会時刻より前に見える約定を再現する。
                create_time=now_utc() - timedelta(seconds=60),
                status="FILLED_ALL",
                raw={},
            )
        ]

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine_with(trading_config)
            summary = engine.run_once()
            engine.close()

        self.assertEqual([], summary["placed_orders"])
        self.assertIn("保有上限", summary["rejected_orders"][0]["reason"])

    def test_約定履歴が使えない場合は未反映数量を予約する(self):
        strategy = FakeStrategy([self._buy_intent(100)])
        trading_config = replace(
            self.trading_config,
            risk_limits=replace(
                self.trading_config.risk_limits,
                max_daily_notional=1000000.0,
            ),
        )
        FakeGateway.deal_history_available = False
        FakeGateway.positions_provider = lambda gateway: []
        FakeGateway.today_orders_provider = lambda now=None: [
            _make_order(
                order_id="filled-1",
                qty=100.0,
                dealt_qty=100.0,
                status="FILLED_ALL",
                create_time=now_utc(),
            )
        ]

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine_with(trading_config)
            summary = engine.run_once()
            engine.close()

        self.assertEqual([], summary["placed_orders"])
        self.assertIn("保有上限", summary["rejected_orders"][0]["reason"])

    def test_同期中に作られた売り注文を売却可能数量から二重控除しない(self):
        strategy = FakeStrategy(
            [
                TradeIntent(
                    code="7203",
                    market="JP",
                    side="SELL",
                    qty=40,
                    limit_price=1000,
                    strategy_id="fake-strategy",
                )
            ]
        )

        def positions(gateway):
            # ポジション照会の実行中に売り注文が作られるケース。
            gateway._open_orders.append(
                _make_order(
                    order_id="sync-sell",
                    side="SELL",
                    qty=60.0,
                    create_time=now_utc(),
                )
            )
            return [
                PositionSnapshot(
                    code="7203",
                    market="JP",
                    full_code="JP.7203",
                    qty=100.0,
                    can_sell_qty=40.0,
                    position_side="LONG",
                    position_id=1,
                    cost_price=1000.0,
                    market_val=100000.0,
                    pl_val=0.0,
                    currency="JPY",
                    raw={},
                )
            ]

        FakeGateway.positions_provider = positions

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            summary = engine.run_once()
            engine.close()

        self.assertEqual(["1"], summary["placed_orders"])

    def test_約定通知が注文通知より先でも適用済み数量を失わない(self):
        strategy = FakeStrategy([])
        FakeGateway.initial_jp_acc_types = ["JP_GENERAL_SHORT"]
        FakeGateway.initial_positions = [
            PositionSnapshot(
                code="7203",
                market="JP",
                full_code="JP.7203",
                qty=10.0,
                can_sell_qty=0.0,
                position_side="SHORT",
                position_id=1,
                cost_price=1000.0,
                market_val=10000.0,
                pl_val=0.0,
                currency="JPY",
                raw={},
            )
        ]

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            engine.bootstrap()
            engine.event_queue.put(
                {
                    "type": "deal",
                    "payload": {
                        "deal_id": "deal-1",
                        "order_id": "buy-back-1",
                        "code": "7203",
                        "market": "JP",
                        "full_code": "JP.7203",
                        "trd_side": "BUY_BACK",
                        "qty": 5,
                        "price": 1000,
                        "create_time": now_utc().isoformat(),
                        "status": "FILLED_ALL",
                    },
                }
            )
            engine.event_queue.put(
                {
                    "type": "order",
                    "payload": {
                        "order_id": "buy-back-1",
                        "code": "7203",
                        "market": "JP",
                        "full_code": "JP.7203",
                        "trd_side": "BUY_BACK",
                        "qty": 5,
                        "price": 1000,
                        "dealt_qty": 5,
                        "order_status": "FILLED_ALL",
                    },
                }
            )
            engine.run_once()
            order = next(
                order
                for order in engine.order_ledger.all_orders()
                if order.order_id == "buy-back-1"
            )
            applied = order.applied_dealt_qty
            position = engine.position_ledger.get("JP.7203")
            strategy._intents = [
                TradeIntent(
                    code="7203",
                    market="JP",
                    side="BUY_BACK",
                    qty=5,
                    limit_price=1000,
                    strategy_id="fake-strategy",
                )
            ]
            summary = engine.run_once()
            engine.close()

        self.assertEqual(5.0, applied)
        assert position is not None
        self.assertEqual(5.0, position.qty)
        self.assertEqual(["1"], summary["placed_orders"])

    def test_前日部分約定のGTC注文を未反映数量として二重計上しない(self):
        strategy = FakeStrategy([])
        FakeGateway.initial_positions = [
            PositionSnapshot(
                code="7203",
                market="JP",
                full_code="JP.7203",
                qty=5.0,
                can_sell_qty=0.0,
                position_side="SHORT",
                position_id=1,
                cost_price=1000.0,
                market_val=5000.0,
                pl_val=0.0,
                currency="JPY",
                raw={},
            )
        ]
        FakeGateway.initial_open_orders = [
            _make_order(
                order_id="gtc-1",
                side="BUY_BACK",
                qty=10.0,
                dealt_qty=5.0,
                status="FILLED_PART",
                create_time=now_utc() - timedelta(days=1),
            )
        ]

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            engine.bootstrap()
            order = next(
                order for order in engine.order_ledger.all_orders() if order.order_id == "gtc-1"
            )
            applied = order.applied_dealt_qty
            engine.close()

        self.assertEqual(5.0, applied)

    def test_旧形式の同一戦略の注文は引き続き管理する(self):
        strategy = FakeStrategy([])
        legacy_remark = "moomoo-trade-cli|strategy=fake-strategy|intent=deadbeefdeadbeef"

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy):
            engine = self._build_engine(strategy)
            engine.close()

        self.assertTrue(
            engine._is_engine_managed_order(_make_order(order_id="legacy", remark=legacy_remark))
        )

    def test_市場日付が変わったら日次台帳を切り替える(self):
        strategy = FakeStrategy([])
        stdout = io.StringIO()
        previous = _make_order(order_id="previous-day", qty=10.0, price=1000.0)
        current = _make_order(order_id="current-day", qty=5.0, price=2000.0)
        FakeGateway.current_orders = [previous]
        FakeGateway.today_orders_provider = lambda gateway: list(FakeGateway.current_orders)

        with patch("moomoo_trader.engine.load_strategy", return_value=strategy), patch(
            "moomoo_trader.engine.market_today_text",
            side_effect=["2026-03-30", "2026-03-31"],
        ):
            engine = self._build_engine(strategy, stdout=stdout)
            engine.bootstrap()
            FakeGateway.current_orders = [current]
            engine._sync_state(refresh=True)
            today_order_ids = [order.order_id for order in engine.today_orders]
            daily_notional = engine._daily_notional(engine.intent_store.active_entries())
            engine.close()

        self.assertEqual(["current-day"], today_order_ids)
        self.assertEqual(10000.0, daily_notional)
        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertTrue(any(event["event"] == "daily_ledger_rolled" for event in events))


if __name__ == "__main__":
    unittest.main()
