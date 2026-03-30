import importlib
import logging
import re
from functools import lru_cache
from typing import Any, Tuple

from .errors import ApiCallError, MoomooTraderError, OpenDConnectionError, SdkNotInstalledError

_TRD_ENV_ALIASES = {
    "0": "SIMULATE",
    "SIMULATE": "SIMULATE",
    "TRDENV.SIMULATE": "SIMULATE",
    "1": "REAL",
    "REAL": "REAL",
    "TRDENV.REAL": "REAL",
}

_CURRENCY_CANDIDATE_NAMES = (
    "JPY",
    "USD",
    "HKD",
    "CNH",
    "SGD",
    "AUD",
    "CAD",
    "MYR",
)

_SESSION_ALIASES = {
    "NONE": "NONE",
    "SESSION.NONE": "NONE",
    "RTH": "RTH",
    "SESSION.RTH": "RTH",
    "ETH": "ETH",
    "SESSION.ETH": "ETH",
    "OVERNIGHT": "OVERNIGHT",
    "SESSION.OVERNIGHT": "OVERNIGHT",
    "ALL": "ALL",
    "SESSION.ALL": "ALL",
}


@lru_cache(maxsize=1)
def get_sdk_module():
    last_error = None
    for module_name in ("moomoo", "futu"):
        try:
            module = importlib.import_module(module_name)
            configure_sdk_logging(module_name)
            return module
        except ImportError as exc:
            last_error = exc

    raise SdkNotInstalledError(
        "Python SDK が見つかりません。`mise exec -- uv sync` を実行して依存関係をインストールしてください。"
    ) from last_error


def create_trade_context(host: str, port: int):
    sdk = get_sdk_module()
    security_firms = get_security_firm_candidates()

    try:
        return sdk.OpenSecTradeContext(
            filter_trdmarket=sdk.TrdMarket.NONE,
            host=host,
            port=port,
            security_firm=security_firms[0],
        )
    except Exception as exc:  # pragma: no cover - 実 SDK の差分吸収用
        raise OpenDConnectionError(
            "OpenD への接続コンテキスト作成に失敗しました。"
            " OpenD が起動していて TCP ポートが有効か確認してください。"
        ) from exc


def create_quote_context(host: str, port: int):
    sdk = get_sdk_module()

    try:
        return sdk.OpenQuoteContext(host=host, port=port)
    except Exception as exc:  # pragma: no cover - 実 SDK の差分吸収用
        raise OpenDConnectionError(
            "OpenD への行情コンテキスト作成に失敗しました。"
            " OpenD が起動していて TCP ポートが有効か確認してください。"
        ) from exc


def create_trade_context_for_security_firm(host: str, port: int, security_firm: Any):
    sdk = get_sdk_module()
    selected_security_firm = security_firm or get_security_firm_candidates()[0]

    try:
        return sdk.OpenSecTradeContext(
            filter_trdmarket=sdk.TrdMarket.NONE,
            host=host,
            port=port,
            security_firm=selected_security_firm,
        )
    except Exception as exc:  # pragma: no cover - 実 SDK の差分吸収用
        raise OpenDConnectionError(
            "OpenD への接続コンテキスト作成に失敗しました。"
            " OpenD が起動していて TCP ポートが有効か確認してください。"
        ) from exc


def close_context(context: Any) -> None:
    try:
        context.close()
    except Exception:
        return


def ensure_ret_ok(ret: Any, payload: Any, action: str) -> None:
    sdk = get_sdk_module()
    if ret != sdk.RET_OK:
        raise ApiCallError(f"{action} に失敗しました: {_localize_api_message(payload)}")


def parse_trd_env(name: str):
    sdk = get_sdk_module()

    if name == "real":
        return sdk.TrdEnv.REAL
    if name == "simulate":
        return sdk.TrdEnv.SIMULATE

    raise MoomooTraderError("`--env` には real または simulate を指定してください。")


def normalize_enum_label(value: Any) -> str:
    if value is None:
        return ""

    if hasattr(value, "name"):
        return str(value.name).upper()

    text = str(value).strip()
    if not text or text == "N/A":
        return ""

    upper_text = text.upper()
    if "." in upper_text:
        upper_text = upper_text.split(".")[-1]
    return upper_text


def normalize_trd_env_label(value: Any) -> str:
    if hasattr(value, "name"):
        return str(value.name).upper()

    return _TRD_ENV_ALIASES.get(str(value).upper(), str(value).upper())


def get_security_firm_candidates():
    sdk = get_sdk_module()
    candidates = []

    for name in dir(sdk.SecurityFirm):
        if not name.isupper() or name == "NONE":
            continue

        value = getattr(sdk.SecurityFirm, name)
        if isinstance(value, str):
            candidates.append(value)

    if not candidates:
        raise OpenDConnectionError("利用可能な SecurityFirm を SDK から取得できませんでした。")

    return candidates


def get_currency_candidates():
    sdk = get_sdk_module()
    candidates = []

    for name in _CURRENCY_CANDIDATE_NAMES:
        if hasattr(sdk.Currency, name):
            candidates.append(getattr(sdk.Currency, name))

    return candidates


def parse_trd_side(name: str):
    sdk = get_sdk_module()
    normalized = normalize_enum_label(name)
    if normalized not in {"BUY", "SELL", "SELL_SHORT", "BUY_BACK"}:
        raise MoomooTraderError("v1 では BUY / SELL / SELL_SHORT / BUY_BACK のみ指定できます。")
    return getattr(sdk.TrdSide, normalized)


def parse_session(name: str):
    sdk = get_sdk_module()
    if not name:
        return sdk.Session.NONE

    normalized = _SESSION_ALIASES.get(normalize_enum_label(name), normalize_enum_label(name))
    if not normalized or not hasattr(sdk.Session, normalized):
        raise MoomooTraderError("許可されていない session 指定です: {}".format(name))
    return getattr(sdk.Session, normalized)


def parse_jp_acc_type(name: str):
    sdk = get_sdk_module()
    normalized = normalize_enum_label(name)
    if not normalized:
        return sdk.SubAccType.JP_GENERAL
    if not hasattr(sdk.SubAccType, normalized):
        raise MoomooTraderError("不明な jp_acc_type です: {}".format(name))
    return getattr(sdk.SubAccType, normalized)


def parse_order_type(name: str):
    sdk = get_sdk_module()
    normalized = normalize_enum_label(name)
    if normalized not in {"NORMAL", "MARKET"}:
        raise MoomooTraderError("v1 では NORMAL または MARKET のみ指定できます。")
    return getattr(sdk.OrderType, normalized)


def parse_time_in_force(name: str):
    sdk = get_sdk_module()
    normalized = normalize_enum_label(name)
    if normalized not in {"DAY", "GTC"}:
        raise MoomooTraderError("v1 では DAY または GTC のみ指定できます。")
    return getattr(sdk.TimeInForce, normalized)


def parse_modify_order_op(name: str):
    sdk = get_sdk_module()
    normalized = normalize_enum_label(name)
    if normalized not in {"NORMAL", "CANCEL", "DISABLE", "ENABLE", "DELETE"}:
        raise MoomooTraderError(
            "modify operation には NORMAL / CANCEL / DISABLE / ENABLE / DELETE のみ指定できます。"
        )
    return getattr(sdk.ModifyOrderOp, normalized)


def get_trade_handler_bases() -> Tuple[Any, Any]:
    sdk = get_sdk_module()
    return sdk.TradeOrderHandlerBase, sdk.TradeDealHandlerBase


def configure_sdk_logging(module_name: str) -> None:
    try:
        ft_logger = importlib.import_module("{}.common.ft_logger".format(module_name))
    except ImportError:
        return

    ft_logger.logger.console_level = logging.CRITICAL + 1


def _localize_api_message(payload: Any) -> str:
    message = str(payload)

    if "has not yet agreed to the disclaimer" in message:
        url_match = re.search(r"https?://\S+", message)
        url_suffix = ""
        if url_match:
            url_suffix = " " + url_match.group(0)
        return (
            "現在の証券口座で免責事項への同意が未完了です。"
            " 表示されたページで同意を完了し、OpenD を再起動してください。" + url_suffix
        )

    if "No available real accounts" in message:
        return "利用可能な実口座が見つかりません。OpenD のログイン状態と口座の市場権限を確認してください。"

    if "No available paper accounts" in message:
        return "利用可能なシミュレート口座が見つかりません。OpenD のログイン状態と口座の市場権限を確認してください。"

    if "Nonexisting acc_id" in message:
        return "指定した acc_id が見つかりません。`moomoo-accounts` の出力を確認してください。"

    if "does not support converting to this currency" in message:
        return "対象口座は指定通貨への換算に対応していません。口座通貨へフォールバックします。"

    if "unlock trade" in message.lower():
        return "取引アンロックに失敗しました。OpenD の取引暗証番号設定を確認してください。"

    return message
