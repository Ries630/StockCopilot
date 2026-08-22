"""report.py と lib/verdicts.py のテスト。

ネットワークにアクセスしない (AGENTS.md の環境前提)。report.py は判断も
指標計算もせず中間表現を描画するだけなので、固定の dict を入力にすれば
出力は決定的になる。
"""

import json
import pathlib
import re
import sys

import pytest

import report
from lib.verdicts import ACTIONABLE_VERDICTS, actionable_items, is_actionable


def market_screen(**over) -> dict:
    """市場別screen統計の有効なテストデータを作る。"""
    item = {
        "universe": 12,
        "evaluated": 12,
        "failures": 0,
        "matched": 0,
        "selected": 0,
    }
    item.update(over)
    return item


def base_data(**over) -> dict:
    """契約の必須キーを満たす最小の中間表現を作る。

    Args:
        **over: 上書きしたいキー。

    Returns:
        中間表現 dict。
    """
    data = {
        "schema": 2,
        "date": "2026-08-20",
        "generated_at": "2026-08-20T17:30:00+09:00",
        "bars": {"jp": "2026-08-20", "us": "2026-08-19"},
        "bar_status": {
            "jp": {"status": "updated", "previous": "2026-08-19"},
            "us": {"status": "updated", "previous": "2026-08-18"},
        },
        "holdings_as_of": [{"as_of": "2026-07-22", "label": "株式", "count": 12}],
        "effective_holdings": {"executions": 0, "lines": ["執行記録なし (as_of 時点のまま)"]},
        "holdings": [],
        "candidates": [],
        "screen": {
            "jp": market_screen(),
            "us": market_screen(universe=13, evaluated=13),
        },
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
        "range": {"low": 100.0, "high": 120.0, "pos_pct": 117.25},
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


def test_missing_decision_key_raises() -> None:
    """判断項目を既定値で埋めず、書き漏らした入力を停止する。"""
    broken = base_data()
    del broken["candidates"]
    with pytest.raises(KeyError, match="candidates"):
        report.render(broken)

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

    broken = report.render(
        base_data(
            screen={
                "jp": market_screen(universe=12, evaluated=9, failures=3),
                "us": market_screen(universe=13, evaluated=13),
            }
        )
    )
    assert "取得失敗 3 件" in broken
    assert "3 銘柄は取得に失敗しており、判定できていない" in broken


def test_missing_failures_count_is_rejected() -> None:
    screen = {"jp": market_screen(), "us": market_screen()}
    del screen["jp"]["failures"]
    with pytest.raises(KeyError, match="failures"):
        report.render(base_data(screen=screen))


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
    with pytest.raises(ValueError, match="weekly"):
        report.render(base_data(candidates=[bad]))


def test_unknown_is_still_a_valid_signal_value() -> None:
    """本物のデータ不足は通す。"""
    ok = position(
        verdict="保留",
        signals={"weekly": "unknown", "daily": "unknown", "overheat": "warn", "volume": "unknown"},
    )
    assert "9999" in report.render(base_data(holdings=[ok]))


def test_missing_market_screen_is_rejected() -> None:
    screen = {"jp": market_screen(), "us": market_screen()}
    del screen["jp"]
    with pytest.raises(KeyError, match="jp"):
        report.render(base_data(screen=screen))


def test_missing_root_display_fields_are_visible_as_unknown() -> None:
    """見出しや総括の欠落でも処理を続け、警告と不明表示を残す。"""
    for key in ("date", "generated_at", "summary", "holdings_as_of"):
        data = base_data()
        del data[key]
        html = report.render(data)
        assert "不明" in html
        assert key in html and "が無い" in html


def test_missing_display_values_are_not_replaced_with_semantic_defaults() -> None:
    """警告へ降格した値も、有効な既定値に見せない。"""
    data = base_data(candidates=[candidate(verdict="見送り")])
    del data["holdings_as_of"][0]["label"]
    del data["effective_holdings"]["executions"]
    del data["candidates"][0]["range"]["pos_pct"]

    html = report.render(data)

    assert "不明 2026-07-22" in html
    assert "執行記録 不明 件" in html
    assert "終値位置 不明" in html


def test_missing_card_display_fields_are_visible_as_unknown() -> None:
    """カードの表示材料が欠けても判断項目が残る限りカードを描く。"""
    pos = position()
    del pos["prose"]
    cand = candidate(verdict="見送り")
    for key in ("score_atr", "pass_reason", "range", "prose"):
        del cand[key]

    html = report.render(base_data(holdings=[pos], candidates=[cand]))

    assert html.count("不明") >= 5
    for key in ("prose", "score_atr", "pass_reason", "range"):
        assert key in html
    assert "が無い" in html


def test_schema_is_validated() -> None:
    """旧schemaを v2 として描かない。"""
    with pytest.raises(ValueError, match="schema"):
        report.render(base_data(schema=1))
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


def test_legacy_stale_bars_is_rejected() -> None:
    with pytest.raises(ValueError, match="stale_bars"):
        report.render(base_data(stale_bars=True))


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


# ─── 銘柄名 ──────────────────────────────────────────────


def test_jp_stock_without_name_raises() -> None:
    """4 桁コードだけでは何の会社か分からないので、日本株は名前を必須にする。"""
    with pytest.raises(KeyError, match="name"):
        pos = position()
        del pos["name"]
        report.render(base_data(holdings=[pos]))
    with pytest.raises(KeyError, match="name"):
        cand = candidate(market="jp", currency="JPY")
        del cand["name"]
        report.render(base_data(candidates=[cand]))


def test_jp_stock_with_blank_name_raises() -> None:
    """キーだけあっても、空の名前は表示名として使えない。"""
    with pytest.raises(ValueError, match="name"):
        report.render(base_data(holdings=[position(name="")]))
    with pytest.raises(ValueError, match="name"):
        report.render(base_data(candidates=[candidate(market="jp", currency="JPY", name="")]))


def test_us_stock_without_name_is_allowed() -> None:
    """米国株はティッカーで判別できる。"""
    cand = candidate()
    del cand["name"]
    html = report.render(base_data(candidates=[cand]))
    assert "AAAA" in html


def test_jp_name_is_rendered_next_to_the_code() -> None:
    html = report.render(base_data(holdings=[position(name="トヨタ自動車")]))
    assert "9999 トヨタ自動車" in html


# ─── 用語の説明 ───────────────────────────────────────────


def visible_text(html: str) -> str:
    """ポップオーバーと title 属性を取り除いた、本文として見える部分を返す。

    Args:
        html: render() の出力。

    Returns:
        本文だけを残した文字列。
    """
    without_pops = re.sub(r"<div popover.*?</div>", "", html, flags=re.DOTALL)
    return re.sub(r'title="[^"]*"', "", without_pops)


def test_explanations_never_appear_in_the_body() -> None:
    """説明はポップオーバーにだけ置く。

    毎日同じ説明文がレポート本文に並ぶのを避けるための規範なので、
    本文側に説明が漏れていないことをテストで固定する。
    """
    html = report.render(base_data(holdings=[position()], candidates=[candidate()]))
    body = visible_text(html)
    for _, description in report.GLOSSARY.values():
        assert description not in body


def test_terms_link_to_their_popover() -> None:
    html = report.render(base_data(holdings=[position()], candidates=[candidate()]))
    assert 'popovertarget="g-weekly"' in html
    assert "<div popover id=\"g-weekly\">" in html


def test_verdict_badges_carry_their_explanation() -> None:
    html = report.render(base_data(candidates=[candidate(verdict="決算後に再判定")]))
    assert 'popovertarget="g-v_after_earnings"' in html


def test_glossary_bodies_are_emitted_once_each() -> None:
    """同じ用語が何度出てもポップオーバーの実体は 1 つ。"""
    html = report.render(
        base_data(holdings=[position(), position(ticker="8888")], candidates=[candidate()])
    )
    assert html.count('<div popover id="g-score">') == 1
    assert html.count('<div popover id="g-weekly">') == 1


def test_glossary_needs_no_javascript() -> None:
    """Popover API だけで開く。未対応ブラウザ向けに title 属性を併記する。"""
    html = report.render(base_data(holdings=[position()]))
    assert "<script" not in html
    assert "onclick" not in html
    assert 'title="' in html


def test_glossary_is_hidden_without_popover_support() -> None:
    """未知のpopover属性を通常要素として描画するブラウザでも説明本文を露出させない。"""
    html = report.render(base_data(holdings=[position()]))
    assert "[popover] { display: none;" in html
    assert "[popover]:popover-open { display: block; }" in html


def test_unknown_term_falls_back_to_plain_text() -> None:
    assert report.term("存在しないキー", "そのまま") == "そのまま"


# ─── latest.json の更新 ────────────────────────────────────


def run_main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    """report.py の main() を引数付きで走らせる。

    Args:
        monkeypatch: pytest の monkeypatch。
        *argv: `report.py` に続くコマンドライン引数。
    """
    monkeypatch.setattr(sys, "argv", ["report.py", *argv])
    report.main()


def test_latest_json_is_updated(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """次回のシリーズ分析の起点を機械的に固定する。

    スキルの手作業にすると、1 回の書き忘れで前回との差分が静かに切れる。
    """
    src = tmp_path / "2026-08-20_evening.json"
    src.write_text(json.dumps(base_data(), ensure_ascii=False), encoding="utf-8")
    run_main(monkeypatch, str(src))

    latest = tmp_path / "latest.json"
    assert json.loads(latest.read_text(encoding="utf-8"))["date"] == "2026-08-20"
    assert (tmp_path / "2026-08-20_evening.html").exists()


def test_no_latest_flag_skips_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    src = tmp_path / "2026-08-20_evening.json"
    src.write_text(json.dumps(base_data(), ensure_ascii=False), encoding="utf-8")
    run_main(monkeypatch, str(src), "--no-latest")
    assert not (tmp_path / "latest.json").exists()


def test_missing_date_does_not_crash_or_update_latest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """日付不明の表示警告では、比較不能なlatest更新だけを飛ばす。"""
    src = tmp_path / "unknown_evening.json"
    data = base_data()
    del data["date"]
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    run_main(monkeypatch, str(src))

    assert (tmp_path / "unknown_evening.html").exists()
    assert not (tmp_path / "latest.json").exists()


def test_historical_report_does_not_rewind_latest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """過去レポートの再生成でシリーズ分析の起点を巻き戻さない。"""
    latest = tmp_path / "latest.json"
    newer = base_data()
    newer["date"] = "2026-08-21"
    latest.write_text(json.dumps(newer, ensure_ascii=False), encoding="utf-8")
    src = tmp_path / "2026-08-20_evening.json"
    src.write_text(json.dumps(base_data(), ensure_ascii=False), encoding="utf-8")

    run_main(monkeypatch, str(src))

    assert json.loads(latest.read_text(encoding="utf-8"))["date"] == "2026-08-21"


def test_contract_violation_leaves_latest_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """落ちる入力で latest.json を上書きしない。

    上書きしてしまうと、壊れた JSON が次回のシリーズ分析の起点になる。
    """
    latest = tmp_path / "latest.json"
    latest.write_text('{"date": "2026-08-19"}', encoding="utf-8")

    broken = base_data()
    del broken["candidates"]
    src = tmp_path / "2026-08-20_evening.json"
    src.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(KeyError):
        run_main(monkeypatch, str(src))
    assert json.loads(latest.read_text(encoding="utf-8"))["date"] == "2026-08-19"


def test_latest_json_is_not_copied_onto_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """latest.json 自体を渡されたときに自分を書き直さない。"""
    src = tmp_path / "latest.json"
    src.write_text(json.dumps(base_data(), ensure_ascii=False), encoding="utf-8")
    run_main(monkeypatch, str(src))
    assert (tmp_path / "latest.html").exists()
