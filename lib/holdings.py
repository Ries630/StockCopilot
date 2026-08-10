"""Investment プロジェクトの生成物から株式保有を読む (read-only)。

Investment 側とは module import せず、生成 JSON を読むだけの疎結合とする。
数量は Investment の report_data 生成時点で分割調整済みなので、
ここで stock_splits.json を再適用してはならない (二重調整になる)。

投資信託 (class=投資信託) は公開ティッカーが無くテクニカル分析の対象外なので除外する。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

INVESTMENT_OUTPUT = Path("~/Documents/Claude/Projects/Investment/output").expanduser()

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
    """最新の report_data_YYYYMMDD.json のパスを返す。

    Returns:
        最新ファイルの Path。

    Raises:
        FileNotFoundError: report_data が 1 件も無い場合。
    """
    files = sorted(
        p for p in INVESTMENT_OUTPUT.glob("report_data_*.json")
        if re.fullmatch(r"report_data_\d{8}\.json", p.name)
    )
    if not files:
        raise FileNotFoundError(f"report_data_*.json が見つからない: {INVESTMENT_OUTPUT}")
    return files[-1]


def load_holdings() -> list[dict]:
    """株式保有の一覧を返す (テクニカル分析可能なもののみ)。

    Returns:
        [{ticker, name, market, quantity, account, class, as_of, as_of_source}, ...]
        market は region から導出 ("日本"→"jp"、それ以外→"us")。
        as_of は口座に対応する資産クラスの基準日で、銘柄ごとに異なりうる。
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
    return out


def held_tickers() -> set[str]:
    """保有ティッカーの集合を返す (スクリーナーの除外リスト用)。"""
    return {h["ticker"] for h in load_holdings()}
