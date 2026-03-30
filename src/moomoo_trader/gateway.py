from __future__ import annotations

from datetime import datetime
from queue import Queue
from typing import Any, Dict, List, Optional

from .accounts import list_accounts, select_account
from .balance import build_balance_snapshot, fetch_balance
from .config import OpenDConfig
from .json_utils import frame_to_records, sanitize_for_json
from .models import MaxTradeQtySnapshot
from .orders import build_order_entry, normalize_order_record
from .positions import build_position_snapshot, normalize_position_record
from .sdk import (
    close_context,
    create_quote_context,
    create_trade_context_for_security_firm,
    ensure_ret_ok,
    get_sdk_module,
    get_trade_handler_bases,
    normalize_enum_label,
    parse_jp_acc_type,
    parse_modify_order_op,
    parse_order_type,
    parse_session,
    parse_time_in_force,
    parse_trd_env,
    parse_trd_side,
)
from .time_utils import to_iso, today_text


class MoomooGateway:
    def __init__(
        self,
        config: OpenDConfig,
        env_name: str,
        account_id: str,
        event_queue: Optional[Queue] = None,
    ) -> None:
        self.config = config
        self.env_name = env_name.lower()
        self.account_id = str(account_id)
        self.event_queue = event_queue
        self.account: Optional[Dict[str, Any]] = None
        self.trade_context: Any = None
        self.quote_context: Any = None
        self.sdk: Any = get_sdk_module()
        self.sdk_trd_env = parse_trd_env(self.env_name)

    def connect(self) -> None:
        if self.trade_context is not None and self.quote_context is not None:
            return

        self.account = self.resolve_account()
        security_firm = self.account.get("security_firm")
        self.trade_context = create_trade_context_for_security_firm(
            self.config.host,
            self.config.port,
            security_firm,
        )
        self.quote_context = create_quote_context(self.config.host, self.config.port)

        if self.event_queue is not None:
            self._install_push_handlers()
            self.trade_context.start()

    def close(self) -> None:
        close_context(self.trade_context)
        close_context(self.quote_context)
        self.trade_context = None
        self.quote_context = None

    def resolve_account(self) -> Dict[str, Any]:
        if self.account is not None:
            return self.account

        accounts = list_accounts(self.config)
        self.account = select_account(
            accounts,
            requested_env=self.env_name.upper(),
            account_id=self.account_id,
        )
        return self.account

    def market_authorities(self) -> Dict[str, bool]:
        account = self.resolve_account()
        authorities = {}
        for item in account.get("trdmarket_auth") or []:
            authorities[str(item).upper()] = True
        return authorities

    def default_jp_acc_type(self) -> str:
        account = self.resolve_account()
        jp_acc_types = account.get("jp_acc_type") or []
        if jp_acc_types:
            return str(jp_acc_types[0]).upper()
        return "JP_GENERAL"

    def available_jp_acc_types(self) -> List[str]:
        account = self.resolve_account()
        return [str(value).upper() for value in account.get("jp_acc_type") or []]

    def resolve_jp_acc_type(self, side: str) -> str:
        normalized_side = str(side).upper()
        jp_acc_types = self.available_jp_acc_types()

        if normalized_side in {"SELL_SHORT", "BUY_BACK"}:
            for value in jp_acc_types:
                if value.endswith("_SHORT"):
                    return value
            return self.default_jp_acc_type()

        for value in jp_acc_types:
            if not value.endswith("_SHORT"):
                return value
        return self.default_jp_acc_type()

    def unlock_trade(self, password: str) -> None:
        self.connect()
        ret, data = self.trade_context.unlock_trade(password=password, is_unlock=True)
        ensure_ret_ok(ret, data, "取引アンロック")

    def query_balance(self, refresh: bool = False):
        payload = fetch_balance(
            config=self.config,
            env_name=self.env_name,
            account_id=self.account_id,
            refresh=refresh,
        )
        return build_balance_snapshot(payload)

    def query_positions(self, refresh: bool = False):
        self.connect()
        ret, data = self.trade_context.position_list_query(
            trd_env=self.sdk_trd_env,
            acc_id=int(self.account_id),
            refresh_cache=refresh,
            position_market=self.sdk.TrdMarket.NONE,
            asset_category=self.sdk.AssetCategory.NONE,
        )
        if _is_data_not_ready(ret, data):
            return []
        ensure_ret_ok(ret, data, "ポジション一覧取得")
        return [
            build_position_snapshot(normalize_position_record(record))
            for record in frame_to_records(data)
        ]

    def query_open_orders(self, refresh: bool = False):
        self.connect()
        ret, data = self.trade_context.order_list_query(
            trd_env=self.sdk_trd_env,
            acc_id=int(self.account_id),
            refresh_cache=refresh,
            order_market=self.sdk.TrdMarket.NONE,
        )
        if _is_data_not_ready(ret, data):
            return []
        ensure_ret_ok(ret, data, "注文一覧取得")
        return [
            build_order_entry(normalize_order_record(record)) for record in frame_to_records(data)
        ]

    def query_today_orders(self, now: Optional[datetime] = None):
        self.connect()
        target_date = today_text(now)
        ret, data = self.trade_context.history_order_list_query(
            start=target_date,
            end=target_date,
            trd_env=self.sdk_trd_env,
            acc_id=int(self.account_id),
            order_market=self.sdk.TrdMarket.NONE,
        )
        if _is_data_not_ready(ret, data):
            return []
        ensure_ret_ok(ret, data, "当日注文履歴取得")
        return [
            build_order_entry(normalize_order_record(record)) for record in frame_to_records(data)
        ]

    def query_today_deals(self, now: Optional[datetime] = None):
        target_date = today_text(now)
        return [
            self._build_deal_entry(record)
            for record in self.query_deal_records(
                start_date=target_date,
                end_date=target_date,
            )
        ]

    def query_deal_records(
        self,
        *,
        start_date: str,
        end_date: str,
        full_code: str = "",
    ) -> List[Dict[str, Any]]:
        self.connect()
        ret, data = self.trade_context.history_deal_list_query(
            code=full_code,
            start=start_date,
            end=end_date,
            trd_env=self.sdk_trd_env,
            acc_id=int(self.account_id),
            deal_market=self.sdk.TrdMarket.NONE,
        )
        if _is_data_not_ready(ret, data) or _is_deal_list_unsupported(ret, data):
            return []
        ensure_ret_ok(ret, data, "当日約定履歴取得")
        return [self._normalize_deal_record(record) for record in frame_to_records(data)]

    def get_market_snapshots(self, full_codes: List[str]):
        self.connect()
        if not full_codes:
            return {}

        ret, data = self.quote_context.get_market_snapshot(full_codes)
        ensure_ret_ok(ret, data, "スナップショット取得")

        snapshots = {}
        for record in frame_to_records(data):
            snapshot = self._build_market_snapshot(record)
            snapshots[snapshot.full_code] = snapshot
        return snapshots

    def query_max_trade_qtys(
        self,
        *,
        full_code: str,
        order_type_name: str,
        price: float,
        session_name: Optional[str],
        jp_acc_type_name: str,
        position_id: Optional[str] = None,
    ) -> MaxTradeQtySnapshot:
        self.connect()
        ret, data = self.trade_context.acctradinginfo_query(
            order_type=parse_order_type(order_type_name),
            code=full_code,
            price=price,
            trd_env=self.sdk_trd_env,
            acc_id=int(self.account_id),
            session=parse_session(session_name or ""),
            jp_acc_type=parse_jp_acc_type(jp_acc_type_name),
            position_id=position_id,
        )
        ensure_ret_ok(ret, data, "最大取引数量取得")
        record = frame_to_records(data)[0]
        return _build_max_trade_qty_snapshot(record)

    def place_order(
        self,
        *,
        full_code: str,
        side: str,
        order_type_name: str,
        time_in_force_name: str,
        qty: float,
        price: float,
        remark: str,
        session_name: Optional[str],
        jp_acc_type_name: str,
        position_id: Optional[str] = None,
    ):
        self.connect()
        ret, data = self.trade_context.place_order(
            price=price,
            qty=qty,
            code=full_code,
            trd_side=parse_trd_side(side),
            order_type=parse_order_type(order_type_name),
            time_in_force=parse_time_in_force(time_in_force_name),
            trd_env=self.sdk_trd_env,
            acc_id=int(self.account_id),
            remark=remark,
            session=parse_session(session_name or ""),
            jp_acc_type=parse_jp_acc_type(jp_acc_type_name),
            position_id=position_id,
        )
        ensure_ret_ok(ret, data, "注文発行")
        record = frame_to_records(data)[0]
        return build_order_entry(normalize_order_record(record))

    def modify_order(
        self,
        *,
        order_id: str,
        operation_name: str,
        qty: float,
        price: float,
    ) -> Dict[str, Any]:
        self.connect()
        ret, data = self.trade_context.modify_order(
            modify_order_op=parse_modify_order_op(operation_name),
            order_id=order_id,
            qty=qty,
            price=price,
            trd_env=self.sdk_trd_env,
            acc_id=int(self.account_id),
        )
        ensure_ret_ok(ret, data, "注文訂正")
        records = frame_to_records(data)
        if not records:
            return {
                "order_id": order_id,
                "operation": operation_name.upper(),
            }
        return _normalize_trade_result_record(records[0])

    def cancel_order(self, order_id: str) -> None:
        self.modify_order(
            order_id=order_id,
            operation_name="CANCEL",
            qty=0,
            price=0,
        )

    def query_order_fees(self, order_ids: List[str]) -> List[Dict[str, Any]]:
        self.connect()
        ret, data = self.trade_context.order_fee_query(
            order_id_list=order_ids,
            trd_env=self.sdk_trd_env,
            acc_id=int(self.account_id),
        )
        ensure_ret_ok(ret, data, "注文手数料取得")
        return [_normalize_fee_record(record) for record in frame_to_records(data)]

    def _install_push_handlers(self) -> None:
        order_handler_base, deal_handler_base = get_trade_handler_bases()
        event_queue = self.event_queue
        sdk = self.sdk

        class OrderPushHandler(order_handler_base):  # type: ignore[misc, valid-type]
            def on_recv_rsp(self, rsp_pb):
                ret, content = super(OrderPushHandler, self).on_recv_rsp(rsp_pb)
                if ret == sdk.RET_OK:
                    for record in frame_to_records(content):
                        event_queue.put(
                            {
                                "type": "order",
                                "payload": normalize_order_record(record),
                            }
                        )
                return ret, content

        class DealPushHandler(deal_handler_base):  # type: ignore[misc, valid-type]
            def on_recv_rsp(self, rsp_pb):
                ret, content = super(DealPushHandler, self).on_recv_rsp(rsp_pb)
                if ret == sdk.RET_OK:
                    for record in frame_to_records(content):
                        event_queue.put(
                            {
                                "type": "deal",
                                "payload": MoomooGateway._normalize_deal_record(record),
                            }
                        )
                return ret, content

        self.trade_context.set_handler(OrderPushHandler())
        self.trade_context.set_handler(DealPushHandler())

    @staticmethod
    def _normalize_deal_record(record: Dict[str, Any]) -> Dict[str, Any]:
        full_code = str(record.get("code") or "").upper()
        market, code = _split_full_code(full_code)
        normalized = {key: sanitize_for_json(value) for key, value in record.items()}
        normalized.update(
            {
                "deal_id": str(record.get("deal_id") or ""),
                "order_id": str(record.get("order_id") or ""),
                "code": code,
                "market": market,
                "full_code": full_code,
                "trd_env": normalize_enum_label(record.get("trd_env")),
                "trd_side": normalize_enum_label(record.get("trd_side")),
                "status": normalize_enum_label(record.get("status")),
                "create_time": to_iso(record.get("create_time")),
            }
        )
        return normalized

    def _build_deal_entry(self, record: Dict[str, Any]):
        from .models import DealLedgerEntry

        normalized = self._normalize_deal_record(record)
        return DealLedgerEntry(
            deal_id=str(normalized.get("deal_id") or ""),
            order_id=str(normalized.get("order_id") or ""),
            code=str(normalized.get("code") or ""),
            market=str(normalized.get("market") or ""),
            full_code=str(normalized.get("full_code") or ""),
            side=str(normalized.get("trd_side") or ""),
            qty=float(normalized.get("qty") or 0.0),
            price=float(normalized.get("price") or 0.0),
            create_time=datetime.fromisoformat(normalized["create_time"])
            if normalized.get("create_time")
            else None,
            status=str(normalized.get("status") or ""),
            raw=normalized,
        )

    def _build_market_snapshot(self, record: Dict[str, Any]):
        from .models import MarketSnapshot

        normalized = {key: sanitize_for_json(value) for key, value in record.items()}
        full_code = str(normalized.get("code") or "").upper()
        market, code = _split_full_code(full_code)
        return MarketSnapshot(
            code=code,
            market=market,
            full_code=full_code,
            last_price=_optional_float(normalized.get("last_price")),
            bid_price=_optional_float(normalized.get("bid_price")),
            ask_price=_optional_float(normalized.get("ask_price")),
            update_time=to_iso(normalized.get("update_time"))
            or to_iso(normalized.get("data_time")),
            raw=normalized,
        )


def _split_full_code(full_code: str) -> tuple:
    if "." not in full_code:
        return "", full_code
    market, code = full_code.split(".", 1)
    return market, code


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_data_not_ready(ret: Any, payload: Any) -> bool:
    return ret != 0 and "This data is not ready yet" in str(payload)


def _is_deal_list_unsupported(ret: Any, payload: Any) -> bool:
    return ret != 0 and "Simulated trade does not support deal list" in str(payload)


def _build_max_trade_qty_snapshot(record: Dict[str, Any]) -> MaxTradeQtySnapshot:
    normalized = {key: sanitize_for_json(value) for key, value in record.items()}
    return MaxTradeQtySnapshot(
        max_cash_buy=_optional_float(normalized.get("max_cash_buy")),
        max_position_sell=_optional_float(normalized.get("max_position_sell")),
        max_sell_short=_optional_float(normalized.get("max_sell_short")),
        max_buy_back=_optional_float(normalized.get("max_buy_back")),
        session=str(normalized.get("session") or "") or None,
        raw=normalized,
    )


def _normalize_trade_result_record(record: Dict[str, Any]) -> Dict[str, Any]:
    if record.get("code") or record.get("order_id"):
        return normalize_order_record(record)
    return {key: sanitize_for_json(value) for key, value in record.items()}


def _normalize_fee_record(record: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {key: sanitize_for_json(value) for key, value in record.items()}
    if "order_id" in record:
        normalized["order_id"] = str(sanitize_for_json(record.get("order_id")))
    return normalized
