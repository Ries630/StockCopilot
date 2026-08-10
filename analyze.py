# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance>=0.2.40", "pandas", "numpy", "ta"]
# ///
"""保有/指定銘柄のテクニカル分析 (TradingCopilot swing/_analyze.py の株式版)。

対象銘柄はコマンドライン引数で渡す (例: uv run analyze.py 7203 AAPL)。
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

from lib.datasource import detect_market, fetch_ohlcv
from lib.earnings import earnings_note
from lib.holdings import load_holdings
from lib.indicators import compute

# 保有データ (Investment の生成物) の as_of がこの日数より古ければ警告する。
#
# as_of はレポートを生成した日ではなく、証券口座の残高を取り込んだ日に動く。
# 取り込みは高頻度では行わない運用なので、**as_of が古いこと自体は異常ではない**。
# as_of 以降の売買はジャーナルの執行記録で追う (実効保有 = as_of 時点 + 執行記録)。
#
# したがってここでの警告は「データが古い」ではなく、「執行記録だけで差分を
# 追いきれているか自体が怪しい」域に入ったことを示す。日次更新を前提にした
# 短い閾値では常時点灯し、警告として機能しなくなるため四半期を目安にする。
STALE_DAYS = 90

# 経過日数の下に必ず出す 1 行。as_of の古さを異常として扱わせない代わりに、
# 何と突合すべきかを毎回明示する
LEDGER_NOTE = (
    "as_of 以降の売買はジャーナルの執行記録と突合すること "
    "(実効保有 = as_of 時点 + 執行記録)"
)


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

    as_of が古いこと自体は運用上の既定なので、警告ではなく突合の指示を毎回添える。
    警告は STALE_DAYS を超えた場合だけに絞る (常時点灯すると警告が機能しなくなる)。

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
    stale = (
        f" ⚠ {STALE_DAYS} 日超。執行記録の網羅性を確認し、Investment の同期を検討すること"
        if days > STALE_DAYS
        else ""
    )
    print(f"{head} ({days} 日前){stale}")
    print(f"  {LEDGER_NOTE}\n")


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
