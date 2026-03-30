from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def build_intent_signature(
    strategy_id: str,
    code: str,
    side: str,
    qty: float,
    limit_price: float,
    order_type: str,
    time_in_force: str,
    session: Optional[str],
) -> str:
    payload = "|".join(
        [
            strategy_id.strip(),
            code.strip().upper(),
            side.strip().upper(),
            "{:.8f}".format(float(qty)),
            "{:.8f}".format(float(limit_price)),
            order_type.strip().upper(),
            time_in_force.strip().upper(),
            (session or "").strip().upper(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class TradeIntent:
    code: str
    market: str
    side: str
    qty: float
    limit_price: float
    strategy_id: str
    order_type: str = "NORMAL"
    time_in_force: str = "DAY"
    session: Optional[str] = None
    expires_at: Optional[datetime] = None

    @property
    def full_code(self) -> str:
        upper_code = self.code.strip().upper()
        if "." in upper_code:
            return upper_code
        return "{}.{}".format(self.market.strip().upper(), upper_code)

    @property
    def intent_signature(self) -> str:
        return build_intent_signature(
            strategy_id=self.strategy_id,
            code=self.full_code,
            side=self.side,
            qty=self.qty,
            limit_price=self.limit_price,
            order_type=self.order_type,
            time_in_force=self.time_in_force,
            session=self.session,
        )

    @property
    def remark(self) -> str:
        return "moomoo-trade-cli|strategy={}|intent={}".format(
            self.strategy_id,
            self.intent_signature,
        )


@dataclass(frozen=True)
class BalanceSnapshot:
    acc_id: str
    trd_env: str
    currency: Optional[str]
    total_assets: Optional[float]
    cash: Optional[float]
    market_val: Optional[float]
    avl_withdrawal_cash: Optional[float]
    power: Optional[float]
    raw: Dict[str, Any]


@dataclass(frozen=True)
class MarketSnapshot:
    code: str
    market: str
    full_code: str
    last_price: Optional[float]
    bid_price: Optional[float]
    ask_price: Optional[float]
    update_time: Optional[str]
    raw: Dict[str, Any]


@dataclass
class PositionSnapshot:
    code: str
    market: str
    full_code: str
    qty: float
    can_sell_qty: float
    position_side: str
    position_id: Optional[str]
    cost_price: Optional[float]
    market_val: Optional[float]
    pl_val: Optional[float]
    currency: Optional[str]
    raw: Dict[str, Any]


@dataclass
class OrderLedgerEntry:
    order_id: str
    code: str
    market: str
    full_code: str
    side: str
    qty: float
    price: float
    dealt_qty: float
    dealt_avg_price: Optional[float]
    status: str
    order_type: Optional[str]
    time_in_force: Optional[str]
    create_time: Optional[datetime]
    updated_time: Optional[datetime]
    remark: Optional[str]
    currency: Optional[str]
    session: Optional[str]
    raw: Dict[str, Any]

    @property
    def is_open(self) -> bool:
        return self.status in {
            "SUBMITTING",
            "WAITING_SUBMIT",
            "SUBMITTED",
            "FILLED_PART",
            "CANCELLING_PART",
            "CANCELLING_ALL",
        }

    @property
    def intent_signature(self) -> Optional[str]:
        if not self.remark:
            return None
        prefix = "intent="
        for part in str(self.remark).split("|"):
            if part.startswith(prefix):
                return part[len(prefix) :]
        return None


@dataclass
class DealLedgerEntry:
    deal_id: str
    order_id: str
    code: str
    market: str
    full_code: str
    side: str
    qty: float
    price: float
    create_time: Optional[datetime]
    status: Optional[str]
    raw: Dict[str, Any]


@dataclass(frozen=True)
class PortfolioState:
    balance: Optional[BalanceSnapshot]
    positions: Dict[str, PositionSnapshot]
    open_orders: List[OrderLedgerEntry]


@dataclass(frozen=True)
class RiskDecision:
    accepted: bool
    reason: str
    intent: TradeIntent
    notional: float


@dataclass(frozen=True)
class MaxTradeQtySnapshot:
    max_cash_buy: Optional[float]
    max_position_sell: Optional[float]
    max_sell_short: Optional[float]
    max_buy_back: Optional[float]
    session: Optional[str]
    raw: Dict[str, Any]


@dataclass
class EngineState:
    balance: Optional[BalanceSnapshot] = None
    positions: Dict[str, PositionSnapshot] = field(default_factory=dict)
    open_orders: List[OrderLedgerEntry] = field(default_factory=list)
