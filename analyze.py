# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance>=0.2.40", "pandas", "numpy", "ta"]
# ///
"""保有/指定銘柄のテクニカル分析 (CryptoTradingCopilot swing/_analyze.py の株式版)。

対象銘柄はコマンドライン引数で渡す (例: uv run analyze.py 7203 AAPL)。
省略時は実効保有 (Investment の保有 + ジャーナルの執行記録) の全銘柄。

時間足は日足 + 週足 (crypto の 1h/4h/1d に対して、株式のスイングでは
週足が上位トレンド、日足が執行判断に対応する)。
**確定足のみで判定する** (lib/datasource.py が保証)。

決算日を併記する。crypto と違い株式にはギャップ要因があり、確定足ベースの
トリガー (「日足終値で X を割ったら」) が決算をまたぐと飛ばされて執行できない。
"""

import argparse
import datetime as dt
import json
from dataclasses import dataclass

import pandas as pd

from config.universe import NAMES_JP
from lib.datasource import detect_market, fetch_ohlcv
from lib.earnings import earnings_note
from lib.holdings import JOURNAL_SOURCE, EffectiveHoldings, effective_holdings
from lib.indicators import compute
from lib.names import display_name

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

# 指標の計算に渡す確定足の本数。表示したい本数ではなく、EMA200 が窓の先頭の終値に
# 左右されなくなる本数で決める。EMA は再帰計算で、先頭の終値の重みは 200 本では 13.7%、
# 600 本では 0.25% 残る (→ docs/adr/0033-ema200-converged-window.md)。
HISTORY_BARS = 600



INTERVALS = {"1d": "日足", "1wk": "週足"}
"""分析する時間足と表示名。週足が上位トレンド、日足が執行判断に対応する。"""


@dataclass(frozen=True)
class Frame:
    """1 つの時間足の分析結果。

    Attributes:
        interval: yfinance の時間足 (``1d`` / ``1wk``)。
        bars: 取得できた確定足の本数。
        last_bar: 最新確定足の日付 (ISO 形式)。日付を持たない系列なら None。
        change_pct: 最新確定足の 1 本前からの変化率 (%)。足が 2 本未満なら None。
        indicators: ``lib.indicators.compute()`` の戻り値。データ不足なら ``error`` キーを持つ。
    """

    interval: str
    bars: int
    last_bar: str | None
    change_pct: float | None
    indicators: dict


@dataclass(frozen=True)
class SymbolAnalysis:
    """1 銘柄の分析結果。表示と JSON 出力の共通の元データ。

    取得の途中で失敗した場合も、それまでに取れた結果を残して ``error`` を埋める。
    従来の表示が失敗の直前までを出していたので、その情報を落とさないため。

    Attributes:
        ticker: 生ティッカー。
        market: ``jp`` または ``us``。
        label: 表示用の補足 (銘柄名・口座・株数)。
        earnings: 決算注記。空文字は注記なし (ETF など)、None は取得していないことを示す。
        frames: 取得できた時間足の結果 (日足、週足の順)。
        error: 取得・計算の失敗内容。成功なら None。
    """

    ticker: str
    market: str
    label: str
    earnings: str | None
    frames: tuple[Frame, ...]
    error: str | None = None


@dataclass(frozen=True)
class Target:
    """分析対象の 1 銘柄と、出力に添える付帯情報。

    Attributes:
        ticker: 生ティッカー。
        market: ``jp`` または ``us``。
        name: 銘柄名。解決できなければ空文字。
        holding: 保有モードなら effective_holdings() の行。ティッカー指定なら None。
    """

    ticker: str
    market: str
    name: str
    holding: dict | None = None

    @property
    def label(self) -> str:
        """表示用の補足 (銘柄名、保有なら口座と株数)。"""
        if self.holding is None:
            return self.name
        account = self.holding["account"] or "口座不明"
        return f"{self.name} ({account} {self.holding['quantity']}株)".lstrip()


def build_frame(interval: str, df: pd.DataFrame) -> Frame:
    """確定足の DataFrame から 1 つの時間足の結果を作る。

    Args:
        interval: yfinance の時間足。
        df: 確定足の OHLCV (古い順)。

    Returns:
        指標と変化率を持つ Frame。
    """
    change = None
    if len(df) > 1:
        change = round((df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100, 2)
    last = df.index[-1] if len(df) else None
    return Frame(
        interval=interval,
        bars=len(df),
        last_bar=last.date().isoformat() if hasattr(last, "date") else None,
        change_pct=change,
        indicators=compute(df),
    )


def analyze_symbol(
    ticker: str, market: str, label: str = "", fetch_earnings: bool = True
) -> SymbolAnalysis:
    """1 銘柄の決算注記と日足・週足の指標を取得して返す。表示はしない。

    1 銘柄の失敗で全体を止めないよう、例外は戻り値の ``error`` に入れる。

    Args:
        ticker: 生ティッカー。
        market: "jp" または "us"。
        label: 銘柄名・口座・株数など表示用の補足。
        fetch_earnings: 決算注記を取得するか。screen.py --earnings で取得済みの
            候補を分析するときに False にして、同じ注記を二重に取得しない。

    Returns:
        SymbolAnalysis。
    """
    earnings: str | None = None
    frames: list[Frame] = []
    try:
        if fetch_earnings:
            earnings = earnings_note(ticker, market) or ""
        for interval in INTERVALS:
            df = fetch_ohlcv(ticker, market=market, interval=interval, limit=HISTORY_BARS)
            frames.append(build_frame(interval, df))
    except Exception as e:  # 1 銘柄の失敗で全体を止めない
        return SymbolAnalysis(ticker, market, label, earnings, tuple(frames), str(e)[:120])
    return SymbolAnalysis(ticker, market, label, earnings, tuple(frames))


def render_analysis(result: SymbolAnalysis) -> str:
    """分析結果を従来の人間向けの表示にする。

    Args:
        result: analyze_symbol() の戻り値。

    Returns:
        print() に渡す複数行の文字列 (先頭は空行)。
    """
    lines = [f"\n########## {result.ticker} {result.label} ##########"]
    if result.earnings:
        lines.append(result.earnings)
    for frame in result.frames:
        lines.append(
            f"--- {INTERVALS[frame.interval]} chg1bar={frame.change_pct}% "
            f"(確定足 {frame.bars}本) ---"
        )
        lines.append(json.dumps(frame.indicators, default=str, ensure_ascii=False))
    if result.error:
        lines.append(f"  [warn] {result.ticker}: {result.error}")
    return "\n".join(lines)


def frame_json(frame: Frame) -> dict:
    """時間足 1 つを JSON 出力の形にする。"""
    return {
        "bars": frame.bars,
        "last_bar": frame.last_bar,
        "change_pct": frame.change_pct,
        "indicators": frame.indicators,
    }


def symbol_json(target: Target, result: SymbolAnalysis | None) -> dict:
    """1 銘柄を JSON 出力の形にする。

    Args:
        target: 分析対象と付帯情報。
        result: 分析結果。指標を計算しなかった場合 (--holdings-only) は None。

    Returns:
        ``docs/output-contract.md`` の「JSON出力（analyze.py）」の 1 要素。
    """
    holding = None
    if target.holding is not None:
        holding = {
            "account": target.holding["account"] or None,
            "quantity": target.holding["quantity"],
            "as_of": target.holding.get("as_of") or None,
            "as_of_source": target.holding.get("as_of_source") or None,
        }
    frames = {} if result is None else {f.interval: f for f in result.frames}
    earnings = None
    if result is not None and result.earnings is not None:
        earnings = {"note": result.earnings, "warn": result.earnings.startswith("⚠")}
    return {
        "ticker": target.ticker,
        "market": target.market,
        "name": target.name or None,
        "holding": holding,
        "earnings": earnings,
        "daily": frame_json(frames["1d"]) if "1d" in frames else None,
        "weekly": frame_json(frames["1wk"]) if "1wk" in frames else None,
        "error": None if result is None else result.error,
    }


def holdings_json(effective: EffectiveHoldings) -> dict:
    """実効保有の組み立ての内訳を JSON 出力の形にする。"""
    return {
        "as_of": effective.as_of or None,
        "executions_read": effective.executions_read,
        "executions_applied": effective.executions_applied,
        "journal_warnings": effective.warnings,
        "stale_days": STALE_DAYS,
    }


def freshness(as_of: str) -> str:
    """as_of と経過日数を 1 行ぶんの文字列にする。

    STALE_DAYS を超えた場合だけ警告を付ける。as_of が古いこと自体は
    運用上の既定なので、常時点灯させると警告として機能しなくなる。

    Args:
        as_of: ISO 形式の基準日。空文字や不正な値も受け付ける。

    Returns:
        表示用の 1 行 (例: "as_of=2026-07-22 (19 日前)")。
    """
    label = f"as_of={as_of or '不明'}"
    try:
        days = (dt.date.today() - dt.date.fromisoformat(as_of)).days
    except ValueError:
        return f"{label} (経過日数を判定できない。増減の判定は保留すること)"
    if days > STALE_DAYS:
        return (
            f"{label} ({days} 日前) "
            f"⚠ {STALE_DAYS} 日超。執行記録の網羅性を確認し、Investment の同期を検討すること"
        )
    return f"{label} ({days} 日前)"


def print_holdings_header(holdings: list[dict], effective: EffectiveHoldings) -> None:
    """実効保有の組み立てと、保有データの鮮度 (as_of と経過日数) を表示する。

    組み立てを毎回目に見える形で出すのは、株数がどこから来たかを読み手が
    最初に確認できるようにするため。鮮度を出すのは、古い保有データを最新と
    誤認したまま増減を語るのを防ぐため。判定できない場合もその旨を出し、黙って進めない。

    **基準日は資産クラスごとに異なりうる** (証券口座と自動運用口座で数週間ずれる)。
    1 つに丸めると古いほうが新しく見えるため、異なる場合は分けて出す。
    執行記録から株数を取った銘柄は Investment の鮮度と無関係なので、別に数える。

    Args:
        holdings: 表示する実効保有の行 (市場で絞り込んだ後。1 件以上)。
        effective: effective_holdings() の戻り値 (組み立ての内訳を持つ)。
    """
    snapshot = [h for h in holdings if h.get("as_of_source") != JOURNAL_SOURCE]
    from_journal = len(holdings) - len(snapshot)
    groups: dict[str, list[dict]] = {}
    for h in snapshot:
        groups.setdefault(h.get("as_of") or "", []).append(h)

    if effective.executions_read:
        ledger = (
            f"執行記録 {effective.executions_read} 件中 "
            f"{effective.executions_applied} 件を反映"
        )
    else:
        ledger = "執行記録なし (as_of 時点のまま)"
    print(
        f"実効保有 {len(holdings)} 銘柄 = "
        f"Investment as_of={effective.as_of or '不明'} + {ledger}"
    )
    if len(groups) == 1:
        print(f"  Investment {freshness(next(iter(groups)))}")
    elif groups:
        print("  Investment as_of は資産クラスで異なる")
        for as_of, members in sorted(groups.items()):
            sources = sorted({m.get("as_of_source") or "?" for m in members})
            print(f"    {freshness(as_of)} — {len(members)} 銘柄 ({', '.join(sources)})")
    if from_journal:
        print(f"  執行記録から株数を取った銘柄 {from_journal} (約定後の残株数)")
    for warning in effective.warnings:
        print(f"  [warn] journal {warning}")
    print()


def holding_target(holding: dict) -> Target:
    """保有 1 行を分析対象にする。

    as_of 以降に新規に買った銘柄は Investment に行が無く、銘柄名と口座が空になる。
    銘柄名は辞書と yfinance から引き、口座は分からないことをそのまま出す。

    Args:
        holding: effective_holdings() の行。

    Returns:
        銘柄名と保有の行を持つ Target。
    """
    name = holding["name"] or display_name(holding["ticker"], holding["market"], NAMES_JP) or ""
    return Target(holding["ticker"], holding["market"], name, holding)


def main() -> None:
    """引数の銘柄 (省略時は保有全銘柄) を順に分析する。"""
    parser = argparse.ArgumentParser(description="保有/指定銘柄のテクニカル分析")
    parser.add_argument("--market", default="all", choices=["jp", "us", "all"])
    parser.add_argument(
        "--holdings-only",
        action="store_true",
        help="実効保有だけを出し、指標は計算しない (確定足が更新されなかった市場の保有用)",
    )
    parser.add_argument("--json", action="store_true", help="構造化データだけを JSON で出力する")
    parser.add_argument(
        "--no-earnings",
        action="store_true",
        help="決算注記を取得しない (screen.py --earnings で取得済みの候補を分析するとき)",
    )
    parser.add_argument("tickers", nargs="*")
    args = parser.parse_args()
    if args.holdings_only and args.tickers:
        parser.error("--holdings-only は保有モード専用で、ティッカーと同時に指定できない")

    effective: EffectiveHoldings | None = None
    if args.tickers:
        # 4 桁コードだけでは何の会社か分からないので、日本株には名前を付ける
        targets = []
        for t in args.tickers:
            market = detect_market(t)
            if args.market != "all" and market != args.market:
                continue
            targets.append(Target(t, market, display_name(t, market, NAMES_JP) or ""))
    else:
        effective = effective_holdings()
        holdings = [
            holding
            for holding in effective.holdings
            if args.market == "all" or holding["market"] == args.market
        ]
        targets = [holding_target(h) for h in holdings]
        if not args.json:
            if not holdings:
                hint = "" if args.holdings_only else "ティッカーを引数で指定すること。"
                print(f"対象市場 ({args.market}) の保有銘柄が見つからない。{hint}")
                return
            print_holdings_header(holdings, effective)
            if args.holdings_only:
                for t in targets:
                    print(f"{t.ticker} {t.market} {t.label}")
                return

    results: list[SymbolAnalysis | None] = []
    for t in targets:
        if args.holdings_only:
            results.append(None)
            continue
        result = analyze_symbol(t.ticker, t.market, t.label, fetch_earnings=not args.no_earnings)
        results.append(result)
        if not args.json:
            print(render_analysis(result))

    if args.json:
        print(json.dumps({
            "mode": "tickers" if args.tickers else "holdings",
            "market": args.market,
            "analyzed": not args.holdings_only,
            "earnings_fetched": not args.holdings_only and not args.no_earnings,
            "holdings": None if effective is None else holdings_json(effective),
            "symbols": [symbol_json(t, r) for t, r in zip(targets, results, strict=True)],
        }, indent=1, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
