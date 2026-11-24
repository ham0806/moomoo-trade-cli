from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import OpenDConfig
from .errors import MoomooTraderError
from .gateway import MoomooGateway
from .time_utils import market_today_text, today_text


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
        account_id=account_id,
    )

    try:
        gateway.connect()
        full_code = _normalize_symbol(symbol) if symbol else ""
        # 省略時の日付は市場の現地日付に合わせる（米国市場の日付ずれを防ぐ）。
        default_date = market_today_text(full_code.split(".", 1)[0]) if full_code else today_text()
        target_start = start or default_date
        target_end = end or default_date
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
