"""長期保有枠 (優待・高配当など、期限までに買って長く持つ銘柄)。

設定は追跡対象外の `config/watchlist.py` の `LONG_TERM_STOCKS` に書く。
このモジュールは設定のパース・検証、次の権利付き最終日の選択、
実効保有に基づく状態判定だけを持ち、株価の取得はしない
(→ [ADR-0035](../docs/adr/0035-long-term-holding-slot.md))。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from lib.datasource import detect_market


class Status(Enum):
    """長期保有枠の銘柄の状態。値は表示用の文言。"""

    PLANNED = "買付予定"
    HELD = "保有中"
    UNKNOWN = "状態不明"


@dataclass(frozen=True)
class LongTermStock:
    """長期保有枠の 1 銘柄。

    Attributes:
        ticker: 生ティッカー (大文字)。
        market: ``jp`` または ``us`` (ティッカーから推定)。
        shares: 買付に必要な株数 (優待の株数条件など)。
        buy_by: 権利付き最終日 (古い順)。年 2 回の銘柄があるので複数持てる。
        purpose: 長期保有の目的 (優待・高配当など)。
    """

    ticker: str
    market: str
    shares: int
    buy_by: tuple[dt.date, ...]
    purpose: str

    def next_deadline(self, today: dt.date) -> dt.date | None:
        """判定日以降で最も近い権利付き最終日を返す。当日を含む。すべて過去なら None。"""
        return next((day for day in self.buy_by if day >= today), None)


@dataclass(frozen=True)
class Plan:
    """長期保有枠の 1 銘柄の判定結果。

    Attributes:
        stock: 設定。
        status: 実効保有に基づく状態。
        deadline: 次の権利付き最終日。すべて過去なら None。
        days_left: 次の権利付き最終日までの暦日数。期限が無ければ None。
    """

    stock: LongTermStock
    status: Status
    deadline: dt.date | None
    days_left: int | None

    @property
    def expired(self) -> bool:
        """``buy_by`` の期限がすべて過去か (書き足しが要る)。"""
        return self.deadline is None


def parse(entries: Iterable[object]) -> tuple[list[LongTermStock], list[str]]:
    """``LONG_TERM_STOCKS`` を検証して読む。

    書式の誤りは手で書いた設定の誤りなので、その 1 件を飛ばして警告に出し、
    残りの銘柄の表示は止めない。

    Args:
        entries: ``LONG_TERM_STOCKS`` の各要素。

    Returns:
        (読めた銘柄, 読めなかった要素の警告)。同じティッカーの 2 件目以降も警告にする。
    """
    stocks: list[LongTermStock] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        try:
            stock = _parse_one(entry)
        except ValueError as e:
            warnings.append(f"LONG_TERM_STOCKS の {index} 件目: {e}")
            continue
        if stock.ticker in seen:
            warnings.append(f"LONG_TERM_STOCKS の {index} 件目: {stock.ticker} が重複している")
            continue
        seen.add(stock.ticker)
        stocks.append(stock)
    return stocks, warnings


def _parse_one(entry: object) -> LongTermStock:
    """1 要素を検証して LongTermStock にする。

    Raises:
        ValueError: 書式の誤り。メッセージは表示用。
    """
    if not isinstance(entry, dict):
        raise ValueError("dict で書くこと")
    ticker = entry.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("ticker が無い")
    ticker = ticker.strip().upper().removesuffix(".T")
    shares = entry.get("shares")
    # bool は int の部分型なので、True を 1 株と読まないよう先に除く
    if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
        raise ValueError(f"{ticker}: shares は正の整数で書くこと")
    raw_dates = entry.get("buy_by")
    if not isinstance(raw_dates, list) or not raw_dates:
        raise ValueError(f"{ticker}: buy_by は日付のリストで書くこと")
    try:
        buy_by = tuple(sorted({dt.date.fromisoformat(str(day)) for day in raw_dates}))
    except ValueError as e:
        raise ValueError(f"{ticker}: buy_by の日付を読めない ({e})") from e
    purpose = entry.get("purpose", "")
    if not isinstance(purpose, str):
        raise ValueError(f"{ticker}: purpose は文字列で書くこと")
    return LongTermStock(ticker, detect_market(ticker), shares, buy_by, purpose.strip())


def plan(
    stocks: Iterable[LongTermStock], held: set[str] | None, today: dt.date
) -> list[Plan]:
    """各銘柄の状態と次の期限を判定する。

    状態は実効保有に含まれるかで決める。手で「保有中」へ移し替える運用にすると、
    買ったのに買付予定に残る、売ったのに保有中に残る、というずれが起きる。

    Args:
        stocks: 長期保有枠の銘柄。
        held: 実効保有のティッカー集合。保有データを読めなければ None。
        today: 判定日 (テストで注入する)。

    Returns:
        設定の順の判定結果。
    """
    held_upper = (
        None if held is None else {ticker.upper().removesuffix(".T") for ticker in held}
    )
    plans = []
    for stock in stocks:
        if held_upper is None:
            status = Status.UNKNOWN
        else:
            status = Status.HELD if stock.ticker in held_upper else Status.PLANNED
        deadline = stock.next_deadline(today)
        days_left = None if deadline is None else (deadline - today).days
        plans.append(Plan(stock, status, deadline, days_left))
    return plans
