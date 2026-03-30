from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import OpenDConfig
from .errors import MoomooTraderError
from .gateway import MoomooGateway
from .time_utils import today_text


def fetch_deals(
    config: OpenDConfig,
    env_name: str,
    account_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    symbol: Optional[str] = None,
) -> List[Dict[str, Any]]:
    gateway = MoomooGateway(
        config=config,
        env_name=env_name,
        account_id=str(account_id or ""),
    )

    try:
        gateway.connect()
        target_start = start or today_text()
        target_end = end or today_text()
        full_code = _normalize_symbol(symbol) if symbol else ""
        return gateway.query_deal_records(
            start_date=target_start,
            end_date=target_end,
            full_code=full_code,
        )
    finally:
        gateway.close()


def _normalize_symbol(symbol: str) -> str:
    normalized = str(symbol).strip().upper()
    if "." not in normalized:
        raise MoomooTraderError(
            "`--symbol` には `JP.7203` のような市場付きコードを指定してください。"
        )
    return normalized
