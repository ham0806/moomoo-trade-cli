from typing import Any, Dict, Optional

from .accounts import list_accounts, select_account
from .config import OpenDConfig
from .errors import OpenDConnectionError
from .json_utils import sanitize_for_json, stringify_identifier
from .sdk import (
    close_context,
    create_trade_context_for_security_firm,
    ensure_ret_ok,
    get_currency_candidates,
    get_security_firm_candidates,
    parse_trd_env,
)


def fetch_balance(
    config: OpenDConfig,
    env_name: str,
    account_id: Optional[str] = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    requested_env = env_name.upper()
    accounts = list_accounts(config)
    account = select_account(accounts, requested_env=requested_env, account_id=account_id)

    security_firm = account.get("security_firm")
    if not security_firm or security_firm == "N/A":
        security_firm = get_security_firm_candidates()[0]

    context = create_trade_context_for_security_firm(config.host, config.port, security_firm)
    sdk_trd_env = parse_trd_env(env_name.lower())

    try:
        ret, data = _query_balance_with_currency_fallback(
            context=context,
            sdk_trd_env=sdk_trd_env,
            account_id=int(account["acc_id"]),
            refresh=refresh,
        )
        ensure_ret_ok(ret, data, "口座残高取得")
        return normalize_balance_frame(data, account)
    finally:
        close_context(context)


def normalize_balance_frame(frame, account: Dict[str, Any]) -> Dict[str, Any]:
    if frame is None or frame.empty:
        raise OpenDConnectionError("口座残高が返されませんでした。対象口座を確認してください。")

    raw_record = {
        key: sanitize_for_json(value) for key, value in frame.to_dict(orient="records")[0].items()
    }

    return {
        "acc_id": stringify_identifier(account.get("acc_id")),
        "trd_env": account.get("trd_env"),
        "currency": sanitize_for_json(raw_record.get("currency")),
        "total_assets": sanitize_for_json(raw_record.get("total_assets")),
        "cash": sanitize_for_json(raw_record.get("cash")),
        "market_val": sanitize_for_json(raw_record.get("market_val")),
        "avl_withdrawal_cash": sanitize_for_json(raw_record.get("avl_withdrawal_cash")),
        "power": sanitize_for_json(raw_record.get("power")),
        "raw": raw_record,
    }


def build_balance_snapshot(payload: Dict[str, Any]):
    from .models import BalanceSnapshot

    return BalanceSnapshot(
        acc_id=stringify_identifier(payload.get("acc_id")) or "",
        trd_env=str(payload.get("trd_env") or ""),
        currency=_optional_text(payload.get("currency")),
        total_assets=_optional_float(payload.get("total_assets")),
        cash=_optional_float(payload.get("cash")),
        market_val=_optional_float(payload.get("market_val")),
        avl_withdrawal_cash=_optional_float(payload.get("avl_withdrawal_cash")),
        power=_optional_float(payload.get("power")),
        raw=dict(payload.get("raw") or {}),
    )


def _query_balance_with_currency_fallback(context, sdk_trd_env, account_id: int, refresh: bool):
    currency_candidates = [None] + list(get_currency_candidates())
    last_error = None

    for currency in currency_candidates:
        try:
            kwargs = {
                "trd_env": sdk_trd_env,
                "acc_id": account_id,
                "refresh_cache": refresh,
            }
            if currency is not None:
                kwargs["currency"] = currency

            ret, data = context.accinfo_query(**kwargs)
        except Exception as exc:  # pragma: no cover - 実 SDK の例外差分吸収用
            raise OpenDConnectionError(
                "口座残高の取得に失敗しました。OpenD が起動していて取引 API が利用可能か確認してください。"
            ) from exc

        if ret == 0:
            return ret, data

        if "does not support converting to this currency" in str(data):
            last_error = (ret, data)
            continue

        return ret, data

    if last_error is not None:
        return last_error

    return ret, data


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
