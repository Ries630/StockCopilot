"""Investment プロジェクトの生成物から株式保有を読む (read-only)。

Investment 側とは module import せず、生成 JSON を読むだけの疎結合とする。
数量は Investment の report_data 生成時点で分割調整済みなので、
ここで stock_splits.json を再適用してはならない (二重調整になる)。

投資信託 (class=投資信託) は公開ティッカーが無くテクニカル分析の対象外なので除外する。

公開するのは、ジャーナルの執行記録と合成した**実効保有** (effective_holdings) だけにする。
スクリーナーの母集団除外も analyze.py の保有モードも、同じこの合成を使う
([ADR-0015](../docs/adr/0015-journal-executions-machine-read.md) /
[ADR-0031](../docs/adr/0031-effective-holdings-for-analyze.md))。

    実効保有 = Investment の as_of 時点の保有 + as_of 以降の執行記録
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from lib.datasource import detect_market
from lib.journal import Execution, load_executions

INVESTMENT_OUTPUT = Path("~/AgentProjects/Investment/output").expanduser()

# テクニカル分析可能な資産クラス
_ANALYZABLE_CLASSES = {"国内株式", "米国株式", "海外ETF"}

# Investment の market セクションは資産クラスごとに `<名前>_as_of` を持ち、
# それぞれ独立に更新される (証券口座と自動運用口座では基準日が数週間ずれる)。
# stock.as_of を既定とし、口座名がキー名を含むものだけを上書きとして扱う。
_DEFAULT_AS_OF_KEY = "stock_as_of"
_AS_OF_SUFFIX = "_as_of"


def _resolve_as_of(
    account: str, as_of_by_key: dict[str, str], default: str
) -> tuple[str, str]:
    """口座名に対応する as_of と、その出所キーを返す。

    全銘柄に stock.as_of を一律で付けると、別系統で管理されている資産クラスが
    実際より新しく見える。口座名と `<名前>_as_of` のキー名を突き合わせて解決する。

    Args:
        account: 保有の口座名。
        as_of_by_key: {"wealthnavi_as_of": "2026-06-30", ...} 形式。
        default: 既定の as_of (stock.as_of)。

    Returns:
        (as_of, 出所キー)。対応が取れなければ (default, _DEFAULT_AS_OF_KEY)。
        出所キーを併せて返すのは、どの基準日を使ったかを呼び出し側から
        確認できるようにするため (黙って別の日付を使わない)。
    """
    normalized = account.replace(" ", "").replace("　", "").lower()
    for key, value in as_of_by_key.items():
        if key == _DEFAULT_AS_OF_KEY or not value:
            continue
        if key.removesuffix(_AS_OF_SUFFIX).lower() in normalized:
            return str(value), key
    return default, _DEFAULT_AS_OF_KEY


def latest_report_path() -> Path:
    """株式保有 (stock.holdings) を持つ最新の report_data_YYYYMMDD.json のパスを返す。

    Investment の生成物は stock.holdings を欠くことがある。最新ファイルを無条件に
    採ると保有が 0 件になり、analyze.py の保有モードは空振りし、screen.py の
    母集団除外も効かなくなる。そこで stock.holdings を持つファイルまで遡り、
    読み飛ばしたファイルは stderr に出す (黙って古いファイルを使わない)。
    遡った場合の基準日は保有ヘッダの as_of と経過日数に現れる。

    Returns:
        stock.holdings を持つ最新ファイルの Path。

    Raises:
        FileNotFoundError: report_data が 1 件も無い場合、または
            どの report_data も stock.holdings を持たない場合。
    """
    files = sorted(
        p for p in INVESTMENT_OUTPUT.glob("report_data_*.json")
        if re.fullmatch(r"report_data_\d{8}\.json", p.name)
    )
    if not files:
        raise FileNotFoundError(f"report_data_*.json が見つからない: {INVESTMENT_OUTPUT}")
    skipped: list[str] = []
    for path in reversed(files):
        if _has_stock_holdings(path):
            if skipped:
                print(
                    f"[warn] {', '.join(skipped)} は stock.holdings を持たないため "
                    f"{path.name} の保有を使う",
                    file=sys.stderr,
                )
            return path
        skipped.append(path.name)
    raise FileNotFoundError(
        f"stock.holdings を持つ report_data_*.json が見つからない: {INVESTMENT_OUTPUT}"
    )


def _has_stock_holdings(path: Path) -> bool:
    """report_data が株式保有の一覧 (stock.holdings) を持つかを返す。

    空リストは「保有 0 件」という有効な状態なので持つ側に数える。
    キーが無い・リストでない・UTF-8 や JSON として読めない場合だけ持たない扱いにする。

    Args:
        path: report_data_YYYYMMDD.json のパス。

    Returns:
        stock.holdings がリストなら True。
    """
    try:
        data = json.loads(path.read_text())
    # JSONDecodeError と UnicodeDecodeError はどちらも ValueError の派生
    except (OSError, ValueError):
        return False
    stock = data.get("stock") if isinstance(data, dict) else None
    return isinstance(stock, dict) and isinstance(stock.get("holdings"), list)


def _load_holdings() -> tuple[list[dict], str]:
    """保有の一覧と、既定の基準日 (stock.as_of) を返す。

    既定の基準日を併せて返すのは実効保有の合成で要るため。銘柄ごとの as_of は
    口座名から解決するが、**Investment に無い銘柄 (as_of 以降に新規購入した銘柄) には
    口座が無い**ので、そこだけは既定の基準日で判定するしかない。

    Returns:
        (保有の一覧, 既定の基準日)。
    """
    path = latest_report_path()
    data = json.loads(path.read_text())
    stock = data.get("stock", {})
    default_as_of = stock.get("as_of", "")
    as_of_by_key = {
        k: v for k, v in data.get("market", {}).items() if k.endswith(_AS_OF_SUFFIX)
    }
    out = []
    for h in stock.get("holdings", []):
        if h.get("class") not in _ANALYZABLE_CLASSES or not h.get("ticker"):
            continue
        account = h.get("account", "")
        as_of, as_of_source = _resolve_as_of(account, as_of_by_key, default_as_of)
        out.append({
            "ticker": str(h["ticker"]),
            "name": h.get("name", ""),
            "market": "jp" if h.get("region") == "日本" else "us",
            "quantity": h.get("quantity"),
            "account": account,
            "class": h.get("class", ""),
            "as_of": as_of,
            "as_of_source": as_of_source,
        })
    return out, default_as_of


JOURNAL_SOURCE = "journal"
"""実効保有の行の as_of_source に入れる値。株数がジャーナルの執行記録から来たことを示す。"""


@dataclass(frozen=True)
class EffectiveHoldings:
    """実効保有の一覧と、その組み立ての内訳。

    一覧だけでなく内訳を返すのは、**合成が壊れたことに呼び出し側が気付ける**
    ようにするため。ジャーナルは人が書く Markdown なので、書式ゆれで
    保有が静かに不完全になるのが最悪の失敗になる。

    Attributes:
        holdings: 実効保有の行。キーは
            {ticker, name, market, quantity, account, class, as_of, as_of_source}。
            執行を反映した銘柄は 1 行にまとめ、quantity は執行後の残株数、
            as_of は約定日、as_of_source は JOURNAL_SOURCE になる。
            as_of 以降に新規に買った銘柄は Investment に行が無いので、
            name・account・class は空文字になる。
        as_of: Investment の既定の基準日。資産クラス別の基準日は銘柄ごとに
            解決しており、これは表示用の代表値でしかない。
        executions_read: ジャーナルから読めた執行の件数。
        executions_applied: そのうち保有に反映した件数 (銘柄ごとに最新の 1 件)。
        warnings: 解釈できなかった行の警告 ("N 行目: 理由")。
    """

    holdings: list[dict]
    as_of: str
    executions_read: int
    executions_applied: int
    warnings: list[str]

    @property
    def tickers(self) -> set[str]:
        """実効保有のティッカー集合 (スクリーナーの除外フィルタに使う)。"""
        return {h["ticker"] for h in self.holdings}


def effective_holdings() -> EffectiveHoldings:
    """実効保有を返す (テクニカル分析可能な資産クラスのみ)。

        実効保有 = Investment の as_of 時点の保有 + as_of 以降の執行記録

    ジャーナルが無ければ Investment の保有そのものになる (執行 0 件)。
    残株数は銘柄単位の合計なので、執行を反映した銘柄は口座をまたいで 1 行にまとめる
    (口座ごとの内訳はジャーナルからは分からない)。

    Returns:
        EffectiveHoldings。合成の内訳を含む。

    Raises:
        FileNotFoundError: Investment の report_data が 1 件も無い場合。
    """
    rows, default_as_of = _load_holdings()
    executions, warnings = load_executions()
    as_of_by_ticker = {h["ticker"].upper(): h["as_of"] for h in rows}
    applied = _latest_executions(executions, as_of_by_ticker, default_as_of)

    out = [h for h in rows if h["ticker"].upper() not in applied]
    for key, execution in applied.items():
        if execution.remaining <= 0:
            continue
        snapshot = [h for h in rows if h["ticker"].upper() == key]
        out.append(_journal_row(key, execution, snapshot))

    return EffectiveHoldings(
        holdings=out,
        as_of=default_as_of,
        executions_read=len(executions),
        executions_applied=len(applied),
        warnings=warnings,
    )


def _journal_row(key: str, execution: Execution, snapshot: list[dict]) -> dict:
    """執行を反映した銘柄の 1 行を組み立てる。

    銘柄名・市場・口座・資産クラスは Investment の行から引き継ぐ。
    Investment に行が無い (as_of 以降に新規に買った) 銘柄では、市場は
    ティッカーから推定し、それ以外は空文字にする。推測で埋めると、
    分からないことが表示から消える。

    Args:
        key: 大文字ティッカー。
        execution: 反映する執行 (残株数 > 0)。
        snapshot: Investment の同じ銘柄の行 (口座ごと。0 件もありうる)。

    Returns:
        effective_holdings() の行と同じ形の dict。
    """
    first = snapshot[0] if snapshot else {}
    accounts = list(dict.fromkeys(h["account"] for h in snapshot if h.get("account")))
    # 残株数は小数株のために float で読むので、整数なら整数に戻す (表示の "200.0株" を防ぐ)
    remaining = execution.remaining
    quantity = int(remaining) if remaining.is_integer() else remaining
    return {
        "ticker": first.get("ticker", key),
        "name": first.get("name", ""),
        "market": first.get("market") or detect_market(key),
        "quantity": quantity,
        "account": "・".join(accounts),
        "class": first.get("class", ""),
        "as_of": execution.date.isoformat(),
        "as_of_source": JOURNAL_SOURCE,
    }


def _latest_executions(
    executions: list[Execution], as_of_by_ticker: dict[str, str], default_as_of: str
) -> dict[str, Execution]:
    """銘柄ごとに、保有へ反映すべき執行 1 件を選ぶ。

    残株数はその行だけで残高が確定する絶対値なので、差分を積み上げず
    最新の 1 件だけを見ればよい (書式側が残株数の記載を必須にしている)。

    Args:
        executions: ジャーナルの出現順 (= 追記順) の執行記録。
        as_of_by_ticker: 大文字ティッカー → その銘柄の基準日。
        default_as_of: Investment に無い銘柄に使う既定の基準日。

    Returns:
        大文字ティッカー → 反映する執行。
    """
    latest: dict[str, Execution] = {}
    for execution in executions:
        base = as_of_by_ticker.get(execution.ticker, default_as_of)
        # 基準日より前の執行は Investment の保有に既に含まれている。
        # 同日はジャーナルを優先する: 誤る場合でも「探索対象が 1 つ減る」側に倒れる
        if base and execution.date < _as_date(base):
            continue
        previous = latest.get(execution.ticker)
        # 同日に複数行あれば後に書かれた行が勝つ (ジャーナルは追記専用)
        if previous is None or execution.date >= previous.date:
            latest[execution.ticker] = execution
    return latest


def _as_date(value: str) -> dt.date:
    """基準日の文字列を date にする。解釈できなければ date.min。

    date.min を返すのは、基準日が壊れているときに執行記録を落とさないため。
    比較に使う側では「すべての執行が基準日以降」として扱われる。

    Args:
        value: ISO 形式の日付文字列。

    Returns:
        date。不正な値なら date.min。
    """
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return dt.date.min
