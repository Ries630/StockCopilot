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
流動性フロアは探索ユニバースだけにかける。ウォッチリストは板の薄さを承知で入れる層なので、
フロアで落とすと永久に候補に出なくなる (docs/adr/0034-no-liquidity-floor-for-watchlist.md)。
保有銘柄は既定で除外する (--include-held で含められる)。除外に使うのは
Investment の as_of 時点の保有ではなく、ジャーナルの執行記録と合成した実効保有
(lib/holdings.py の effective_holdings)。

長期保有枠 (config/watchlist.py の LONG_TERM_STOCKS) は母集団から外し、「買付予定」の節で
事象によらず全件の現況と権利付き最終日までの残日数を出す。買い判断はしない
(docs/adr/0035-long-term-holding-slot.md)。

使い方:
    uv run screen.py                     # JP + US 全ユニバース
    uv run screen.py --market jp         # 日本株のみ
    uv run screen.py --include-held      # 保有銘柄も母集団に含める
    uv run screen.py --earnings          # 候補に決算日を併記
    uv run screen.py --json              # JSON 出力
"""

import argparse
import datetime as dt
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, NamedTuple

from config.universe import (
    LONG_TERM_STOCKS,
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
from lib.holdings import EffectiveHoldings, effective_holdings
from lib.indicators import daily_stats
from lib.journal import JOURNAL_PATH
from lib.long_term import Plan, Status
from lib.long_term import parse as parse_long_term
from lib.long_term import plan as plan_long_term
from lib.market_observation import (
    active_markets,
    candidate_observation_labels,
    compare_bars,
    load_previous_bars,
)
from lib.names import display_name, label

LATEST_REPORT_PATH = Path(__file__).resolve().parent / "reports" / "latest.json"
"""前回の夕方ブリーフ。市場別の確定足比較に使う。"""


Layer = Literal["watch", "explore"]
"""母集団の層。watch はウォッチリスト、explore は探索ユニバース (ADR-0010)。"""


class Member(NamedTuple):
    """母集団の 1 銘柄。

    Attributes:
        ticker: 生ティッカー。
        market: ``jp`` または ``us``。
        layer: どの層から入ったか。流動性フロアの適用と非通過内訳の表示を決める。
    """

    ticker: str
    market: str
    layer: Layer


class Rejection(Enum):
    """候補にならなかった理由。値は表示用の文言。"""

    BELOW_FLOOR = "流動性フロア未満"
    NO_EVENT = "事象なし"
    INSUFFICIENT = "データ不足"
    UNAVAILABLE = "取得できず"


COUNT_KEYS = {
    Rejection.BELOW_FLOOR: "below_floor",
    Rejection.NO_EVENT: "no_event",
    Rejection.INSUFFICIENT: "insufficient",
    # 上場廃止・ティッカーの誤りでは例外ではなく空の結果が返る。上場直後の
    # データ不足と区別するため、取得の例外と同じ「取得失敗」に数える
    Rejection.UNAVAILABLE: "failures",
}
"""非通過の理由 → 層別件数のキー。"""


@dataclass(frozen=True)
class ScreenOutcome:
    """1銘柄の取得・評価結果。候補か非通過の理由のどちらか一方を持つ。

    Attributes:
        candidate: 条件を通過した候補。非通過ならNone。
        bar_date: 取得できた最新確定足日。取得不能ならNone。
        rejection: 非通過の理由。通過したならNone。
    """

    candidate: dict | None
    bar_date: str | None
    rejection: Rejection | None

    def __post_init__(self) -> None:
        if (self.candidate is None) == (self.rejection is None):
            raise ValueError("候補と非通過の理由はどちらか一方だけを持つ")

    @property
    def evaluated(self) -> bool:
        """指標を計算できたか (取得できず、データ不足でもなかったか)。"""
        return self.rejection not in (Rejection.INSUFFICIENT, Rejection.UNAVAILABLE)


def build_universe(
    market: str, include_held: bool, long_term: Iterable[str] = ()
) -> tuple[list[Member], EffectiveHoldings | None]:
    """層付きの母集団を返す。保有銘柄は既定で除外し、長期保有枠は常に除外する。

    ウォッチリスト (保有検討中) を探索ユニバースより先に置く。取得順が
    そのまま失敗時の到達順になるため、優先度の高い層から取りに行く。
    両方に載る銘柄はウォッチリスト層として扱う (流動性フロアをかけない側)。

    Args:
        market: "jp" / "us" / "all"。
        include_held: True なら保有銘柄を除外しない。
        long_term: 長期保有枠のティッカー。買うと決めている銘柄を事象で拾い直す
            意味がないので、``include_held`` によらず除外する。

    Returns:
        ([Member, ...], 実効保有)。ティッカーの重複は除去済み。
        実効保有は除外しなかった場合と保有データを読めなかった場合に None。
        表示は呼び出し側に任せる (この関数は入出力を持たない)。
    """
    universe: list[Member] = []
    if market in ("jp", "all"):
        universe += [Member(t, "jp", "watch") for t in WATCHLIST_JP]
        universe += [Member(t, "jp", "explore") for t in UNIVERSE_JP]
    if market in ("us", "all"):
        universe += [Member(t, "us", "watch") for t in WATCHLIST_US]
        universe += [Member(t, "us", "explore") for t in UNIVERSE_US]

    # ウォッチリストと探索ユニバースは重複しうる。yfinance は 1 銘柄 1 リクエストなので、
    # 重複を残すと同じ銘柄を二度取りに行くことになる
    deduped: list[Member] = []
    seen: set[str] = set()
    for member in universe:
        if member.ticker not in seen:
            seen.add(member.ticker)
            deduped.append(member)
    excluded = {ticker.upper() for ticker in long_term}
    universe = [member for member in deduped if member.ticker.upper() not in excluded]

    if include_held:
        return universe, None
    try:
        held = effective_holdings()
    except FileNotFoundError:
        # 保有データが無い環境 (Investment 未生成) では除外せず続行する。
        # 除外できなかったことは呼び出し側が None から判別する
        return universe, None
    universe = [member for member in universe if member.ticker not in held.tickers]
    return universe, held


def screen_one(
    ticker: str,
    market: str,
    bar_dates: dict[str, str] | None = None,
    apply_floor: bool = True,
) -> dict | None:
    """1 銘柄を機械条件にかける。通過しなければ None。

    Args:
        ticker: 生ティッカー。
        market: "jp" または "us"。
        bar_dates: 市場ごとの最新確定足日を収集する辞書。省略時は収集しない。
        apply_floor: 流動性フロアをかけるか。ウォッチリスト層では False。

    Returns:
        候補 dict (score / reasons 付き) または None。
    """
    outcome = evaluate_one(ticker, market, apply_floor)
    if bar_dates is not None and outcome.bar_date is not None:
        bar_dates[market] = max(bar_dates.get(market, outcome.bar_date), outcome.bar_date)
    return outcome.candidate


def evaluate_one(ticker: str, market: str, apply_floor: bool = True) -> ScreenOutcome:
    """1銘柄を取得し、候補か非通過の理由を返す。

    Args:
        ticker: 生ティッカー。
        market: ``jp`` または ``us``。
        apply_floor: 流動性フロアをかけるか。ウォッチリスト層では False。

    Returns:
        確定足日と、候補または非通過の理由を持つ結果。
    """
    df = fetch_ohlcv(ticker, market=market, interval="1d", limit=60)
    if df.empty:
        return ScreenOutcome(None, None, Rejection.UNAVAILABLE)
    bar_date = df.index[-1].date().isoformat()
    stats = daily_stats(df)
    if not stats:
        return ScreenOutcome(None, bar_date, Rejection.INSUFFICIENT)

    # 流動性フロア: 20 日平均売買代金
    floor = MIN_TURNOVER_JPY if market == "jp" else MIN_TURNOVER_USD
    if apply_floor and stats["turnover_avg20"] < floor:
        return ScreenOutcome(None, bar_date, Rejection.BELOW_FLOOR)

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
        return ScreenOutcome(None, bar_date, Rejection.NO_EVENT)

    candidate = {
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
    return ScreenOutcome(candidate, bar_date, None)


def ensure_bar_dates(bar_dates: dict[str, str]) -> None:
    """スクリーニング範囲と独立してJP/USの最新確定足日を補う。"""
    for market, ticker in (("jp", UNIVERSE_JP[0]), ("us", UNIVERSE_US[0])):
        if market in bar_dates:
            continue
        try:
            df = fetch_ohlcv(ticker, market=market, interval="1d", limit=2)
        except Exception:
            continue
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


def held_summary(held: EffectiveHoldings | None, include_held: bool) -> str:
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


def collect_screen(
    universe: list[Member], json_mode: bool
) -> tuple[list[dict], dict[str, str], dict[str, dict], dict[str, dict]]:
    """全候補・確定足・市場別の評価件数・層別の件数を収集する。

    Args:
        universe: 層付きの母集団。
        json_mode: 標準出力をJSONだけにする場合はTrue。

    Returns:
        ``(全候補, 市場別確定足日, 市場別screen統計, 層別件数)``。
        層別件数は ``layer_counts()`` の形。
    """
    tally: dict[tuple[str, Layer], Counter] = {
        (market, layer): Counter() for market in ("jp", "us") for layer in ("watch", "explore")
    }
    stats = {
        market: {
            "universe": sum(1 for member in universe if member.market == market),
            "evaluated": 0,
            "failures": 0,
            "matched": 0,
            "selected": 0,
            "failure_details": [],
        }
        for market in ("jp", "us")
    }
    candidates: list[dict] = []
    bar_dates: dict[str, str] = {}
    for ticker, market, layer in universe:
        counts = tally[market, layer]
        counts["universe"] += 1
        try:
            outcome = evaluate_one(ticker, market, apply_floor=layer == "explore")
        except Exception as exc:  # 1 銘柄の失敗で全体を止めない
            message = str(exc)[:100]
            counts["failures"] += 1
            stats[market]["failures"] += 1
            stats[market]["failure_details"].append({"ticker": ticker, "message": message})
            if not json_mode:
                print(f"  [warn] {ticker}: {message}")
            continue
        if outcome.bar_date is not None:
            bar_dates[market] = max(
                bar_dates.get(market, outcome.bar_date), outcome.bar_date
            )
        if outcome.evaluated:
            stats[market]["evaluated"] += 1
        if outcome.candidate is not None:
            candidates.append(outcome.candidate)
            stats[market]["matched"] += 1
            counts["matched"] += 1
        else:
            counts[COUNT_KEYS[outcome.rejection]] += 1
    return candidates, bar_dates, stats, layer_counts(tally)


def layer_counts(tally: dict[tuple[str, Layer], Counter]) -> dict[str, dict]:
    """層別の件数を出力用の形にする。

    非通過の内訳はウォッチリスト層だけ出す。探索層で事象が起きないのは常態で、
    内訳を出しても読み手が取る行動がない。ウォッチリスト層にはフロアをかけないので、
    フロア未満の件数は出さない。銘柄は出さない (ウォッチリストは購入意図そのもの)。

    Args:
        tally: ``(市場, 層)`` ごとの件数。

    Returns:
        ``{market: {"watch": {...}, "explore": {...}}}``。
    """
    result: dict[str, dict] = {}
    for market in ("jp", "us"):
        watch, explore = tally[market, "watch"], tally[market, "explore"]
        result[market] = {
            "watch": {
                key: watch[key]
                for key in ("universe", "matched", "no_event", "insufficient", "failures")
            },
            "explore": {key: explore[key] for key in ("universe", "matched")},
        }
    return result


def held_for_long_term(held: EffectiveHoldings | None) -> set[str] | None:
    """長期保有枠の状態判定に使う実効保有のティッカーを返す。

    ``--include-held`` で除外しなかった回も状態は判定したいので、そのときは読み直す。

    Args:
        held: build_universe が返した実効保有。

    Returns:
        ティッカー集合。保有データを読めなければ None (状態不明として扱う)。
    """
    if held is not None:
        return held.tickers
    try:
        return effective_holdings().tickers
    except FileNotFoundError:
        return None


def long_term_rows(plans: list[Plan]) -> list[dict]:
    """買付予定 (保有中以外) の銘柄の現況を取得する。

    フロアも事象ゲートも通さず、全件を返す。出すのは現況で、採否ではない (ADR-0006)。
    期限の近い順に並べる。期限がすべて過去の銘柄は最後に置く。

    Args:
        plans: 長期保有枠の判定結果。

    Returns:
        出力用の行。取得できなかった値は None で、理由を ``error`` に入れる。
    """
    rows = []
    for item in plans:
        if item.status is Status.HELD:
            continue
        stock = item.stock
        row = {
            "ticker": stock.ticker,
            "market": stock.market,
            "currency": "JPY" if stock.market == "jp" else "USD",
            "name": None,
            "purpose": stock.purpose,
            "status": item.status.value,
            "shares": stock.shares,
            "deadline": None if item.deadline is None else item.deadline.isoformat(),
            "days_left": item.days_left,
            "expired": item.expired,
            "price": None,
            "range": None,
            "required_amount": None,
            "error": None,
        }
        # 1 銘柄の取得失敗で節全体を落とさない (期限の提示は株価と独立)
        try:
            df = fetch_ohlcv(stock.ticker, market=stock.market, interval="1d", limit=60)
        except Exception as exc:
            row["error"] = str(exc)[:100]
            df = None
        if df is not None and df.empty:
            row["error"] = Rejection.UNAVAILABLE.value
        elif df is not None:
            price = float(df["close"].iloc[-1])
            row["price"] = price
            row["required_amount"] = price * stock.shares
            if stats := daily_stats(df):
                row["range"] = {
                    "low": stats["low20"],
                    "high": stats["high20"],
                    "pos_pct": stats["range_pos"] * 100,
                }
            else:
                row["error"] = Rejection.INSUFFICIENT.value
        try:
            row["name"] = display_name(stock.ticker, stock.market, NAMES_JP)
        except Exception:
            row["name"] = None
        rows.append(row)
    rows.sort(key=lambda row: (row["days_left"] is None, row["days_left"] or 0))
    return rows


def long_term_section(rows: list[dict], held_count: int, warnings: list[str]) -> list[str]:
    """「買付予定」の節の表示行を作る。長期保有枠が空なら空リスト。

    保有中の銘柄は件数だけを出す (保有一覧を出力に残さない。ADR-0008)。

    Args:
        rows: long_term_rows() の戻り値。
        held_count: 保有中の銘柄数。
        warnings: 設定を読めなかった要素の警告。

    Returns:
        表示する行。
    """
    if not rows and not held_count and not warnings:
        return []
    lines = [
        f"\n買付予定 (長期保有枠 {len(rows) + held_count} 銘柄: 買付予定 {len(rows)} / "
        f"保有中 {held_count}。採否ではなく現況):"
    ]
    lines += [f"  [warn] {warning}" for warning in warnings]
    for row in rows:
        cur = "¥" if row["currency"] == "JPY" else "$"
        purpose = f" [{row['purpose']}]" if row["purpose"] else ""
        unknown = " (保有を確認できず)" if row["status"] == Status.UNKNOWN.value else ""
        if row["expired"]:
            deadline = "次の権利付き最終日なし"
        elif row["days_left"] == 0:
            deadline = f"権利付き最終日 {row['deadline']} (本日)"
        else:
            deadline = f"権利付き最終日 {row['deadline']} (あと {row['days_left']} 日)"
        lines.append(f"  {label(row['ticker'], row['name'])}{purpose}{unknown} {deadline}")
        parts = []
        if row["price"] is not None:
            parts.append(f"現値 {cur}{row['price']:,.2f}")
        if row["range"] is not None:
            parts.append(
                f"20日レンジ {row['range']['low']:,.2f}〜{row['range']['high']:,.2f} "
                f"(終値位置 {row['range']['pos_pct']:.0f}%)"
            )
        parts.append(f"必要 {row['shares']}株")
        if row["required_amount"] is not None:
            parts.append(f"概算 {cur}{row['required_amount']:,.0f}")
        if row["error"]:
            parts.append(f"[warn] {row['error']}")
        lines.append(f"    {' / '.join(parts)}")
        if row["expired"]:
            lines.append(
                "    ⚠ buy_by の期限がすべて過去。次の権利付き最終日を書き足すこと"
            )
    return lines


def watch_summary(market: str, counts: dict) -> str | None:
    """ウォッチリスト層の非通過内訳を 1 行にする。ウォッチリストが空なら None。"""
    if not counts["universe"]:
        return None
    return (
        f"{market.upper()} ウォッチリスト {counts['universe']} 銘柄: "
        f"通過 {counts['matched']} / 事象なし {counts['no_event']} / "
        f"データ不足 {counts['insufficient']} / 取得失敗 {counts['failures']}"
    )


def select_candidates(
    candidates: list[dict], bar_status: dict[str, dict], stats: dict[str, dict]
) -> list[dict]:
    """更新市場だけから候補を選び、score順と上限を適用する。

    Args:
        candidates: 上限適用前の全候補。
        bar_status: 市場別の確定足更新状態。
        stats: 市場別screen統計。selected件数を更新する。

    Returns:
        更新市場から選ばれた最大 ``MAX_CANDIDATES`` 件。
    """
    markets = active_markets(bar_status)
    selected = [row for row in candidates if row["market"] in markets]
    selected.sort(key=lambda row: -row["score"])
    selected = selected[:MAX_CANDIDATES]
    for market in ("jp", "us"):
        stats[market]["selected"] = sum(1 for row in selected if row["market"] == market)
    return selected


def main() -> None:
    """ユニバースを機械条件にかけ、更新市場の候補を表示する。"""
    ap = argparse.ArgumentParser(description="株式候補の機械スクリーニング")
    ap.add_argument("--market", default="all", choices=["jp", "us", "all"])
    ap.add_argument("--include-held", action="store_true", help="保有銘柄も母集団に含める")
    ap.add_argument("--earnings", action="store_true",
                    help="候補の決算日も取得する (候補 1 件につき 1 リクエスト増える)")
    ap.add_argument("--json", action="store_true", help="JSON で出力")
    args = ap.parse_args()

    stocks, long_term_warnings = parse_long_term(LONG_TERM_STOCKS)
    universe, held = build_universe(
        args.market, args.include_held, [stock.ticker for stock in stocks]
    )
    stocks = [stock for stock in stocks if args.market in ("all", stock.market)]
    plans = plan_long_term(stocks, held_for_long_term(held) if stocks else None, dt.date.today())
    long_term = long_term_rows(plans)
    held_long_term = sum(1 for item in plans if item.status is Status.HELD)
    if not args.json:
        print(held_summary(held, args.include_held))
    candidates, bar_dates, screen_stats, layers = collect_screen(universe, args.json)
    ensure_bar_dates(bar_dates)
    previous_bars = load_previous_bars(LATEST_REPORT_PATH, JOURNAL_PATH)
    bar_status = compare_bars(bar_dates, previous_bars)
    candidates = select_candidates(candidates, bar_status, screen_stats)
    attach_names(candidates)
    if args.earnings:
        attach_earnings(candidates)

    if args.json:
        print(json.dumps({
            "bars": bar_dates,
            "bar_status": bar_status,
            "screen": screen_stats,
            # 銘柄そのものは出さない (public リポジトリに貼られうる出力のため)。
            # 除外が効いていたかを件数で確認できるだけにする
            "held": {
                "excluded": None if held is None else len(held.tickers),
                "as_of": None if held is None else held.as_of,
                "executions_read": None if held is None else held.executions_read,
                "executions_applied": None if held is None else held.executions_applied,
                "journal_warnings": [] if held is None else held.warnings,
            },
            # 層ごとの件数だけ。ウォッチリストの銘柄は出さない (ADR-0034)
            "layers": layers,
            "candidates": [json_candidate(row) for row in candidates],
            # 買付予定は銘柄ごとの期限が目的なので銘柄を出す。保有中は件数だけ (ADR-0035)
            "long_term": {
                "planned": long_term,
                "held": held_long_term,
                "warnings": long_term_warnings,
            },
            "params": {
                "min_move_in_atr": MIN_MOVE_IN_ATR,
                "min_break_in_atr": MIN_BREAK_IN_ATR,
                "min_turnover_jpy": MIN_TURNOVER_JPY,
                "min_turnover_usd": MIN_TURNOVER_USD,
            },
        }, indent=1, default=str, ensure_ascii=False))
        return

    print(
        f"母集団 {len(universe)} 銘柄 (market={args.market}) / "
        f"取得失敗 {sum(item['failures'] for item in screen_stats.values())} 件"
    )
    display_markets = ("jp", "us") if args.market == "all" else (args.market,)
    for market in display_markets:
        item = bar_status[market]
        print(f"  {market.upper()} {bar_dates.get(market, '不明')} ({item['status']})")
    observation_labels = candidate_observation_labels(screen_stats, bar_status)
    print()
    for market in display_markets:
        print(observation_labels[market])
    for market in display_markets:
        if line := watch_summary(market, layers[market]["watch"]):
            print(line)
    for line in long_term_section(long_term, held_long_term, long_term_warnings):
        print(line)
    if not candidates:
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
