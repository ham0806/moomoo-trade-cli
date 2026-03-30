import io
import json
import unittest
from unittest.mock import patch

from moomoo_trader.config import OpenDConfig
from moomoo_trader.engine import TradingEngine
from moomoo_trader.models import (
    BalanceSnapshot,
    MarketSnapshot,
    MaxTradeQtySnapshot,
    OrderLedgerEntry,
    PositionSnapshot,
    TradeIntent,
)
from moomoo_trader.trading_config import RiskLimits, SymbolConfig, TradingConfig


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
        self._open_orders = []
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
        return list(self._positions)

    def query_open_orders(self, refresh=False):
        return list(self._open_orders)

    def query_today_orders(self, now=None):
        return list(self._today_orders)

    def query_today_deals(self, now=None):
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
                update_time="2026-03-29T09:00:00",
                raw={},
            )
        return snapshots

    def query_max_trade_qtys(self, **kwargs):
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
        FakeGateway.initial_positions = []
        FakeGateway.initial_jp_acc_types = ["JP_GENERAL"]
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
                position_id="short-1",
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
            )
            summary = engine.run_once()
            engine.close()

        gateway = FakeGateway.last_instance
        assert gateway is not None
        self.assertEqual(["1"], summary["placed_orders"])
        self.assertEqual("JP_GENERAL_SHORT", gateway.max_trade_qty_queries[0]["jp_acc_type_name"])
        self.assertEqual("short-1", gateway.max_trade_qty_queries[0]["position_id"])
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
            return_value="secret",
        ):
            engine = TradingEngine(
                opend_config=self.opend_config,
                trading_config=trading_config,
                env_name="real",
                gateway_cls=FakeGateway,
                stdout=stdout,
                stderr=stderr,
            )
            engine.bootstrap()
            engine.close()

        self.assertEqual("secret", FakeGateway.last_instance.unlocked_password)


if __name__ == "__main__":
    unittest.main()
