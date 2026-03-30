from __future__ import annotations

from .errors import CredentialStoreError


def get_trade_password(credential_name: str) -> str:
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover - 依存未導入時の保険
        raise CredentialStoreError(
            "`keyring` が見つかりません。`mise exec -- uv sync` を再実行してください。"
        ) from exc

    secret = keyring.get_password("moomoo-trade-cli", credential_name)
    if not secret:
        raise CredentialStoreError(
            "Windows 資格情報ストアに `{}` が見つかりません。".format(credential_name)
        )
    return secret
