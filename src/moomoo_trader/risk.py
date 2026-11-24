from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from .models import MaxTradeQtySnapshot, PortfolioState, RiskDecision, TradeIntent
from .trading_config import RiskLimits, SymbolConfig


class RiskManager:
    def __init__(
        self,
        allow_live: bool,
        env_name: str,
        market_authorities: Dict[str, bool],
        symbol_map: Dict[str, SymbolConfig],
        risk_limits: RiskLimits,
        available_jp_acc_types: List[str],
    ) -> None:
        self.allow_live = allow_live
        self.env_name = env_name.upper()
        self.market_authorities = market_authorities
        self.symbol_map = symbol_map
        self.risk_limits = risk_limits
        self.available_jp_acc_types = available_jp_acc_types

    def evaluate_static(
        self,
        intent: TradeIntent,
        portfolio_state: PortfolioState,
        *,
        daily_order_count: int,
        daily_notional: float,
        effective_price: Optional[float] = None,
        pending_qtys: Optional[Dict[Tuple[str, str], float]] = None,
    ) -> Optional[RiskDecision]:
        """acctradinginfo_query の結果を必要としない判定だけを先に評価する。

        effective_price には成行注文の保守的な想定価格を渡す。成行注文の
        price は約定価格を制限しないため、そのまま予算判定に使ってはならない。
        """
        notional = _intent_notional(intent, effective_price)

        if self.env_name == "REAL" and not self.allow_live:
            return RiskDecision(
                False, "allow_live=false のため実口座注文を拒否しました。", intent, notional
            )

        if intent.side not in {"BUY", "SELL", "SELL_SHORT", "BUY_BACK"}:
            return RiskDecision(
                False,
                "v1 では BUY / SELL / SELL_SHORT / BUY_BACK のみ扱います。",
                intent,
                notional,
            )

        qty = _optional_number(intent.qty)
        limit_price = _optional_number(intent.limit_price)
        if qty is None or limit_price is None or qty <= 0 or limit_price <= 0:
            return RiskDecision(
                False, "数量と価格は 0 より大きい必要があります。", intent, notional
            )

        if intent.order_type.upper() == "MARKET" and effective_price is None:
            return RiskDecision(
                False,
                "成行注文の保守的な基準価格を取得できないため拒否しました。",
                intent,
                notional,
            )

        symbol_config = self.symbol_map.get(intent.full_code)
        if symbol_config is None:
            return RiskDecision(False, "設定ファイルに存在しない銘柄です。", intent, notional)

        if not self.market_authorities.get(intent.market.upper(), False):
            return RiskDecision(False, "口座に対象市場の取引権限がありません。", intent, notional)

        if intent.side not in symbol_config.allowed_sides:
            return RiskDecision(
                False, "この銘柄では指定した売買方向を許可していません。", intent, notional
            )

        if intent.order_type not in symbol_config.allowed_order_types:
            return RiskDecision(
                False, "この銘柄では指定した注文方法を許可していません。", intent, notional
            )

        if intent.time_in_force not in symbol_config.allowed_time_in_force:
            return RiskDecision(
                False, "この銘柄では指定した有効期限を許可していません。", intent, notional
            )

        if intent.market.upper() != "US" and intent.session not in {None, "", "NONE"}:
            return RiskDecision(False, "session は US 注文でのみ指定できます。", intent, notional)

        if intent.market.upper() == "US":
            requested_session = (intent.session or symbol_config.allowed_sessions[0]).upper()
            if requested_session not in symbol_config.allowed_sessions:
                return RiskDecision(
                    False, "この銘柄では指定した session を許可していません。", intent, notional
                )
            if self.env_name == "SIMULATE" and requested_session not in {"NONE", "RTH"}:
                return RiskDecision(
                    False,
                    "simulate の US 注文では時間外 / overnight session を扱いません。",
                    intent,
                    notional,
                )

        if qty > symbol_config.max_order_qty:
            return RiskDecision(False, "1 回あたりの最大発注数量を超えています。", intent, notional)

        open_orders = portfolio_state.open_orders
        for order in open_orders:
            if order.intent_signature == intent.intent_signature:
                return RiskDecision(False, "重複した注文意図です。", intent, notional)

        position = portfolio_state.positions.get(intent.full_code)
        current_qty = _safe_position_qty(position.qty) if position else 0.0
        sellable_qty = _safe_position_qty(position.can_sell_qty) if position else 0.0
        position_side = position.position_side if position else "FLAT"
        pending = pending_qtys or {}
        pending_same_side = _pending_qty(pending, intent.full_code, intent.side)
        is_reducing = _is_risk_reducing(intent.side, position_side, current_qty, qty)

        # ポジションを減らす注文は、日次上限や未完了注文枠で決済を止めない。
        if not is_reducing:
            open_order_count = sum(
                1 for order in open_orders if order.full_code == intent.full_code
            )
            if open_order_count >= self.risk_limits.max_open_orders_per_symbol:
                return RiskDecision(
                    False, "同一銘柄の未完了注文数上限に達しています。", intent, notional
                )

            if daily_order_count >= self.risk_limits.max_daily_orders:
                return RiskDecision(False, "当日注文数の上限に達しています。", intent, notional)

            if daily_notional + notional > self.risk_limits.max_daily_notional:
                return RiskDecision(
                    False, "当日想定約定代金の上限を超えています。", intent, notional
                )

        if intent.side == "BUY":
            if position_side == "SHORT" and current_qty > 0:
                return RiskDecision(
                    False,
                    "ショート残高があるため BUY ではなく BUY_BACK を使ってください。",
                    intent,
                    notional,
                )
            if current_qty + pending_same_side + qty > symbol_config.max_position_qty:
                return RiskDecision(
                    False, "保有上限数量を超えるため買い注文を拒否しました。", intent, notional
                )
        elif intent.side == "SELL":
            if position_side == "SHORT":
                return RiskDecision(
                    False,
                    "ショート建玉に対して SELL は使えません。BUY_BACK を使ってください。",
                    intent,
                    notional,
                )
            if qty + pending_same_side > sellable_qty:
                return RiskDecision(
                    False, "現物口座で売却可能数量を超えています。", intent, notional
                )
        elif intent.side == "SELL_SHORT":
            if not _has_short_subaccount(self.available_jp_acc_types):
                return RiskDecision(
                    False,
                    "この口座に short 用の jp_acc_type がないため SELL_SHORT を許可できません。",
                    intent,
                    notional,
                )
            if position_side == "LONG" and current_qty > 0:
                return RiskDecision(
                    False,
                    "ロング残高があるため、先に SELL で解消してから SELL_SHORT してください。",
                    intent,
                    notional,
                )
            if current_qty + pending_same_side + qty > symbol_config.max_position_qty:
                return RiskDecision(
                    False,
                    "ショート建玉上限数量を超えるため SELL_SHORT を拒否しました。",
                    intent,
                    notional,
                )
        else:
            if not _has_short_subaccount(self.available_jp_acc_types):
                return RiskDecision(
                    False,
                    "この口座に short 用の jp_acc_type がないため BUY_BACK を許可できません。",
                    intent,
                    notional,
                )
            if position_side != "SHORT" or current_qty <= 0:
                return RiskDecision(
                    False,
                    "BUY_BACK できるショート建玉がありません。",
                    intent,
                    notional,
                )
            if qty + pending_same_side > current_qty:
                return RiskDecision(False, "最大買戻可能数量を超えています。", intent, notional)

        return None

    def evaluate(
        self,
        intent: TradeIntent,
        portfolio_state: PortfolioState,
        max_trade_qtys: MaxTradeQtySnapshot,
        *,
        daily_order_count: int,
        daily_notional: float,
        effective_price: Optional[float] = None,
        pending_qtys: Optional[Dict[Tuple[str, str], float]] = None,
    ) -> RiskDecision:
        decision = self.evaluate_static(
            intent,
            portfolio_state,
            daily_order_count=daily_order_count,
            daily_notional=daily_notional,
            effective_price=effective_price,
            pending_qtys=pending_qtys,
        )
        if decision is not None:
            return decision

        notional = _intent_notional(intent, effective_price)
        if intent.side == "BUY":
            if _safe_qty(max_trade_qtys.max_cash_buy) < intent.qty:
                return RiskDecision(False, "最大買付可能数量を超えています。", intent, notional)
        elif intent.side == "SELL":
            if _safe_qty(max_trade_qtys.max_position_sell) < intent.qty:
                return RiskDecision(
                    False, "現物口座で売却可能数量を超えています。", intent, notional
                )
        elif intent.side == "SELL_SHORT":
            if _safe_qty(max_trade_qtys.max_sell_short) < intent.qty:
                return RiskDecision(False, "最大売建可能数量を超えています。", intent, notional)
        elif _safe_qty(max_trade_qtys.max_buy_back) < intent.qty:
            return RiskDecision(False, "最大買戻可能数量を超えています。", intent, notional)

        return RiskDecision(True, "accepted", intent, notional)


def _has_short_subaccount(jp_acc_types: List[str]) -> bool:
    return any(value.endswith("_SHORT") for value in jp_acc_types)


def _is_risk_reducing(side: str, position_side: str, current_qty: float, qty: float) -> bool:
    """ポジションを減らすだけの注文かどうか。"""
    if position_side == "SHORT":
        return side == "BUY_BACK" and 0 < qty <= current_qty
    return side == "SELL" and 0 < qty <= current_qty


def _pending_qty(
    pending_qtys: Dict[Tuple[str, str], float],
    full_code: str,
    side: str,
) -> float:
    value = pending_qtys.get((full_code, side))
    if value is None:
        return 0.0
    if not math.isfinite(float(value)):
        return 0.0
    return max(0.0, float(value))


def _safe_position_qty(value: Any) -> float:
    number = _optional_number(value)
    if number is None:
        return 0.0
    return max(0.0, number)


def _safe_qty(value: Optional[float]) -> float:
    number = _optional_number(value)
    if number is None:
        return 0.0
    return number


def _intent_notional(intent: TradeIntent, effective_price: Optional[float] = None) -> float:
    qty = _optional_number(intent.qty)
    price = _optional_number(effective_price)
    if price is None or price <= 0:
        price = _optional_number(intent.limit_price)
    if qty is None or price is None:
        return 0.0
    return qty * price


def _optional_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
