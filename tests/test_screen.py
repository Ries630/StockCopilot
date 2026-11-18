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
from lib.holdings import EffectiveHoldings


def _held(tickers: set[str] | None = None, **overrides) -> EffectiveHoldings:
    """effective_holdings() の戻り値を組み立てる (実保有・実ジャーナルに依存させない)。"""
    fields = {
        "holdings": [{"ticker": t} for t in sorted(tickers or ())],
        "as_of": "2026-07-22",
        "executions_read": 0,
        "executions_applied": 0,
        "warnings": [],
    }
    fields.update(overrides)
    return EffectiveHoldings(**fields)


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
    monkeypatch.setattr(screen, "effective_holdings", lambda: _held(held))


def test_watchlist_is_included(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストは探索ユニバースと並んで母集団に入る。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"])
    assert screen.build_universe("jp", False)[0] == [screen.Member("1111", "jp", "watch"), screen.Member("2222", "jp", "explore")]


def test_watchlist_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストを先に取りに行く (優先度の高い層から到達させる)。"""
    _patch_lists(monkeypatch, watch_us=["WWW"], uni_us=["UUU"])
    assert [m.ticker for m in screen.build_universe("us", False)[0]] == ["WWW", "UUU"]


def test_duplicates_are_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストと探索ユニバースの重複は 1 回だけ取りに行く。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["1111", "2222"])
    assert screen.build_universe("jp", False)[0] == [screen.Member("1111", "jp", "watch"), screen.Member("2222", "jp", "explore")]


def test_held_excluded_from_watchlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリスト銘柄も保有になれば既定で落ちる (買い済みは候補にしない)。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"], held={"1111"})
    assert screen.build_universe("jp", False)[0] == [screen.Member("2222", "jp", "explore")]


def test_include_held_keeps_watchlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """--include-held では保有と重なるウォッチリスト銘柄も残る。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"], held={"1111"})
    assert screen.build_universe("jp", True)[0] == [screen.Member("1111", "jp", "watch"), screen.Member("2222", "jp", "explore")]


def test_market_filter_splits_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    """--market jp は JP 側のウォッチリストと探索ユニバースだけを組む。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], watch_us=["WWW"], uni_us=["UUU"])
    assert screen.build_universe("jp", False)[0] == [screen.Member("1111", "jp", "watch")]


def test_missing_holdings_does_not_break(monkeypatch: pytest.MonkeyPatch) -> None:
    """Investment の生成物が無くても母集団は組める (保有の除外だけ効かない)。"""
    _patch_lists(monkeypatch, watch_jp=["1111"])

    def _raise() -> set[str]:
        raise FileNotFoundError("report_data_*.json が見つからない")

    monkeypatch.setattr(screen, "effective_holdings", _raise)
    universe, held = screen.build_universe("jp", False)
    assert universe == [screen.Member("1111", "jp", "watch")]
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
    df = pd.DataFrame(
        {"close": closes},
        index=pd.date_range("2026-08-19", periods=len(closes), freq="D"),
    )
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


def test_illiquid_passes_when_floor_is_not_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """フロアをかけない (ウォッチリスト層) なら、フロア未満でも事象ゲートで評価する。"""
    _patch_bar(monkeypatch, [501.9, 502.0], turnover_avg20=1e7)
    assert screen.screen_one("TEST", "us", apply_floor=False) is not None


def test_illiquid_without_event_is_still_rejected_by_event_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """フロアをかけなくても、事象が起きていなければ候補にしない (ADR-0011)。"""
    _patch_bar(monkeypatch, [499.0, 499.2], turnover_avg20=1e7)
    assert screen.screen_one("TEST", "us", apply_floor=False) is None


@pytest.mark.parametrize(
    ("closes", "turnover", "apply_floor", "expected"),
    [
        ([501.9, 502.0], 1e7, True, screen.Rejection.BELOW_FLOOR),
        ([499.0, 499.2], 1e7, False, screen.Rejection.NO_EVENT),
        ([501.9, 502.0], 1e7, False, None),
    ],
)
def test_evaluate_one_classifies_rejection(
    monkeypatch: pytest.MonkeyPatch,
    closes: list[float],
    turnover: float,
    apply_floor: bool,
    expected: screen.Rejection | None,
) -> None:
    """非通過は理由つきで返り、通過したときは理由を持たない。"""
    _patch_bar(monkeypatch, closes, turnover_avg20=turnover)
    outcome = screen.evaluate_one("TEST", "us", apply_floor)
    assert outcome.rejection is expected
    assert (outcome.candidate is None) == (expected is not None)


def test_evaluate_one_reports_insufficient_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """確定足はあるが指標を計算できない銘柄はデータ不足として返り、評価済みに数えない。"""
    df = pd.DataFrame({"close": [500.0]}, index=pd.date_range("2026-08-20", periods=1))
    monkeypatch.setattr(screen, "fetch_ohlcv", lambda *_, **__: df)
    monkeypatch.setattr(screen, "daily_stats", lambda df: None)
    outcome = screen.evaluate_one("TEST", "us")
    assert outcome.rejection is screen.Rejection.INSUFFICIENT
    assert outcome.bar_date == "2026-08-20"
    assert not outcome.evaluated


def test_evaluate_one_reports_empty_fetch_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """上場廃止やティッカーの誤りで空の結果が返った銘柄は、データ不足と分けて返す。"""
    monkeypatch.setattr(screen, "fetch_ohlcv", lambda *_, **__: pd.DataFrame())
    outcome = screen.evaluate_one("TEST", "us")
    assert outcome.rejection is screen.Rejection.UNAVAILABLE
    assert not outcome.evaluated


def test_outcome_requires_candidate_or_rejection() -> None:
    """候補も理由も持たない (または両方持つ) 結果は作れない。"""
    with pytest.raises(ValueError):
        screen.ScreenOutcome(None, "2026-08-20", None)
    with pytest.raises(ValueError):
        screen.ScreenOutcome({"market": "jp"}, "2026-08-20", screen.Rejection.NO_EVENT)


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
    universe = [screen.Member("GOOD", "jp", "explore"), screen.Member("FAIL", "jp", "explore")]
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

    def fake_evaluate_one(ticker: str, market: str, apply_floor: bool) -> screen.ScreenOutcome:
        if ticker == "FAIL":
            raise RuntimeError("取得できない")
        return screen.ScreenOutcome(good, "2026-08-20", None)

    monkeypatch.setattr(screen, "build_universe", lambda *_: (universe, None))
    monkeypatch.setattr(screen, "evaluate_one", fake_evaluate_one)
    monkeypatch.setattr(screen, "ensure_bar_dates", lambda bars: None)
    monkeypatch.setattr(screen, "load_previous_bars", lambda *_: {})
    monkeypatch.setattr(sys, "argv", ["screen.py", "--json", "--market", "jp"])

    screen.main()

    output = capsys.readouterr().out
    assert output.lstrip().startswith("{")
    parsed = json.loads(output)
    assert parsed["bars"] == {"jp": "2026-08-20"}
    assert parsed["bar_status"]["jp"] == {"status": "initial"}
    assert parsed["screen"]["jp"]["universe"] == 2
    assert parsed["screen"]["jp"]["evaluated"] == 1
    assert parsed["screen"]["jp"]["failures"] == 1
    assert parsed["screen"]["jp"]["failure_details"] == [
        {"ticker": "FAIL", "message": "取得できない"}
    ]
    assert parsed["candidates"][0]["market"] == "jp"
    assert parsed["candidates"][0]["currency"] == "JPY"
    assert parsed["candidates"][0]["range"]["pos_pct"] == 50.0


def test_stale_high_score_does_not_consume_updated_market_limit() -> None:
    """停滞市場の上位候補を除外してから上限を適用する。"""
    stale = [
        {"ticker": f"JP{i}", "market": "jp", "score": 100 - i} for i in range(5)
    ]
    updated = {"ticker": "US1", "market": "us", "score": 1}
    stats = {
        "jp": {"selected": 0},
        "us": {"selected": 0},
    }
    status = {
        "jp": {"status": "unchanged", "previous": "2026-08-20"},
        "us": {"status": "updated", "previous": "2026-08-19"},
    }

    selected = screen.select_candidates([*stale, updated], status, stats)

    assert [item["ticker"] for item in selected] == ["US1"]
    assert stats == {"jp": {"selected": 0}, "us": {"selected": 1}}


def test_candidate_zero_requires_an_evaluated_updated_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """評価不能は候補ゼロ観測と区別できる件数になる。"""
    outcomes = {
        "NO_DATA": screen.ScreenOutcome(None, None, screen.Rejection.INSUFFICIENT),
        "NO_MATCH": screen.ScreenOutcome(None, "2026-08-20", screen.Rejection.NO_EVENT),
    }

    def fake(ticker: str, market: str, apply_floor: bool) -> screen.ScreenOutcome:
        return outcomes[ticker]

    monkeypatch.setattr(screen, "evaluate_one", fake)
    _, _, stats, _ = screen.collect_screen(
        [screen.Member("NO_DATA", "jp", "explore"), screen.Member("NO_MATCH", "us", "explore")],
        json_mode=True,
    )

    assert stats["jp"]["evaluated"] == 0
    assert stats["us"]["evaluated"] == 1


def test_human_output_does_not_call_unchanged_market_candidate_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(screen, "build_universe", lambda *_: ([], None))
    monkeypatch.setattr(
        screen, "ensure_bar_dates", lambda bars: bars.update({"jp": "2026-08-20"})
    )
    monkeypatch.setattr(screen, "load_previous_bars", lambda *_: {"jp": "2026-08-20"})
    monkeypatch.setattr(sys, "argv", ["screen.py", "--market", "jp"])

    screen.main()

    output = capsys.readouterr().out
    assert "JP: 新規スクリーニングなし（確定足は前回と同じ）" in output
    assert "候補なし。" not in output


# --- 層別の件数: ウォッチリスト層の非通過内訳 ---


def _layered_run(monkeypatch: pytest.MonkeyPatch) -> tuple[dict, list[bool]]:
    """ウォッチリスト 4 銘柄・探索 2 銘柄を、理由を決め打ちして集計する。"""
    outcomes = {
        "W_PASS": screen.ScreenOutcome({"market": "jp"}, "2026-08-20", None),
        "W_QUIET": screen.ScreenOutcome(None, "2026-08-20", screen.Rejection.NO_EVENT),
        "W_EMPTY": screen.ScreenOutcome(None, None, screen.Rejection.INSUFFICIENT),
        "E_THIN": screen.ScreenOutcome(None, "2026-08-20", screen.Rejection.BELOW_FLOOR),
        "E_QUIET": screen.ScreenOutcome(None, "2026-08-20", screen.Rejection.NO_EVENT),
    }
    floors: list[bool] = []

    def fake(ticker: str, market: str, apply_floor: bool) -> screen.ScreenOutcome:
        floors.append(apply_floor)
        if ticker == "W_FAIL":
            raise RuntimeError("取得できない")
        return outcomes[ticker]

    monkeypatch.setattr(screen, "evaluate_one", fake)
    universe = [
        *(screen.Member(t, "jp", "watch") for t in ("W_PASS", "W_QUIET", "W_EMPTY", "W_FAIL")),
        *(screen.Member(t, "jp", "explore") for t in ("E_THIN", "E_QUIET")),
    ]
    _, _, _, layers = screen.collect_screen(universe, json_mode=True)
    return layers, floors


def test_floor_is_applied_only_to_explore_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """流動性フロアは探索層にだけかかる。"""
    _, floors = _layered_run(monkeypatch)
    assert floors == [False, False, False, False, True, True]


def test_watch_layer_counts_rejections_by_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリスト層は非通過の理由ごとに数える。"""
    layers, _ = _layered_run(monkeypatch)
    assert layers["jp"]["watch"] == {
        "universe": 4, "matched": 1, "no_event": 1, "insufficient": 1, "failures": 1,
    }


def test_explore_layer_has_no_rejection_breakdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """探索層は件数だけで、非通過の内訳を持たない。"""
    layers, _ = _layered_run(monkeypatch)
    assert layers["jp"]["explore"] == {"universe": 2, "matched": 0}


def test_watch_summary_shows_breakdown_without_tickers(monkeypatch: pytest.MonkeyPatch) -> None:
    """表示は件数だけで、ウォッチリストの銘柄を出さない (購入意図そのもののため)。"""
    layers, _ = _layered_run(monkeypatch)
    line = screen.watch_summary("jp", layers["jp"]["watch"])
    assert line == "JP ウォッチリスト 4 銘柄: 通過 1 / 事象なし 1 / データ不足 1 / 取得失敗 1"
    assert "W_" not in line


def test_watch_summary_is_omitted_without_watchlist() -> None:
    """ウォッチリストが空の市場では内訳の行を出さない。"""
    counts = {"universe": 0, "matched": 0, "no_event": 0, "insufficient": 0, "failures": 0}
    assert screen.watch_summary("us", counts) is None


def test_json_output_has_layer_counts_without_tickers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json は層ごとの件数を出し、ウォッチリストの銘柄を出さない。"""
    universe = [screen.Member("SECRET", "jp", "watch")]
    monkeypatch.setattr(screen, "build_universe", lambda *_: (universe, None))
    monkeypatch.setattr(
        screen,
        "evaluate_one",
        lambda *_, **__: screen.ScreenOutcome(None, "2026-08-20", screen.Rejection.NO_EVENT),
    )
    monkeypatch.setattr(screen, "ensure_bar_dates", lambda bars: None)
    monkeypatch.setattr(screen, "load_previous_bars", lambda *_: {})
    monkeypatch.setattr(sys, "argv", ["screen.py", "--json", "--market", "jp"])

    screen.main()

    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed["layers"]["jp"]["watch"]["no_event"] == 1
    assert set(parsed["screen"]["jp"]) == {
        "universe", "evaluated", "failures", "matched", "selected", "failure_details",
    }
    assert "SECRET" not in output


def test_watch_layer_counts_empty_fetch_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """空の結果は「取得失敗」に数え、上場直後の「データ不足」と混ぜない。"""
    monkeypatch.setattr(
        screen,
        "evaluate_one",
        lambda *_, **__: screen.ScreenOutcome(None, None, screen.Rejection.UNAVAILABLE),
    )
    _, _, _, layers = screen.collect_screen([screen.Member("GONE", "jp", "watch")], json_mode=True)
    assert layers["jp"]["watch"]["failures"] == 1
    assert layers["jp"]["watch"]["insufficient"] == 0
