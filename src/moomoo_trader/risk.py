from __future__ import annotations

from typing import Dict, List, Optional

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

    def evaluate(
        self,
        intent: TradeIntent,
        portfolio_state: PortfolioState,
        max_trade_qtys: MaxTradeQtySnapshot,
        *,
        daily_order_count: int,
        daily_notional: float,
    ) -> RiskDecision:
        notional = float(intent.qty) * float(intent.limit_price)

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

        if intent.qty <= 0 or intent.limit_price <= 0:
            return RiskDecision(
                False, "数量と価格は 0 より大きい必要があります。", intent, notional
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

        if intent.qty > symbol_config.max_order_qty:
            return RiskDecision(False, "1 回あたりの最大発注数量を超えています。", intent, notional)

        open_orders = portfolio_state.open_orders
        for order in open_orders:
            if order.intent_signature == intent.intent_signature:
                return RiskDecision(False, "重複した注文意図です。", intent, notional)

        open_order_count = sum(1 for order in open_orders if order.full_code == intent.full_code)
        if open_order_count >= self.risk_limits.max_open_orders_per_symbol:
            return RiskDecision(
                False, "同一銘柄の未完了注文数上限に達しています。", intent, notional
            )

        if daily_order_count >= self.risk_limits.max_daily_orders:
            return RiskDecision(False, "当日注文数の上限に達しています。", intent, notional)

        if daily_notional + notional > self.risk_limits.max_daily_notional:
            return RiskDecision(False, "当日想定約定代金の上限を超えています。", intent, notional)

        position = portfolio_state.positions.get(intent.full_code)
        current_qty = position.qty if position else 0.0
        sellable_qty = position.can_sell_qty if position else 0.0
        position_side = position.position_side if position else "FLAT"

        if intent.side == "BUY":
            if position_side == "SHORT" and current_qty > 0:
                return RiskDecision(
                    False,
                    "ショート残高があるため BUY ではなく BUY_BACK を使ってください。",
                    intent,
                    notional,
                )
            if current_qty + intent.qty > symbol_config.max_position_qty:
                return RiskDecision(
                    False, "保有上限数量を超えるため買い注文を拒否しました。", intent, notional
                )
            if _safe_qty(max_trade_qtys.max_cash_buy) < intent.qty:
                return RiskDecision(False, "最大買付可能数量を超えています。", intent, notional)
        elif intent.side == "SELL":
            if position_side == "SHORT":
                return RiskDecision(
                    False,
                    "ショート建玉に対して SELL は使えません。BUY_BACK を使ってください。",
                    intent,
                    notional,
                )
            if (
                intent.qty > sellable_qty
                or _safe_qty(max_trade_qtys.max_position_sell) < intent.qty
            ):
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
            if current_qty + intent.qty > symbol_config.max_position_qty:
                return RiskDecision(
                    False,
                    "ショート建玉上限数量を超えるため SELL_SHORT を拒否しました。",
                    intent,
                    notional,
                )
            if _safe_qty(max_trade_qtys.max_sell_short) < intent.qty:
                return RiskDecision(False, "最大売建可能数量を超えています。", intent, notional)
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
            if intent.qty > current_qty or _safe_qty(max_trade_qtys.max_buy_back) < intent.qty:
                return RiskDecision(False, "最大買戻可能数量を超えています。", intent, notional)

        return RiskDecision(True, "accepted", intent, notional)


def _has_short_subaccount(jp_acc_types: List[str]) -> bool:
    return any(value.endswith("_SHORT") for value in jp_acc_types)


def _safe_qty(value: Optional[float]) -> float:
    if value is None:
        return 0.0
    return float(value)
