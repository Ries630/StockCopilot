# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance>=0.2.40", "pandas", "numpy", "ta"]
# ///
"""保有/指定銘柄のテクニカル分析 (TradingCopilot swing/_analyze.py の株式版)。

対象銘柄はコマンドライン引数で渡す (例: uv run analyze.py 9433 AAPL)。
省略時は Investment の保有銘柄すべて。

時間足は日足 + 週足 (crypto の 1h/4h/1d に対して、株式のスイングでは
週足が上位トレンド、日足が執行判断に対応する)。
**確定足のみで判定する** (lib/datasource.py が保証)。

決算日を併記する。crypto と違い株式にはギャップ要因があり、確定足ベースの
トリガー (「日足終値で X を割ったら」) が決算をまたぐと飛ばされて執行できない。
"""

import datetime as dt
import json
import sys

from lib.datasource import detect_market, fetch_next_earnings, fetch_ohlcv
from lib.holdings import load_holdings
from lib.indicators import compute

# 保有データ (Investment の生成物) がこの日数より古ければ警告する。
# Investment は日次更新ではないため、古い as_of のまま「前回からの増減」を
# 判定すると、実際には無かった売買を読み取ってしまう
STALE_DAYS = 7

# 決算日がこの日数以内 (前後) なら警告を付ける。
# 前方 = 確定足ベースのトリガーがギャップで飛ばされうる期間、
# 後方 = 直近の値動きが決算反応である可能性を疑うべき期間
EARNINGS_ALERT_DAYS = 7


def earnings_note(ticker: str, market: str) -> str:
    """決算日の注記を返す。取得できなければ空文字。

    決算日そのものを報告するためではなく、確定足ベースのトリガーが
    ギャップで飛ばされうる期間かどうかを示すために出す。

    Args:
        ticker: 生ティッカー。
        market: "jp" または "us"。

    Returns:
        表示用の 1 行 (例: "⚠ 決算 2026-08-07 (本日) — ギャップ注意")。
    """
    day = fetch_next_earnings(ticker, market)
    if day is None:
        return ""
    delta = (day - dt.date.today()).days
    if delta > 0:
        note = f"決算 {day} (あと {delta} 日)"
        warn = delta <= EARNINGS_ALERT_DAYS
    elif delta == 0:
        note, warn = f"決算 {day} (本日)", True
    else:
        note = f"直近決算 {day} ({-delta} 日前)"
        warn = -delta <= EARNINGS_ALERT_DAYS
    if not warn:
        return note
    tail = "直近の値動きは決算反応の可能性" if delta < 0 else "ギャップでトリガーが飛びうる"
    return f"⚠ {note} — {tail}"


def analyze_symbol(ticker: str, market: str, label: str = "") -> None:
    """1 銘柄の日足・週足の指標を表示する。

    Args:
        ticker: 生ティッカー。
        market: "jp" または "us"。
        label: 銘柄名など表示用の補足。
    """
    print(f"\n########## {ticker} {label} ##########")
    if note := earnings_note(ticker, market):
        print(note)
    for interval, name in (("1d", "日足"), ("1wk", "週足")):
        df = fetch_ohlcv(ticker, market=market, interval=interval, limit=200)
        ind = compute(df)
        chg = None
        if len(df) > 1:
            chg = round((df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100, 2)
        print(f"--- {name} chg1bar={chg}% (確定足 {len(df)}本) ---")
        print(json.dumps(ind, default=str, ensure_ascii=False))


def print_holdings_header(holdings: list[dict]) -> None:
    """保有データの鮮度 (as_of と経過日数) を表示する。

    鮮度を毎回目に見える形で出すのは、古い保有データを最新と誤認したまま
    増減を語るのを防ぐため。判定できない場合もその旨を出し、黙って進めない。

    Args:
        holdings: load_holdings() の戻り値 (1 件以上)。
    """
    as_of = holdings[0].get("as_of") or ""
    head = f"保有 {len(holdings)} 銘柄 / Investment as_of={as_of or '不明'}"
    try:
        days = (dt.date.today() - dt.date.fromisoformat(as_of)).days
    except ValueError:
        print(f"{head} (経過日数を判定できない。増減の判定は保留すること)\n")
        return
    stale = " ⚠ 増減の判定には古い。Investment の更新を確認すること" if days > STALE_DAYS else ""
    print(f"{head} ({days} 日前){stale}\n")


def main() -> None:
    """引数の銘柄 (省略時は保有全銘柄) を順に分析する。"""
    args = sys.argv[1:]
    if args:
        targets = [(t, detect_market(t), "") for t in args]
    else:
        holdings = load_holdings()
        if not holdings:
            print("保有銘柄が見つからない。ティッカーを引数で指定すること。")
            return
        print_holdings_header(holdings)
        targets = [
            (h["ticker"], h["market"], f"{h['name']} ({h['account']} {h['quantity']}株)")
            for h in holdings
        ]

    for ticker, market, label in targets:
        try:
            analyze_symbol(ticker, market, label)
        except Exception as e:  # 1 銘柄の失敗で全体を止めない
            print(f"  [warn] {ticker}: {str(e)[:120]}")


if __name__ == "__main__":
    main()
