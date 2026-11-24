from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

_REMARK_PREFIX = "moomoo-trade-cli"
_STRATEGY_TOKEN_BYTES = 14


def strategy_token(strategy_id: str) -> str:
    """戦略IDから作る固定長の所有識別子。

    備考の切り詰めで別戦略と前方一致することを避けるため、完全な戦略IDを
    ハッシュ化した固定長トークンを使う。
    """
    return hashlib.sha256(str(strategy_id).strip().encode("utf-8")).hexdigest()[
        :_STRATEGY_TOKEN_BYTES
    ]


def build_order_remark(strategy_id: str, intent_signature: str) -> str:
    """注文備考を SDK の上限（64 バイト）以内に収める。

    intent= は重複判定に使い、strategy= は所有判定に使う固定長トークンにする。
    """
    return "{}|strategy={}|intent={}".format(
        _REMARK_PREFIX,
        strategy_token(strategy_id),
        str(intent_signature),
    )


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
        return build_order_remark(self.strategy_id, self.intent_signature)


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
    position_id: Optional[int]
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
    # 約定通知をポジション台帳へ反映済みの数量。通知順序の差で保有上限を超えないために使う。
    applied_dealt_qty: float = 0.0

    @property
    def is_open(self) -> bool:
        # TIMEOUT は証券側の結果が確定していないため、未完了として扱い再発注を止める。
        return self.status in {
            "SUBMITTING",
            "WAITING_SUBMIT",
            "SUBMITTED",
            "FILLED_PART",
            "CANCELLING_PART",
            "CANCELLING_ALL",
            "TIMEOUT",
        }

    @property
    def unapplied_dealt_qty(self) -> float:
        """約定したがポジション台帳へまだ反映していない数量。"""
        return max(0.0, float(self.dealt_qty) - float(self.applied_dealt_qty))

    @property
    def pending_qty(self) -> float:
        """まだポジション台帳に反映されていない数量。

        未約定残数量に加え、約定通知だけ先行して届き台帳へ未反映の数量も含める。
        """
        remaining = max(0.0, float(self.qty) - float(self.dealt_qty))
        return remaining + self.unapplied_dealt_qty

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
