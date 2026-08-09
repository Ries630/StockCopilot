"""決算日の注記。

決算日そのものを報告するためではなく、**確定足ベースのトリガーが
ギャップで飛ばされうる期間かどうか**を示すために出す。

analyze.py (保有分析) と screen.py (候補スクリーニング) の双方が使う。
文言と警告期間をここ 1 箇所に置くのは、片方だけ変わると
「同じ銘柄なのに分析とスクリーニングで決算の扱いが違う」状態になるため。
"""

from __future__ import annotations

import datetime as dt

from lib.datasource import fetch_next_earnings

# 決算日がこの日数以内 (前後) なら警告を付ける。
# 前方 = 確定足ベースのトリガーがギャップで飛ばされうる期間、
# 後方 = 直近の値動きが決算反応である可能性を疑うべき期間
EARNINGS_ALERT_DAYS = 7


def earnings_note(ticker: str, market: str) -> str:
    """決算日の注記を返す。取得できなければ空文字。

    Args:
        ticker: 生ティッカー。
        market: "jp" または "us"。

    Returns:
        表示用の 1 行 (例: "⚠ 決算 2026-08-07 (本日) — ギャップ注意")。
    """
    day = fetch_next_earnings(ticker, market)
    if day is None:
        return ""
    delta = (day - dt.date.today()).days
    if delta > 0:
        note = f"決算 {day} (あと {delta} 日)"
        warn = delta <= EARNINGS_ALERT_DAYS
    elif delta == 0:
        note, warn = f"決算 {day} (本日)", True
    else:
        note = f"直近決算 {day} ({-delta} 日前)"
        warn = -delta <= EARNINGS_ALERT_DAYS
    if not warn:
        return note
    tail = "直近の値動きは決算反応の可能性" if delta < 0 else "ギャップでトリガーが飛びうる"
    return f"⚠ {note} — {tail}"
