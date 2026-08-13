"""lib/datasource.py のテスト (ネットワークアクセスなし)。

確定足の判定 (drop_forming_bar) が本命。ここが壊れると形成中の足が混ざり、
「抜けた」が引け後に戻る誤検知＝look-ahead バイアスになる。
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
import pytest

from lib.datasource import (
    MARKET_CONFIG,
    _quiet_yfinance,
    detect_market,
    drop_forming_bar,
    normalize_instrument_type,
    normalize_ticker,
)


@pytest.mark.parametrize(
    ("ticker", "expected"),
    [
        ("7203", "jp"),      # 4 桁数字 = JP
        ("7203.T", "jp"),    # サフィックス付きでも JP
        ("AAPL", "us"),
        ("aapl", "us"),      # 小文字も判定できる
        ("VTI", "us"),
        ("A123", "us"),      # 4 文字でも先頭が数字でなければ US
        ("12345", "us"),     # 5 桁は JP の証券コードではない
    ],
)
def test_detect_market(ticker: str, expected: str) -> None:
    """ティッカーから市場を推定できる。"""
    assert detect_market(ticker) == expected


@pytest.mark.parametrize(
    ("ticker", "market", "expected"),
    [
        ("7203", "jp", "7203.T"),
        ("7203.T", "jp", "7203.T"),   # 二重付与しない
        ("7203.t", "jp", "7203.T"),   # 小文字サフィックスも正規化される
        ("AAPL", "us", "AAPL"),
        ("aapl", "us", "AAPL"),
        (" 7203 ", "jp", "7203.T"),   # 前後の空白を落とす
    ],
)
def test_normalize_ticker(ticker: str, market: str, expected: str) -> None:
    """yfinance 用シンボルへ正規化できる。"""
    assert normalize_ticker(ticker, market) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("EQUITY", "equity"),      # yfinance が個別株に返す値 (AAPL / 7203.T で実測)
        ("ETF", "etf"),            # 同 ETF (VTI / 1306.T で実測)
        ("etf", "etf"),            # 大小文字は問わない
        (" ETF ", "etf"),
        ("MUTUALFUND", None),      # 未対応の種別は判定できない扱いにする
        ("", None),
        (None, None),              # メタデータに項目が無い場合
    ],
)
def test_normalize_instrument_type(raw: str | None, expected: str | None) -> None:
    """yfinance の instrumentType を上位の語彙に正規化できる。"""
    assert normalize_instrument_type(raw) == expected


def _frame(dates: list[dt.datetime], tz) -> pd.DataFrame:
    """テスト用の最小 OHLCV を作る。

    Args:
        dates: index に使う naive な日時のリスト (古い順)。
        tz: 取引所タイムゾーン。

    Returns:
        close 列だけを持つ DataFrame。
    """
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz=tz) for d in dates])
    return pd.DataFrame({"close": range(len(dates))}, index=idx)


def test_drop_forming_bar_empty() -> None:
    """空の DataFrame はそのまま返す。"""
    empty = pd.DataFrame()
    assert drop_forming_bar(empty, "1d", "jp").empty


def test_drop_forming_bar_unknown_interval() -> None:
    """未対応の interval は明示的に落とす (黙って通すと誤判定になる)。"""
    tz = MARKET_CONFIG["jp"]["tz"]
    df = _frame([dt.datetime(2026, 8, 6)], tz)
    with pytest.raises(ValueError, match="未対応の interval"):
        drop_forming_bar(df, "1h", "jp")


def test_drop_forming_bar_daily_drops_today_before_close() -> None:
    """JP の場中 (引け+バッファ前) は当日足を形成中として落とす。"""
    tz = MARKET_CONFIG["jp"]["tz"]
    df = _frame([dt.datetime(2026, 8, 5), dt.datetime(2026, 8, 6)], tz)
    now = dt.datetime(2026, 8, 6, 12, 0, tzinfo=tz)  # 12:00 JST = 場中
    out = drop_forming_bar(df, "1d", "jp", now=now)
    assert len(out) == 1
    assert out.index[-1].date() == dt.date(2026, 8, 5)


def test_drop_forming_bar_daily_keeps_today_after_close() -> None:
    """引け+バッファ後は当日足を確定として残す。"""
    tz = MARKET_CONFIG["jp"]["tz"]
    df = _frame([dt.datetime(2026, 8, 5), dt.datetime(2026, 8, 6)], tz)
    now = dt.datetime(2026, 8, 6, 16, 30, tzinfo=tz)  # 16:00 のバッファ後
    out = drop_forming_bar(df, "1d", "jp", now=now)
    assert len(out) == 2


def test_drop_forming_bar_daily_keeps_past_bar() -> None:
    """末尾が過去日なら時刻に関係なく確定。"""
    tz = MARKET_CONFIG["jp"]["tz"]
    df = _frame([dt.datetime(2026, 8, 5), dt.datetime(2026, 8, 6)], tz)
    now = dt.datetime(2026, 8, 7, 9, 0, tzinfo=tz)  # 翌日の寄り前
    assert len(drop_forming_bar(df, "1d", "jp", now=now)) == 2


def test_drop_forming_bar_us_uses_new_york_close() -> None:
    """US は NY 16:30 を基準に判定する (JP の閾値を流用しない)。"""
    tz = MARKET_CONFIG["us"]["tz"]
    df = _frame([dt.datetime(2026, 8, 5), dt.datetime(2026, 8, 6)], tz)
    intraday = dt.datetime(2026, 8, 6, 12, 0, tzinfo=tz)
    after = dt.datetime(2026, 8, 6, 17, 0, tzinfo=tz)
    assert len(drop_forming_bar(df, "1d", "us", now=intraday)) == 1
    assert len(drop_forming_bar(df, "1d", "us", now=after)) == 2


def test_drop_forming_bar_weekly_drops_current_week() -> None:
    """当週の週足は金曜引け+バッファまで形成中として落とす。

    yfinance の週足 index は週初 (月曜)。2026-08-03 は月曜、8/7 が金曜。
    """
    tz = MARKET_CONFIG["jp"]["tz"]
    df = _frame([dt.datetime(2026, 7, 27), dt.datetime(2026, 8, 3)], tz)
    now = dt.datetime(2026, 8, 7, 12, 11, tzinfo=tz)  # 金曜の場中
    out = drop_forming_bar(df, "1wk", "jp", now=now)
    assert len(out) == 1
    assert out.index[-1].date() == dt.date(2026, 7, 27)


def test_drop_forming_bar_weekly_keeps_completed_week() -> None:
    """金曜引け+バッファを過ぎた週足は確定として残す。"""
    tz = MARKET_CONFIG["jp"]["tz"]
    df = _frame([dt.datetime(2026, 7, 27), dt.datetime(2026, 8, 3)], tz)
    now = dt.datetime(2026, 8, 7, 16, 30, tzinfo=tz)
    assert len(drop_forming_bar(df, "1wk", "jp", now=now)) == 2


# --- _quiet_yfinance: ETF の 404 ログ抑制 ---


def test_quiet_yfinance_silences_inside() -> None:
    """ブロック内では yfinance のログが CRITICAL まで抑制される。"""
    logger = logging.getLogger("yfinance")
    logger.setLevel(logging.WARNING)
    with _quiet_yfinance():
        assert logger.level == logging.CRITICAL
    assert logger.level == logging.WARNING


def test_quiet_yfinance_restores_on_exception() -> None:
    """例外で抜けてもレベルを戻す (以降の失敗ログを黙らせない)。"""
    logger = logging.getLogger("yfinance")
    logger.setLevel(logging.INFO)
    with pytest.raises(RuntimeError), _quiet_yfinance():
        raise RuntimeError("取得に失敗")
    assert logger.level == logging.INFO
