import unittest
from datetime import datetime, timedelta, timezone

from moomoo_trader.time_utils import (
    market_timezone,
    market_today_text,
    parse_timestamp,
    to_market_aware,
    to_market_iso,
)


class MarketTimezoneTest(unittest.TestCase):
    def test_米国東部の冬時間をUTCへ正規化する(self):
        parsed = to_market_aware("2026-01-15 10:00:00", "US")

        assert parsed is not None
        self.assertEqual(datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc), parsed)

    def test_米国東部の夏時間をUTCへ正規化する(self):
        parsed = to_market_aware("2026-07-15 10:00:00", "US")

        assert parsed is not None
        self.assertEqual(datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc), parsed)

    def test_日本時間をUTCへ正規化する(self):
        parsed = to_market_aware("2026-07-15 09:00:00", "JP")

        assert parsed is not None
        self.assertEqual(datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc), parsed)

    def test_オフセット付きの時刻はそのままUTCへ変換する(self):
        parsed = to_market_aware("2026-01-15T10:00:00+09:00", "JP")

        assert parsed is not None
        self.assertEqual(datetime(2026, 1, 15, 1, 0, tzinfo=timezone.utc), parsed)

    def test_市場現地の日付を返す(self):
        now = datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc)

        self.assertEqual("2026-03-29", market_today_text("US", now))
        self.assertEqual("2026-03-30", market_today_text("JP", now))

    def test_不正な時刻はNoneを返す(self):
        self.assertIsNone(to_market_aware("N/A", "JP"))
        self.assertIsNone(to_market_iso(None, "JP"))

    def test_夏時間境界を判定する(self):
        winter_offset = market_timezone("US").utcoffset(datetime(2026, 3, 7, 12))
        summer_offset = market_timezone("US").utcoffset(datetime(2026, 3, 9, 12))
        self.assertEqual(-5, winter_offset.total_seconds() // 3600)
        self.assertEqual(-4, summer_offset.total_seconds() // 3600)

    def test_夏時間終了の重複時刻を往復変換できる(self):
        tz = market_timezone("US")
        utc_before_end = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
        utc_after_end = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)

        first = utc_before_end.astimezone(tz)
        second = utc_after_end.astimezone(tz)

        self.assertEqual(datetime(2026, 11, 1, 1, 30), first.replace(tzinfo=None))
        self.assertEqual(datetime(2026, 11, 1, 1, 30), second.replace(tzinfo=None))
        self.assertEqual(timedelta(hours=-4), first.utcoffset())
        self.assertEqual(timedelta(hours=-5), second.utcoffset())
        self.assertEqual(utc_before_end, first.astimezone(timezone.utc))
        self.assertEqual(utc_after_end, second.astimezone(timezone.utc))

    def test_夏時間開始の境界を変換する(self):
        self.assertEqual(
            datetime(2026, 3, 8, 6, 59, 59, tzinfo=timezone.utc),
            to_market_aware("2026-03-08 01:59:59", "US"),
        )
        self.assertEqual(
            datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc),
            to_market_aware("2026-03-08 03:00:00", "US"),
        )

    def test_パースは既存の挙動を保つ(self):
        parsed = parse_timestamp("2026-03-30T09:00:00")

        assert parsed is not None
        self.assertIsNone(parsed.tzinfo)


if __name__ == "__main__":
    unittest.main()
