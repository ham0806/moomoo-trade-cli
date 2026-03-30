import json
from typing import Any, Dict, List, Optional

from .config import OpenDConfig
from .errors import AccountSelectionError, OpenDConnectionError
from .json_utils import sanitize_for_json, stringify_identifier
from .sdk import (
    close_context,
    create_trade_context_for_security_firm,
    ensure_ret_ok,
    get_security_firm_candidates,
    normalize_trd_env_label,
)

ACCOUNT_OUTPUT_FIELDS = (
    "acc_id",
    "trd_env",
    "acc_type",
    "trdmarket_auth",
    "uni_card_num",
    "card_num",
    "security_firm",
    "jp_acc_type",
)


def list_accounts(config: OpenDConfig) -> List[Dict[str, Any]]:
    discovered: Dict[tuple, Dict[str, Any]] = {}
    last_error = None

    for security_firm in get_security_firm_candidates():
        context = create_trade_context_for_security_firm(config.host, config.port, security_firm)

        try:
            try:
                ret, data = context.get_acc_list()
            except Exception as exc:  # pragma: no cover - 実 SDK の例外差分吸収用
                last_error = exc
                continue

            ensure_ret_ok(ret, data, "口座一覧取得")
            for account in normalize_accounts_frame(data):
                key = (account.get("acc_id"), account.get("trd_env"))
                if key not in discovered:
                    discovered[key] = account
        finally:
            close_context(context)

    if discovered:
        return list(discovered.values())

    if last_error is not None:
        raise OpenDConnectionError(
            "口座一覧の取得に失敗しました。OpenD が起動していてログイン済みか確認してください。"
        ) from last_error

    return []


def normalize_accounts_frame(frame) -> List[Dict[str, Any]]:
    if frame is None:
        return []

    records = frame.to_dict(orient="records")
    return [normalize_account_record(record) for record in records]


def normalize_account_record(record: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {
        "acc_id": stringify_identifier(record.get("acc_id")),
        "trd_env": normalize_trd_env_label(record.get("trd_env")),
        "acc_type": sanitize_for_json(record.get("acc_type")),
        "trdmarket_auth": sanitize_for_json(record.get("trdmarket_auth")),
        "uni_card_num": stringify_identifier(record.get("uni_card_num")),
        "card_num": stringify_identifier(record.get("card_num")),
        "security_firm": sanitize_for_json(record.get("security_firm")),
        "jp_acc_type": sanitize_for_json(record.get("jp_acc_type")),
        "acc_status": sanitize_for_json(record.get("acc_status")),
    }
    return normalized


def summarize_account(account: Dict[str, Any]) -> Dict[str, Any]:
    return {field: account.get(field) for field in ACCOUNT_OUTPUT_FIELDS}


def select_account(
    accounts: List[Dict[str, Any]],
    requested_env: str,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    if account_id is not None:
        account_id_text = str(account_id)
        matches = [account for account in accounts if account.get("acc_id") == account_id_text]
        if not matches:
            raise AccountSelectionError(f"account_id={account_id_text} の口座が見つかりません。")

        env_matches = [account for account in matches if account.get("trd_env") == requested_env]
        if not env_matches:
            raise AccountSelectionError(
                f"account_id={account_id_text} は {requested_env} 環境の口座ではありません。"
            )

        return env_matches[0]

    candidates = [account for account in accounts if account.get("trd_env") == requested_env]
    if not candidates:
        raise AccountSelectionError(f"{requested_env} 環境の口座が見つかりません。")

    if len(candidates) > 1:
        summaries = [summarize_account(account) for account in candidates]
        raise AccountSelectionError(
            "対象口座を一意に決められません。`--account-id` を指定してください。"
            f" 候補: {json.dumps(summaries, ensure_ascii=False)}"
        )

    return candidates[0]
