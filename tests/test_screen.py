"""screen.py の母集団構築と通過判定のテスト (ネットワークアクセスなし)。

母集団の組み立ては「何を取りに行くか」を決めており、
ここが崩れると保有銘柄の混入 (public リポジトリ規範に関わる) や
同一銘柄の二重取得 (yfinance は 1 銘柄 1 リクエスト) が起きる。

通過判定は「候補の定義」そのもので、閾値が効かなくなると候補が緩み、
分析工数を無駄に食う。閾値は境界を明示的に固定する。
"""

from __future__ import annotations

import pandas as pd
import pytest

import screen


def _patch_lists(
    monkeypatch: pytest.MonkeyPatch,
    *,
    watch_jp: list[str] | None = None,
    watch_us: list[str] | None = None,
    uni_jp: list[str] | None = None,
    uni_us: list[str] | None = None,
    held: set[str] | None = None,
) -> None:
    """母集団の入力 4 層と保有を差し替える (実ファイル・実保有に依存させない)。"""
    monkeypatch.setattr(screen, "WATCHLIST_JP", watch_jp or [])
    monkeypatch.setattr(screen, "WATCHLIST_US", watch_us or [])
    monkeypatch.setattr(screen, "UNIVERSE_JP", uni_jp or [])
    monkeypatch.setattr(screen, "UNIVERSE_US", uni_us or [])
    monkeypatch.setattr(screen, "held_tickers", lambda: held or set())


def test_watchlist_is_included(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストは探索ユニバースと並んで母集団に入る。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"])
    assert screen.build_universe("jp", False) == [("1111", "jp"), ("2222", "jp")]


def test_watchlist_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストを先に取りに行く (優先度の高い層から到達させる)。"""
    _patch_lists(monkeypatch, watch_us=["WWW"], uni_us=["UUU"])
    assert [t for t, _ in screen.build_universe("us", False)] == ["WWW", "UUU"]


def test_duplicates_are_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストと探索ユニバースの重複は 1 回だけ取りに行く。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["1111", "2222"])
    assert screen.build_universe("jp", False) == [("1111", "jp"), ("2222", "jp")]


def test_held_excluded_from_watchlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリスト銘柄も保有になれば既定で落ちる (買い済みは候補にしない)。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"], held={"1111"})
    assert screen.build_universe("jp", False) == [("2222", "jp")]


def test_include_held_keeps_watchlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """--include-held では保有と重なるウォッチリスト銘柄も残る。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"], held={"1111"})
    assert screen.build_universe("jp", True) == [("1111", "jp"), ("2222", "jp")]


def test_market_filter_splits_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    """--market jp は JP 側のウォッチリストと探索ユニバースだけを組む。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], watch_us=["WWW"], uni_us=["UUU"])
    assert screen.build_universe("jp", False) == [("1111", "jp")]


def test_missing_holdings_does_not_break(monkeypatch: pytest.MonkeyPatch) -> None:
    """Investment の生成物が無くても母集団は組める (保有の除外だけ効かない)。"""
    _patch_lists(monkeypatch, watch_jp=["1111"])

    def _raise() -> set[str]:
        raise FileNotFoundError("report_data_*.json が見つからない")

    monkeypatch.setattr(screen, "held_tickers", _raise)
    assert screen.build_universe("jp", False) == [("1111", "jp")]


# --- screen_one: 通過判定 ---
#
# atr_pct=1.0 に固定してあるので、変化率 (%) がそのまま move_atr になる。
# atr=10.0 なので、20 日レンジからの超過額 10.0 が break_atr 1.0 に対応する。
_BASE_STATS = {
    "atr": 10.0,
    "atr_pct": 1.0,
    "high20": 499.0,
    "low20": 400.0,
    "range_pos": 1.0,
    "turnover_avg20": 1e9,
    "closed_bars": 60,
}


def _patch_bar(
    monkeypatch: pytest.MonkeyPatch, closes: list[float], **stats_override: float
) -> None:
    """終値 2 本と統計を差し替える (yfinance を叩かせない)。"""
    df = pd.DataFrame({"close": closes})
    monkeypatch.setattr(screen, "fetch_ohlcv", lambda *a, **kw: df)
    monkeypatch.setattr(screen, "daily_stats", lambda _df: {**_BASE_STATS, **stats_override})


def test_marginal_break_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """ノイズ幅の突破 (0.02 ATR) は候補にしない。#3 の実測ケース。"""
    _patch_bar(monkeypatch, [499.0, 499.2])
    assert screen.screen_one("TEST", "us") is None


def test_break_at_threshold_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """閾値ちょうどの突破は通す (境界は通過側)。"""
    _patch_bar(monkeypatch, [501.9, 502.0])  # 499.0 + 3.0 = 0.3 ATR
    row = screen.screen_one("TEST", "us")
    assert row is not None
    assert row["break_in_atr"] == pytest.approx(screen.MIN_BREAK_IN_ATR)
    assert "突破" in row["reasons"][0]


def test_marginal_break_excluded_from_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    """変化率だけで通った候補に、閾値未満の突破を理由として混ぜない。"""
    _patch_bar(monkeypatch, [480.0, 499.2])  # move 4.0 ATR / break 0.02 ATR
    row = screen.screen_one("TEST", "us")
    assert row is not None
    assert row["reasons"] == ["直近足の動きが日次 ATR の 4.0 倍"]
    assert row["score"] == pytest.approx(4.0)


def test_downside_break_uses_same_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """下抜けも同じ閾値で判定する (方向で非対称にしない)。"""
    _patch_bar(monkeypatch, [396.2, 396.0])  # 400.0 - 4.0 = 0.4 ATR
    row = screen.screen_one("TEST", "us")
    assert row is not None
    assert "下に突破" in row["reasons"][0]


def test_marginal_downside_break_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """下抜けもノイズ幅なら候補にしない。"""
    _patch_bar(monkeypatch, [399.1, 399.0])  # 0.1 ATR
    assert screen.screen_one("TEST", "us") is None


def test_threshold_comes_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """閾値は設定値を参照する (ハードコードしていない)。"""
    _patch_bar(monkeypatch, [499.0, 499.2])
    monkeypatch.setattr(screen, "MIN_BREAK_IN_ATR", 0.0)
    assert screen.screen_one("TEST", "us") is not None


def test_illiquid_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """流動性フロア未満は突破していても候補にしない。"""
    _patch_bar(monkeypatch, [501.9, 502.0], turnover_avg20=1e7)
    assert screen.screen_one("TEST", "us") is None
