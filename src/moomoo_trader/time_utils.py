from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Optional

_KNOWN_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
)

# 夏時間のない市場の標準時（UTC からの時差）。
_MARKET_FIXED_OFFSET_HOURS = {
    "JP": 9,
    "HK": 8,
    "CN": 8,
    "SG": 8,
}


class _UsEasternTimezone(tzinfo):
    """米国東部時間。夏時間の境界は 2007 年以降の規則に従う。"""

    def utcoffset(self, dt: Optional[datetime]) -> timedelta:
        if dt is not None and _is_us_dst_wall(dt):
            return timedelta(hours=-4)
        return timedelta(hours=-5)

    def dst(self, dt: Optional[datetime]) -> timedelta:
        if dt is not None and _is_us_dst_wall(dt):
            return timedelta(hours=1)
        return timedelta(0)

    def tzname(self, dt: Optional[datetime]) -> str:
        if dt is not None and _is_us_dst_wall(dt):
            return "EDT"
        return "EST"

    def fromutc(self, dt: datetime) -> datetime:
        """UTC から現地時刻へ変換する。夏時間終了の重複時刻も正しく扱う。"""
        utc_naive = dt.replace(tzinfo=None)
        is_dst = _is_us_dst_utc(utc_naive)
        offset_hours = -4 if is_dst else -5
        local = utc_naive + timedelta(hours=offset_hours)
        # 夏時間終了直後の 01:00-02:00 は 2 回現れる。2 回目は標準時（fold=1）。
        fold = 0
        if not is_dst and _is_us_ambiguous_wall(local):
            fold = 1
        return local.replace(tzinfo=self, fold=fold)


_US_EASTERN = _UsEasternTimezone()


def now_local() -> datetime:
    return datetime.now().astimezone()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_text(now: Optional[datetime] = None) -> str:
    return (now or now_local()).strftime("%Y-%m-%d")


def market_today_text(market: Any, now: Optional[datetime] = None) -> str:
    """市場の現地日付を返す。履歴照会の日付ずれを防ぐ。"""
    target = now or now_utc()
    return target.astimezone(market_timezone(market)).strftime("%Y-%m-%d")


def market_timezone(market: Any) -> tzinfo:
    name = str(market or "").strip().upper()
    if name == "US":
        return _US_EASTERN

    offset_hours = _MARKET_FIXED_OFFSET_HOURS.get(name, 0)
    return timezone(timedelta(hours=offset_hours), "UTC{:+d}".format(offset_hours))


def parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    if not text or text == "N/A":
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in _KNOWN_TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def to_market_aware(value: Any, market: Any) -> Optional[datetime]:
    """市場時刻として解釈し、UTC の aware datetime に正規化する。

    取引 API の時刻は取引所の現地時刻で返るため、PC のローカル時刻と
    直接比較してはならない。
    """
    parsed = parse_timestamp(value)
    if parsed is None:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=market_timezone(market))
    return parsed.astimezone(timezone.utc)


def to_iso(value: Any) -> Optional[str]:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return parsed.isoformat()


def to_market_iso(value: Any, market: Any) -> Optional[str]:
    parsed = to_market_aware(value, market)
    if parsed is None:
        return None
    return parsed.isoformat()


def elapsed_seconds(now: datetime, reference: datetime) -> float:
    """aware / naive が混在しても経過秒数を計算する。naive はローカル時刻とみなす。"""
    if now.tzinfo is None and reference.tzinfo is None:
        return (now - reference).total_seconds()

    if now.tzinfo is None:
        now = now.astimezone()
    if reference.tzinfo is None:
        reference = reference.astimezone()
    return (now - reference).total_seconds()


def _is_us_dst_wall(value: datetime) -> bool:
    """現地の壁時計時刻が夏時間かどうか。重複時刻は fold で区別する。"""
    naive = value.replace(tzinfo=None)
    start = _nth_sunday(naive.year, 3, 2) + timedelta(hours=2)
    end = _nth_sunday(naive.year, 11, 1) + timedelta(hours=2)
    if not (start <= naive < end):
        return False
    if _is_us_ambiguous_wall(naive) and getattr(value, "fold", 0) == 1:
        return False
    return True


def _is_us_dst_utc(value: datetime) -> bool:
    """UTC の壁時計時刻が米国東部の夏時間に含まれるかどうか。"""
    start_utc = _nth_sunday(value.year, 3, 2) + timedelta(hours=7)
    end_utc = _nth_sunday(value.year, 11, 1) + timedelta(hours=6)
    return start_utc <= value < end_utc


def _is_us_ambiguous_wall(value: datetime) -> bool:
    """夏時間終了日の 01:00-02:00（2 回現れる時刻）かどうか。"""
    naive = value.replace(tzinfo=None)
    if naive.month != 11:
        return False
    end = _nth_sunday(naive.year, 11, 1) + timedelta(hours=2)
    return end - timedelta(hours=1) <= naive < end


def _nth_sunday(year: int, month: int, nth: int) -> datetime:
    first_day = datetime(year, month, 1)
    days_until_sunday = (6 - first_day.weekday()) % 7
    return first_day + timedelta(days=days_until_sunday + 7 * (nth - 1))
