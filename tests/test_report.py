"""report.py と lib/verdicts.py のテスト。

ネットワークにアクセスしない (AGENTS.md の環境前提)。report.py は判断も
指標計算もせず中間表現を描画するだけなので、固定の dict を入力にすれば
出力は決定的になる。
"""

import pytest

import report
from lib.verdicts import ACTIONABLE_VERDICTS, actionable_items, is_actionable


def base_data(**over) -> dict:
    """契約の必須キーを満たす最小の中間表現を作る。

    Args:
        **over: 上書きしたいキー。

    Returns:
        中間表現 dict。
    """
    data = {
        "schema": 1,
        "date": "2026-08-20",
        "generated_at": "2026-08-20T17:30:00+09:00",
        "bars": {"jp": "2026-08-20", "us": "2026-08-19"},
        "holdings_as_of": [{"as_of": "2026-07-22", "label": "株式", "count": 12}],
        "effective_holdings": {"executions": 0, "lines": ["執行記録なし (as_of 時点のまま)"]},
        "holdings": [],
        "candidates": [],
        "screen": {"universe": 25, "market": "all", "failures": 0},
        "summary": "総括の本文。",
    }
    data.update(over)
    return data


def position(**over) -> dict:
    """契約の必須キーを満たす Position を作る。

    Args:
        **over: 上書きしたいキー。

    Returns:
        Position dict。
    """
    pos = {
        "ticker": "9999",
        "name": "テスト銘柄",
        "shares": 100,
        "currency": "JPY",
        "price": 1234.0,
        "change_pct": 1.2,
        "verdict": "ホールド",
        "scenario": "前進",
        "signals": {"weekly": "good", "daily": "warn", "overheat": "bad", "volume": "unknown"},
        "levels": {"support": 1100, "resistance": 1400, "invalidation": 1050},
        "closes": [1200, 1210, 1190, 1234],
        "prose": {
            "change": "前回からの変化。",
            "scenario": "シナリオ進捗。",
            "reasons": ["根拠 1", "根拠 2"],
            "trigger": "日足終値で ¥1,100 を割ったら手仕舞い。",
        },
    }
    pos.update(over)
    return pos


def candidate(**over) -> dict:
    """契約の必須キーを満たす Candidate を作る。

    Args:
        **over: 上書きしたいキー。

    Returns:
        Candidate dict。
    """
    cand = {
        "ticker": "AAAA",
        "name": "候補銘柄",
        "market": "us",
        "currency": "USD",
        "price": 123.45,
        "change_pct": 3.2,
        "score_atr": 1.4,
        "pass_reason": "20 日レンジを上に突破 (ATR 0.5 倍)",
        "range": {"low": 100.0, "high": 120.0, "pos_pct": 105},
        "atr_pct": 2.4,
        "verdict": "買い",
        "signals": {"weekly": "good", "daily": "good", "overheat": "warn", "volume": "good"},
        "prose": {
            "strong": ["強い点"],
            "weak": ["弱い点"],
            "check": "日足終値で $120 を維持できるか",
        },
    }
    cand.update(over)
    return cand


# ─── メンションのゲート (lib/verdicts) ───────────────────────


@pytest.mark.parametrize("verdict", ["買い", "積増し", "売却"])
def test_money_moving_verdicts_are_actionable(verdict: str) -> None:
    assert is_actionable(verdict)


@pytest.mark.parametrize(
    "verdict", ["ホールド", "部分利確", "見送り", "決算後に再判定", "保留", None, ""]
)
def test_other_verdicts_are_not_actionable(verdict: str | None) -> None:
    assert not is_actionable(verdict)


def test_actionable_set_is_pinned() -> None:
    """対象ラベルを変えたらここが落ちる。

    りーすさんが 2026-08-20 に 買い / 積増し / 売却 の 3 つと決めた。
    「部分利確」は意図的に外している。変えるときは lib/verdicts.py と同時に直すこと。
    """
    assert ACTIONABLE_VERDICTS == {"買い", "積増し", "売却"}


def test_actionable_items_merges_holdings_and_candidates() -> None:
    data = base_data(
        holdings=[position(verdict="積増し"), position(ticker="8888", verdict="ホールド")],
        candidates=[candidate(verdict="買い"), candidate(ticker="BBBB", verdict="見送り")],
    )
    items = actionable_items(data)
    assert [(i["ticker"], i["kind"]) for i in items] == [
        ("9999", "holding"),
        ("AAAA", "candidate"),
    ]


def test_reference_only_positions_never_actionable() -> None:
    """自動運用口座の銘柄は執行されないので、ラベルによらずゲートに乗せない。"""
    data = base_data(holdings=[position(verdict="売却", reference_only=True)])
    assert actionable_items(data) == []


# ─── HTML 生成 ───────────────────────────────────────────


def test_quiet_day_hero_says_no_action() -> None:
    html = report.render(base_data())
    assert "本日、資金が動く判断なし" in html
    assert "候補なし" in html


def test_actionable_day_hero_lists_tickers() -> None:
    html = report.render(base_data(candidates=[candidate()]))
    assert "資金が動く判断 1 件" in html
    assert "AAAA" in html


def test_prose_is_always_rendered() -> None:
    """HTML 単独でレポートとして成立させるための本文。図だけでは判断を再構成できない。"""
    html = report.render(base_data(holdings=[position()], candidates=[candidate()]))
    for text in ("前回からの変化。", "シナリオ進捗。", "根拠 1", "強い点", "弱い点"):
        assert text in html
    assert "日足終値で ¥1,100 を割ったら手仕舞い。" in html


def test_missing_required_key_raises() -> None:
    """既定値で埋めて進むと「書き漏らした日」と「判断が無かった日」が区別できなくなる。"""
    broken = base_data()
    del broken["summary"]
    with pytest.raises(KeyError, match="summary"):
        report.render(broken)

    bad = position()
    del bad["prose"]
    with pytest.raises(KeyError, match="prose"):
        report.render(base_data(holdings=[bad]))


def test_no_external_resources() -> None:
    html = report.render(base_data(holdings=[position()], candidates=[candidate()]))
    for token in ("http://", "https://", "<script", "localStorage", "@import"):
        assert token not in html


def test_names_are_html_escaped() -> None:
    html = report.render(base_data(holdings=[position(name="<script>x</script>&")]))
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_stale_bars_are_surfaced() -> None:
    html = report.render(base_data(stale_bars=True))
    assert "独立した観測として数えない" in html


def test_money_formats_per_currency() -> None:
    assert report.money(1234.5, "JPY") == "¥1,234"
    assert report.money(1234.5, "USD") == "$1,234.50"
    assert report.money(None, "JPY") == "—"


def test_sparkline_needs_two_points() -> None:
    assert report.sparkline([]) == ""
    assert report.sparkline([100]) == ""
    assert "<polyline" in report.sparkline([100, 110])


def test_range_marker_clamped_but_percent_is_not() -> None:
    """マーカーは軸内に丸めるが、表示する % は丸めない。

    レンジをどれだけ上抜けたかは候補の強さそのもので、丸めると突破の度合いが消える。
    """
    html = report.range_axis({"low": 100.0, "high": 120.0, "pos_pct": 400}, 200.0, "USD")
    assert "left:108.0%" in html
    assert "終値位置 400%" in html


def test_level_axis_skipped_without_material() -> None:
    assert report.level_axis(100.0, {}, "USD") == ""
    assert report.level_axis(None, {"support": 90}, "USD") == ""
