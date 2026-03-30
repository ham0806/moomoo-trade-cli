from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import OpenDConfig
from .credentials import get_trade_password
from .errors import MoomooTraderError
from .gateway import MoomooGateway
from .json_utils import sanitize_for_json
from .models import MaxTradeQtySnapshot
from .orders import serialize_order_entry


def place_order(
    *,
    config: OpenDConfig,
    env_name: str,
    account_id: str,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    order_type: str,
    time_in_force: str,
    session: Optional[str] = None,
    jp_acc_type: Optional[str] = None,
    position_id: Optional[str] = None,
    remark: Optional[str] = None,
    credential_name: Optional[str] = None,
) -> Dict[str, Any]:
    gateway = MoomooGateway(
        config=config,
        env_name=env_name,
        account_id=str(account_id),
    )

    try:
        gateway.connect()
        _maybe_unlock_trade(gateway, credential_name)
        resolved_jp_acc_type = jp_acc_type or gateway.resolve_jp_acc_type(side)
        order = gateway.place_order(
            full_code=_normalize_symbol(symbol),
            side=side,
            order_type_name=order_type,
            time_in_force_name=time_in_force,
            qty=qty,
            price=price,
            remark=remark or "",
            session_name=session,
            jp_acc_type_name=resolved_jp_acc_type,
            position_id=position_id,
        )
        return serialize_order_entry(order)
    finally:
        gateway.close()


def modify_order(
    *,
    config: OpenDConfig,
    env_name: str,
    account_id: str,
    order_id: str,
    operation: str,
    qty: float,
    price: float,
    credential_name: Optional[str] = None,
) -> Dict[str, Any]:
    gateway = MoomooGateway(
        config=config,
        env_name=env_name,
        account_id=str(account_id),
    )

    try:
        gateway.connect()
        _maybe_unlock_trade(gateway, credential_name)
        return gateway.modify_order(
            order_id=order_id,
            operation_name=operation,
            qty=qty,
            price=price,
        )
    finally:
        gateway.close()


def query_max_trade_qtys(
    *,
    config: OpenDConfig,
    env_name: str,
    account_id: str,
    symbol: str,
    side: str,
    order_type: str,
    price: float,
    session: Optional[str] = None,
    jp_acc_type: Optional[str] = None,
    position_id: Optional[str] = None,
) -> Dict[str, Any]:
    gateway = MoomooGateway(
        config=config,
        env_name=env_name,
        account_id=str(account_id),
    )

    try:
        gateway.connect()
        resolved_jp_acc_type = jp_acc_type or gateway.resolve_jp_acc_type(side)
        snapshot = gateway.query_max_trade_qtys(
            full_code=_normalize_symbol(symbol),
            order_type_name=order_type,
            price=price,
            session_name=session,
            jp_acc_type_name=resolved_jp_acc_type,
            position_id=position_id,
        )
        return serialize_max_trade_qtys(snapshot, resolved_jp_acc_type)
    finally:
        gateway.close()


def query_order_fees(
    *,
    config: OpenDConfig,
    env_name: str,
    account_id: str,
    order_ids: List[str],
) -> List[Dict[str, Any]]:
    gateway = MoomooGateway(
        config=config,
        env_name=env_name,
        account_id=str(account_id),
    )

    try:
        gateway.connect()
        return gateway.query_order_fees(order_ids)
    finally:
        gateway.close()


def serialize_max_trade_qtys(
    snapshot: MaxTradeQtySnapshot,
    jp_acc_type_name: str,
) -> Dict[str, Any]:
    return {
        "max_cash_buy": snapshot.max_cash_buy,
        "max_position_sell": snapshot.max_position_sell,
        "max_sell_short": snapshot.max_sell_short,
        "max_buy_back": snapshot.max_buy_back,
        "session": snapshot.session,
        "jp_acc_type": jp_acc_type_name,
        "raw": sanitize_for_json(snapshot.raw),
    }


def _maybe_unlock_trade(gateway: MoomooGateway, credential_name: Optional[str]) -> None:
    if gateway.env_name != "real" or not credential_name:
        return

    password = get_trade_password(credential_name)
    gateway.unlock_trade(password)


def _normalize_symbol(symbol: str) -> str:
    normalized = str(symbol).strip().upper()
    if "." not in normalized:
        raise MoomooTraderError(
            "`--symbol` には `JP.7203` のような市場付きコードを指定してください。"
        )
    return normalized
