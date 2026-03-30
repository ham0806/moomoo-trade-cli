from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import tomli

from .errors import InvalidConfigError

_ALLOWED_SIDES = {"BUY", "SELL", "SELL_SHORT", "BUY_BACK"}
_ALLOWED_ORDER_TYPES = {"NORMAL", "MARKET"}
_ALLOWED_TIME_IN_FORCE = {"DAY", "GTC"}


@dataclass(frozen=True)
class SymbolConfig:
    code: str
    market: str
    max_position_qty: float
    max_order_qty: float
    allowed_sides: List[str]
    allowed_order_types: List[str]
    allowed_time_in_force: List[str]
    allowed_sessions: List[str]

    @property
    def full_code(self) -> str:
        upper_code = self.code.strip().upper()
        if "." in upper_code:
            return upper_code
        return "{}.{}".format(self.market.strip().upper(), upper_code)


@dataclass(frozen=True)
class RiskLimits:
    max_daily_orders: int
    max_daily_notional: float
    max_open_orders_per_symbol: int
    cancel_after_seconds: int


@dataclass(frozen=True)
class TradingConfig:
    account_id: str
    symbols: List[SymbolConfig]
    strategy: str
    poll_interval_seconds: int
    allow_live: bool
    credential_name: Optional[str]
    risk_limits: RiskLimits
    strategy_options: Dict[str, Any] = field(default_factory=dict)


def load_trading_config(path: str) -> TradingConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise InvalidConfigError("設定ファイルが見つかりません: {}".format(config_path))

    try:
        raw = tomli.loads(config_path.read_text(encoding="utf-8"))
    except tomli.TOMLDecodeError as exc:
        raise InvalidConfigError("設定ファイルの TOML 解析に失敗しました。") from exc

    return parse_trading_config(raw)


def parse_trading_config(raw: Dict[str, Any]) -> TradingConfig:
    if not isinstance(raw, dict):
        raise InvalidConfigError("設定ファイルの先頭はテーブルである必要があります。")

    account_id = _required_text(raw, "account_id")
    strategy = _required_text(raw, "strategy")
    poll_interval_seconds = _positive_int(raw.get("poll_interval_seconds"), "poll_interval_seconds")
    allow_live = _required_bool(raw, "allow_live")
    credential_name = _optional_text(raw.get("credential_name"), "credential_name")

    raw_symbols = raw.get("symbols")
    if not isinstance(raw_symbols, list) or not raw_symbols:
        raise InvalidConfigError("`symbols` には 1 件以上の銘柄設定が必要です。")

    symbols = [parse_symbol_config(item, index) for index, item in enumerate(raw_symbols, start=1)]

    raw_limits = raw.get("risk_limits")
    if not isinstance(raw_limits, dict):
        raise InvalidConfigError("`risk_limits` は必須のテーブルです。")

    risk_limits = RiskLimits(
        max_daily_orders=_positive_int(
            raw_limits.get("max_daily_orders"), "risk_limits.max_daily_orders"
        ),
        max_daily_notional=_positive_float(
            raw_limits.get("max_daily_notional"),
            "risk_limits.max_daily_notional",
        ),
        max_open_orders_per_symbol=_positive_int(
            raw_limits.get("max_open_orders_per_symbol"),
            "risk_limits.max_open_orders_per_symbol",
        ),
        cancel_after_seconds=_positive_int(
            raw_limits.get("cancel_after_seconds"),
            "risk_limits.cancel_after_seconds",
        ),
    )

    strategy_options = raw.get("strategy_options") or {}
    if not isinstance(strategy_options, dict):
        raise InvalidConfigError("`strategy_options` はテーブルで指定してください。")

    if allow_live and not credential_name:
        raise InvalidConfigError(
            "`allow_live = true` の場合は `credential_name` に Windows 資格情報名が必要です。"
        )

    return TradingConfig(
        account_id=account_id,
        symbols=symbols,
        strategy=strategy,
        poll_interval_seconds=poll_interval_seconds,
        allow_live=allow_live,
        credential_name=credential_name,
        risk_limits=risk_limits,
        strategy_options=strategy_options,
    )


def parse_symbol_config(raw: Dict[str, Any], index: int) -> SymbolConfig:
    if not isinstance(raw, dict):
        raise InvalidConfigError("`symbols[{}`] はテーブルで指定してください。".format(index - 1))

    allowed_sessions = raw.get("allowed_sessions")
    if not isinstance(allowed_sessions, list) or not allowed_sessions:
        raise InvalidConfigError(
            "`symbols[{}`].allowed_sessions` には 1 件以上のセッション指定が必要です。".format(
                index - 1
            )
        )

    return SymbolConfig(
        code=_required_text(raw, "code", prefix="symbols[{}].".format(index - 1)),
        market=_required_text(raw, "market", prefix="symbols[{}].".format(index - 1)).upper(),
        max_position_qty=_positive_float(
            raw.get("max_position_qty"),
            "symbols[{}].max_position_qty".format(index - 1),
        ),
        max_order_qty=_positive_float(
            raw.get("max_order_qty"),
            "symbols[{}].max_order_qty".format(index - 1),
        ),
        allowed_sides=_enum_list(
            raw.get("allowed_sides"),
            "symbols[{}].allowed_sides".format(index - 1),
            _ALLOWED_SIDES,
        ),
        allowed_order_types=_enum_list(
            raw.get("allowed_order_types"),
            "symbols[{}].allowed_order_types".format(index - 1),
            _ALLOWED_ORDER_TYPES,
        ),
        allowed_time_in_force=_enum_list(
            raw.get("allowed_time_in_force"),
            "symbols[{}].allowed_time_in_force".format(index - 1),
            _ALLOWED_TIME_IN_FORCE,
        ),
        allowed_sessions=[
            _required_text(
                {"value": value}, "value", prefix="symbols[{}].allowed_sessions.".format(index - 1)
            ).upper()
            for value in allowed_sessions
        ],
    )


def _required_text(raw: Dict[str, Any], key: str, prefix: str = "") -> str:
    value = raw.get(key)
    text = _optional_text(value, prefix + key, required=True)
    assert text is not None
    return text


def _optional_text(value: Any, name: str, required: bool = False) -> Optional[str]:
    if value is None:
        if required:
            raise InvalidConfigError("`{}` は必須です。".format(name))
        return None

    text = str(value).strip()
    if not text:
        if required:
            raise InvalidConfigError("`{}` は空文字にできません。".format(name))
        return None
    return text


def _required_bool(raw: Dict[str, Any], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise InvalidConfigError("`{}` には true または false を指定してください。".format(key))
    return value


def _positive_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidConfigError("`{}` には正の整数を指定してください。".format(name)) from exc

    if number <= 0:
        raise InvalidConfigError("`{}` には 1 以上の値を指定してください。".format(name))
    return number


def _positive_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidConfigError("`{}` には正の数値を指定してください。".format(name)) from exc

    if number <= 0:
        raise InvalidConfigError("`{}` には 0 より大きい値を指定してください。".format(name))
    return number


def _enum_list(value: Any, name: str, allowed_values: set) -> List[str]:
    if not isinstance(value, list) or not value:
        raise InvalidConfigError("`{}` には 1 件以上の値が必要です。".format(name))

    normalized_values = []
    for item in value:
        text = _optional_text(item, name, required=True)
        assert text is not None
        normalized = text.upper()
        if normalized not in allowed_values:
            raise InvalidConfigError("`{}` に不正な値があります: {}。".format(name, normalized))
        normalized_values.append(normalized)

    return normalized_values
