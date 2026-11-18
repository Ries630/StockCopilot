"""analyze.py の保有ヘッダのテスト (ネットワークアクセスなし)。

保有データの鮮度警告は「黙って進めない」ための仕掛け。文言が変わると
判断の前提が伝わらなくなるので、警告の有無を明示的に固定する。
決算注記のテストは test_earnings.py にある。
"""

from __future__ import annotations

import datetime as dt
import json
import sys

import pytest

import analyze
from lib.holdings import JOURNAL_SOURCE, EffectiveHoldings


def _effective(rows: list[dict], read: int = 0, applied: int = 0, warnings=()) -> EffectiveHoldings:
    """effective_holdings() の戻り値を組み立てる (実保有・実ジャーナルに依存させない)。"""
    return EffectiveHoldings(
        holdings=rows,
        as_of="2026-08-20",
        executions_read=read,
        executions_applied=applied,
        warnings=list(warnings),
    )


def _recorder(calls: list[tuple]):
    """analyze_symbol の差し替え。位置引数を記録し、空の分析結果を返す。"""

    def fake(ticker: str, market: str, label: str = "", **_) -> analyze.SymbolAnalysis:
        calls.append((ticker, market, label))
        return analyze.SymbolAnalysis(ticker, market, label, "", ())

    return fake


def _header(rows: list[dict], **kwargs) -> None:
    """保有ヘッダを、表示する行と同じ行から組み立てた内訳で出す。"""
    analyze.print_holdings_header(rows, _effective(rows, **kwargs))


def test_holdings_market_filter_preserves_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """市場限定しても保有モードの銘柄名・口座・株数を失わない。"""
    holdings = [
        {
            "ticker": "1111",
            "market": "jp",
            "name": "国内銘柄",
            "account": "口座A",
            "quantity": 10,
            "as_of": "2026-08-20",
        },
        {
            "ticker": "USAA",
            "market": "us",
            "name": "US Asset",
            "account": "口座B",
            "quantity": 5,
            "as_of": "2026-08-20",
        },
    ]
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(analyze, "effective_holdings", lambda: _effective(holdings))
    monkeypatch.setattr(analyze, "analyze_symbol", _recorder(calls))
    monkeypatch.setattr(sys, "argv", ["analyze.py", "--market", "us"])

    analyze.main()

    assert calls == [("USAA", "us", "US Asset (口座B 5株)")]


def test_explicit_tickers_are_filtered_before_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候補分析でも更新市場以外のティッカーを呼ばない。"""
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(analyze, "display_name", lambda *_: "表示名")
    monkeypatch.setattr(analyze, "analyze_symbol", _recorder(calls))
    monkeypatch.setattr(sys, "argv", ["analyze.py", "--market", "us", "1111", "USAA"])

    analyze.main()

    assert calls == [("USAA", "us", "表示名")]


def test_holdings_header_fresh(capsys: pytest.CaptureFixture[str]) -> None:
    """鮮度が十分なら警告を出さない。"""
    as_of = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    _header([{"as_of": as_of}, {"as_of": as_of}])
    out = capsys.readouterr().out
    assert "保有 2 銘柄" in out
    assert "(2 日前)" in out
    assert "⚠" not in out


def test_holdings_header_within_stale_days_has_no_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """as_of が古いこと自体は既定なので、STALE_DAYS 以内なら警告しない。

    Investment の取り込みは高頻度では行わないため、日次更新前提の短い閾値だと
    警告が常時点灯して機能しなくなる。境界を明示的に固定する。
    """
    as_of = (dt.date.today() - dt.timedelta(days=analyze.STALE_DAYS)).isoformat()
    _header([{"as_of": as_of}])
    out = capsys.readouterr().out
    assert "⚠" not in out


def test_holdings_header_always_points_at_the_ledger(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """鮮度によらず、突合先 (ジャーナルの執行記録) を毎回示す。

    実効保有 = as_of 時点 + 執行記録 という定義が伝わらないと、
    as_of の古さをそのまま保有のずれとして読んでしまう。
    """
    as_of = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    _header([{"as_of": as_of}])
    out = capsys.readouterr().out
    assert "執行記録" in out
    assert "実効保有" in out


def test_holdings_header_stale_warns(capsys: pytest.CaptureFixture[str]) -> None:
    """STALE_DAYS を超えたら、執行記録の網羅性を疑うよう警告する。

    警告の意味は「データが古い」ではなく「台帳だけで差分を追いきれているか怪しい」。
    """
    days = analyze.STALE_DAYS + 1
    as_of = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    _header([{"as_of": as_of}])
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "執行記録の網羅性" in out


def test_holdings_header_splits_differing_as_of(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """基準日が資産クラスで異なる場合は分けて出す。

    1 つに丸めると、別系統で管理されている銘柄が実際より新しく見える。
    どちらの日付も、それぞれの銘柄数と出所つきで見えている必要がある。
    """
    recent = (dt.date.today() - dt.timedelta(days=5)).isoformat()
    older = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    _header([
        {"as_of": recent, "as_of_source": "stock_as_of"},
        {"as_of": recent, "as_of_source": "stock_as_of"},
        {"as_of": older, "as_of_source": "other_as_of"},
    ])
    out = capsys.readouterr().out
    assert "保有 3 銘柄" in out
    assert "資産クラスで異なる" in out
    assert "(5 日前) — 2 銘柄 (stock_as_of)" in out
    assert "(40 日前) — 1 銘柄 (other_as_of)" in out


@pytest.mark.parametrize("holding", [{"as_of": ""}, {"as_of": None}, {}])
def test_holdings_header_unparseable_as_of(
    holding: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    """as_of が読めない場合も黙って進めず、保留を促す。"""
    _header([holding])
    out = capsys.readouterr().out
    assert "不明" in out
    assert "増減の判定は保留すること" in out


def test_holdings_mode_analyzes_ticker_bought_after_as_of(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """as_of 以降に買った銘柄も保有モードで分析し、売り切った銘柄は分析しない (#55)。

    執行記録から来た行は銘柄名・口座を持たないので、名前は辞書から引き、
    口座は分からないことをそのまま出す。
    """
    rows = [
        {
            "ticker": "5555",
            "market": "jp",
            "name": "",
            "account": "",
            "quantity": 300,
            "as_of": "2026-08-25",
            "as_of_source": JOURNAL_SOURCE,
        },
    ]
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(analyze, "effective_holdings", lambda: _effective(rows, read=2, applied=2))
    monkeypatch.setattr(analyze, "display_name", lambda *_: "新規銘柄")
    monkeypatch.setattr(analyze, "analyze_symbol", _recorder(calls))
    monkeypatch.setattr(sys, "argv", ["analyze.py", "--market", "jp"])

    analyze.main()

    assert calls == [("5555", "jp", "新規銘柄 (口座不明 300株)")]


def test_holdings_header_shows_composition(capsys: pytest.CaptureFixture[str]) -> None:
    """実効保有の組み立て (基準日・執行記録の件数・警告) を冒頭に出す。

    株数がどこから来たかを読み手が最初に確認できるようにするため。
    執行記録から株数を取った銘柄は Investment の鮮度と分けて数える。
    """
    as_of = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    _header(
        [
            {"as_of": as_of, "as_of_source": "stock_as_of"},
            {"as_of": "2026-08-25", "as_of_source": JOURNAL_SOURCE},
        ],
        read=3,
        applied=1,
        warnings=["84 行目: 残株数が読めない"],
    )
    out = capsys.readouterr().out
    assert "実効保有 2 銘柄 = Investment as_of=2026-08-20 + 執行記録 3 件中 1 件を反映" in out
    assert "Investment as_of=" + as_of + " (2 日前)" in out
    assert "執行記録から株数を取った銘柄 1" in out
    assert "[warn] journal 84 行目: 残株数が読めない" in out


def test_holdings_header_without_executions(capsys: pytest.CaptureFixture[str]) -> None:
    """執行記録が 0 件でも黙って省略せず、as_of 時点のままだと明記する。"""
    as_of = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    _header([{"as_of": as_of}])
    assert "執行記録なし (as_of 時点のまま)" in capsys.readouterr().out


def test_holdings_only_lists_without_analysis(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--holdings-only は実効保有だけを出し、指標を計算しない (確定足が更新されない市場用)。"""
    rows = [
        {
            "ticker": "1111",
            "market": "jp",
            "name": "国内銘柄",
            "account": "口座A",
            "quantity": 10,
            "as_of": "2026-08-20",
        },
    ]
    monkeypatch.setattr(analyze, "effective_holdings", lambda: _effective(rows))
    monkeypatch.setattr(analyze, "analyze_symbol", lambda *_: pytest.fail("指標を計算した"))
    monkeypatch.setattr(sys, "argv", ["analyze.py", "--market", "jp", "--holdings-only"])

    analyze.main()

    assert "1111 jp 国内銘柄 (口座A 10株)" in capsys.readouterr().out


def test_holdings_only_rejects_tickers(monkeypatch: pytest.MonkeyPatch) -> None:
    """--holdings-only とティッカーの同時指定は、黙って片方を無視せずに拒む。"""
    monkeypatch.setattr(sys, "argv", ["analyze.py", "--holdings-only", "1111"])
    with pytest.raises(SystemExit):
        analyze.main()


def test_analyze_symbol_outputs_converged_ema200(monkeypatch: pytest.MonkeyPatch) -> None:
    """日足・週足とも、出力する EMA200 が全履歴で計算した値と実質的に一致する。

    単調に下がる系列では、窓の先頭の終値が収束値より高い。200 本ちょうどの窓だと
    EMA200 が約 1.8% 下にずれるので、窓の長さが足りなければこのテストで検出できる。
    """
    import pandas as pd
    import ta

    closes = pd.Series([400.0 - i * 0.15 for i in range(2000)])
    history = pd.DataFrame(
        {"open": closes, "high": closes + 1, "low": closes - 1, "close": closes, "volume": 1000.0}
    )
    converged = ta.trend.EMAIndicator(closes, 200).ema_indicator().iloc[-1]

    monkeypatch.setattr(analyze, "earnings_note", lambda *_: None)
    monkeypatch.setattr(
        analyze, "fetch_ohlcv", lambda *_, limit, **__: history.tail(limit).reset_index(drop=True)
    )
    result = analyze.analyze_symbol("TEST", "us")

    assert [frame.interval for frame in result.frames] == ["1d", "1wk"]
    for frame in result.frames:
        assert frame.indicators["ema200"] == pytest.approx(converged, rel=1e-3)


def _bars(n: int, start: str = "2026-01-05", freq: str = "B"):
    """日付付きの確定足を作る (終値は 100 から 1 ずつ上がる)。"""
    import pandas as pd

    index = pd.date_range(start, periods=n, freq=freq)
    closes = pd.Series([100.0 + i for i in range(n)], index=index)
    return pd.DataFrame(
        {"open": closes, "high": closes + 1, "low": closes - 1, "close": closes, "volume": 1000.0}
    )


def test_analyze_symbol_returns_structured_data_without_printing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """取得・計算は表示せず、時間足ごとの本数・最新足日・変化率・指標を返す。"""
    frames = {"1d": _bars(40), "1wk": _bars(35, freq="W-FRI")}
    monkeypatch.setattr(analyze, "earnings_note", lambda *_: "決算 2026-12-01 (あと 13 日)")
    monkeypatch.setattr(analyze, "fetch_ohlcv", lambda *_, interval, **__: frames[interval])

    result = analyze.analyze_symbol("TEST", "us", "表示名")

    assert capsys.readouterr().out == ""
    assert result.error is None
    assert result.earnings == "決算 2026-12-01 (あと 13 日)"
    daily, weekly = result.frames
    assert (daily.interval, daily.bars, daily.last_bar) == ("1d", 40, "2026-02-27")
    assert daily.change_pct == pytest.approx(round((139 / 138 - 1) * 100, 2))
    assert daily.indicators["close"] == 139.0
    assert (weekly.interval, weekly.bars) == ("1wk", 35)


def test_analyze_symbol_keeps_partial_result_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """週足の取得で失敗しても、取れた日足と失敗内容の両方を返す。"""

    def fetch(*_, interval, **__):
        if interval == "1wk":
            raise RuntimeError("rate limited")
        return _bars(40)

    monkeypatch.setattr(analyze, "earnings_note", lambda *_: "")
    monkeypatch.setattr(analyze, "fetch_ohlcv", fetch)

    result = analyze.analyze_symbol("TEST", "us")

    assert [frame.interval for frame in result.frames] == ["1d"]
    assert result.error == "rate limited"


def test_analyze_symbol_can_skip_earnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """screen.py で取得済みの候補では、決算注記を二重に取得しない。"""
    monkeypatch.setattr(analyze, "earnings_note", lambda *_: pytest.fail("決算注記を再取得した"))
    monkeypatch.setattr(analyze, "fetch_ohlcv", lambda *_, **__: _bars(40))

    result = analyze.analyze_symbol("TEST", "us", fetch_earnings=False)

    assert result.earnings is None
    assert len(result.frames) == 2


def test_render_analysis_keeps_human_format() -> None:
    """表示は従来どおり、見出し・決算注記・時間足ごとの見出しと指標・失敗の警告を並べる。"""
    result = analyze.SymbolAnalysis(
        "TEST",
        "us",
        "表示名",
        "⚠ 決算 2026-11-20 (あと 2 日) — ギャップでトリガーが飛びうる",
        (analyze.Frame("1d", 40, "2026-11-17", 1.23, {"close": 10.0}),),
        "rate limited",
    )

    assert analyze.render_analysis(result).splitlines() == [
        "",
        "########## TEST 表示名 ##########",
        "⚠ 決算 2026-11-20 (あと 2 日) — ギャップでトリガーが飛びうる",
        "--- 日足 chg1bar=1.23% (確定足 40本) ---",
        '{"close": 10.0}',
        "  [warn] TEST: rate limited",
    ]


def test_render_analysis_omits_empty_earnings() -> None:
    """注記の無い銘柄 (ETF) と取得しなかった銘柄では、決算の行を出さない。"""
    for earnings in ("", None):
        result = analyze.SymbolAnalysis("ETF", "us", "", earnings, ())
        assert analyze.render_analysis(result).splitlines() == ["", "########## ETF  ##########"]


def test_json_mode_prints_single_document_with_holding_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json は保有ヘッダを混ぜず、口座・株数と時間足・決算・失敗を 1 つの JSON で返す。"""
    rows = [
        {
            "ticker": "1111",
            "market": "jp",
            "name": "国内銘柄",
            "account": "口座A",
            "quantity": 10,
            "as_of": "2026-08-20",
            "as_of_source": "stock_as_of",
        },
        {
            "ticker": "2222",
            "market": "jp",
            "name": "",
            "account": "",
            "quantity": 5,
            "as_of": "2026-08-25",
            "as_of_source": JOURNAL_SOURCE,
        },
    ]

    def fake(ticker, market, label="", fetch_earnings=True):
        if ticker == "2222":
            return analyze.SymbolAnalysis(ticker, market, label, None, (), "boom")
        frame = analyze.Frame("1d", 40, "2026-11-17", -0.5, {"close": 1.0})
        return analyze.SymbolAnalysis(ticker, market, label, "⚠ 決算 本日", (frame,))

    monkeypatch.setattr(analyze, "effective_holdings", lambda: _effective(rows, read=1, applied=1))
    monkeypatch.setattr(analyze, "display_name", lambda *_: None)
    monkeypatch.setattr(analyze, "analyze_symbol", fake)
    monkeypatch.setattr(sys, "argv", ["analyze.py", "--json"])

    analyze.main()

    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "holdings"
    assert data["holdings"]["executions_applied"] == 1
    first, second = data["symbols"]
    assert first["holding"] == {
        "account": "口座A",
        "quantity": 10,
        "as_of": "2026-08-20",
        "as_of_source": "stock_as_of",
    }
    assert first["name"] == "国内銘柄"
    assert first["earnings"] == {"note": "⚠ 決算 本日", "warn": True}
    assert first["daily"]["change_pct"] == -0.5
    assert first["weekly"] is None
    assert first["error"] is None
    assert second["holding"]["account"] is None
    assert second["error"] == "boom"


def test_json_mode_with_no_earnings_marks_earnings_as_not_fetched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--no-earnings では決算注記を取らず、JSON でも未取得と分かる。"""
    monkeypatch.setattr(analyze, "display_name", lambda *_: "表示名")
    monkeypatch.setattr(analyze, "earnings_note", lambda *_: pytest.fail("決算注記を再取得した"))
    monkeypatch.setattr(analyze, "fetch_ohlcv", lambda *_, **__: _bars(40))
    monkeypatch.setattr(sys, "argv", ["analyze.py", "--json", "--no-earnings", "USAA"])

    analyze.main()

    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "tickers"
    assert data["holdings"] is None
    assert data["earnings_fetched"] is False
    (symbol,) = data["symbols"]
    assert symbol["earnings"] is None
    assert symbol["holding"] is None
    assert symbol["daily"]["bars"] == 40


def test_json_holdings_only_lists_without_analysis(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json --holdings-only は指標を計算せず、実効保有だけを JSON で返す。"""
    rows = [
        {"ticker": "1111", "market": "jp", "name": "国内銘柄", "account": "口座A", "quantity": 10},
    ]
    monkeypatch.setattr(analyze, "effective_holdings", lambda: _effective(rows))
    monkeypatch.setattr(analyze, "analyze_symbol", lambda *_, **__: pytest.fail("指標を計算した"))
    monkeypatch.setattr(sys, "argv", ["analyze.py", "--json", "--holdings-only"])

    analyze.main()

    data = json.loads(capsys.readouterr().out)
    assert data["analyzed"] is False
    (symbol,) = data["symbols"]
    assert symbol["holding"]["quantity"] == 10
    assert symbol["daily"] is None
