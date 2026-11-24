from __future__ import annotations

from datetime import datetime
from queue import Queue
from typing import Any, Dict, List, Optional

from .accounts import list_accounts, select_account
from .balance import build_balance_snapshot, fetch_balance
from .config import OpenDConfig
from .errors import (
    ApiCallError,
    DataNotReadyError,
    MoomooTraderError,
    OrderResultUnknownError,
)
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
    normalize_trd_env_label,
    parse_jp_acc_type,
    parse_modify_order_op,
    parse_order_type,
    parse_session,
    parse_time_in_force,
    parse_trd_env,
    parse_trd_side,
)
from .time_utils import to_iso, to_market_iso, today_text


class MoomooGateway:
    def __init__(
        self,
        config: OpenDConfig,
        env_name: str,
        account_id: Optional[str],
        event_queue: Optional[Queue] = None,
    ) -> None:
        self.config = config
        self.env_name = env_name.lower()
        self.account_id = str(account_id) if account_id else None
        self.event_queue = event_queue
        self.account: Optional[Dict[str, Any]] = None
        self.trade_context: Any = None
        self.quote_context: Any = None
        self.sdk: Any = get_sdk_module()
        self.sdk_trd_env = parse_trd_env(self.env_name)
        # 約定履歴 API が使えない環境（simulate など）では False になる。
        self.deal_history_available = True

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

    def _resolved_acc_id(self) -> int:
        account = self.resolve_account()
        return int(account["acc_id"])

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
            acc_id=self._resolved_acc_id(),
            refresh_cache=refresh,
            position_market=self.sdk.TrdMarket.NONE,
            asset_category=self.sdk.AssetCategory.NONE,
        )
        if _is_data_not_ready(ret, data):
            raise DataNotReadyError("ポジション一覧がまだ準備できていません。")
        ensure_ret_ok(ret, data, "ポジション一覧取得")
        return [
            build_position_snapshot(normalize_position_record(record))
            for record in frame_to_records(data)
        ]

    def query_open_orders(self, refresh: bool = False):
        self.connect()
        ret, data = self.trade_context.order_list_query(
            trd_env=self.sdk_trd_env,
            acc_id=self._resolved_acc_id(),
            refresh_cache=refresh,
            order_market=self.sdk.TrdMarket.NONE,
        )
        if _is_data_not_ready(ret, data):
            raise DataNotReadyError("注文一覧がまだ準備できていません。")
        ensure_ret_ok(ret, data, "注文一覧取得")
        return [
            build_order_entry(normalize_order_record(record)) for record in frame_to_records(data)
        ]

    def query_today_orders(
        self,
        now: Optional[datetime] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        self.connect()
        target_start = start_date or today_text(now)
        target_end = end_date or target_start
        ret, data = self.trade_context.history_order_list_query(
            start=target_start,
            end=target_end,
            trd_env=self.sdk_trd_env,
            acc_id=self._resolved_acc_id(),
            order_market=self.sdk.TrdMarket.NONE,
        )
        if _is_data_not_ready(ret, data):
            raise DataNotReadyError("当日注文履歴がまだ準備できていません。")
        ensure_ret_ok(ret, data, "当日注文履歴取得")
        return [
            build_order_entry(normalize_order_record(record)) for record in frame_to_records(data)
        ]

    def query_today_deals(
        self,
        now: Optional[datetime] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        target_start = start_date or today_text(now)
        target_end = end_date or target_start
        return [
            self._build_deal_entry(record)
            for record in self.query_deal_records(
                start_date=target_start,
                end_date=target_end,
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
            acc_id=self._resolved_acc_id(),
            deal_market=self.sdk.TrdMarket.NONE,
        )
        if _is_data_not_ready(ret, data):
            raise DataNotReadyError("約定履歴がまだ準備できていません。")
        if _is_deal_list_unsupported(ret, data):
            self.deal_history_available = False
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
        position_id: Optional[int] = None,
    ) -> MaxTradeQtySnapshot:
        self.connect()
        ret, data = self.trade_context.acctradinginfo_query(
            order_type=parse_order_type(order_type_name),
            code=full_code,
            price=price,
            trd_env=self.sdk_trd_env,
            acc_id=self._resolved_acc_id(),
            session=parse_session(session_name or ""),
            jp_acc_type=parse_jp_acc_type(jp_acc_type_name),
            position_id=_validate_position_id(position_id),
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
        position_id: Optional[int] = None,
    ):
        self.connect()
        try:
            ret, data = self.trade_context.place_order(
                price=price,
                qty=qty,
                code=full_code,
                trd_side=parse_trd_side(side),
                order_type=parse_order_type(order_type_name),
                time_in_force=parse_time_in_force(time_in_force_name),
                trd_env=self.sdk_trd_env,
                acc_id=self._resolved_acc_id(),
                remark=remark,
                session=parse_session(session_name or ""),
                jp_acc_type=parse_jp_acc_type(jp_acc_type_name),
                position_id=_validate_position_id(position_id),
            )
        except MoomooTraderError:
            raise
        except Exception as exc:
            # 通信断・タイムアウトなどは受理済みの可能性があるため「結果不明」として扱う。
            raise OrderResultUnknownError(
                "注文発行の結果を確認できません。証券側の状態を照合するまで再発注しません。"
            ) from exc

        if ret != 0:
            # 未受理を確認できる情報がないため、結果不明として予約を維持する。
            try:
                ensure_ret_ok(ret, data, "注文発行")
            except ApiCallError as exc:
                raise OrderResultUnknownError(
                    "注文発行の結果を確認できません（{0}）。"
                    "証券側の状態を照合するまで再発注しません。".format(exc)
                ) from exc
            raise OrderResultUnknownError(
                "注文発行の結果を確認できません。証券側の状態を照合するまで再発注しません。"
            )

        try:
            record = frame_to_records(data)[0]
            return build_order_entry(normalize_order_record(record))
        except Exception as exc:
            raise OrderResultUnknownError(
                "注文は送信されましたが結果を解釈できません。証券側の状態を照合するまで再発注しません。"
            ) from exc

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
            acc_id=self._resolved_acc_id(),
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
            acc_id=self._resolved_acc_id(),
        )
        ensure_ret_ok(ret, data, "注文手数料取得")
        return [_normalize_fee_record(record) for record in frame_to_records(data)]

    def _install_push_handlers(self) -> None:
        order_handler_base, deal_handler_base = get_trade_handler_bases()
        event_queue = self.event_queue
        sdk = self.sdk
        gateway = self

        class OrderPushHandler(order_handler_base):  # type: ignore[misc, valid-type]
            def on_recv_rsp(self, rsp_pb):
                ret, content = super(OrderPushHandler, self).on_recv_rsp(rsp_pb)
                if ret == sdk.RET_OK:
                    for record in frame_to_records(content):
                        account = gateway._resolve_push_account(rsp_pb, record)
                        if account is None:
                            continue
                        event_queue.put(
                            {
                                "type": "order",
                                "payload": normalize_order_record(record),
                                "account": account,
                            }
                        )
                return ret, content

        class DealPushHandler(deal_handler_base):  # type: ignore[misc, valid-type]
            def on_recv_rsp(self, rsp_pb):
                ret, content = super(DealPushHandler, self).on_recv_rsp(rsp_pb)
                if ret == sdk.RET_OK:
                    for record in frame_to_records(content):
                        account = gateway._resolve_push_account(rsp_pb, record)
                        if account is None:
                            continue
                        event_queue.put(
                            {
                                "type": "deal",
                                "payload": MoomooGateway._normalize_deal_record(record),
                                "account": account,
                            }
                        )
                return ret, content

        self.trade_context.set_handler(OrderPushHandler())
        self.trade_context.set_handler(DealPushHandler())

    def _resolve_push_account(
        self, rsp_pb: Any, record: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """通知の口座識別情報を返す。対象外の通知なら None。

        実 SDK の protobuf はヘッダーのフィールド名が `accID` / `trdEnv` で、
        通知 DataFrame には口座 ID が含まれない。ヘッダーを優先して検証し、
        正規化した識別情報をイベントへ引き継ぐ。
        """
        header = _extract_push_identity(rsp_pb)
        record_identity = _record_identity(record)
        if not header and not record_identity:
            # 口座を特定できない通知は、他口座の約定を混入させないため捨てる。
            return None
        for identity in (header, record_identity):
            if identity and not self._matches_account(
                identity.get("acc_id"), identity.get("trd_env")
            ):
                return None

        merged: Dict[str, Any] = {}
        merged.update(record_identity)
        merged.update(header)
        return merged

    def _accept_push_record(self, rsp_pb: Any, record: Dict[str, Any]) -> bool:
        return self._resolve_push_account(rsp_pb, record) is not None

    def _matches_account(self, acc_id: Any, trd_env: Any) -> bool:
        env_label = normalize_trd_env_label(trd_env) if trd_env is not None else ""
        if env_label in {"N/A", "NONE"}:
            env_label = ""
        if env_label in {"REAL", "SIMULATE"}:
            if env_label != self.env_name.upper():
                return False
        elif env_label:
            # 未知の環境表記は対象口座の通知と断定できないため捨てる。
            return False

        expected = _coerce_account_id(acc_id)
        if expected is None:
            return True

        try:
            return expected == self._resolved_acc_id()
        except (TypeError, ValueError):
            return False

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
                "create_time": to_market_iso(record.get("create_time"), market),
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


def _extract_push_identity(message: Any) -> Dict[str, Any]:
    """生の通知メッセージから口座 ID と取引環境を探す。

    実 SDK の protobuf は `s2c.header` に `accID` / `trdEnv` を持つ。
    フィールド名の表記揺れ（snake_case）も吸収する。
    """
    header = _nested_attr(_nested_attr(message, "s2c"), "header")
    found = _read_identity_fields(header)
    if found:
        return found

    found = _find_push_fields(message, 0)
    if "acc_id" in found or "trd_env" in found:
        return found
    return {}


def _find_push_fields(message: Any, depth: int) -> Dict[str, Any]:
    if depth > 3 or not hasattr(message, "ListFields"):
        return {}

    found = _read_identity_fields(message)
    if "acc_id" in found and "trd_env" in found:
        return found

    try:
        fields = message.ListFields()
    except Exception:  # pragma: no cover - 実 SDK の protobuf 差分吸収用
        return found

    for descriptor, value in fields:
        if isinstance(value, (list, tuple)):
            continue
        for key, nested in _find_push_fields(value, depth + 1).items():
            found.setdefault(key, nested)
    return found


def _read_identity_fields(message: Any) -> Dict[str, Any]:
    if message is None or not hasattr(message, "ListFields"):
        return {}

    try:
        fields = message.ListFields()
    except Exception:  # pragma: no cover - 実 SDK の protobuf 差分吸収用
        return {}

    found: Dict[str, Any] = {}
    for descriptor, value in fields:
        name = _normalized_field_name(getattr(descriptor, "name", ""))
        if name == "accid":
            found["acc_id"] = value
        elif name == "trdenv":
            found["trd_env"] = value
    return found


def _normalized_field_name(name: Any) -> str:
    return str(name).replace("_", "").lower()


def _nested_attr(message: Any, name: str) -> Any:
    if message is None:
        return None
    return getattr(message, name, None)


def _record_identity(record: Dict[str, Any]) -> Dict[str, Any]:
    identity: Dict[str, Any] = {}
    for key in ("acc_id", "trd_env"):
        value = record.get(key)
        if value is not None and str(value).strip().upper() not in {"", "N/A", "NONE", "NULL"}:
            identity[key] = value
    return identity


def _coerce_account_id(value: Any) -> Optional[int]:
    """口座 ID は浮動小数点を経由せず整数で比較する。"""
    if value is None:
        return None

    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NONE", "NULL", "0"}:
        return None

    try:
        return int(text)
    except ValueError:
        pass

    try:
        number = float(text)
    except ValueError:
        return None
    if not number.is_integer():
        return None
    return int(number)


def _validate_position_id(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoomooTraderError("position_id は整数で指定してください。")
    return value


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
