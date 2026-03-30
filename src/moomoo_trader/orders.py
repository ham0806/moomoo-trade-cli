from __future__ import annotations

from typing import Any, Dict, List, Optional

from .accounts import list_accounts, select_account
from .config import OpenDConfig
from .json_utils import frame_to_records, sanitize_for_json, stringify_identifier
from .sdk import (
    close_context,
    create_trade_context_for_security_firm,
    ensure_ret_ok,
    normalize_enum_label,
    parse_trd_env,
)
from .time_utils import parse_timestamp, to_iso


def fetch_orders(
    config: OpenDConfig,
    env_name: str,
    account_id: Optional[str] = None,
    refresh: bool = False,
) -> List[Dict[str, Any]]:
    account = _resolve_account(config, env_name, account_id)
    context = create_trade_context_for_security_firm(
        config.host,
        config.port,
        account.get("security_firm"),
    )

    try:
        ret, data = context.order_list_query(
            trd_env=parse_trd_env(env_name),
            acc_id=int(account["acc_id"]),
            refresh_cache=refresh,
        )
        ensure_ret_ok(ret, data, "注文一覧取得")
        return [normalize_order_record(record) for record in frame_to_records(data)]
    finally:
        close_context(context)


def normalize_order_record(record: Dict[str, Any]) -> Dict[str, Any]:
    full_code = str(record.get("code") or "").upper()
    market, code = _split_full_code(full_code)
    normalized = {key: sanitize_for_json(value) for key, value in record.items()}
    normalized.update(
        {
            "order_id": stringify_identifier(record.get("order_id")),
            "code": code,
            "market": market,
            "full_code": full_code,
            "trd_env": normalize_enum_label(record.get("trd_env")),
            "trd_side": normalize_enum_label(record.get("trd_side")),
            "order_status": normalize_enum_label(record.get("order_status")),
            "order_type": normalize_enum_label(record.get("order_type")),
            "time_in_force": normalize_enum_label(record.get("time_in_force")),
            "session": normalize_enum_label(record.get("session")),
            "create_time": to_iso(record.get("create_time")),
            "updated_time": to_iso(record.get("updated_time")),
        }
    )
    return normalized


def build_order_entry(record: Dict[str, Any]):
    from .models import OrderLedgerEntry

    return OrderLedgerEntry(
        order_id=stringify_identifier(record.get("order_id")) or "",
        code=str(record.get("code") or ""),
        market=str(record.get("market") or ""),
        full_code=str(record.get("full_code") or ""),
        side=str(record.get("trd_side") or ""),
        qty=float(record.get("qty") or 0.0),
        price=float(record.get("price") or 0.0),
        dealt_qty=float(record.get("dealt_qty") or 0.0),
        dealt_avg_price=_optional_float(record.get("dealt_avg_price")),
        status=str(record.get("order_status") or ""),
        order_type=_optional_text(record.get("order_type")),
        time_in_force=_optional_text(record.get("time_in_force")),
        create_time=parse_timestamp(record.get("create_time")),
        updated_time=parse_timestamp(record.get("updated_time")),
        remark=_optional_text(record.get("remark")),
        currency=_optional_text(record.get("currency")),
        session=_optional_text(record.get("session")),
        raw=dict(record),
    )


def serialize_order_entry(order) -> Dict[str, Any]:
    return {
        "order_id": order.order_id,
        "code": order.code,
        "market": order.market,
        "full_code": order.full_code,
        "side": order.side,
        "qty": order.qty,
        "price": order.price,
        "dealt_qty": order.dealt_qty,
        "dealt_avg_price": order.dealt_avg_price,
        "status": order.status,
        "order_type": order.order_type,
        "time_in_force": order.time_in_force,
        "create_time": to_iso(order.create_time),
        "updated_time": to_iso(order.updated_time),
        "remark": order.remark,
        "currency": order.currency,
        "session": order.session,
        "raw": dict(order.raw),
    }


def _resolve_account(
    config: OpenDConfig, env_name: str, account_id: Optional[str]
) -> Dict[str, Any]:
    requested_env = env_name.upper()
    accounts = list_accounts(config)
    return select_account(accounts, requested_env=requested_env, account_id=account_id)


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


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None
