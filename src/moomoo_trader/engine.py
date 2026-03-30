from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from queue import Empty, Queue
from typing import Any, Dict, List, Optional, Type

from .credentials import get_trade_password
from .gateway import MoomooGateway
from .ledger import OrderLedger, PositionLedger
from .models import (
    BalanceSnapshot,
    DealLedgerEntry,
    OrderLedgerEntry,
    PortfolioState,
    PositionSnapshot,
    TradeIntent,
)
from .risk import RiskManager
from .strategy import load_strategy
from .time_utils import now_local
from .trading_config import TradingConfig


class TradingEngine:
    def __init__(
        self,
        *,
        opend_config,
        trading_config: TradingConfig,
        env_name: str,
        gateway_cls: Type[MoomooGateway] = MoomooGateway,
        stdout=None,
        stderr=None,
        sleep_fn=time.sleep,
    ) -> None:
        self.opend_config = opend_config
        self.trading_config = trading_config
        self.env_name = env_name.lower()
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.sleep_fn = sleep_fn
        self.event_queue: Queue = Queue()
        self.gateway = gateway_cls(
            config=opend_config,
            env_name=self.env_name,
            account_id=trading_config.account_id,
            event_queue=self.event_queue,
        )
        self.strategy = load_strategy(
            trading_config.strategy,
            trading_config.strategy_options,
        )
        self.order_ledger = OrderLedger()
        self.position_ledger = PositionLedger()
        self.balance: Optional[BalanceSnapshot] = None
        self.today_orders: List[OrderLedgerEntry] = []
        self.today_deals: List[DealLedgerEntry] = []
        self._bootstrapped = False
        self._symbol_map = {symbol.full_code: symbol for symbol in self.trading_config.symbols}
        self.risk_manager: Optional[RiskManager] = None

    def run_once(self) -> Dict[str, object]:
        self.bootstrap()
        self._drain_events()

        now = now_local()
        self._cancel_stale_orders(now)

        portfolio_state = self._portfolio_state()
        snapshots = self.gateway.get_market_snapshots(list(self._symbol_map.keys()))
        intents = self.strategy.evaluate(snapshots, portfolio_state, now)
        assert self.risk_manager is not None

        placed_orders = []
        rejected_orders = []

        for intent in intents:
            if intent.expires_at is not None and intent.expires_at < now:
                rejected_orders.append(
                    {"intent": intent.intent_signature, "reason": "有効期限切れです。"}
                )
                self._emit_event(
                    "intent_rejected", intent=intent.intent_signature, reason="有効期限切れです。"
                )
                continue

            current_position = self._resolve_position(intent)
            jp_acc_type_name = self.gateway.resolve_jp_acc_type(intent.side)
            session_name = self._resolve_session(intent)
            max_trade_qtys = self.gateway.query_max_trade_qtys(
                full_code=intent.full_code,
                order_type_name=intent.order_type,
                price=float(intent.limit_price),
                session_name=session_name,
                jp_acc_type_name=jp_acc_type_name,
                position_id=current_position.position_id if current_position else None,
            )

            decision = self.risk_manager.evaluate(
                intent,
                portfolio_state,
                max_trade_qtys,
                daily_order_count=len(self.today_orders),
                daily_notional=self._daily_notional(),
            )
            if not decision.accepted:
                rejected_orders.append(
                    {"intent": intent.intent_signature, "reason": decision.reason}
                )
                self._emit_event(
                    "intent_rejected",
                    intent=intent.intent_signature,
                    reason=decision.reason,
                    code=intent.full_code,
                )
                continue

            order = self._place_order(
                intent,
                session_name=session_name,
                jp_acc_type_name=jp_acc_type_name,
                position=current_position,
            )
            placed_orders.append(order.order_id)
            self.today_orders.append(order)
            self.order_ledger.upsert_order(order)
            self._emit_event(
                "order_submitted",
                order_id=order.order_id,
                code=order.full_code,
                qty=order.qty,
                price=order.price,
                strategy_id=intent.strategy_id,
            )
            portfolio_state = self._portfolio_state()

        summary = {
            "env": self.env_name,
            "strategy": self.strategy.strategy_id,
            "symbols": list(self._symbol_map.keys()),
            "placed_orders": placed_orders,
            "rejected_orders": rejected_orders,
            "open_order_count": len(self.order_ledger.open_orders()),
            "position_count": len(self.position_ledger.codes()),
        }
        self._emit_event("cycle_complete", **summary)
        return summary

    def start(self) -> None:
        self.bootstrap()
        self._emit_stderr("自動売買エンジンを開始します。Ctrl+C で停止します。")

        try:
            while True:
                self.run_once()
                self.sleep_fn(self.trading_config.poll_interval_seconds)
        except KeyboardInterrupt:
            self._emit_stderr("停止要求を受けたためエンジンを終了します。")
        finally:
            self.close()

    def close(self) -> None:
        self.gateway.close()

    def bootstrap(self) -> None:
        if self._bootstrapped:
            return

        self.gateway.connect()
        if self.env_name == "real" and self.trading_config.allow_live:
            password = get_trade_password(self.trading_config.credential_name or "")
            self.gateway.unlock_trade(password)
            self._emit_stderr("実口座の取引アンロックに成功しました。")

        self._sync_state(refresh=True)
        self.risk_manager = RiskManager(
            allow_live=self.trading_config.allow_live,
            env_name=self.env_name,
            market_authorities=self.gateway.market_authorities(),
            symbol_map=self._symbol_map,
            risk_limits=self.trading_config.risk_limits,
            available_jp_acc_types=self.gateway.available_jp_acc_types(),
        )
        self._bootstrapped = True
        self._emit_event(
            "engine_bootstrapped",
            env=self.env_name,
            account_id=self.trading_config.account_id,
            allow_live=self.trading_config.allow_live,
            strategy=self.strategy.strategy_id,
        )

    def _sync_state(self, refresh: bool) -> None:
        self.balance = self.gateway.query_balance(refresh=refresh)
        positions = self.gateway.query_positions(refresh=refresh)
        open_orders = self.gateway.query_open_orders(refresh=refresh)
        self.today_orders = self.gateway.query_today_orders(now=now_local())
        self.today_deals = self.gateway.query_today_deals(now=now_local())

        self.position_ledger.replace_positions(positions)
        self.order_ledger.replace_orders(open_orders)
        for order in self.today_orders:
            self.order_ledger.upsert_order(order)
        self.order_ledger.replace_deals(self.today_deals)

    def _portfolio_state(self) -> PortfolioState:
        return PortfolioState(
            balance=self.balance,
            positions=self.position_ledger.all(),
            open_orders=self.order_ledger.open_orders(),
        )

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.event_queue.get_nowait()
            except Empty:
                return

            event_type = event.get("type")
            payload = event.get("payload") or {}

            if event_type == "order":
                from .orders import build_order_entry

                order = build_order_entry(payload)
                self.order_ledger.upsert_order(order)
                if not any(existing.order_id == order.order_id for existing in self.today_orders):
                    self.today_orders.append(order)
                self._emit_event(
                    "order_push",
                    order_id=order.order_id,
                    status=order.status,
                    code=order.full_code,
                )
                continue

            if event_type == "deal":
                deal = self._build_deal_entry(payload)
                if self.order_ledger.add_deal(deal):
                    self.position_ledger.apply_deal(deal)
                    self.today_deals.append(deal)
                self._emit_event(
                    "deal_push",
                    deal_id=deal.deal_id,
                    order_id=deal.order_id,
                    code=deal.full_code,
                    qty=deal.qty,
                    price=deal.price,
                )

    def _cancel_stale_orders(self, now: datetime) -> None:
        stale_orders = self.order_ledger.stale_orders(
            now,
            self.trading_config.risk_limits.cancel_after_seconds,
        )
        for order in stale_orders:
            self.gateway.cancel_order(order.order_id)
            self._emit_event(
                "order_cancel_requested",
                order_id=order.order_id,
                code=order.full_code,
            )

    def _place_order(
        self,
        intent: TradeIntent,
        *,
        session_name: Optional[str],
        jp_acc_type_name: str,
        position: Optional[PositionSnapshot],
    ):
        return self.gateway.place_order(
            full_code=intent.full_code,
            side=intent.side,
            order_type_name=intent.order_type,
            time_in_force_name=intent.time_in_force,
            qty=intent.qty,
            price=intent.limit_price,
            remark=intent.remark,
            session_name=session_name,
            jp_acc_type_name=jp_acc_type_name,
            position_id=position.position_id if position else None,
        )

    def _daily_notional(self) -> float:
        return sum(float(order.qty) * float(order.price) for order in self.today_orders)

    def _resolve_position(self, intent: TradeIntent) -> Optional[PositionSnapshot]:
        return self.position_ledger.get(intent.full_code)

    def _resolve_session(self, intent: TradeIntent) -> Optional[str]:
        if intent.market.upper() != "US":
            return None

        if intent.session:
            return intent.session.upper()

        symbol = self._symbol_map[intent.full_code]
        if symbol.allowed_sessions:
            return symbol.allowed_sessions[0]
        return None

    def _build_deal_entry(self, payload: Dict[str, Any]) -> DealLedgerEntry:
        create_time = None
        if payload.get("create_time"):
            create_time = datetime.fromisoformat(str(payload["create_time"]))
        return DealLedgerEntry(
            deal_id=str(payload.get("deal_id") or ""),
            order_id=str(payload.get("order_id") or ""),
            code=str(payload.get("code") or ""),
            market=str(payload.get("market") or ""),
            full_code=str(payload.get("full_code") or ""),
            side=str(payload.get("trd_side") or ""),
            qty=float(payload.get("qty") or 0.0),
            price=float(payload.get("price") or 0.0),
            create_time=create_time,
            status=str(payload.get("status") or ""),
            raw=dict(payload),
        )

    def _emit_event(self, event_type: str, **payload) -> None:
        line = {"event": event_type, **payload}
        json.dump(line, self.stdout, ensure_ascii=False)
        self.stdout.write("\n")
        self.stdout.flush()

    def _emit_stderr(self, message: str) -> None:
        self.stderr.write(message + "\n")
        self.stderr.flush()
