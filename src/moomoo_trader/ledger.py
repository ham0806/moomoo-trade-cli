from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set

from .models import DealLedgerEntry, OrderLedgerEntry, PositionSnapshot
from .time_utils import elapsed_seconds


class OrderLedger:
    def __init__(self) -> None:
        self._orders: Dict[str, OrderLedgerEntry] = {}
        self._deals: Dict[str, DealLedgerEntry] = {}
        # 注文通知より先に届いた約定も失わないよう、注文IDごとに適用済み数量を保持する。
        self._applied_by_order: Dict[str, float] = {}

    def replace_orders(self, orders: Iterable[OrderLedgerEntry]) -> None:
        self._orders = {order.order_id: order for order in orders}

    def replace_deals(self, deals: Iterable[DealLedgerEntry]) -> None:
        self._deals = {deal.deal_id: deal for deal in deals}

    def upsert_order(self, order: OrderLedgerEntry) -> None:
        existing = self._orders.get(order.order_id)
        carried = self._applied_by_order.get(order.order_id, 0.0)
        if existing is not None:
            # 通知で約定適用済み数量が巻き戻らないように引き継ぐ。
            carried = max(carried, existing.applied_dealt_qty)
        order.applied_dealt_qty = min(
            max(0.0, float(order.dealt_qty)),
            max(0.0, carried),
        )
        self._orders[order.order_id] = order

    def add_deal(self, deal: DealLedgerEntry) -> bool:
        if deal.deal_id in self._deals:
            return False
        self._deals[deal.deal_id] = deal
        return True

    def record_applied_deal(self, order_id: str, qty: float) -> None:
        applied = self._applied_by_order.get(order_id, 0.0) + max(0.0, float(qty))
        self._applied_by_order[order_id] = applied
        order = self._orders.get(order_id)
        if order is not None:
            order.applied_dealt_qty = min(max(0.0, float(order.qty)), applied)

    def reset_applied_deals(self, applied_by_order: Dict[str, float]) -> None:
        """口座照会の結果に合わせて、約定適用済み数量を更新する。"""
        for order_id, applied in applied_by_order.items():
            self._applied_by_order[order_id] = max(0.0, float(applied))
        for order in self._orders.values():
            applied = self._applied_by_order.get(order.order_id, 0.0)
            order.applied_dealt_qty = min(
                max(0.0, float(order.dealt_qty)),
                max(0.0, applied),
            )

    def all_orders(self) -> List[OrderLedgerEntry]:
        return list(self._orders.values())

    def open_orders(self) -> List[OrderLedgerEntry]:
        return [order for order in self._orders.values() if order.is_open]

    def count_open_orders_for_code(self, full_code: str) -> int:
        return sum(1 for order in self.open_orders() if order.full_code == full_code)

    def has_duplicate_intent(self, intent_signature: str) -> bool:
        for order in self.open_orders():
            if order.intent_signature == intent_signature:
                return True
        return False

    def daily_order_count(self) -> int:
        return len(self._orders)

    def daily_notional(self) -> float:
        return sum(float(order.qty) * float(order.price) for order in self._orders.values())

    def stale_orders(self, now: datetime, cancel_after_seconds: int) -> List[OrderLedgerEntry]:
        stale: List[OrderLedgerEntry] = []
        for order in self.open_orders():
            # 結果不明（TIMEOUT）の注文は取消要求を送らない。
            if order.status == "TIMEOUT":
                continue
            reference_time = order.updated_time or order.create_time
            if reference_time is None:
                continue
            if elapsed_seconds(now, reference_time) >= cancel_after_seconds:
                stale.append(order)
        return stale


class PositionLedger:
    def __init__(self) -> None:
        self._positions: Dict[str, PositionSnapshot] = {}

    def replace_positions(self, positions: Iterable[PositionSnapshot]) -> None:
        self._positions = {position.full_code: position for position in positions}

    def get(self, full_code: str) -> Optional[PositionSnapshot]:
        return self._positions.get(full_code)

    def all(self) -> Dict[str, PositionSnapshot]:
        return dict(self._positions)

    def apply_deal(self, deal: DealLedgerEntry) -> None:
        qty = _finite(deal.qty)
        price = _finite(deal.price)
        if qty <= 0 or price <= 0:
            return

        position = self._positions.get(deal.full_code)
        if position is None:
            if deal.side not in {"BUY", "SELL_SHORT"}:
                return
            is_short = deal.side == "SELL_SHORT"
            self._positions[deal.full_code] = PositionSnapshot(
                code=deal.code,
                market=deal.market,
                full_code=deal.full_code,
                qty=qty,
                can_sell_qty=0.0 if is_short else qty,
                position_side="SHORT" if is_short else "LONG",
                position_id=None,
                cost_price=price,
                market_val=qty * price,
                pl_val=0.0,
                currency=None,
                raw=dict(deal.raw),
            )
            return

        if position.position_side == "SHORT":
            cost_price: Optional[float]
            if deal.side == "SELL_SHORT":
                new_qty = position.qty + qty
                cost_price = _weighted_cost(position.cost_price, position.qty, price, qty)
            elif deal.side == "BUY_BACK":
                new_qty = max(0.0, position.qty - qty)
                cost_price = position.cost_price
            else:
                return

            if new_qty <= 0:
                self._positions.pop(deal.full_code, None)
                return

            self._positions[deal.full_code] = replace(
                position,
                qty=new_qty,
                can_sell_qty=0.0,
                cost_price=cost_price,
                market_val=new_qty * price,
                pl_val=_pl_value(cost_price, price, new_qty, is_short=True),
            )
            return

        cost_price = None
        if deal.side == "BUY":
            new_qty = position.qty + qty
            cost_price = _weighted_cost(position.cost_price, position.qty, price, qty)
            can_sell_qty = position.can_sell_qty + qty
        elif deal.side == "SELL":
            new_qty = max(0.0, position.qty - qty)
            cost_price = position.cost_price
            can_sell_qty = position.can_sell_qty - qty
        else:
            return

        if new_qty <= 0:
            self._positions.pop(deal.full_code, None)
            return

        self._positions[deal.full_code] = replace(
            position,
            qty=new_qty,
            can_sell_qty=min(new_qty, max(0.0, can_sell_qty)),
            cost_price=cost_price,
            market_val=new_qty * price,
            pl_val=_pl_value(cost_price, price, new_qty, is_short=False),
        )

    def codes(self) -> Set[str]:
        return set(self._positions.keys())


def _finite(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return number


def _weighted_cost(
    current_cost: Optional[float],
    current_qty: float,
    deal_price: float,
    deal_qty: float,
) -> float:
    base_cost = _finite(current_cost)
    total_qty = current_qty + deal_qty
    if total_qty <= 0:
        return deal_price
    if base_cost <= 0:
        return deal_price
    return (base_cost * current_qty + deal_price * deal_qty) / total_qty


def _pl_value(
    cost_price: Optional[float],
    market_price: float,
    qty: float,
    *,
    is_short: bool,
) -> Optional[float]:
    cost = _finite(cost_price)
    if cost <= 0:
        return None
    if is_short:
        return (cost - market_price) * qty
    return (market_price - cost) * qty
