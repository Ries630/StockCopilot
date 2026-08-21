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


def test_missing_holdings_or_candidates_raises() -> None:
    """必須のリストを空に潰さない。

    潰すと「LLM が書き漏らした日」が「保有なし・候補なし」という正常な出力に化け、
    静穏日と区別できなくなる (レビュー指摘 / docs/report-contract.md)。
    """
    for key in ("holdings", "candidates"):
        broken = base_data()
        del broken[key]
        with pytest.raises(KeyError, match=key):
            report.render(broken)


def test_missing_verdict_raises_for_normal_position() -> None:
    """判断対象外でない銘柄の verdict 欠落を「—」に潰さない。

    潰すと、書き漏らした「売却」がヒーローにも actionable_items() にも出ず、
    「判断なし」として表示される。
    """
    bad = position()
    del bad["verdict"]
    with pytest.raises(KeyError, match="verdict"):
        report.render(base_data(holdings=[bad]))


def test_reference_only_position_may_omit_verdict() -> None:
    """自動運用口座の銘柄は判断を付けないので、欠けていてよい。"""
    ref = position(reference_only=True)
    del ref["verdict"]
    html = report.render(base_data(holdings=[ref]))
    assert report.NOT_APPLICABLE in html


def test_unknown_verdict_raises() -> None:
    """契約外のラベルを通さない。

    通すとカードには表示されるのに actionable_items() が拾わず、
    買い判断がヒーローと Slack のメンションから静かに消える。
    """
    with pytest.raises(ValueError, match="契約外"):
        report.render(base_data(candidates=[candidate(verdict="購入")]))
    with pytest.raises(ValueError, match="契約外"):
        report.render(base_data(candidates=[candidate(verdict="買い ")]))
    # 保有側は別の語彙。候補のラベルを入れたら落ちる
    with pytest.raises(ValueError, match="契約外"):
        report.render(base_data(holdings=[position(verdict="買い")]))


def test_missing_signals_raises() -> None:
    """signals の欠落を unknown に潰さない。

    契約上の unknown は「データ不足」を意味するので、書き漏らしが
    実際の分析結果として描かれてしまう。
    """
    bad = position()
    del bad["signals"]
    with pytest.raises(KeyError, match="signals"):
        report.render(base_data(holdings=[bad]))

    bad_cand = candidate()
    del bad_cand["signals"]
    with pytest.raises(KeyError, match="signals"):
        report.render(base_data(candidates=[bad_cand]))


def test_empty_effective_holdings_lines_raises() -> None:
    """執行 0 件でも「執行記録なし」の行が要る。

    空にすると、記録が無いのか拾い忘れたのかが読み手に区別できない。
    """
    with pytest.raises(ValueError, match="effective_holdings"):
        report.render(base_data(effective_holdings={"executions": 0, "lines": []}))
    with pytest.raises(KeyError, match="lines"):
        report.render(base_data(effective_holdings={"executions": 0}))


def test_fetch_failures_are_not_reported_as_zero_candidates() -> None:
    """取得失敗と候補ゼロを混ぜない。

    混ぜると、取得障害の日を「静かな日」として読んでしまう。
    """
    quiet = report.render(base_data())
    assert "取得失敗" not in quiet

    broken = report.render(base_data(screen={"universe": 25, "market": "all", "failures": 3}))
    assert "取得失敗 3 件" in broken
    assert "3 銘柄は取得に失敗しており、判定できていない" in broken


def test_missing_failures_count_raises() -> None:
    with pytest.raises(KeyError, match="failures"):
        report.render(base_data(screen={"universe": 25, "market": "all"}))


def test_missing_ticker_raises() -> None:
    """銘柄を特定できない判断を通さない。

    名前も省略されていると、ヒーローと Slack に「売却」「買い」だけが並び、
    どの銘柄の話か分からなくなる。
    """
    bad = position()
    del bad["ticker"]
    with pytest.raises(KeyError, match="ticker"):
        report.render(base_data(holdings=[bad]))

    bad_cand = candidate()
    del bad_cand["ticker"]
    with pytest.raises(KeyError, match="ticker"):
        report.render(base_data(candidates=[bad_cand]))


@pytest.mark.parametrize("currency", ["jpy", "JPY ", "EUR", ""])
def test_unknown_currency_raises(currency: str) -> None:
    """未知の通貨コードを USD に倒さない。

    倒すと日本株の ¥3,120 が `$3,120.00` として表示される。
    """
    with pytest.raises(ValueError, match="通貨"):
        report.render(base_data(holdings=[position(currency=currency)]))


def test_missing_signal_axis_raises() -> None:
    """軸の欠落を unknown に倒さない。"""
    bad = position()
    del bad["signals"]["overheat"]
    with pytest.raises(KeyError, match="overheat"):
        report.render(base_data(holdings=[bad]))


def test_unknown_signal_value_raises() -> None:
    """表記揺れを unknown に倒さない。

    unknown は「データ不足」を意味し、その銘柄の判断が保留になる根拠でもあるため、
    書き漏らしから偽の根拠が作られる。
    """
    bad = candidate()
    bad["signals"]["weekly"] = "goood"
    with pytest.raises(ValueError, match="週足"):
        report.render(base_data(candidates=[bad]))


def test_unknown_is_still_a_valid_signal_value() -> None:
    """本物のデータ不足は通す。"""
    ok = position(
        verdict="保留",
        signals={"weekly": "unknown", "daily": "unknown", "overheat": "warn", "volume": "unknown"},
    )
    assert "9999" in report.render(base_data(holdings=[ok]))


def test_missing_universe_or_market_raises() -> None:
    """どの母集団を調べたか分からないまま「候補なし」と報告しない。"""
    for key in ("universe", "market"):
        screen = {"universe": 25, "market": "all", "failures": 0}
        del screen[key]
        with pytest.raises(KeyError, match=key):
            report.render(base_data(screen=screen))


def test_schema_is_validated() -> None:
    """未対応バージョンを v1 として描かない。"""
    with pytest.raises(ValueError, match="schema"):
        report.render(base_data(schema=2))
    with pytest.raises(KeyError, match="schema"):
        broken = base_data()
        del broken["schema"]
        report.render(broken)


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
