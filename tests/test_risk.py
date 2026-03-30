import unittest

from moomoo_trader.models import (
    MaxTradeQtySnapshot,
    OrderLedgerEntry,
    PortfolioState,
    PositionSnapshot,
    TradeIntent,
)
from moomoo_trader.risk import RiskManager
from moomoo_trader.trading_config import RiskLimits, SymbolConfig


def _build_symbol(**overrides) -> SymbolConfig:
    payload = {
        "code": "7203",
        "market": "JP",
        "max_position_qty": 100,
        "max_order_qty": 100,
        "allowed_sides": ["BUY", "SELL", "SELL_SHORT", "BUY_BACK"],
        "allowed_order_types": ["NORMAL", "MARKET"],
        "allowed_time_in_force": ["DAY", "GTC"],
        "allowed_sessions": ["RTH"],
    }
    payload.update(overrides)
    return SymbolConfig(**payload)


def _build_order(**overrides) -> OrderLedgerEntry:
    payload = {
        "order_id": "1",
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
        "create_time": None,
        "updated_time": None,
        "remark": "moomoo-trade-cli|strategy=test|intent=dup",
        "currency": "JPY",
        "session": "RTH",
        "raw": {},
    }
    payload.update(overrides)
    return OrderLedgerEntry(**payload)


def _build_position(**overrides) -> PositionSnapshot:
    payload = {
        "code": "7203",
        "market": "JP",
        "full_code": "JP.7203",
        "qty": 20.0,
        "can_sell_qty": 20.0,
        "position_side": "LONG",
        "position_id": "p-1",
        "cost_price": 1000.0,
        "market_val": 20000.0,
        "pl_val": 0.0,
        "currency": "JPY",
        "raw": {},
    }
    payload.update(overrides)
    return PositionSnapshot(**payload)


def _build_qtys(**overrides) -> MaxTradeQtySnapshot:
    payload = {
        "max_cash_buy": 100.0,
        "max_position_sell": 100.0,
        "max_sell_short": 100.0,
        "max_buy_back": 100.0,
        "session": None,
        "raw": {},
    }
    payload.update(overrides)
    return MaxTradeQtySnapshot(**payload)


class RiskManagerTest(unittest.TestCase):
    def setUp(self):
        self.symbol = _build_symbol()
        self.risk_limits = RiskLimits(
            max_daily_orders=5,
            max_daily_notional=100000.0,
            max_open_orders_per_symbol=1,
            cancel_after_seconds=300,
        )

    def _build_manager(self, **overrides) -> RiskManager:
        payload = {
            "allow_live": True,
            "env_name": "simulate",
            "market_authorities": {"JP": True, "US": True},
            "symbol_map": {self.symbol.full_code: self.symbol},
            "risk_limits": self.risk_limits,
            "available_jp_acc_types": ["JP_GENERAL"],
        }
        payload.update(overrides)
        return RiskManager(**payload)

    def test_allow_live_falseの実口座は拒否する(self):
        manager = self._build_manager(allow_live=False, env_name="real")
        portfolio = PortfolioState(balance=None, positions={}, open_orders=[])
        intent = TradeIntent(
            code="7203",
            market="JP",
            side="BUY",
            qty=10,
            limit_price=1000,
            strategy_id="test",
        )

        decision = manager.evaluate(
            intent,
            portfolio,
            _build_qtys(),
            daily_order_count=0,
            daily_notional=0.0,
        )

        self.assertFalse(decision.accepted)
        self.assertIn("allow_live=false", decision.reason)

    def test_重複注文意図を拒否する(self):
        manager = self._build_manager()
        intent = TradeIntent(
            code="7203",
            market="JP",
            side="BUY",
            qty=10,
            limit_price=1000,
            strategy_id="test",
        )
        portfolio = PortfolioState(
            balance=None,
            positions={},
            open_orders=[
                _build_order(
                    remark="moomoo-trade-cli|strategy=test|intent={}".format(
                        intent.intent_signature
                    )
                )
            ],
        )

        decision = manager.evaluate(
            intent,
            portfolio,
            _build_qtys(),
            daily_order_count=1,
            daily_notional=10000.0,
        )

        self.assertFalse(decision.accepted)
        self.assertIn("重複", decision.reason)

    def test_保有上限を超える買いを拒否する(self):
        manager = self._build_manager()
        intent = TradeIntent(
            code="7203",
            market="JP",
            side="BUY",
            qty=90,
            limit_price=1000,
            strategy_id="test",
        )
        portfolio = PortfolioState(
            balance=None,
            positions={self.symbol.full_code: _build_position()},
            open_orders=[],
        )

        decision = manager.evaluate(
            intent,
            portfolio,
            _build_qtys(),
            daily_order_count=0,
            daily_notional=0.0,
        )

        self.assertFalse(decision.accepted)
        self.assertIn("保有上限", decision.reason)

    def test_売却可能数量を超える売りを拒否する(self):
        manager = self._build_manager()
        intent = TradeIntent(
            code="7203",
            market="JP",
            side="SELL",
            qty=30,
            limit_price=1000,
            strategy_id="test",
        )
        portfolio = PortfolioState(
            balance=None,
            positions={self.symbol.full_code: _build_position(can_sell_qty=10.0)},
            open_orders=[],
        )

        decision = manager.evaluate(
            intent,
            portfolio,
            _build_qtys(max_position_sell=10.0),
            daily_order_count=0,
            daily_notional=0.0,
        )

        self.assertFalse(decision.accepted)
        self.assertIn("売却可能数量", decision.reason)

    def test_short口座がないと売建を拒否する(self):
        manager = self._build_manager(available_jp_acc_types=["JP_GENERAL"])
        intent = TradeIntent(
            code="7203",
            market="JP",
            side="SELL_SHORT",
            qty=10,
            limit_price=1000,
            strategy_id="test",
        )

        decision = manager.evaluate(
            intent,
            PortfolioState(balance=None, positions={}, open_orders=[]),
            _build_qtys(max_sell_short=100.0),
            daily_order_count=0,
            daily_notional=0.0,
        )

        self.assertFalse(decision.accepted)
        self.assertIn("short 用", decision.reason)

    def test_long保有中の売建を拒否する(self):
        manager = self._build_manager(available_jp_acc_types=["JP_GENERAL_SHORT"])
        intent = TradeIntent(
            code="7203",
            market="JP",
            side="SELL_SHORT",
            qty=10,
            limit_price=1000,
            strategy_id="test",
        )

        decision = manager.evaluate(
            intent,
            PortfolioState(
                balance=None,
                positions={self.symbol.full_code: _build_position(qty=5.0, can_sell_qty=5.0)},
                open_orders=[],
            ),
            _build_qtys(max_sell_short=100.0),
            daily_order_count=0,
            daily_notional=0.0,
        )

        self.assertFalse(decision.accepted)
        self.assertIn("先に SELL", decision.reason)

    def test_buy_backを許可する(self):
        manager = self._build_manager(available_jp_acc_types=["JP_GENERAL_SHORT"])
        intent = TradeIntent(
            code="7203",
            market="JP",
            side="BUY_BACK",
            qty=5,
            limit_price=1000,
            strategy_id="test",
        )
        portfolio = PortfolioState(
            balance=None,
            positions={
                self.symbol.full_code: _build_position(
                    qty=10.0,
                    can_sell_qty=0.0,
                    position_side="SHORT",
                    position_id="short-1",
                )
            },
            open_orders=[],
        )

        decision = manager.evaluate(
            intent,
            portfolio,
            _build_qtys(max_buy_back=10.0),
            daily_order_count=0,
            daily_notional=0.0,
        )

        self.assertTrue(decision.accepted)


if __name__ == "__main__":
    unittest.main()
