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
        [{ticker, name, market, quantity, account, class, as_of}, ...]
        market は region から導出 ("日本"→"jp"、それ以外→"us")。
    """
    path = latest_report_path()
    data = json.loads(path.read_text())
    stock = data.get("stock", {})
    as_of = stock.get("as_of", "")
    out = []
    for h in stock.get("holdings", []):
        if h.get("class") not in _ANALYZABLE_CLASSES or not h.get("ticker"):
            continue
        out.append({
            "ticker": str(h["ticker"]),
            "name": h.get("name", ""),
            "market": "jp" if h.get("region") == "日本" else "us",
            "quantity": h.get("quantity"),
            "account": h.get("account", ""),
            "class": h.get("class", ""),
            "as_of": as_of,
        })
    return out


def held_tickers() -> set[str]:
    """保有ティッカーの集合を返す (スクリーナーの除外リスト用)。"""
    return {h["ticker"] for h in load_holdings()}
