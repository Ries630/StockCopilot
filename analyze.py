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
"""

import json
import sys

from lib.datasource import detect_market, fetch_ohlcv
from lib.holdings import load_holdings
from lib.indicators import compute


def analyze_symbol(ticker: str, market: str, label: str = "") -> None:
    """1 銘柄の日足・週足の指標を表示する。

    Args:
        ticker: 生ティッカー。
        market: "jp" または "us"。
        label: 銘柄名など表示用の補足。
    """
    print(f"\n########## {ticker} {label} ##########")
    for interval, name in (("1d", "日足"), ("1wk", "週足")):
        df = fetch_ohlcv(ticker, market=market, interval=interval, limit=200)
        ind = compute(df)
        chg = None
        if len(df) > 1:
            chg = round((df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100, 2)
        print(f"--- {name} chg1bar={chg}% (確定足 {len(df)}本) ---")
        print(json.dumps(ind, default=str, ensure_ascii=False))


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
