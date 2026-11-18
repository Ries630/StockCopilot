"""長期保有枠 (lib/long_term.py) と screen.py の「買付予定」の節のテスト (ネットワークアクセスなし)。

権利付き最終日を過ぎると次の機会は最長 1 年後になる。期限の選択と状態判定が崩れると、
その見落としを機械に拾わせる意味がなくなるので、判定日を注入して境界を固定する。
"""

from __future__ import annotations

import datetime as dt
import json
import sys

import pandas as pd
import pytest

import screen
from lib import long_term
from lib.holdings import EffectiveHoldings

TODAY = dt.date(2026, 11, 18)


def _stock(**overrides) -> dict:
    """LONG_TERM_STOCKS の 1 要素を作る。"""
    entry = {"ticker": "9999", "shares": 100, "buy_by": ["2026-03-27", "2026-09-28"], "purpose": "優待"}
    entry.update(overrides)
    return entry


def _parse_one(**overrides) -> long_term.LongTermStock:
    stocks, warnings = long_term.parse([_stock(**overrides)])
    assert warnings == []
    return stocks[0]


def test_next_deadline_picks_the_nearest_future_date() -> None:
    """複数の権利付き最終日から、判定日以降で最も近い 1 つを選ぶ (書いた順によらない)。"""
    stock = _parse_one(buy_by=["2027-03-26", "2026-03-27", "2026-12-25"])
    assert stock.next_deadline(TODAY) == dt.date(2026, 12, 25)


def test_next_deadline_includes_today() -> None:
    """権利付き最終日の当日はまだ買えるので、期限として残す。"""
    stock = _parse_one(buy_by=["2026-11-18"])
    (item,) = long_term.plan([stock], set(), TODAY)
    assert (item.deadline, item.days_left, item.expired) == (TODAY, 0, False)


def test_all_past_deadlines_are_expired() -> None:
    """期限がすべて過去なら、次の期限なしとして書き足しを促す。"""
    stock = _parse_one(buy_by=["2026-03-27", "2026-09-28"])
    (item,) = long_term.plan([stock], set(), TODAY)
    assert item.expired
    assert item.days_left is None


def test_status_follows_effective_holdings() -> None:
    """実効保有に含まれれば保有中、含まれなければ買付予定。手で移し替えない。"""
    held, planned = long_term.parse([_stock(ticker="1111"), _stock(ticker="2222")])[0]
    items = long_term.plan([held, planned], {"1111.T"}, TODAY)
    assert [item.status for item in items] == [long_term.Status.HELD, long_term.Status.PLANNED]


def test_status_is_unknown_without_holdings() -> None:
    """保有データを読めない回は、買付予定と決めつけずに状態不明にする。"""
    (item,) = long_term.plan([_parse_one()], None, TODAY)
    assert item.status is long_term.Status.UNKNOWN


def test_market_is_inferred_from_ticker() -> None:
    """市場は持たせず、ティッカーから推定する。"""
    assert _parse_one(ticker="9999").market == "jp"
    assert _parse_one(ticker="ko").market == "us"
    assert _parse_one(ticker="ko").ticker == "KO"


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ("9999", "dict で書くこと"),
        (_stock(ticker=""), "ticker が無い"),
        (_stock(shares=0), "shares は正の整数"),
        (_stock(shares=True), "shares は正の整数"),
        (_stock(buy_by="2026-12-25"), "buy_by は日付のリスト"),
        (_stock(buy_by=[]), "buy_by は日付のリスト"),
        (_stock(buy_by=["12/25"]), "buy_by の日付を読めない"),
        (_stock(purpose=1), "purpose は文字列"),
    ],
)
def test_invalid_entries_are_skipped_with_warning(entry: object, reason: str) -> None:
    """書式の誤りはその 1 件を飛ばして警告し、残りの銘柄は読む。"""
    stocks, warnings = long_term.parse([entry, _stock(ticker="1111")])
    assert [stock.ticker for stock in stocks] == ["1111"]
    assert len(warnings) == 1
    assert warnings[0].startswith("LONG_TERM_STOCKS の 1 件目")
    assert reason in warnings[0]


def test_duplicate_ticker_is_warned() -> None:
    """同じ銘柄を 2 回書いたら、2 件目を警告にする。"""
    stocks, warnings = long_term.parse([_stock(), _stock()])
    assert len(stocks) == 1
    assert "重複" in warnings[0]


def _held(tickers: set[str]) -> EffectiveHoldings:
    return EffectiveHoldings(
        holdings=[{"ticker": t} for t in sorted(tickers)],
        as_of="2026-11-01",
        executions_read=0,
        executions_applied=0,
        warnings=[],
    )


def test_long_term_stocks_are_removed_from_universe(monkeypatch: pytest.MonkeyPatch) -> None:
    """長期保有枠の銘柄は、ウォッチリストにも探索ユニバースにも、--include-held でも入らない。"""
    monkeypatch.setattr(screen, "WATCHLIST_JP", ["1111", "3333"])
    monkeypatch.setattr(screen, "WATCHLIST_US", [])
    monkeypatch.setattr(screen, "UNIVERSE_JP", ["2222", "4444"])
    monkeypatch.setattr(screen, "UNIVERSE_US", [])
    monkeypatch.setattr(screen, "effective_holdings", lambda: _held(set()))

    for include_held in (False, True):
        universe, _ = screen.build_universe("jp", include_held, ["1111", "2222"])
        assert [member.ticker for member in universe] == ["3333", "4444"]


def _bars(closes: list[float]) -> pd.DataFrame:
    index = pd.date_range("2026-10-01", periods=len(closes), freq="B")
    series = pd.Series(closes, index=index)
    return pd.DataFrame(
        {"open": series, "high": series + 1, "low": series - 1, "close": series, "volume": 10.0}
    )


def test_long_term_rows_show_status_quo_without_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """買付予定は流動性も事象も問わず全件を、期限の近い順に、現況と概算必要額つきで返す。

    出来高 10 株の板の薄い銘柄でも、値動きの無い銘柄でも落とさない。保有中は返さない。
    """
    stocks, _ = long_term.parse([
        _stock(ticker="1111", buy_by=["2027-03-26"]),
        _stock(ticker="2222", buy_by=["2026-12-25"], shares=200),
        _stock(ticker="3333"),
        _stock(ticker="4444", buy_by=["2026-12-01"]),
    ])
    items = long_term.plan(stocks, {"3333"}, TODAY)
    flat = _bars([1000.0] * 30)
    monkeypatch.setattr(screen, "fetch_ohlcv", lambda ticker, **_: flat)
    monkeypatch.setattr(screen, "display_name", lambda *_: None)

    rows = screen.long_term_rows(items)

    assert [row["ticker"] for row in rows] == ["4444", "2222", "1111"]
    second = rows[1]
    assert second["days_left"] == 37
    assert second["price"] == 1000.0
    assert second["required_amount"] == 200_000.0
    assert second["range"] == {"low": 999.0, "high": 1001.0, "pos_pct": 50.0}
    assert second["status"] == "買付予定"
    assert second["error"] is None


def test_long_term_rows_keep_rows_when_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """株価を取れなくても期限の行は残し、取れなかった理由を添える。"""
    stocks, _ = long_term.parse([
        _stock(ticker="1111", buy_by=["2026-12-25"]),
        _stock(ticker="2222", buy_by=["2026-12-26"]),
        _stock(ticker="3333", buy_by=["2026-12-27"]),
    ])
    frames = {"2222": _bars([]).iloc[0:0], "3333": _bars([500.0] * 5)}

    def fetch(ticker, **_):
        if ticker == "1111":
            raise RuntimeError("rate limited")
        return frames[ticker]

    monkeypatch.setattr(screen, "fetch_ohlcv", fetch)
    monkeypatch.setattr(screen, "display_name", lambda *_: None)

    rows = screen.long_term_rows(long_term.plan(stocks, set(), TODAY))

    assert [row["error"] for row in rows] == ["rate limited", "取得できず", "データ不足"]
    assert rows[2]["price"] == 500.0
    assert rows[2]["required_amount"] == 50_000.0
    assert rows[2]["range"] is None


def test_long_term_section_lists_planned_and_counts_held() -> None:
    """節は買付予定を銘柄ごとに出し、保有中は件数だけ出す。期限切れは書き足しを促す。"""
    rows = [
        {
            "ticker": "2222", "market": "jp", "currency": "JPY", "name": "銘柄B", "purpose": "優待",
            "status": "買付予定", "shares": 200, "deadline": "2026-12-25", "days_left": 37,
            "expired": False, "price": 1000.0,
            "range": {"low": 900.0, "high": 1100.0, "pos_pct": 50.0},
            "required_amount": 200_000.0, "error": None,
        },
        {
            "ticker": "KO", "market": "us", "currency": "USD", "name": None, "purpose": "",
            "status": "状態不明", "shares": 10, "deadline": None, "days_left": None,
            "expired": True, "price": None, "range": None, "required_amount": None,
            "error": "取得できず",
        },
    ]

    lines = screen.long_term_section(rows, held_count=1, warnings=["LONG_TERM_STOCKS の 3 件目: x"])

    text = "\n".join(lines)
    assert "長期保有枠 3 銘柄: 買付予定 2 / 保有中 1" in text
    assert "[warn] LONG_TERM_STOCKS の 3 件目: x" in text
    assert "[優待] 権利付き最終日 2026-12-25 (あと 37 日)" in text
    assert "現値 ¥1,000.00 / 20日レンジ 900.00〜1,100.00 (終値位置 50%) / 必要 200株 / 概算 ¥200,000" in text
    assert "(保有を確認できず) 次の権利付き最終日なし" in text
    assert "必要 10株 / [warn] 取得できず" in text
    assert "buy_by の期限がすべて過去" in text


def test_long_term_section_is_empty_without_config() -> None:
    """長期保有枠を設定していなければ節を出さない。"""
    assert screen.long_term_section([], 0, []) == []


def test_json_includes_long_term_planned_and_held_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json は買付予定を銘柄つきで、保有中を件数だけで出し、長期保有枠を母集団に入れない。"""
    monkeypatch.setattr(screen, "LONG_TERM_STOCKS", [
        _stock(ticker="1111", buy_by=["2026-12-25"]),
        _stock(ticker="2222"),
        _stock(ticker="AAPL", buy_by=["2026-12-25"]),
    ])
    monkeypatch.setattr(screen, "WATCHLIST_JP", ["1111"])
    monkeypatch.setattr(screen, "WATCHLIST_US", [])
    monkeypatch.setattr(screen, "UNIVERSE_JP", [])
    monkeypatch.setattr(screen, "UNIVERSE_US", [])
    monkeypatch.setattr(screen, "effective_holdings", lambda: _held({"2222"}))
    monkeypatch.setattr(screen, "fetch_ohlcv", lambda *_, **__: _bars([1000.0] * 30))
    monkeypatch.setattr(screen, "display_name", lambda *_: None)
    monkeypatch.setattr(screen, "ensure_bar_dates", lambda bars: None)
    monkeypatch.setattr(screen, "load_previous_bars", lambda *_: {})
    monkeypatch.setattr(sys, "argv", ["screen.py", "--json", "--market", "jp"])

    screen.main()

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["screen"]["jp"]["universe"] == 0
    section = parsed["long_term"]
    assert [row["ticker"] for row in section["planned"]] == ["1111"]
    assert section["held"] == 1
    assert section["warnings"] == []
    assert "2222" not in json.dumps(section)
