# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance>=0.2.40", "pandas", "numpy", "ta"]
# ///
"""株式候補の機械スクリーニング。

**このスクリプトは「買い」の判定をしない。** 機械条件で候補を絞るだけで、
採否は必ず analyze.py のテクニカル分析を通して判断する。
誤検知のコストを「分析 1 回分の工数」に抑えるための分離であり、
ここに判断ロジックを足してはならない (swing/screen.py と同じ規範)。

通過条件 (いずれかの「事象」が起きた銘柄のみ):
  - 直近確定足の変化率が日次 ATR の MIN_MOVE_IN_ATR 倍以上
  - 確定終値が直前 20 日レンジを MIN_BREAK_IN_ATR 倍以上 上抜け / 下抜け
「レンジの端に近い」は状態であって事象ではないので条件にしない。
どちらの条件も ATR 単位の下限を持たせている。突破側に下限が無いと、
わずかに超えただけの銘柄が通り、避けたはずの「端にいる」状態を拾うことになる。

母集団はウォッチリスト (config/watchlist.py) + 探索ユニバース (config/universe.py)。
保有銘柄は既定で除外する (--include-held で含められる)。

使い方:
    uv run screen.py                     # JP + US 全ユニバース
    uv run screen.py --market jp         # 日本株のみ
    uv run screen.py --include-held      # 保有銘柄も母集団に含める
    uv run screen.py --json              # JSON 出力
"""

import argparse
import json

from config.universe import (
    MAX_CANDIDATES,
    MIN_BREAK_IN_ATR,
    MIN_MOVE_IN_ATR,
    MIN_TURNOVER_JPY,
    MIN_TURNOVER_USD,
    UNIVERSE_JP,
    UNIVERSE_US,
    WATCHLIST_JP,
    WATCHLIST_US,
)
from lib.datasource import fetch_ohlcv
from lib.holdings import held_tickers
from lib.indicators import daily_stats


def build_universe(market: str, include_held: bool) -> list[tuple[str, str]]:
    """(ticker, market) のリストを返す。保有銘柄は既定で除外する。

    ウォッチリスト (保有検討中) を探索ユニバースより先に置く。取得順が
    そのまま失敗時の到達順になるため、優先度の高い層から取りに行く。

    Args:
        market: "jp" / "us" / "all"。
        include_held: True なら保有銘柄を除外しない。

    Returns:
        [(ticker, market), ...] ティッカーの重複は除去済み。
    """
    universe: list[tuple[str, str]] = []
    if market in ("jp", "all"):
        universe += [(t, "jp") for t in [*WATCHLIST_JP, *UNIVERSE_JP]]
    if market in ("us", "all"):
        universe += [(t, "us") for t in [*WATCHLIST_US, *UNIVERSE_US]]

    # ウォッチリストと探索ユニバースは重複しうる。yfinance は 1 銘柄 1 リクエストなので、
    # 重複を残すと同じ銘柄を二度取りに行くことになる
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for ticker, mkt in universe:
        if ticker not in seen:
            seen.add(ticker)
            deduped.append((ticker, mkt))
    universe = deduped

    if not include_held:
        try:
            held = held_tickers()
        except FileNotFoundError:
            held = set()
        universe = [(t, m) for t, m in universe if t not in held]
    return universe


def screen_one(ticker: str, market: str) -> dict | None:
    """1 銘柄を機械条件にかける。通過しなければ None。

    Args:
        ticker: 生ティッカー。
        market: "jp" または "us"。

    Returns:
        候補 dict (score / reasons 付き) または None。
    """
    df = fetch_ohlcv(ticker, market=market, interval="1d", limit=60)
    stats = daily_stats(df)
    if not stats:
        return None

    # 流動性フロア: 20 日平均売買代金
    floor = MIN_TURNOVER_JPY if market == "jp" else MIN_TURNOVER_USD
    if stats["turnover_avg20"] < floor:
        return None

    close = float(df["close"].iloc[-1])
    prev = float(df["close"].iloc[-2])
    chg_pct = (close / prev - 1) * 100
    move_atr = abs(chg_pct) / stats["atr_pct"] if stats["atr_pct"] else 0.0

    # レンジ突破の度合い: 確定終値が直前 20 日高安をどれだけ超えたか (ATR 単位)
    atr = stats["atr"] or 1.0
    if close > stats["high20"]:
        break_atr, break_dir = (close - stats["high20"]) / atr, "上"
    elif close < stats["low20"]:
        break_atr, break_dir = (stats["low20"] - close) / atr, "下"
    else:
        break_atr, break_dir = 0.0, ""

    # score は「通過した条件」だけから取る。閾値未満の値を max() で拾うと
    # 理由欄と点数が食い違い、候補間で順位の意味が壊れる
    reasons = []
    passed = []
    if move_atr >= MIN_MOVE_IN_ATR:
        reasons.append(f"直近足の動きが日次 ATR の {move_atr:.1f} 倍")
        passed.append(move_atr)
    if break_atr >= MIN_BREAK_IN_ATR:
        reasons.append(f"20 日レンジを{break_dir}に突破 (ATR {break_atr:.1f} 倍)")
        passed.append(break_atr)
    if not passed:
        return None

    return {
        "ticker": ticker,
        "market": market,
        "close": close,
        "change_pct": chg_pct,
        **stats,
        "move_in_atr": move_atr,
        "break_in_atr": break_atr,
        # 両条件を ATR 単位に揃えているため、順位の意味が候補間で一貫する
        "score": max(passed),
        "reasons": reasons,
    }


def main() -> None:
    """ユニバースを機械条件にかけ、候補を score 順で表示する。"""
    ap = argparse.ArgumentParser(description="株式候補の機械スクリーニング")
    ap.add_argument("--market", default="all", choices=["jp", "us", "all"])
    ap.add_argument("--include-held", action="store_true", help="保有銘柄も母集団に含める")
    ap.add_argument("--json", action="store_true", help="JSON で出力")
    args = ap.parse_args()

    universe = build_universe(args.market, args.include_held)
    candidates = []
    for ticker, market in universe:
        try:
            row = screen_one(ticker, market)
        except Exception as e:  # 1 銘柄の失敗で全体を止めない
            print(f"  [warn] {ticker}: {str(e)[:100]}")
            continue
        if row:
            candidates.append(row)

    candidates.sort(key=lambda r: -r["score"])
    candidates = candidates[:MAX_CANDIDATES]

    if args.json:
        print(json.dumps({
            "screened": len(universe),
            "candidates": candidates,
            "params": {
                "min_move_in_atr": MIN_MOVE_IN_ATR,
                "min_break_in_atr": MIN_BREAK_IN_ATR,
                "min_turnover_jpy": MIN_TURNOVER_JPY,
                "min_turnover_usd": MIN_TURNOVER_USD,
            },
        }, indent=1, default=str, ensure_ascii=False))
        return

    print(f"母集団 {len(universe)} 銘柄 (market={args.market})")
    if not candidates:
        # 該当なしは異常ではない。埋め草の候補を出さないための正常な出力
        print("\n候補なし。無理に候補を作らないこと。")
        return
    print(f"\n候補 {len(candidates)} 件 (採否は analyze.py の分析で判断する):\n")
    for c in candidates:
        cur = "¥" if c["market"] == "jp" else "$"
        print(f"  {c['ticker']:<8} {cur}{c['close']:<12,.2f} 直近足 {c['change_pct']:+.2f}% "
              f"[score {c['score']:.1f} ATR]")
        print(f"    {' / '.join(c['reasons'])}")
        print(f"    20日レンジ {c['low20']:,.2f}〜{c['high20']:,.2f} "
              f"(終値位置 {c['range_pos']:.0%}) / ATR {c['atr_pct']:.1f}% "
              f"/ 売買代金20日平均 {cur}{c['turnover_avg20']:,.0f}\n")


if __name__ == "__main__":
    main()
