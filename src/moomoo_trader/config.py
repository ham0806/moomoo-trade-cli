import os
from dataclasses import dataclass

from .errors import MoomooTraderError


@dataclass(frozen=True)
class OpenDConfig:
    host: str
    port: int


def load_opend_config() -> OpenDConfig:
    host = os.environ.get("MOOMOO_OPEND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port_raw = os.environ.get("MOOMOO_OPEND_PORT", "11111").strip() or "11111"

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise MoomooTraderError("環境変数 MOOMOO_OPEND_PORT には整数を指定してください。") from exc

    if port < 1 or port > 65535:
        raise MoomooTraderError(
            "環境変数 MOOMOO_OPEND_PORT は 1 から 65535 の範囲で指定してください。"
        )

    return OpenDConfig(host=host, port=port)
