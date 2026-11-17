"""決算日の注記。

決算日そのものを報告するためではなく、**確定足ベースのトリガーが
ギャップで飛ばされうる期間かどうか**を示すために出す。

analyze.py (保有分析) と screen.py (候補スクリーニング) の双方が使う。
文言と警告期間をここ 1 箇所に置くのは、片方だけ変わると
「同じ銘柄なのに分析とスクリーニングで決算の扱いが違う」状態になるため。

yfinance の決算日は、決算が過ぎると数日のうちに次回の予定日へ切り替わる。
そのまま使うと、決算直後の値動きに付くはずの警告が消える。そこで取得した決算日を
`data/earnings_seen.json` に覚えておき、警告期間内の過去の決算日を見落とさない
(→ [ADR-0032](../docs/adr/0032-remember-seen-earnings-dates.md))。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

from lib.datasource import fetch_instrument_type, fetch_next_earnings

# 決算日がこの日数以内 (前後) なら警告を付ける。
# 前方 = 確定足ベースのトリガーがギャップで飛ばされうる期間、
# 後方 = 直近の値動きが決算反応である可能性を疑うべき期間
EARNINGS_ALERT_DAYS = 7

# 個別株で決算日を取得できなかったときに出す 1 行。
# 決算が無いのではなく分からないだけなので、ギャップの可能性は残っている
# (→ ADR-0016)
UNAVAILABLE_NOTE = "⚠ 決算日 不明 (取得できず) — ギャップの可能性を残したままトリガーを書く"

# 取得した決算日の記録。保有銘柄の決算日は保有を推測させるので、追跡対象外の data/ に置く
# (→ ADR-0008)。cwd に依存させない (screen.py はリポジトリ外から呼ばれうる)
EARNINGS_SEEN_PATH = Path(__file__).resolve().parent.parent / "data" / "earnings_seen.json"


def earnings_note(ticker: str, market: str, today: dt.date | None = None) -> str:
    """決算日の注記を返す。

    戻り値は 3 通りある。日付が取れれば日付の行、ETF なら空文字 (決算の概念が
    無いので出ないのが正常)、個別株で取れなければ「不明」の行。
    **空文字と「不明」を分けるのがこの関数の要点**で、両方を空文字にすると
    読み手が「決算が無い」と「取得できなかった」を区別できない。

    取得した決算日は記録し、警告期間内に過ぎた決算日があれば、取得値が次回の予定日へ
    切り替わっていてもそちらで注記する (決算直後の警告を落とさない)。

    Args:
        ticker: 生ティッカー。
        market: "jp" または "us"。
        today: 判定日。省略時は実行日 (テストで判定日を注入するため)。

    Returns:
        表示用の 1 行 (例: "⚠ 決算 2026-08-07 (本日) — ギャップ注意")。
        行を出さない場合は空文字。
    """
    today = today or dt.date.today()
    fetched = fetch_next_earnings(ticker, market)
    seen = _remember(f"{market}:{ticker.upper()}", fetched, today)
    recent = _recent_past(seen, today)
    if recent and (fetched is None or abs((fetched - today).days) > EARNINGS_ALERT_DAYS):
        note = _format_note(recent, today)
        if fetched is not None and fetched > today:
            note += f" (次回 {fetched})"
        return note
    if fetched is None:
        return _unavailable_note(ticker, market)
    return _format_note(fetched, today)


def _format_note(day: dt.date, today: dt.date) -> str:
    """決算日 1 つを注記の 1 行にする。

    Args:
        day: 決算日。
        today: 判定日。

    Returns:
        警告期間内なら "⚠" と但し書きの付いた行、それ以外は日付だけの行。
    """
    delta = (day - today).days
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


def _recent_past(seen: list[dt.date], today: dt.date) -> dt.date | None:
    """記録した決算日のうち、警告期間内に過ぎた最新の日を返す。

    Args:
        seen: 記録した決算日。
        today: 判定日。

    Returns:
        判定日より前で EARNINGS_ALERT_DAYS 以内の最新の決算日。無ければ None。
    """
    past = [d for d in seen if 0 < (today - d).days <= EARNINGS_ALERT_DAYS]
    return max(past, default=None)


def _remember(key: str, fetched: dt.date | None, today: dt.date) -> list[dt.date]:
    """取得した決算日を記録に足し、その銘柄の記録を返す。

    警告期間より前に過ぎた日は使い道が無いので消す。まだ来ていない日で今回の取得値と
    違うものも消す。推定日から確定日への変更や延期で置き換わった予定で、残すと
    実際には無かった決算日で「直近決算」の警告を出すことになる。
    記録が読めない・書けない場合も注記は止めない (取得値だけで判定する従来の動作に戻るだけ)。

    Args:
        key: "市場:大文字ティッカー"。
        fetched: 今回取得できた決算日。取れなければ None。
        today: 判定日。

    Returns:
        その銘柄の記録 (今回の取得値を含む。古い順)。
    """
    try:
        records = json.loads(EARNINGS_SEEN_PATH.read_text(encoding="utf-8"))
        if not isinstance(records, dict):
            records = {}
    except FileNotFoundError:
        records = {}
    except (OSError, ValueError) as e:
        print(f"[warn] 決算日の記録を読めない ({e})。今回の取得値だけで判定する", file=sys.stderr)
        records = {}

    dates: set[dt.date] = set()
    values = records.get(key)
    for value in values if isinstance(values, list) else []:
        try:
            dates.add(dt.date.fromisoformat(value))
        except (TypeError, ValueError):
            continue
    if fetched is not None:
        dates.add(fetched)
    kept = sorted(
        d
        for d in dates
        if (today - d).days <= EARNINGS_ALERT_DAYS
        and (fetched is None or d <= today or d == fetched)
    )

    records[key] = [d.isoformat() for d in kept]
    if not kept:
        del records[key]
    try:
        EARNINGS_SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        # 書きかけのファイルを次の読み込みが「壊れた記録」と見なさないよう、置き換えで書く
        tmp = EARNINGS_SEEN_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(records, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, EARNINGS_SEEN_PATH)
    except OSError as e:
        print(f"[warn] 決算日の記録を書けない ({e})", file=sys.stderr)
    return kept


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
