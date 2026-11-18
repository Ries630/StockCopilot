"""長期保有枠 (優待・高配当など、期限までに買って長く持つ銘柄)。

設定は追跡対象外の `config/watchlist.py` の `LONG_TERM_STOCKS` に書く。
このモジュールは設定のパース・検証、次の権利付き最終日の選択、
実効保有に基づく状態判定、保有中の銘柄に添える注記 (株数条件・権利落ち日) だけを持ち、
株価の取得はしない (→ [ADR-0035](../docs/adr/0035-long-term-holding-slot.md) /
[ADR-0036](../docs/adr/0036-long-term-holding-verdicts.md))。
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


@dataclass(frozen=True)
class HeldNote:
    """保有中の長期保有枠の 1 銘柄に添える注記の元データ。

    Attributes:
        stock: 設定。
        quantity: 実効保有の株数 (口座をまたいで合算)。
        deadline: 次の権利付き最終日。すべて過去なら None。
        days_left: 次の権利付き最終日までの暦日数。期限が無ければ None。
        ex_rights_of: 日足の最新確定足が権利落ち日なら、その元の権利付き最終日。
            それ以外 (足が無い場合を含む) は None。
    """

    stock: LongTermStock
    quantity: float
    deadline: dt.date | None
    days_left: int | None
    ex_rights_of: dt.date | None

    @property
    def below_floor(self) -> bool:
        """株数が優待などの株数条件 (``shares``) を割っているか。"""
        return self.quantity < self.stock.shares

    def lines(self) -> list[str]:
        """表示用の注記行。1 行目が枠の現況で、警告と権利落ちの注記が続く。"""
        purpose = f"[{self.stock.purpose}] " if self.stock.purpose else ""
        if self.deadline is None:
            deadline = "次の権利付き最終日なし (buy_by の書き足しが要る)"
        elif self.days_left == 0:
            deadline = f"次の権利付き最終日 {self.deadline} (本日)"
        else:
            deadline = f"次の権利付き最終日 {self.deadline} (あと {self.days_left} 日)"
        shares = f"必要 {self.stock.shares}株 / 現在 {_shares(self.quantity)}株"
        lines = [f"長期保有枠 {purpose}{shares} / {deadline}"]
        if self.below_floor:
            lines.append(
                f"⚠ 株数が必要株数を {_shares(self.stock.shares - self.quantity)}株 割っている"
                " — 優待などの株数条件と継続保有の起算日を確認すること"
            )
        if self.ex_rights_of is not None:
            lines.append(
                f"日足の最新確定足は権利落ち日 (権利付き最終日 {self.ex_rights_of} の翌営業日)"
                " — この日の下落は制度上の正常な値動きで、トリガー割れ・シナリオ否定と読まない"
            )
        return lines

    def to_json(self) -> dict:
        """``docs/output-contract.md`` の ``symbols[].long_term`` の形にする。"""
        return {
            "purpose": self.stock.purpose or None,
            "shares": self.stock.shares,
            "quantity": self.quantity,
            "below_floor": self.below_floor,
            "deadline": None if self.deadline is None else self.deadline.isoformat(),
            "days_left": self.days_left,
            "ex_rights_of": None if self.ex_rights_of is None else self.ex_rights_of.isoformat(),
            "lines": self.lines(),
        }


def _shares(value: float) -> str:
    """株数を表示用にする。整数なら小数点を付けない (米国株の端株は小数で出す)。"""
    return str(int(value)) if float(value).is_integer() else str(value)


def ex_rights_of(
    stock: LongTermStock, prev_bar: dt.date | None, last_bar: dt.date | None
) -> dt.date | None:
    """日足の最新確定足が権利落ち日なら、その元の権利付き最終日を返す。

    権利落ち日は権利付き最終日の翌営業日である。営業日の暦を持たないので、
    足の日付で判定する: 直前の足 ≤ 権利付き最終日 < 最新の足 なら、最新の足が権利落ち日。

    Args:
        stock: 設定。
        prev_bar: 日足の最新から 1 本前の確定足の日付。
        last_bar: 日足の最新確定足の日付。

    Returns:
        該当する権利付き最終日。該当しない、または足の日付が無ければ None。
    """
    if prev_bar is None or last_bar is None:
        return None
    return next((day for day in stock.buy_by if prev_bar <= day < last_bar), None)


def held_note(
    stock: LongTermStock,
    quantity: float,
    today: dt.date,
    prev_bar: dt.date | None = None,
    last_bar: dt.date | None = None,
) -> HeldNote:
    """保有中の長期保有枠の注記を作る (→ ADR-0036)。

    Args:
        stock: 設定。
        quantity: 実効保有の株数 (同じティッカーの行を合算した値)。
        today: 判定日 (テストで注入する)。
        prev_bar: 日足の最新から 1 本前の確定足の日付。指標を計算しなければ None。
        last_bar: 日足の最新確定足の日付。指標を計算しなければ None。

    Returns:
        HeldNote。
    """
    deadline = stock.next_deadline(today)
    days_left = None if deadline is None else (deadline - today).days
    return HeldNote(stock, quantity, deadline, days_left, ex_rights_of(stock, prev_bar, last_bar))
