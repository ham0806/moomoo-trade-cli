from __future__ import annotations

import json
import math
import sys
import time
import uuid
from datetime import datetime, timedelta
from queue import Empty, Queue
from typing import Any, Dict, List, Optional, Set, Tuple, Type

from .credentials import get_trade_password
from .errors import OrderResultUnknownError
from .gateway import MoomooGateway
from .ledger import OrderLedger, PositionLedger
from .models import (
    BalanceSnapshot,
    DealLedgerEntry,
    OrderLedgerEntry,
    PortfolioState,
    PositionSnapshot,
    TradeIntent,
    strategy_token,
)
from .reservations import OrderIntentReservation, OrderIntentStore, default_state_dir
from .risk import RiskManager
from .sdk import normalize_trd_env_label
from .strategy import load_strategy
from .time_utils import (
    elapsed_seconds,
    market_today_text,
    now_utc,
    parse_timestamp,
    to_market_aware,
)
from .trading_config import TradingConfig


class TradingEngine:
    MAX_CONSECUTIVE_CYCLE_FAILURES = 5
    MAX_CYCLE_FAILURE_BACKOFF_SECONDS = 300
    # 約定時刻とポジション照会時刻の比較に許容する時計差・時刻精度の余裕。
    DEAL_SETTLEMENT_MARGIN_SECONDS = 300

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
        state_dir: Optional[str] = None,
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
        self.owner_id = uuid.uuid4().hex
        self.intent_store = OrderIntentStore(
            state_dir or trading_config.state_dir or default_state_dir(),
            namespace=_reservation_namespace(
                self.env_name,
                trading_config.account_id,
                opend_config,
            ),
        )
        self._state_ready = False
        self._last_sync_at: Optional[datetime] = None
        self._position_snapshot_at: Optional[datetime] = None
        self._ledger_date: Optional[str] = None
        self._announced_unknown: Set[str] = set()

    def run_once(self) -> Dict[str, object]:
        self.bootstrap()
        now = now_utc()
        self._refresh_state_if_due(now)
        self._drain_events()

        placed_orders: List[str] = []
        rejected_orders: List[Dict[str, object]] = []

        if not self._state_ready:
            self._emit_event(
                "state_sync_incomplete",
                reason="口座状態を同期できていないため新規発注と取消を停止しています。",
            )
            return self._build_summary(placed_orders, rejected_orders)

        now = now_utc()
        self._cancel_stale_orders(now)
        self._reconcile_intent_reservations()

        portfolio_state = self._portfolio_state()
        snapshots = self.gateway.get_market_snapshots(list(self._symbol_map.keys()))
        intents = self.strategy.evaluate(snapshots, portfolio_state, now)
        assert self.risk_manager is not None

        for intent in intents:
            signature = ""
            try:
                if self._intent_expired(intent, now_utc()):
                    self._reject_intent(intent, rejected_orders, "有効期限切れです。")
                    continue

                effective_price = self._effective_price(intent, snapshots, now_utc())

                # 数量・予算判定から予約・送信状態の記録までを同じロック区間で行う。
                with self.intent_store.transaction(self.owner_id) as transaction:
                    active_entries = transaction.active_entries()
                    pending_qtys = self._pending_qtys(active_entries)
                    static_decision = self.risk_manager.evaluate_static(
                        intent,
                        portfolio_state,
                        daily_order_count=self._daily_order_count(active_entries),
                        daily_notional=self._daily_notional(active_entries),
                        effective_price=effective_price,
                        pending_qtys=pending_qtys,
                    )
                    if static_decision is not None:
                        self._reject_intent(intent, rejected_orders, static_decision.reason)
                        continue

                    current_position = self._resolve_position(intent)
                    jp_acc_type_name = self.gateway.resolve_jp_acc_type(intent.side)
                    session_name = self._resolve_session(intent)
                    max_trade_qtys = self.gateway.query_max_trade_qtys(
                        full_code=intent.full_code,
                        order_type_name=intent.order_type,
                        price=effective_price,
                        session_name=session_name,
                        jp_acc_type_name=jp_acc_type_name,
                        position_id=current_position.position_id if current_position else None,
                    )

                    decision = self.risk_manager.evaluate(
                        intent,
                        portfolio_state,
                        max_trade_qtys,
                        daily_order_count=self._daily_order_count(active_entries),
                        daily_notional=self._daily_notional(active_entries),
                        effective_price=effective_price,
                        pending_qtys=pending_qtys,
                    )
                    if not decision.accepted:
                        self._reject_intent(intent, rejected_orders, decision.reason)
                        continue

                    # 数量照会などの API 待機中に期限切れ・行情の劣化が起きていないか再検証する。
                    now_before_send = now_utc()
                    if self._intent_expired(intent, now_before_send):
                        self._reject_intent(intent, rejected_orders, "有効期限切れです。")
                        continue
                    effective_price = self._effective_price(intent, snapshots, now_before_send)

                    signature = intent.intent_signature
                    if transaction.is_blocked(signature):
                        self._reject_intent(
                            intent,
                            rejected_orders,
                            "同じ注文意図の結果を照合中のため再発注しません。",
                        )
                        continue

                    if not transaction.reserve(
                        intent_signature=signature,
                        full_code=intent.full_code,
                        side=intent.side,
                        qty=intent.qty,
                        effective_price=effective_price,
                    ):
                        self._reject_intent(
                            intent,
                            rejected_orders,
                            "同じ注文意図が予約済みのため再発注しません。",
                        )
                        continue

                    transaction.mark_sent(signature)

                # 予約と送信状態を確定してから送信する。他のエンジンは SENT を見て待機する。
                try:
                    order = self._place_order(
                        intent,
                        session_name=session_name,
                        jp_acc_type_name=jp_acc_type_name,
                        position=current_position,
                    )
                except OrderResultUnknownError as exc:
                    self.intent_store.mark_unknown(signature, self.owner_id, str(exc))
                    self._emit_event(
                        "order_result_unknown",
                        intent=_intent_label(intent),
                        code=_intent_code(intent),
                        error=str(exc),
                    )
                    self._reject_intent(
                        intent,
                        rejected_orders,
                        "発注結果が不明です。証券側の照合が完了するまで再発注しません。",
                    )
                    # 二重発注の危険がある状態を常駐ループの連続失敗として数える。
                    raise
                except Exception as exc:
                    self.intent_store.release(signature, self.owner_id)
                    self._reject_intent(intent, rejected_orders, str(exc))
                    continue
            except OrderResultUnknownError:
                raise
            except Exception as exc:
                self._reject_intent(intent, rejected_orders, str(exc))
                continue

            self.intent_store.mark_placed(signature, self.owner_id, order.order_id)
            placed_orders.append(order.order_id)
            self._upsert_today_order(order)
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

        return self._build_summary(placed_orders, rejected_orders)

    def start(self) -> None:
        self.bootstrap()
        self._emit_stderr("自動売買エンジンを開始します。Ctrl+C で停止します。")

        consecutive_failures = 0
        try:
            while True:
                try:
                    self.run_once()
                    consecutive_failures = 0
                    delay = self.trading_config.poll_interval_seconds
                except Exception as exc:
                    consecutive_failures += 1
                    self._emit_event(
                        "engine_cycle_error",
                        error=str(exc),
                        consecutive_failures=consecutive_failures,
                    )
                    if consecutive_failures >= self.MAX_CONSECUTIVE_CYCLE_FAILURES:
                        self._emit_stderr(
                            "エラーが {} 回連続したためエンジンを終了します。".format(
                                consecutive_failures
                            )
                        )
                        raise
                    delay = min(
                        self.trading_config.poll_interval_seconds
                        * (2 ** (consecutive_failures - 1)),
                        self.MAX_CYCLE_FAILURE_BACKOFF_SECONDS,
                    )
                self.sleep_fn(delay)
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
        now = now_utc()
        target_dates = sorted(
            {market_today_text(symbol.market, now) for symbol in self.trading_config.symbols}
        )
        start_date = target_dates[0] if target_dates else market_today_text("JP", now)
        end_date = target_dates[-1] if target_dates else start_date

        try:
            balance = self.gateway.query_balance(refresh=refresh)
            positions = self.gateway.query_positions(refresh=refresh)
            # ポジション照会の完了時点。これより前の売り注文は売却可能数量へ反映済み、
            # これより後の約定は保有へ未反映として扱う。
            position_snapshot_at = now_utc()
            open_orders = self.gateway.query_open_orders(refresh=refresh)
            today_orders = self.gateway.query_today_orders(
                now=now,
                start_date=start_date,
                end_date=end_date,
            )
            today_deals = self.gateway.query_today_deals(
                now=now,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            self._state_ready = False
            raise

        if self._ledger_date is not None and self._ledger_date != start_date:
            self._emit_event(
                "daily_ledger_rolled",
                previous=self._ledger_date,
                current=start_date,
            )

        self.balance = balance
        self.today_orders = list(today_orders)
        self.today_deals = list(today_deals)
        self.position_ledger.replace_positions(positions)
        self.order_ledger.replace_orders(open_orders)
        for order in self.today_orders:
            self.order_ledger.upsert_order(order)
        self.order_ledger.replace_deals(self.today_deals)
        synced_orders = {order.order_id: order for order in list(today_orders) + list(open_orders)}
        applied = self._applied_qty_from_deals(
            today_deals,
            list(synced_orders.values()),
            position_snapshot_at,
        )
        self.order_ledger.reset_applied_deals(applied)
        self._position_snapshot_at = position_snapshot_at
        self._ledger_date = start_date
        self._state_ready = True
        self._last_sync_at = now

    def _applied_qty_from_deals(
        self,
        deals: List[DealLedgerEntry],
        orders: List[OrderLedgerEntry],
        position_snapshot_at: datetime,
    ) -> Dict[str, float]:
        """ポジション照会に反映済みと判断できる約定数量を返す。

        注文の約定数量は、照会完了直前以降に成立した約定を除いて反映済みとみなす。
        時刻の精度・時計差を考慮して一定の余裕を取り、判断できない約定は未反映側に残す。
        """
        if not getattr(self.gateway, "deal_history_available", True):
            # 約定履歴が使えず反映範囲を確認できないため、安全側で全数量を未反映とする。
            return {order.order_id: 0.0 for order in orders}

        margin = timedelta(seconds=self.DEAL_SETTLEMENT_MARGIN_SECONDS)
        boundary = position_snapshot_at - margin
        recent_dealt: Dict[str, float] = {}
        for deal in deals:
            deal_time = to_market_aware(deal.create_time, deal.market) if deal.create_time else None
            if deal_time is not None and deal_time < boundary:
                # 照会に反映済みとみなせる古い約定。
                continue
            recent_dealt[deal.order_id] = recent_dealt.get(deal.order_id, 0.0) + max(0.0, deal.qty)

        applied: Dict[str, float] = {}
        for order in orders:
            dealt = max(0.0, float(order.dealt_qty))
            applied[order.order_id] = max(0.0, dealt - recent_dealt.get(order.order_id, 0.0))
        return applied

    def _refresh_state_if_due(self, now: datetime) -> None:
        if self._last_sync_at is not None:
            interval = self.trading_config.state_sync_interval_seconds
            if elapsed_seconds(now, self._last_sync_at) < interval:
                return

        try:
            self._sync_state(refresh=True)
        except Exception as exc:
            self._state_ready = False
            self._emit_event("state_sync_failed", error=str(exc))

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
            account = event.get("account") or {}
            if account and not self._event_belongs_to_account(account):
                continue
            if payload and not self._event_belongs_to_account(payload):
                continue

            if event_type == "order":
                from .orders import build_order_entry

                order = build_order_entry(payload)
                self.order_ledger.upsert_order(order)
                self._upsert_today_order(order)
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
                    self.order_ledger.record_applied_deal(deal.order_id, deal.qty)
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
        # 実口座の取消も発注と同じ許可判定の対象にする。
        if not self._live_operations_allowed() or not self._state_ready:
            return

        stale_orders = self.order_ledger.stale_orders(
            now,
            self.trading_config.risk_limits.cancel_after_seconds,
        )
        for order in stale_orders:
            if not self._is_engine_managed_order(order):
                continue
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

    def _reconcile_intent_reservations(self) -> None:
        remaining = self.intent_store.reconcile(
            self.order_ledger.all_orders(),
            owner_id=self.owner_id,
        )
        active_unknown = {
            entry.intent_signature for entry in remaining if entry.status == "UNKNOWN"
        }
        for signature in sorted(active_unknown - self._announced_unknown):
            self._emit_event("order_result_unknown_pending", intent=signature)
        self._announced_unknown = active_unknown

    def _pending_qtys(
        self, active_entries: List[OrderIntentReservation]
    ) -> Dict[Tuple[str, str], float]:
        all_orders = self.order_ledger.all_orders()
        known_order_ids = {order.order_id for order in all_orders}
        pending: Dict[Tuple[str, str], float] = {}
        for order in all_orders:
            if order.side == "SELL" and self._is_reflected_in_position(order):
                # 売却可能数量はポジション照会時点の未約定売りを既に控除している。
                continue
            if order.is_open:
                pending_qty = order.pending_qty
            else:
                # 全約定・取消済みでも、約定が台帳へ未反映なら数量を予約し続ける。
                pending_qty = order.unapplied_dealt_qty
            if pending_qty <= 0:
                continue
            key = (order.full_code, order.side)
            pending[key] = pending.get(key, 0.0) + pending_qty

        for entry in active_entries:
            if entry.status == "PLACED" and entry.order_id in known_order_ids:
                # 証券側に受理済みの注文は注文台帳側で数える。
                continue
            key = (entry.full_code, entry.side)
            pending[key] = pending.get(key, 0.0) + max(0.0, entry.qty)
        return pending

    def _is_reflected_in_position(self, order: OrderLedgerEntry) -> bool:
        if self._position_snapshot_at is None:
            return False
        if order.create_time is None:
            return False
        created_at = to_market_aware(order.create_time, order.market)
        if created_at is None:
            return False
        # ポジション照会の完了時刻より前に作られた売りは、照会結果の
        # 売却可能数量に控除済みとみなす（同期開始時刻では判定しない）。
        return created_at <= self._position_snapshot_at

    def _daily_order_count(self, active_entries: List[OrderIntentReservation]) -> int:
        known_order_ids = {order.order_id for order in self.today_orders}
        pending_entries = sum(
            1
            for entry in active_entries
            if not (entry.status == "PLACED" and entry.order_id in known_order_ids)
        )
        return len(self.today_orders) + pending_entries

    def _daily_notional(self, active_entries: List[OrderIntentReservation]) -> float:
        effective_prices = {
            entry.intent_signature: entry.effective_price for entry in active_entries
        }
        known_order_ids = {order.order_id for order in self.today_orders}

        total = 0.0
        for order in self.today_orders:
            unit_price = _order_unit_price(order)
            reservation_price = effective_prices.get(order.intent_signature or "")
            if str(order.order_type or "").upper() == "MARKET" and reservation_price:
                # 成行注文の price は約定価格を制限しないため、保守的な予約価格を使う。
                unit_price = max(unit_price, reservation_price)
            total += max(0.0, float(order.qty)) * unit_price

        for entry in active_entries:
            if entry.status == "PLACED" and entry.order_id in known_order_ids:
                continue
            total += max(0.0, entry.qty) * max(0.0, entry.effective_price)
        return total

    def _effective_price(
        self,
        intent: TradeIntent,
        snapshots: Dict[str, Any],
        now: datetime,
    ) -> float:
        limit_price = _finite_positive(intent.limit_price)
        if str(intent.order_type).upper() != "MARKET":
            return limit_price or 0.0

        snapshot = snapshots.get(intent.full_code) if snapshots else None
        if snapshot is None:
            raise _RejectedIntent("成行注文に必要な行情スナップショットがありません。")

        self._validate_quote_freshness(snapshot, intent.market, now)

        prices: List[float] = []
        for candidate in (
            snapshot.last_price,
            snapshot.ask_price,
            snapshot.bid_price,
            limit_price,
        ):
            value = _finite_positive(candidate)
            if value is not None:
                prices.append(value)
        if not prices:
            raise _RejectedIntent("成行注文の基準価格を取得できません。")
        return max(prices) * (1.0 + self.trading_config.risk_limits.market_order_slippage_ratio)

    def _validate_quote_freshness(self, snapshot: Any, market: str, now: datetime) -> None:
        max_age = self.trading_config.risk_limits.max_quote_age_seconds
        if max_age <= 0:
            return

        updated_at = to_market_aware(snapshot.update_time, market)
        if updated_at is None:
            raise _RejectedIntent("行情の更新時刻を確認できないため成行注文を拒否しました。")

        age = elapsed_seconds(now, updated_at)
        if age > max_age or age < -max_age:
            raise _RejectedIntent("行情が古いため成行注文を拒否しました。")

    def _resolve_position(self, intent: TradeIntent) -> Optional[PositionSnapshot]:
        return self.position_ledger.get(intent.full_code)

    def _resolve_session(self, intent: TradeIntent) -> Optional[str]:
        if intent.market.upper() != "US":
            return None

        if intent.session:
            return intent.session.upper()

        symbol = self._symbol_map.get(intent.full_code)
        if symbol is not None and symbol.allowed_sessions:
            return symbol.allowed_sessions[0]
        return None

    def _intent_expired(self, intent: TradeIntent, now: datetime) -> bool:
        expires_at = parse_timestamp(intent.expires_at)
        if expires_at is None:
            return False
        return elapsed_seconds(now, expires_at) > 0

    def _live_operations_allowed(self) -> bool:
        if self.env_name.upper() == "REAL" and not self.trading_config.allow_live:
            return False
        return True

    def _is_engine_managed_order(self, order: OrderLedgerEntry) -> bool:
        """このエンジンが発注した注文だけを取消対象にする。"""
        if not order.remark or not order.remark.startswith("moomoo-trade-cli|"):
            return False
        if order.full_code not in self._symbol_map:
            return False
        if not order.intent_signature:
            return False

        token = _remark_strategy_token(order.remark)
        if token is None:
            return False

        strategy_id = str(self.strategy.strategy_id)
        if token == strategy_token(strategy_id):
            return True
        # 変更前の備考形式（strategy=<戦略ID>）も完全一致なら引き継ぐ。
        return token == strategy_id

    def _event_belongs_to_account(self, identity: Dict[str, Any]) -> bool:
        env_label = normalize_trd_env_label(identity.get("trd_env"))
        if env_label in {"REAL", "SIMULATE"} and env_label != self.env_name.upper():
            return False

        payload_acc = identity.get("acc_id")
        if payload_acc in (None, ""):
            return True
        text = str(payload_acc).strip()
        if text.upper() in {"N/A", "NONE", "NULL", "0"}:
            return True

        try:
            expected = int(text)
        except (TypeError, ValueError):
            return False
        try:
            return expected == int(str(self.trading_config.account_id).strip())
        except (TypeError, ValueError):
            return False

    def _upsert_today_order(self, order: OrderLedgerEntry) -> None:
        for index, existing in enumerate(self.today_orders):
            if existing.order_id == order.order_id:
                self.today_orders[index] = order
                return
        self.today_orders.append(order)

    def _build_deal_entry(self, payload: Dict[str, Any]) -> DealLedgerEntry:
        create_time = to_market_aware(payload.get("create_time"), payload.get("market"))
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

    def _build_summary(
        self,
        placed_orders: List[str],
        rejected_orders: List[Dict[str, object]],
    ) -> Dict[str, object]:
        return {
            "env": self.env_name,
            "strategy": self.strategy.strategy_id,
            "symbols": list(self._symbol_map.keys()),
            "placed_orders": placed_orders,
            "rejected_orders": rejected_orders,
            "open_order_count": len(self.order_ledger.open_orders()),
            "position_count": len(self.position_ledger.codes()),
        }

    def _reject_intent(
        self,
        intent: TradeIntent,
        rejected_orders: List[Dict[str, object]],
        reason: str,
    ) -> None:
        label = _intent_label(intent)
        rejected_orders.append({"intent": label, "reason": reason})
        self._emit_event(
            "intent_rejected",
            intent=label,
            reason=reason,
            code=_intent_code(intent),
        )

    def _emit_event(self, event_type: str, **payload) -> None:
        line = {"event": event_type, **payload}
        json.dump(line, self.stdout, ensure_ascii=False)
        self.stdout.write("\n")
        self.stdout.flush()

    def _emit_stderr(self, message: str) -> None:
        self.stderr.write(message + "\n")
        self.stderr.flush()


class _RejectedIntent(Exception):
    """行情不足などで注文意図を実行できないことを表す内部例外。"""


def _reservation_namespace(env_name: str, account_id: Any, opend_config: Any) -> str:
    """口座・取引環境・接続先ごとに発注予約を分離するための識別子。"""
    return "{}|{}|{}:{}".format(
        str(env_name).upper(),
        account_id,
        getattr(opend_config, "host", ""),
        getattr(opend_config, "port", ""),
    )


def _finite_positive(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _order_unit_price(order: OrderLedgerEntry) -> float:
    prices = [order.price, order.dealt_avg_price]
    valid = [price for price in (_finite_positive(value) for value in prices) if price is not None]
    if not valid:
        return 0.0
    return max(valid)


def _remark_strategy_token(remark: str) -> Optional[str]:
    prefix = "strategy="
    for part in str(remark).split("|"):
        if part.startswith(prefix):
            return part[len(prefix) :]
    return None


def _intent_label(intent: TradeIntent) -> str:
    try:
        return intent.intent_signature
    except Exception:  # pragma: no cover - 不正な intent の保険
        pass
    try:
        return "{}|{}".format(intent.strategy_id, intent.full_code)
    except Exception:  # pragma: no cover - 不正な intent の保険
        return repr(intent)


def _intent_code(intent: TradeIntent) -> Optional[str]:
    try:
        return intent.full_code
    except Exception:  # pragma: no cover - 不正な intent の保険
        return None
