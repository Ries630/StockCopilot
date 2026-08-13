"""決算日の注記。

決算日そのものを報告するためではなく、**確定足ベースのトリガーが
ギャップで飛ばされうる期間かどうか**を示すために出す。

analyze.py (保有分析) と screen.py (候補スクリーニング) の双方が使う。
文言と警告期間をここ 1 箇所に置くのは、片方だけ変わると
「同じ銘柄なのに分析とスクリーニングで決算の扱いが違う」状態になるため。
"""

from __future__ import annotations

import datetime as dt

from lib.datasource import fetch_instrument_type, fetch_next_earnings

# 決算日がこの日数以内 (前後) なら警告を付ける。
# 前方 = 確定足ベースのトリガーがギャップで飛ばされうる期間、
# 後方 = 直近の値動きが決算反応である可能性を疑うべき期間
EARNINGS_ALERT_DAYS = 7

# 個別株で決算日を取得できなかったときに出す 1 行。
# 決算が無いのではなく分からないだけなので、ギャップの可能性は残っている
# (→ ADR-0016)
UNAVAILABLE_NOTE = "⚠ 決算日 不明 (取得できず) — ギャップの可能性を残したままトリガーを書く"


def earnings_note(ticker: str, market: str) -> str:
    """決算日の注記を返す。

    戻り値は 3 通りある。日付が取れれば日付の行、ETF なら空文字 (決算の概念が
    無いので出ないのが正常)、個別株で取れなければ「不明」の行。
    **空文字と「不明」を分けるのがこの関数の要点**で、両方を空文字にすると
    読み手が「決算が無い」と「取得できなかった」を区別できない。

    Args:
        ticker: 生ティッカー。
        market: "jp" または "us"。

    Returns:
        表示用の 1 行 (例: "⚠ 決算 2026-08-07 (本日) — ギャップ注意")。
        行を出さない場合は空文字。
    """
    day = fetch_next_earnings(ticker, market)
    if day is None:
        return _unavailable_note(ticker, market)
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


def _unavailable_note(ticker: str, market: str) -> str:
    """決算日を取得できなかったときの注記を返す。

    銘柄種別の取得は 1 銘柄 1 リクエストなので、**決算日が取れなかったときだけ**
    呼ぶ (日付が取れた銘柄でリクエストを増やさない)。

    判定できなかった場合も「不明」を出す。ETF と確定できていない以上、
    警告を出す側に倒す — 出しすぎは読み飛ばせるが、出さない側に外すと
    決算反応をテクニカルの進捗として読む事故に戻る。

    Args:
        ticker: 生ティッカー。
        market: "jp" または "us"。

    Returns:
        ETF なら空文字、それ以外は UNAVAILABLE_NOTE。
    """
    return "" if fetch_instrument_type(ticker, market) == "etf" else UNAVAILABLE_NOTE
