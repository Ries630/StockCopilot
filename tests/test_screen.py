"""screen.py の母集団構築と通過判定のテスト (ネットワークアクセスなし)。

母集団の組み立ては「何を取りに行くか」を決めており、
ここが崩れると保有銘柄の混入 (public リポジトリ規範に関わる) や
同一銘柄の二重取得 (yfinance は 1 銘柄 1 リクエスト) が起きる。

通過判定は「候補の定義」そのもので、閾値が効かなくなると候補が緩み、
分析工数を無駄に食う。閾値は境界を明示的に固定する。
"""

from __future__ import annotations

import json
import sys

import pandas as pd
import pytest

import screen
from lib.holdings import HeldTickers


def _held(tickers: set[str] | None = None, **overrides) -> HeldTickers:
    """held_tickers() の戻り値を組み立てる (実保有・実ジャーナルに依存させない)。"""
    fields = {
        "tickers": set(tickers or ()),
        "as_of": "2026-07-22",
        "executions_read": 0,
        "executions_applied": 0,
        "warnings": [],
    }
    fields.update(overrides)
    return HeldTickers(**fields)


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
    monkeypatch.setattr(screen, "held_tickers", lambda: _held(held))


def test_watchlist_is_included(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストは探索ユニバースと並んで母集団に入る。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"])
    assert screen.build_universe("jp", False)[0] == [("1111", "jp"), ("2222", "jp")]


def test_watchlist_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストを先に取りに行く (優先度の高い層から到達させる)。"""
    _patch_lists(monkeypatch, watch_us=["WWW"], uni_us=["UUU"])
    assert [t for t, _ in screen.build_universe("us", False)[0]] == ["WWW", "UUU"]


def test_duplicates_are_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストと探索ユニバースの重複は 1 回だけ取りに行く。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["1111", "2222"])
    assert screen.build_universe("jp", False)[0] == [("1111", "jp"), ("2222", "jp")]


def test_held_excluded_from_watchlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリスト銘柄も保有になれば既定で落ちる (買い済みは候補にしない)。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"], held={"1111"})
    assert screen.build_universe("jp", False)[0] == [("2222", "jp")]


def test_include_held_keeps_watchlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """--include-held では保有と重なるウォッチリスト銘柄も残る。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"], held={"1111"})
    assert screen.build_universe("jp", True)[0] == [("1111", "jp"), ("2222", "jp")]


def test_market_filter_splits_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    """--market jp は JP 側のウォッチリストと探索ユニバースだけを組む。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], watch_us=["WWW"], uni_us=["UUU"])
    assert screen.build_universe("jp", False)[0] == [("1111", "jp")]


def test_missing_holdings_does_not_break(monkeypatch: pytest.MonkeyPatch) -> None:
    """Investment の生成物が無くても母集団は組める (保有の除外だけ効かない)。"""
    _patch_lists(monkeypatch, watch_jp=["1111"])

    def _raise() -> set[str]:
        raise FileNotFoundError("report_data_*.json が見つからない")

    monkeypatch.setattr(screen, "held_tickers", _raise)
    universe, held = screen.build_universe("jp", False)
    assert universe == [("1111", "jp")]
    assert held is None


# --- held_summary: 除外の内訳の表示 ---
#
# 母集団から何が落ちたかが見えないと、保有銘柄が候補に出たときに
# 「執行記録の漏れなのか、保有データが読めていないのか」を切り分けられない。


def test_held_summary_shows_as_of_and_execution_counts() -> None:
    """除外の内訳に基準日と執行記録の件数を出す。"""
    text = screen.held_summary(_held({"1111"}, executions_read=3, executions_applied=2), False)
    assert "2026-07-22" in text
    assert "3 件中 2 件" in text


def test_held_summary_states_when_no_executions() -> None:
    """執行記録が無いことは明示する (拾い忘れと区別できるように)。"""
    assert "執行記録なし" in screen.held_summary(_held({"1111"}), False)


def test_held_summary_surfaces_journal_warnings() -> None:
    """解釈できないジャーナルの行は警告として出力に載る (黙って落とさない)。"""
    text = screen.held_summary(_held({"1111"}, warnings=["84 行目: 残株数 (`残 N株`) が読めない"]), False)
    assert "[warn] journal 84 行目" in text


def test_held_summary_warns_when_holdings_unreadable() -> None:
    """保有を読めずに除外できなかったことは警告として出す。"""
    assert screen.held_summary(None, False).startswith("[warn]")


def test_held_summary_notes_include_held() -> None:
    """--include-held では除外していないことを明示する。"""
    assert "--include-held" in screen.held_summary(None, True)


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


def test_screen_one_records_confirmed_bar_even_without_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候補ゼロでも、市場の確定足日は中間表現へ渡せる。"""
    df = pd.DataFrame(
        {"close": [499.0, 499.2]},
        index=pd.to_datetime(["2026-08-19", "2026-08-20"]),
    )
    monkeypatch.setattr(screen, "fetch_ohlcv", lambda *a, **kw: df)
    monkeypatch.setattr(screen, "daily_stats", lambda _df: None)
    bars: dict[str, str] = {}

    assert screen.screen_one("TEST", "us", bars) is None
    assert bars == {"us": "2026-08-20"}


def test_ensure_bar_dates_fills_market_missing_from_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """市場限定や全銘柄除外でも、もう一方の確定足日を独立取得する。"""
    df = pd.DataFrame(
        {"close": [100.0]}, index=pd.to_datetime(["2026-08-20"])
    )
    monkeypatch.setattr(screen, "fetch_ohlcv", lambda *a, **kw: df)
    bars = {"jp": "2026-08-21"}

    screen.ensure_bar_dates(bars)

    assert bars == {"jp": "2026-08-21", "us": "2026-08-20"}


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


# --- attach_earnings: 決算注記 ---


def test_attach_earnings_adds_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """候補に決算注記が付く。"""
    monkeypatch.setattr(screen, "earnings_note", lambda t, m: f"決算 {t}/{m}")
    candidates = [{"ticker": "TEST", "market": "us"}]
    screen.attach_earnings(candidates)
    assert candidates[0]["earnings_note"] == "決算 TEST/us"


def test_attach_earnings_survives_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """決算の取得に失敗しても候補は落とさない (決算の有無は採否と別)。"""

    def _boom(ticker: str, market: str) -> str:
        raise RuntimeError("yfinance が落ちた")

    monkeypatch.setattr(screen, "earnings_note", _boom)
    candidates = [{"ticker": "TEST", "market": "us"}]
    screen.attach_earnings(candidates)
    assert candidates[0]["earnings_note"] == ""


def test_json_candidate_uses_report_keys_and_percent_units() -> None:
    """LLMへキー名・単位変換を委ねない。"""
    row = {
        "ticker": "TEST",
        "market": "us",
        "close": 121.0,
        "change_pct": 2.5,
        "score": 1.2,
        "reasons": ["20日レンジを上に突破"],
        "low20": 100.0,
        "high20": 120.0,
        "range_pos": 1.05,
        "atr_pct": 2.0,
        "turnover_avg20": 12_345_678.0,
        "name": "Test Company",
        "earnings_note": "⚠ 決算 2026-08-25",
    }

    result = screen.json_candidate(row)

    assert result["market"] == "us"
    assert result["currency"] == "USD"
    assert result["price"] == 121.0
    assert result["score_atr"] == 1.2
    assert result["pass_reason"] == "20日レンジを上に突破"
    assert result["range"] == {"low": 100.0, "high": 120.0, "pos_pct": 105.0}
    assert result["turnover"] == "$12,345,678"
    assert result["name"] == "Test Company"
    assert result["earnings"] == {"note": "⚠ 決算 2026-08-25", "warn": True}


def test_json_output_is_machine_readable_and_counts_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """取得警告をJSONの前へ出さず、件数と内容を構造化する。"""
    universe = [("GOOD", "jp"), ("FAIL", "jp")]
    good = {
        "ticker": "GOOD",
        "market": "jp",
        "close": 110.0,
        "change_pct": 1.0,
        "score": 1.0,
        "reasons": ["直近足の動き"],
        "low20": 100.0,
        "high20": 120.0,
        "range_pos": 0.5,
        "atr_pct": 2.0,
        "turnover_avg20": 1_000_000_000.0,
    }

    def fake_screen_one(ticker: str, market: str, bars: dict[str, str]) -> dict:
        bars[market] = "2026-08-20"
        if ticker == "FAIL":
            raise RuntimeError("取得できない")
        return good

    monkeypatch.setattr(screen, "build_universe", lambda *_: (universe, None))
    monkeypatch.setattr(screen, "screen_one", fake_screen_one)
    monkeypatch.setattr(screen, "ensure_bar_dates", lambda bars: None)
    monkeypatch.setattr(sys, "argv", ["screen.py", "--json", "--market", "jp"])

    screen.main()

    output = capsys.readouterr().out
    assert output.lstrip().startswith("{")
    parsed = json.loads(output)
    assert parsed["universe"] == 2
    assert parsed["market"] == "jp"
    assert parsed["bars"] == {"jp": "2026-08-20"}
    assert parsed["failures"] == 1
    assert parsed["failure_details"] == [{"ticker": "FAIL", "message": "取得できない"}]
    assert parsed["candidates"][0]["market"] == "jp"
    assert parsed["candidates"][0]["currency"] == "JPY"
    assert parsed["candidates"][0]["range"]["pos_pct"] == 50.0
