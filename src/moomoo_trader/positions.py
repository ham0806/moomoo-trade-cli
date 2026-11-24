from __future__ import annotations

from typing import Any, Dict, List, Optional

from .accounts import list_accounts, select_account
from .config import OpenDConfig
from .json_utils import frame_to_records, sanitize_for_json
from .sdk import (
    close_context,
    create_trade_context_for_security_firm,
    ensure_ret_ok,
    normalize_enum_label,
    parse_trd_env,
)
from .time_utils import to_market_iso


def fetch_positions(
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
        ret, data = context.position_list_query(
            trd_env=parse_trd_env(env_name),
            acc_id=int(account["acc_id"]),
            refresh_cache=refresh,
        )
        ensure_ret_ok(ret, data, "ポジション一覧取得")
        return [normalize_position_record(record) for record in frame_to_records(data)]
    finally:
        close_context(context)


def normalize_position_record(record: Dict[str, Any]) -> Dict[str, Any]:
    full_code = str(record.get("code") or "").upper()
    market, code = _split_full_code(full_code)
    normalized = {key: sanitize_for_json(value) for key, value in record.items()}
    normalized.update(
        {
            "code": code,
            "market": market,
            "full_code": full_code,
            "trd_env": normalize_enum_label(record.get("trd_env")),
            "create_time": to_market_iso(record.get("create_time"), market),
            "update_time": to_market_iso(record.get("update_time"), market),
        }
    )
    return normalized


def build_position_snapshot(record: Dict[str, Any]):
    from .models import PositionSnapshot

    return PositionSnapshot(
        code=str(record.get("code") or ""),
        market=str(record.get("market") or ""),
        full_code=str(record.get("full_code") or ""),
        qty=float(record.get("qty") or record.get("stock_qty") or 0.0),
        can_sell_qty=float(record.get("can_sell_qty") or 0.0),
        position_side=_normalize_position_side(record.get("position_side")),
        position_id=_optional_int(record.get("position_id")),
        cost_price=_optional_float(record.get("cost_price")),
        market_val=_optional_float(record.get("market_val")),
        pl_val=_optional_float(record.get("pl_val")),
        currency=_optional_text(record.get("currency")),
        raw=dict(record),
    )


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


def _optional_int(value: Any) -> Optional[int]:
    """SDK の position_id は整数。文字列のまま渡すと発注が失敗するため境界で変換する。"""
    if value is None:
        return None

    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NONE", "NULL"}:
        return None

    try:
        return int(text)
    except ValueError:
        pass

    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError("position_id が整数ではありません: {}".format(value)) from exc

    if not number.is_integer():
        raise ValueError("position_id が整数ではありません: {}".format(value))
    return int(number)


def _normalize_position_side(value: Any) -> str:
    text = _optional_text(value)
    if not text:
        return "LONG"
    return text.upper()
