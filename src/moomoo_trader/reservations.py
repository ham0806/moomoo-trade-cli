from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from .errors import MoomooTraderError
from .models import OrderLedgerEntry

# 予約が有効な状態。これらが残っている間は同じ注文意図を再送しない。
_ACTIVE_STATUSES = {"RESERVED", "SENT", "UNKNOWN", "PLACED"}
_FILE_NAME = "order_intents.json"
_LOCK_FILE_NAME = "order_intents.lock"


@dataclass(frozen=True)
class OrderIntentReservation:
    intent_signature: str
    full_code: str
    side: str
    qty: float
    effective_price: float
    status: str
    created_at: datetime
    updated_at: datetime
    owner_id: str
    attempt_id: str = ""
    order_id: Optional[str] = None
    last_error: Optional[str] = None
    sent_at: Optional[datetime] = None
    reconciled_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_signature": self.intent_signature,
            "full_code": self.full_code,
            "side": self.side,
            "qty": self.qty,
            "effective_price": self.effective_price,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "owner_id": self.owner_id,
            "attempt_id": self.attempt_id,
            "order_id": self.order_id,
            "last_error": self.last_error,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "reconciled_at": self.reconciled_at.isoformat() if self.reconciled_at else None,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "OrderIntentReservation":
        return cls(
            intent_signature=str(payload["intent_signature"]),
            full_code=str(payload.get("full_code") or ""),
            side=str(payload.get("side") or ""),
            qty=float(payload.get("qty") or 0.0),
            effective_price=float(payload.get("effective_price") or 0.0),
            status=str(payload.get("status") or "RESERVED").upper(),
            created_at=_parse_time(payload.get("created_at")),
            updated_at=_parse_time(payload.get("updated_at")),
            owner_id=str(payload.get("owner_id") or ""),
            attempt_id=str(payload.get("attempt_id") or ""),
            order_id=payload.get("order_id"),
            last_error=payload.get("last_error"),
            sent_at=_parse_optional_time(payload.get("sent_at")),
            reconciled_at=_parse_optional_time(payload.get("reconciled_at")),
        )


class ReservationTransaction:
    """予約ストアのロックを保持したまま判定・予約・送信記録を行う。"""

    def __init__(
        self,
        entries: Dict[str, OrderIntentReservation],
        owner_id: str,
    ) -> None:
        self.entries = entries
        self.owner_id = str(owner_id)

    def active_entries(self) -> List[OrderIntentReservation]:
        return [entry for entry in self.entries.values() if entry.status in _ACTIVE_STATUSES]

    def is_blocked(self, intent_signature: str) -> bool:
        entry = self.entries.get(intent_signature)
        return entry is not None and entry.status in _ACTIVE_STATUSES

    def reserve(
        self,
        *,
        intent_signature: str,
        full_code: str,
        side: str,
        qty: float,
        effective_price: float,
    ) -> bool:
        if self.is_blocked(intent_signature):
            return False
        now = _utcnow()
        self.entries[intent_signature] = OrderIntentReservation(
            intent_signature=intent_signature,
            full_code=full_code,
            side=str(side).upper(),
            qty=float(qty),
            effective_price=float(effective_price),
            status="RESERVED",
            created_at=now,
            updated_at=now,
            owner_id=self.owner_id,
            attempt_id=uuid.uuid4().hex,
        )
        return True

    def mark_sent(self, intent_signature: str) -> bool:
        entry = self.entries.get(intent_signature)
        if entry is None or entry.owner_id != self.owner_id:
            return False
        now = _utcnow()
        self.entries[intent_signature] = replace(
            entry,
            status="SENT",
            updated_at=now,
            sent_at=now,
        )
        return True

    def release(self, intent_signature: str) -> None:
        entry = self.entries.get(intent_signature)
        if entry is None or entry.owner_id != self.owner_id:
            return
        if entry.status not in {"RESERVED", "SENT"}:
            return
        del self.entries[intent_signature]


class OrderIntentStore:
    """発注意図を永続化し、プロセスをまたいで排他的に予約する。

    発注結果が不明な注文を再送すると二重注文になるため、送信前に予約を
    ファイルへ書き込み、証券側で決済が確定するまで同じ意図をブロックする。
    予約は口座・取引環境・接続先ごとのディレクトリへ分離する。
    """

    def __init__(
        self,
        state_dir: str,
        *,
        namespace: str = "default",
        lock_timeout_seconds: float = 30.0,
    ) -> None:
        self.state_dir = Path(state_dir) / _safe_namespace(namespace)
        self.namespace = namespace
        self.path = self.state_dir / _FILE_NAME
        self.lock_path = self.state_dir / _LOCK_FILE_NAME
        self.lock_timeout_seconds = lock_timeout_seconds

    @contextmanager
    def transaction(self, owner_id: str) -> Iterator[ReservationTransaction]:
        """ロックを保持したまま、数量判定から送信状態の記録までを行う。

        ブロック内で例外が発生した場合は変更を保存しない。
        """
        with self._locked():
            transaction = ReservationTransaction(self._load(), owner_id)
            yield transaction
            self._save(transaction.entries)

    def reserve(
        self,
        *,
        intent_signature: str,
        full_code: str,
        side: str,
        qty: float,
        effective_price: float,
        owner_id: str,
    ) -> bool:
        """予約を獲得できたら True。既に有効な予約があれば False。"""
        with self.transaction(owner_id) as transaction:
            return transaction.reserve(
                intent_signature=intent_signature,
                full_code=full_code,
                side=side,
                qty=qty,
                effective_price=effective_price,
            )

    def mark_sent(self, intent_signature: str, owner_id: str) -> None:
        with self.transaction(owner_id) as transaction:
            transaction.mark_sent(intent_signature)

    def mark_unknown(self, intent_signature: str, owner_id: str, error: str) -> None:
        self._update(
            intent_signature,
            owner_id=owner_id,
            status="UNKNOWN",
            updated_at=_utcnow(),
            last_error=str(error),
        )

    def mark_placed(self, intent_signature: str, owner_id: str, order_id: str) -> None:
        self._update(
            intent_signature,
            owner_id=owner_id,
            status="PLACED",
            updated_at=_utcnow(),
            order_id=str(order_id),
        )

    def release(self, intent_signature: str, owner_id: str) -> None:
        """自分が作成した未確定の予約だけを解放する。"""
        with self.transaction(owner_id) as transaction:
            transaction.release(intent_signature)

    def is_blocked(self, intent_signature: str) -> bool:
        with self._locked():
            entries = self._load()
            entry = entries.get(intent_signature)
            return entry is not None and entry.status in _ACTIVE_STATUSES

    def active_entries(self) -> List[OrderIntentReservation]:
        with self._locked():
            entries = self._load()
            return [entry for entry in entries.values() if entry.status in _ACTIVE_STATUSES]

    def reconcile(
        self,
        orders: Iterable[OrderLedgerEntry],
        *,
        owner_id: str,
    ) -> List[OrderIntentReservation]:
        """証券側の注文一覧と突き合わせて予約の状態を更新する。

        注文IDが一致する注文だけを照合し、過去の同条件注文とは対応付けない。
        一覧に注文が無いだけでは未受理と断定できないため、予約は解放しない。
        """
        del owner_id  # 所有者に関係なく状態観測だけを行う
        by_order_id: Dict[str, OrderLedgerEntry] = {}
        for candidate in orders:
            if candidate.order_id:
                by_order_id[candidate.order_id] = candidate

        now = _utcnow()
        with self._locked():
            entries = self._load()
            for signature, entry in list(entries.items()):
                if not entry.order_id:
                    # 送信したが注文IDを取得できていない予約は解放しない。
                    entries[signature] = replace(entry, reconciled_at=now)
                    continue
                matched = by_order_id.get(entry.order_id)
                if matched is None:
                    entries[signature] = replace(entry, reconciled_at=now)
                    continue
                if not matched.is_open:
                    # 注文IDが一致し、決済確定した注文だけ解放する。
                    del entries[signature]
                    continue
                entries[signature] = replace(
                    entry,
                    status="PLACED",
                    updated_at=now,
                    reconciled_at=now,
                )
            self._save(entries)
            return list(entries.values())

    def _update(
        self,
        intent_signature: str,
        *,
        owner_id: str,
        **changes: Any,
    ) -> None:
        with self._locked():
            entries = self._load()
            entry = entries.get(intent_signature)
            if entry is None:
                return
            if entry.owner_id != str(owner_id):
                # 他プロセスの予約状態は変更しない。
                return
            entries[intent_signature] = replace(entry, **changes)
            self._save(entries)

    def _load(self) -> Dict[str, OrderIntentReservation]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise MoomooTraderError(
                "注文予約ファイルを読み込めません: {}".format(self.path)
            ) from exc

        if not text.strip():
            return {}

        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise MoomooTraderError(
                "注文予約ファイルが壊れています。二重発注を防ぐため停止しました: {}".format(
                    self.path
                )
            ) from exc

        raw_entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(raw_entries, dict):
            raise MoomooTraderError("注文予約ファイルの形式が不正です: {}".format(self.path))

        entries: Dict[str, OrderIntentReservation] = {}
        for signature, raw in raw_entries.items():
            if not isinstance(raw, dict):
                raise MoomooTraderError("注文予約ファイルの形式が不正です: {}".format(self.path))
            entries[str(signature)] = OrderIntentReservation.from_dict(raw)
        return entries

    def _save(self, entries: Dict[str, OrderIntentReservation]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "namespace": self.namespace,
            "entries": {signature: entry.to_dict() for signature, entry in entries.items()},
        }
        temp_path = self.path.with_name(self.path.name + ".tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(str(temp_path), str(self.path))
        except OSError as exc:
            raise MoomooTraderError(
                "注文予約ファイルを書き込めません: {}".format(self.path)
            ) from exc

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+b")
        try:
            self._acquire(handle)
            try:
                yield
            finally:
                self._release(handle)
        finally:
            handle.close()

    def _acquire(self, handle) -> None:
        deadline = time.monotonic() + self.lock_timeout_seconds
        while True:
            if _try_lock(handle):
                return
            if time.monotonic() >= deadline:
                raise MoomooTraderError(
                    "注文予約ファイルのロックを取得できません: {}".format(self.lock_path)
                )
            time.sleep(0.05)

    def _release(self, handle) -> None:
        _unlock(handle)


def default_state_dir() -> str:
    override = os.environ.get("MOOMOO_TRADER_STATE_DIR", "").strip()
    if override:
        return override

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    if base:
        return os.path.join(base, "moomoo-trade-cli")
    return os.path.join(os.path.expanduser("~"), ".moomoo-trade-cli")


def _safe_namespace(namespace: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(namespace)).strip("._")
    return cleaned or "default"


def _try_lock(handle) -> bool:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
    except OSError:
        return


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise MoomooTraderError("注文予約ファイルの日時が不正です: {}".format(value)) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_optional_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    return _parse_time(value)
