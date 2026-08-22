"""analyze.py の保有ヘッダのテスト (ネットワークアクセスなし)。

保有データの鮮度警告は「黙って進めない」ための仕掛け。文言が変わると
判断の前提が伝わらなくなるので、警告の有無を明示的に固定する。
決算注記のテストは test_earnings.py にある。
"""

from __future__ import annotations

import datetime as dt
import sys

import pytest

import analyze


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
    monkeypatch.setattr(analyze, "load_holdings", lambda: holdings)
    monkeypatch.setattr(analyze, "analyze_symbol", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["analyze.py", "--market", "us"])

    analyze.main()

    assert calls == [("USAA", "us", "US Asset (口座B 5株)")]


def test_explicit_tickers_are_filtered_before_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候補分析でも更新市場以外のティッカーを呼ばない。"""
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(analyze, "display_name", lambda *_: "表示名")
    monkeypatch.setattr(analyze, "analyze_symbol", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["analyze.py", "--market", "us", "1111", "USAA"])

    analyze.main()

    assert calls == [("USAA", "us", "表示名")]


def test_holdings_header_fresh(capsys: pytest.CaptureFixture[str]) -> None:
    """鮮度が十分なら警告を出さない。"""
    as_of = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    analyze.print_holdings_header([{"as_of": as_of}, {"as_of": as_of}])
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
    analyze.print_holdings_header([{"as_of": as_of}])
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
    analyze.print_holdings_header([{"as_of": as_of}])
    out = capsys.readouterr().out
    assert "執行記録" in out
    assert "実効保有" in out


def test_holdings_header_stale_warns(capsys: pytest.CaptureFixture[str]) -> None:
    """STALE_DAYS を超えたら、執行記録の網羅性を疑うよう警告する。

    警告の意味は「データが古い」ではなく「台帳だけで差分を追いきれているか怪しい」。
    """
    days = analyze.STALE_DAYS + 1
    as_of = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    analyze.print_holdings_header([{"as_of": as_of}])
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
    analyze.print_holdings_header([
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
    analyze.print_holdings_header([holding])
    out = capsys.readouterr().out
    assert "不明" in out
    assert "増減の判定は保留すること" in out
