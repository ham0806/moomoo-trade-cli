from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set

from .models import DealLedgerEntry, OrderLedgerEntry, PositionSnapshot


class OrderLedger:
    def __init__(self) -> None:
        self._orders: Dict[str, OrderLedgerEntry] = {}
        self._deals: Dict[str, DealLedgerEntry] = {}

    def replace_orders(self, orders: Iterable[OrderLedgerEntry]) -> None:
        self._orders = {order.order_id: order for order in orders}

    def replace_deals(self, deals: Iterable[DealLedgerEntry]) -> None:
        self._deals = {deal.deal_id: deal for deal in deals}

    def upsert_order(self, order: OrderLedgerEntry) -> None:
        self._orders[order.order_id] = order

    def add_deal(self, deal: DealLedgerEntry) -> bool:
        if deal.deal_id in self._deals:
            return False
        self._deals[deal.deal_id] = deal
        return True

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
            reference_time = order.updated_time or order.create_time
            if reference_time is None:
                continue
            elapsed_seconds = (now - reference_time).total_seconds()
            if elapsed_seconds >= cancel_after_seconds:
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
        position = self._positions.get(deal.full_code)
        if position is None:
            if deal.side == "BUY":
                self._positions[deal.full_code] = PositionSnapshot(
                    code=deal.code,
                    market=deal.market,
                    full_code=deal.full_code,
                    qty=deal.qty,
                    can_sell_qty=deal.qty,
                    position_side="LONG",
                    position_id=None,
                    cost_price=deal.price,
                    market_val=None,
                    pl_val=None,
                    currency=None,
                    raw=dict(deal.raw),
                )
            elif deal.side == "SELL_SHORT":
                self._positions[deal.full_code] = PositionSnapshot(
                    code=deal.code,
                    market=deal.market,
                    full_code=deal.full_code,
                    qty=deal.qty,
                    can_sell_qty=0.0,
                    position_side="SHORT",
                    position_id=None,
                    cost_price=deal.price,
                    market_val=None,
                    pl_val=None,
                    currency=None,
                    raw=dict(deal.raw),
                )
            return

        if position.position_side == "SHORT":
            if deal.side == "SELL_SHORT":
                new_qty = position.qty + deal.qty
            elif deal.side == "BUY_BACK":
                new_qty = max(0.0, position.qty - deal.qty)
            else:
                return

            if new_qty <= 0:
                self._positions.pop(deal.full_code, None)
                return

            self._positions[deal.full_code] = replace(
                position,
                qty=new_qty,
                can_sell_qty=0.0,
            )
            return

        if deal.side == "BUY":
            qty_delta = deal.qty
        elif deal.side == "SELL":
            qty_delta = -deal.qty
        else:
            return

        new_qty = max(0.0, position.qty + qty_delta)
        new_can_sell_qty = max(0.0, position.can_sell_qty + qty_delta)
        if new_qty <= 0:
            self._positions.pop(deal.full_code, None)
            return

        self._positions[deal.full_code] = replace(
            position, qty=new_qty, can_sell_qty=new_can_sell_qty
        )

    def codes(self) -> Set[str]:
        return set(self._positions.keys())
