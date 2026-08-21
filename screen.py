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
保有銘柄は既定で除外する (--include-held で含められる)。除外に使うのは
Investment の as_of 時点の保有ではなく、ジャーナルの執行記録と合成した実効保有
(lib/holdings.py の held_tickers)。

使い方:
    uv run screen.py                     # JP + US 全ユニバース
    uv run screen.py --market jp         # 日本株のみ
    uv run screen.py --include-held      # 保有銘柄も母集団に含める
    uv run screen.py --earnings          # 候補に決算日を併記
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
    NAMES_JP,
    UNIVERSE_JP,
    UNIVERSE_US,
    WATCHLIST_JP,
    WATCHLIST_US,
)
from lib.datasource import fetch_ohlcv
from lib.earnings import earnings_note
from lib.holdings import HeldTickers, held_tickers
from lib.indicators import daily_stats
from lib.names import display_name, label


def build_universe(
    market: str, include_held: bool
) -> tuple[list[tuple[str, str]], HeldTickers | None]:
    """(ticker, market) のリストを返す。保有銘柄は既定で除外する。

    ウォッチリスト (保有検討中) を探索ユニバースより先に置く。取得順が
    そのまま失敗時の到達順になるため、優先度の高い層から取りに行く。

    Args:
        market: "jp" / "us" / "all"。
        include_held: True なら保有銘柄を除外しない。

    Returns:
        ([(ticker, market), ...], 実効保有)。ティッカーの重複は除去済み。
        実効保有は除外しなかった場合と保有データを読めなかった場合に None。
        表示は呼び出し側に任せる (この関数は入出力を持たない)。
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

    if include_held:
        return universe, None
    try:
        held = held_tickers()
    except FileNotFoundError:
        # 保有データが無い環境 (Investment 未生成) では除外せず続行する。
        # 除外できなかったことは呼び出し側が None から判別する
        return universe, None
    universe = [(t, m) for t, m in universe if t not in held.tickers]
    return universe, held


def screen_one(
    ticker: str,
    market: str,
    bar_dates: dict[str, str] | None = None,
) -> dict | None:
    """1 銘柄を機械条件にかける。通過しなければ None。

    Args:
        ticker: 生ティッカー。
        market: "jp" または "us"。
        bar_dates: 市場ごとの最新確定足日を収集する辞書。省略時は収集しない。

    Returns:
        候補 dict (score / reasons 付き) または None。
    """
    df = fetch_ohlcv(ticker, market=market, interval="1d", limit=60)
    if bar_dates is not None and not df.empty:
        bar_date = df.index[-1].date().isoformat()
        bar_dates[market] = max(bar_dates.get(market, bar_date), bar_date)
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


def ensure_bar_dates(bar_dates: dict[str, str]) -> None:
    """スクリーニング範囲と独立してJP/USの最新確定足日を補う。"""
    for market, ticker in (("jp", UNIVERSE_JP[0]), ("us", UNIVERSE_US[0])):
        if market in bar_dates:
            continue
        df = fetch_ohlcv(ticker, market=market, interval="1d", limit=2)
        if not df.empty:
            bar_dates[market] = df.index[-1].date().isoformat()


def attach_earnings(candidates: list[dict]) -> None:
    """候補に決算注記を付ける (破壊的更新)。

    決算はギャップ要因で、確定足ベースのトリガーを飛び越えて執行不能にする。
    候補に絞ってから呼ぶ: yfinance は 1 銘柄 1 リクエストで、母集団全体に
    かけると通過しない銘柄の分まで待つことになる。

    Args:
        candidates: screen_one() の戻り値のリスト。
    """
    for c in candidates:
        # 1 銘柄の取得失敗で候補全体を落とさない (決算の有無は候補の採否と別)
        try:
            c["earnings_note"] = earnings_note(c["ticker"], c["market"])
        except Exception:
            c["earnings_note"] = ""


def attach_names(candidates: list[dict]) -> None:
    """候補に表示名を付ける (破壊的更新)。

    **4 桁コードだけでは何の会社か分からない**日本株のために付ける。
    解決の順序は lib/names.py が持つ (config の NAMES_JP → yfinance → None)。
    決算日と同じく候補に絞ってから呼ぶ: 辞書に無い日本株は 1 銘柄 1 リクエストになる。

    Args:
        candidates: screen_one() の戻り値のリスト。
    """
    for c in candidates:
        # 1 銘柄の取得失敗で候補全体を落とさない (名前の有無は候補の採否と別)
        try:
            c["name"] = display_name(c["ticker"], c["market"], NAMES_JP)
        except Exception:
            c["name"] = None


def held_summary(held: HeldTickers | None, include_held: bool) -> str:
    """保有除外の内訳を表示用の文字列にする。

    除外に使った基準日と執行記録の件数を必ず出す。母集団から何が落ちたかが
    見えないと、保有銘柄が候補に出たときに「記録漏れなのかデータが古いのか」を
    切り分けられない。解釈できなかったジャーナルの行も同時に出す。

    Args:
        held: build_universe が返した実効保有 (除外しなかったなら None)。
        include_held: --include-held が指定されたか。

    Returns:
        表示用の文字列 (複数行になりうる)。
    """
    if include_held:
        return "保有除外なし (--include-held)"
    if held is None:
        return "[warn] 保有データを読めなかったため保有除外なし"
    if held.executions_read:
        note = f"執行記録 {held.executions_read} 件中 {held.executions_applied} 件を適用"
    else:
        note = "執行記録なし"
    lines = [
        f"保有 {len(held.tickers)} 銘柄を母集団から除外 "
        f"(Investment as_of={held.as_of or '不明'} / {note})"
    ]
    lines += [f"  [warn] journal {w}" for w in held.warnings]
    return "\n".join(lines)


def json_candidate(row: dict) -> dict:
    """スクリーナーの内部行を中間表現へ合流できる機械データに変換する。

    単位変換やキー名の変換をLLMへ委ねると、`range_pos=1.05`を`pos_pct=1.05`と
    誤って写す余地がある。決定的に変換できる項目はJSON出力時点で揃える。

    Args:
        row: `screen_one()`が返した内部形式の候補。

    Returns:
        `docs/report-contract.schema.json`のCandidateへそのまま合流できる機械データ。
        判断・signals・proseは分析後に追加するため含めない。
    """
    currency = "JPY" if row["market"] == "jp" else "USD"
    turnover = row.get("turnover_avg20")
    result = {
        "ticker": row["ticker"],
        "market": row["market"],
        "currency": currency,
        "price": row["close"],
        "change_pct": row["change_pct"],
        "score_atr": row["score"],
        "pass_reason": " / ".join(row["reasons"]),
        "range": {
            "low": row["low20"],
            "high": row["high20"],
            "pos_pct": row["range_pos"] * 100,
        },
        "atr_pct": row["atr_pct"],
    }
    if turnover is not None:
        symbol = "¥" if currency == "JPY" else "$"
        result["turnover"] = f"{symbol}{turnover:,.0f}"
    if row.get("name"):
        result["name"] = row["name"]
    if row.get("earnings_note"):
        note = row["earnings_note"]
        result["earnings"] = {"note": note, "warn": note.startswith("⚠")}
    return result


def main() -> None:
    """ユニバースを機械条件にかけ、候補を score 順で表示する。"""
    ap = argparse.ArgumentParser(description="株式候補の機械スクリーニング")
    ap.add_argument("--market", default="all", choices=["jp", "us", "all"])
    ap.add_argument("--include-held", action="store_true", help="保有銘柄も母集団に含める")
    ap.add_argument("--earnings", action="store_true",
                    help="候補の決算日も取得する (候補 1 件につき 1 リクエスト増える)")
    ap.add_argument("--json", action="store_true", help="JSON で出力")
    args = ap.parse_args()

    universe, held = build_universe(args.market, args.include_held)
    if not args.json:
        print(held_summary(held, args.include_held))
    candidates = []
    failures = []
    bar_dates: dict[str, str] = {}
    for ticker, market in universe:
        try:
            row = screen_one(ticker, market, bar_dates)
        except Exception as e:  # 1 銘柄の失敗で全体を止めない
            message = str(e)[:100]
            failures.append({"ticker": ticker, "message": message})
            # JSONモードでは標準出力をJSONだけにし、警告は構造化データへ入れる
            if not args.json:
                print(f"  [warn] {ticker}: {message}")
            continue
        if row:
            candidates.append(row)

    candidates.sort(key=lambda r: -r["score"])
    candidates = candidates[:MAX_CANDIDATES]
    attach_names(candidates)
    if args.earnings:
        attach_earnings(candidates)

    if args.json:
        ensure_bar_dates(bar_dates)
        print(json.dumps({
            "universe": len(universe),
            "market": args.market,
            "bars": bar_dates,
            "failures": len(failures),
            "failure_details": failures,
            # 銘柄そのものは出さない (public リポジトリに貼られうる出力のため)。
            # 除外が効いていたかを件数で確認できるだけにする
            "held": {
                "excluded": None if held is None else len(held.tickers),
                "as_of": None if held is None else held.as_of,
                "executions_read": None if held is None else held.executions_read,
                "executions_applied": None if held is None else held.executions_applied,
                "journal_warnings": [] if held is None else held.warnings,
            },
            "candidates": [json_candidate(row) for row in candidates],
            "params": {
                "min_move_in_atr": MIN_MOVE_IN_ATR,
                "min_break_in_atr": MIN_BREAK_IN_ATR,
                "min_turnover_jpy": MIN_TURNOVER_JPY,
                "min_turnover_usd": MIN_TURNOVER_USD,
            },
        }, indent=1, default=str, ensure_ascii=False))
        return

    print(
        f"母集団 {len(universe)} 銘柄 (market={args.market}) "
        f"/ 取得失敗 {len(failures)} 件"
    )
    if not candidates:
        # 該当なしは異常ではない。埋め草の候補を出さないための正常な出力
        print("\n候補なし。無理に候補を作らないこと。")
        return
    print(f"\n候補 {len(candidates)} 件 (採否は analyze.py の分析で判断する):\n")
    for c in candidates:
        cur = "¥" if c["market"] == "jp" else "$"
        print(f"  {label(c['ticker'], c.get('name')):<28} "
              f"{cur}{c['close']:<12,.2f} 直近足 {c['change_pct']:+.2f}% "
              f"[score {c['score']:.1f} ATR]")
        print(f"    {' / '.join(c['reasons'])}")
        if c.get("earnings_note"):
            print(f"    {c['earnings_note']}")
        print(f"    20日レンジ {c['low20']:,.2f}〜{c['high20']:,.2f} "
              f"(終値位置 {c['range_pos']:.0%}) / ATR {c['atr_pct']:.1f}% "
              f"/ 売買代金20日平均 {cur}{c['turnover_avg20']:,.0f}\n")


if __name__ == "__main__":
    main()
