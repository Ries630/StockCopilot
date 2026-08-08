"""lib/indicators.py のテスト (ネットワークアクセスなし)。

daily_stats のレンジ算出が本命。high20/low20 は **末尾の足を除いた** 直前 20 本から
取らなければならない。末尾を含めると「自分自身を超えたか」を見ることになり、
ブレイク判定が常に不成立になる。
"""

from __future__ import annotations

import pandas as pd
import pytest

from lib.indicators import compute, daily_stats


def _ohlcv(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    """(open, high, low, close, volume) のリストから DataFrame を作る。"""
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])


def _flat_with_spike(n: int = 25) -> pd.DataFrame:
    """末尾 1 本だけが極端な値を持つ平坦な系列。

    末尾を除外できているかを、レンジ (100/90) と外れ値 (999/1) の差で検出する。
    """
    rows = [(95.0, 100.0, 90.0, 95.0, 1000.0) for _ in range(n - 1)]
    rows.append((95.0, 999.0, 1.0, 95.0, 1000.0))
    return _ohlcv(rows)


@pytest.mark.parametrize("bars", [0, 1, 21])
def test_daily_stats_insufficient(bars: int) -> None:
    """22 本未満は None (直前 20 本 + 末尾 1 本 + TR 用の前日終値が要る)。"""
    df = _flat_with_spike(bars + 1).head(bars) if bars else pd.DataFrame()
    assert daily_stats(df) is None


def test_daily_stats_range_excludes_last_bar() -> None:
    """high20/low20 が末尾の足を含まない。"""
    stats = daily_stats(_flat_with_spike())
    assert stats is not None
    assert stats["high20"] == 100.0  # 末尾の 999 を拾っていない
    assert stats["low20"] == 90.0    # 末尾の 1 を拾っていない


def test_daily_stats_values() -> None:
    """ATR・レンジ位置・売買代金が定義どおり計算される。"""
    stats = daily_stats(_flat_with_spike())
    assert stats is not None
    # TR は直近 14 本平均。平坦部 13 本が 10.0、末尾が 998.0
    assert stats["atr"] == pytest.approx((13 * 10.0 + 998.0) / 14)
    assert stats["atr_pct"] == pytest.approx(stats["atr"] / 95.0 * 100)
    # 終値 95 はレンジ 90〜100 のちょうど中央
    assert stats["range_pos"] == pytest.approx(0.5)
    assert stats["turnover_avg20"] == pytest.approx(95.0 * 1000.0)
    assert stats["closed_bars"] == 25


def test_daily_stats_zero_span_does_not_divide_by_zero() -> None:
    """レンジ幅ゼロ (値動きなし) でも range_pos を返す。"""
    df = _ohlcv([(50.0, 50.0, 50.0, 50.0, 10.0) for _ in range(25)])
    stats = daily_stats(df)
    assert stats is not None
    assert stats["range_pos"] == 0.5


def _trend(n: int) -> pd.DataFrame:
    """単調増加の系列 (指標が定義される最小限の素性)。"""
    rows = []
    for i in range(n):
        c = 100.0 + i * 0.5
        rows.append((c, c + 1.0, c - 1.0, c, 1000.0))
    return _ohlcv(rows)


@pytest.mark.parametrize("bars", [0, 29])
def test_compute_insufficient(bars: int) -> None:
    """30 本未満は error を返す (指標の計算に足りない)。"""
    out = compute(_trend(bars) if bars else pd.DataFrame())
    assert out["error"] == "insufficient"
    assert out["bars"] == bars


def test_compute_returns_full_indicator_set() -> None:
    """200 本以上あれば EMA200 まで含めた一式が揃う。"""
    out = compute(_trend(250))
    expected = {
        "close", "rsi", "macd_hist", "macd_hist_prev", "ema20", "ema50", "ema200",
        "bb_high", "bb_low", "bb_pctb", "atr", "stochrsi_k", "obv", "obv_prev10",
        "adx", "dip", "dim", "high20", "low20", "high60", "low60", "bars",
    }
    assert set(out) == expected
    assert out["ema200"] is not None
    assert out["bars"] == 250


def test_compute_ema200_is_none_when_history_is_short() -> None:
    """200 本未満では EMA200 を出さない (不完全な値で長期トレンドを語らないため)。"""
    out = compute(_trend(100))
    assert out["ema200"] is None
    assert out["ema50"] is not None
