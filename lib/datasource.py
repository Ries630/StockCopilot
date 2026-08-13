# /// script
# requires-python = ">=3.10"
# dependencies = ["yfinance>=0.2.40", "pandas"]
# ///
"""株価データ源アダプタ (yfinance 実装)。

データ源の差し替え (例: J-Quants Light プラン契約時) はこのファイルに閉じる。
上位 (screen.py / analyze.py) は `fetch_ohlcv()` の I/F だけに依存すること。

**形成中の足は必ず除外する。** ブレイクアウト判定は確定足前提のため、
未確定の足を混ぜると「抜けた」が引け後に戻る誤検知になる
(TradingCopilot swing/_analyze.py の drop_forming_bar と同じ思想)。
株式は 24/7 の crypto と違い取引所カレンダーがあるので、
市場ごとの引け時刻 (JP=15:30 JST / US=16:00 ET) で判定する。
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
from zoneinfo import ZoneInfo

import pandas as pd

# 市場ごとの取引所タイムゾーンと「この時刻を過ぎたら当日足を確定とみなす」時刻。
# 引け直後はデータ反映が遅れることがあるため 30 分のバッファを載せている。
# (JP の現物引けは 2024-11-05 から 15:30)
MARKET_CONFIG = {
    "jp": {"tz": ZoneInfo("Asia/Tokyo"), "confirmed_after": dt.time(16, 0)},
    "us": {"tz": ZoneInfo("America/New_York"), "confirmed_after": dt.time(16, 30)},
}

# limit (必要本数) から yfinance の period を決めるための余裕係数。
# 営業日は暦日の約 7 割なので 1.6 倍あれば足りる
_CALENDAR_MARGIN = 1.6


def detect_market(ticker: str) -> str:
    """ティッカーから市場を推定する。4 桁数字 (+英字 1 文字の優先株等) なら JP、それ以外は US。

    Args:
        ticker: "7203" / "AAPL" のような生ティッカー。

    Returns:
        "jp" または "us"。
    """
    t = ticker.strip().upper().removesuffix(".T")
    return "jp" if len(t) == 4 and t[0].isdigit() else "us"


def normalize_ticker(ticker: str, market: str) -> str:
    """yfinance 用のシンボルに正規化する (JP は `.T` サフィックス)。

    Args:
        ticker: 生ティッカー ("7203", "7203.T", "AAPL")。
        market: "jp" または "us"。

    Returns:
        yfinance に渡すシンボル ("7203.T", "AAPL")。
    """
    t = ticker.strip().upper()
    if market == "jp" and not t.endswith(".T"):
        t += ".T"
    return t


def drop_forming_bar(
    df: pd.DataFrame,
    interval: str,
    market: str,
    now: dt.datetime | None = None,
) -> pd.DataFrame:
    """形成中 (未確定) の足を落とし、確定足だけを返す。

    - 日足: 末尾の足が「取引所ローカルの今日」かつ引け+バッファ前なら形成中。
    - 週足: その週の金曜引け+バッファを過ぎるまで形成中とみなす
      (祝日で金曜休場の週は 1 本余分に落ちるが、安全側なので許容する)。

    Args:
        df: fetch_ohlcv 内部で取得した OHLCV。index は取引所 TZ の DatetimeIndex。
        interval: "1d" または "1wk"。
        market: "jp" または "us"。
        now: 判定の基準時刻 (取引所 TZ)。省略時は現在時刻。
            テストから決定的に検証するための注入点で、通常は渡さない。

    Returns:
        確定足のみの DataFrame。
    """
    if df.empty:
        return df
    cfg = MARKET_CONFIG[market]
    now = now or dt.datetime.now(cfg["tz"])
    last = df.index[-1]
    if last.tzinfo is None:
        last = last.tz_localize(cfg["tz"])

    if interval == "1d":
        is_forming = last.date() == now.date() and now.time() < cfg["confirmed_after"]
    elif interval == "1wk":
        # yfinance の週足は月曜始まり。金曜 (開始+4日) の引け+バッファが確定時刻
        week_end = (last + pd.Timedelta(days=4)).replace(
            hour=cfg["confirmed_after"].hour, minute=cfg["confirmed_after"].minute
        )
        is_forming = now < week_end
    else:
        raise ValueError(f"未対応の interval: {interval}")

    return df.iloc[:-1] if is_forming else df


def fetch_ohlcv(
    ticker: str,
    market: str | None = None,
    interval: str = "1d",
    limit: int = 200,
) -> pd.DataFrame:
    """確定足の OHLCV を取得する。

    Args:
        ticker: 生ティッカー ("7203", "AAPL")。
        market: "jp" / "us"。省略時は detect_market() で推定。
        interval: "1d" または "1wk"。
        limit: 返す最大本数 (確定足ベース)。

    Returns:
        columns = open/high/low/close/volume (小文字)、index = 日付の DataFrame。
        分割・配当調整済み (yfinance auto_adjust=True)。取得失敗・上場前は空 DataFrame。
    """
    import yfinance as yf  # import が遅いので使用時に読み込む

    market = market or detect_market(ticker)
    symbol = normalize_ticker(ticker, market)

    days = int(limit * (7 if interval == "1wk" else 1) * _CALENDAR_MARGIN) + 10
    raw = yf.Ticker(symbol).history(period=f"{days}d", interval=interval, auto_adjust=True)
    if raw.empty:
        return raw

    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df = df.dropna(subset=["close"])
    df = drop_forming_bar(df, interval, market)
    return df.tail(limit)


@contextlib.contextmanager
def _quiet_yfinance():
    """yfinance のログを一時的に黙らせる。

    ETF は fundamentals を持たないため calendar の取得が**必ず** 404 になり、
    yfinance が自前のロガーで stderr に出す (例外は送出されない)。
    こちらは「決算が取れない = None」として正常に扱うので、この行は
    正常な経路で出るエラー表示になり、本物の取得失敗と区別が付かなくなる。

    抑制は決算取得の呼び出し中だけに限る。価格取得のログまで消すと、
    本当に困る失敗が見えなくなる。
    """
    logger = logging.getLogger("yfinance")
    previous = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(previous)


# yfinance が返す instrumentType を、上位で使う語彙に正規化するための対応表。
# データ源固有の語彙をこのファイルより上に漏らさない (差し替え時に触る範囲を閉じるため)。
# 投資信託など、ここに無い種別は「判定できない」= None に倒す
_INSTRUMENT_TYPES = {"EQUITY": "equity", "ETF": "etf"}


def normalize_instrument_type(raw: str | None) -> str | None:
    """yfinance の instrumentType を "equity" / "etf" に正規化する。

    Args:
        raw: yfinance が返す種別 ("EQUITY" / "ETF" など)。None も受け付ける。

    Returns:
        "equity" または "etf"。対応表に無ければ None。
    """
    return _INSTRUMENT_TYPES.get((raw or "").strip().upper())


def fetch_instrument_type(ticker: str, market: str | None = None) -> str | None:
    """銘柄種別 (個別株か ETF か) を返す。

    決算日が取れなかったときに、**ETF なので存在しない**のか
    **個別株なのに取得できなかった**のかを切り分けるために使う。後者は決算を
    またぐギャップの可能性が残るため、出力に出す必要がある
    (→ ADR-0016)。1 銘柄 1 リクエストなので、呼ぶ側は決算日が
    取れなかった銘柄だけに絞ること。

    Args:
        ticker: 生ティッカー ("7203", "AAPL")。
        market: "jp" / "us"。省略時は detect_market() で推定。

    Returns:
        "equity" / "etf"。取得できない、または未対応の種別なら None。
    """
    import yfinance as yf  # import が遅いので使用時に読み込む

    market = market or detect_market(ticker)
    symbol = normalize_ticker(ticker, market)
    try:
        # history_metadata は価格チャートのメタデータで、ETF でも 404 にならない。
        # ログを抑制しないのは、ここでの失敗が本物の取得失敗だけだから
        meta = yf.Ticker(symbol).history_metadata or {}
    except Exception:
        return None
    return normalize_instrument_type(meta.get("instrumentType"))


def fetch_next_earnings(ticker: str, market: str | None = None) -> dt.date | None:
    """次回 (未来) または直近 (過去) の決算発表日を返す。

    決算はギャップ要因であり、「日足終値で X を割ったら」という**確定足ベースの
    トリガーを飛び越えて執行不能にする**。日程の報告のためではなく、
    トリガーが有効かどうかを判断するために取得する。

    Args:
        ticker: 生ティッカー ("7203", "AAPL")。
        market: "jp" / "us"。省略時は detect_market() で推定。

    Returns:
        決算日。ETF など決算の概念が無い銘柄、取得失敗時は None。
        この 2 つは戻り値では区別できないので、切り分けが要るときは
        fetch_instrument_type() を併用する (→ ADR-0016)。
    """
    import yfinance as yf  # import が遅いので使用時に読み込む

    market = market or detect_market(ticker)
    symbol = normalize_ticker(ticker, market)
    try:
        with _quiet_yfinance():
            dates = (yf.Ticker(symbol).calendar or {}).get("Earnings Date") or []
    except Exception:
        # ETF は fundamentals が無く 404 になる。決算の有無は分析を止める理由ではない
        return None
    if not dates:
        return None
    first = dates[0]
    if isinstance(first, dt.datetime):
        return first.date()
    return first if isinstance(first, dt.date) else None


if __name__ == "__main__":
    # 疎通確認用 CLI: uv run lib/datasource.py --ticker 7203
    import argparse

    ap = argparse.ArgumentParser(description="データ源アダプタの疎通確認")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--interval", default="1d", choices=["1d", "1wk"])
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    out = fetch_ohlcv(args.ticker, interval=args.interval, limit=args.limit)
    print(f"market={detect_market(args.ticker)} bars={len(out)} "
          f"earnings={fetch_next_earnings(args.ticker)}")
    print(out)
