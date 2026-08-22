"""市場別の確定足比較と前回結果の合流。

構造の正は ``docs/report-contract.schema.json``、状態と組み合わせの意味の正は
``docs/report-contract.md`` に置く。このモジュールは、その契約を決定的に適用する。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

MARKETS = ("jp", "us")
"""夕方ブリーフが扱う市場。"""

ACTIVE_STATUSES = frozenset({"updated", "initial"})
"""新しい市場観測として分析する状態。"""

_ENTRY_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\b")
_DATE_TOKEN = r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2})"
_BOTH_RE = re.compile(rf"JP\s*[・/]\s*US\s*とも\s*{_DATE_TOKEN}", re.IGNORECASE)
_MARKET_RE = {
    market: re.compile(rf"\b{market}\b\s*(?:は\s*)?{_DATE_TOKEN}", re.IGNORECASE)
    for market in MARKETS
}


def market_from_currency(currency: str) -> str:
    """保有の通貨から市場を決定する。

    Args:
        currency: 中間表現の通貨。``JPY`` または ``USD``。

    Returns:
        ``jp`` または ``us``。

    Raises:
        ValueError: 契約外の通貨の場合。
    """
    try:
        return {"JPY": "jp", "USD": "us"}[currency]
    except KeyError:
        raise ValueError(f"保有の市場を通貨 {currency!r} から決定できない") from None


def compare_bars(current: dict[str, str], previous: dict[str, str]) -> dict[str, dict]:
    """現在と前回の確定足日を市場別に比較する。

    Args:
        current: 今回取得できた市場別の確定足日。
        previous: 前回取得できた市場別の確定足日。

    Returns:
        市場ごとの ``status`` と、存在する場合は ``previous``。

    Raises:
        ValueError: 日付が不正か、前回より後退している場合。
    """
    result: dict[str, dict] = {}
    for market in MARKETS:
        current_value = current.get(market)
        previous_value = previous.get(market)
        if current_value is None:
            item = {"status": "unavailable"}
            if previous_value is not None:
                _as_date(previous_value, f"previous.{market}")
                item["previous"] = previous_value
            result[market] = item
            continue

        current_day = _as_date(current_value, f"current.{market}")
        if previous_value is None:
            result[market] = {"status": "initial"}
            continue
        previous_day = _as_date(previous_value, f"previous.{market}")
        if current_day < previous_day:
            raise ValueError(
                f"{market.upper()} の確定足日が後退した: {previous_value} -> {current_value}"
            )
        status = "updated" if current_day > previous_day else "unchanged"
        result[market] = {"status": status, "previous": previous_value}
    return result


def active_markets(bar_status: dict[str, dict]) -> set[str]:
    """分析対象となる市場集合を返す。

    Args:
        bar_status: :func:`compare_bars` の戻り値。

    Returns:
        ``updated`` または ``initial`` の市場集合。
    """
    return {
        market
        for market in MARKETS
        if (bar_status.get(market) or {}).get("status") in ACTIVE_STATUSES
    }


def candidate_zero_markets(screen: dict[str, dict], bar_status: dict[str, dict]) -> list[str]:
    """候補ゼロを有効な観測として数えられる市場を返す。

    Args:
        screen: 市場別のスクリーニング件数。
        bar_status: 市場別の確定足更新状態。

    Returns:
        更新市場かつ1件以上を評価し、通過が0件だった市場。
    """
    active = active_markets(bar_status)
    return [
        market
        for market in MARKETS
        if market in active
        and screen[market]["evaluated"] > 0
        and screen[market]["matched"] == 0
    ]


def merge_market_results(previous: dict | None, current: dict, bar_status: dict[str, dict]) -> dict:
    """停滞・取得不能市場の判断を前回結果から引き継ぐ。

    ``market_tone`` など市場に分解できないトップレベル項目は今回値を維持する。
    市場別に合流するのは ``holdings`` と ``candidates`` だけである。

    Args:
        previous: 前回の中間表現。初回は ``None``。
        current: 今回作成した中間表現。
        bar_status: 市場別の更新状態。

    Returns:
        市場別合流後の新しいdict。入力は変更しない。
    """
    merged = dict(current)
    old = previous or {}
    merged["holdings"] = _merge_collection(
        old.get("holdings") or [], current.get("holdings") or [], bar_status, _holding_market
    )
    merged["candidates"] = _merge_collection(
        old.get("candidates") or [],
        current.get("candidates") or [],
        bar_status,
        lambda item: item["market"],
    )
    return merged


def load_previous_bars(latest_path: Path, journal_path: Path) -> dict[str, str]:
    """前回の確定足日をlatest優先、旧ジャーナルfallbackで読む。

    Args:
        latest_path: ``reports/latest.json`` のパス。
        journal_path: 移行元の旧ジャーナルのパス。

    Returns:
        取得できた市場別の確定足日。どちらも無ければ空dict。
    """
    try:
        data = json.loads(latest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        try:
            text = journal_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        return parse_legacy_bar_dates(text)
    bars = data.get("bars")
    if not isinstance(bars, dict):
        raise ValueError(f"{latest_path}: bars を読めない")
    status = data.get("bar_status")
    result: dict[str, str] = {}
    for market in MARKETS:
        value = bars.get(market)
        if isinstance(value, str):
            result[market] = value
            continue
        if isinstance(status, dict):
            previous = (status.get(market) or {}).get("previous")
            if isinstance(previous, str):
                result[market] = previous
    return result


def parse_legacy_bar_dates(text: str) -> dict[str, str]:
    """旧ジャーナルの確定足記述から市場別の最新日を読む。

    Args:
        text: 旧形式を含むジャーナル本文。

    Returns:
        読み取れた最後のJP/US確定足日。
    """
    result: dict[str, str] = {}
    entry_day: dt.date | None = None
    for line in text.splitlines():
        if match := _ENTRY_RE.match(line):
            entry_day = _as_date(match.group(1), "journal heading")
        if "確定足" not in line:
            continue
        if both := _BOTH_RE.search(line):
            value = _normalize_legacy_date(both.group(1), entry_day)
            result.update({"jp": value, "us": value})
        for market, pattern in _MARKET_RE.items():
            if found := pattern.search(line):
                result[market] = _normalize_legacy_date(found.group(1), entry_day)
    return result


def _merge_collection(
    previous: list[dict], current: list[dict], bar_status: dict, market_of
) -> list[dict]:
    """市場状態に応じて配列要素の取得元を切り替える。"""
    active = active_markets(bar_status)
    result: list[dict] = []
    for market in MARKETS:
        source = current if market in active else previous
        result.extend(dict(item) for item in source if market_of(item) == market)
    return result


def _holding_market(item: dict) -> str:
    """保有項目の市場を一か所で決定する。"""
    return market_from_currency(item["currency"])


def _as_date(value: str, where: str) -> dt.date:
    """ISO日付を検証してdateへ変換する。"""
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: ISO日付を読めない: {value!r}") from None


def _normalize_legacy_date(value: str, entry_day: dt.date | None) -> str:
    """旧形式の日付をISO形式へ正規化する。"""
    if "-" in value:
        return _as_date(value, "journal bars").isoformat()
    if entry_day is None:
        raise ValueError("旧ジャーナルの月日を解釈する見出し日が無い")
    month, day = (int(part) for part in value.split("/"))
    year = entry_day.year - 1 if month > entry_day.month else entry_day.year
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        raise ValueError(f"旧ジャーナルの確定足日を読めない: {value!r}") from None
