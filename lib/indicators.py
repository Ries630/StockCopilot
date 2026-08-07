"""指標エンジン (TradingCopilot swing/_analyze.py の ind() から移植)。

指標セットは資産クラス非依存なので crypto 版と同一構成:
RSI(14) / MACD(12,26,9) / EMA(20,50,200) / Bollinger(20,2σ) / ATR(14) /
StochRSI / OBV / ADX±DI / 20・60 本高安。

入力の DataFrame は **確定足のみ** であること (lib/datasource.py が保証する)。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import ta


def _f(x) -> float | None:
    """NaN を None に潰しつつ丸める表示用ヘルパー。"""
    try:
        x = float(x)
        return None if np.isnan(x) else round(x, 5)
    except (TypeError, ValueError):
        return None


def compute(df: pd.DataFrame) -> dict:
    """OHLCV からテクニカル指標一式を計算する。

    Args:
        df: columns = open/high/low/close/volume の確定足 DataFrame (古い順)。

    Returns:
        指標名 → 値の dict。データ不足 (30 本未満) なら {"error": "insufficient"}。
    """
    if df.empty or len(df) < 30:
        return {"error": "insufficient", "bars": len(df)}
    c, h, lo, v = df["close"], df["high"], df["low"], df["volume"]
    macd = ta.trend.MACD(c, 26, 12, 9)
    bb = ta.volatility.BollingerBands(c, 20, 2)
    srsi = ta.momentum.StochRSIIndicator(c, 14, 3, 3)
    adx = ta.trend.ADXIndicator(h, lo, c, 14)
    obv = ta.volume.OnBalanceVolumeIndicator(c, v).on_balance_volume()
    return {
        "close": _f(c.iloc[-1]),
        "rsi": _f(ta.momentum.RSIIndicator(c, 14).rsi().iloc[-1]),
        "macd_hist": _f(macd.macd_diff().iloc[-1]),
        "macd_hist_prev": _f(macd.macd_diff().iloc[-2]),
        "ema20": _f(ta.trend.EMAIndicator(c, 20).ema_indicator().iloc[-1]),
        "ema50": _f(ta.trend.EMAIndicator(c, 50).ema_indicator().iloc[-1]),
        # EMA200 は 200 本未満だと不完全な値になるので出さない (長期トレンドを誤らせる)
        "ema200": (
            _f(ta.trend.EMAIndicator(c, 200).ema_indicator().iloc[-1]) if len(c) >= 200 else None
        ),
        "bb_high": _f(bb.bollinger_hband().iloc[-1]),
        "bb_low": _f(bb.bollinger_lband().iloc[-1]),
        "bb_pctb": _f(bb.bollinger_pband().iloc[-1]),
        "atr": _f(ta.volatility.AverageTrueRange(h, lo, c, 14).average_true_range().iloc[-1]),
        "stochrsi_k": _f(srsi.stochrsi_k().iloc[-1] * 100),
        "obv": _f(obv.iloc[-1]),
        "obv_prev10": _f(obv.iloc[max(0, len(obv) - 11)]),
        "adx": _f(adx.adx().iloc[-1]),
        "dip": _f(adx.adx_pos().iloc[-1]),
        "dim": _f(adx.adx_neg().iloc[-1]),
        "high20": _f(h.tail(20).max()),
        "low20": _f(lo.tail(20).min()),
        "high60": _f(h.tail(60).max()),
        "low60": _f(lo.tail(60).min()),
        "bars": len(df),
    }


def daily_stats(df: pd.DataFrame) -> dict | None:
    """スクリーナー用の軽量統計: ATR(14)、直前 20 本レンジ、平均売買代金。

    ブレイクアウト判定を確定足だけで行えるよう、レンジ (high20/low20) は
    **末尾の足を除いた** 直前 20 本から計算する (swing/screen.py と同じ発想を
    確定終値ベースに置き換えたもの)。

    Args:
        df: 確定足の OHLCV DataFrame (古い順、22 本以上)。

    Returns:
        統計 dict。データ不足なら None。
    """
    if df is None or len(df) < 22:
        return None
    h, lo, c, v = df["high"], df["low"], df["close"], df["volume"]

    # True Range: 当日の値幅と、前日終値からのギャップの大きいほう
    prev_c = c.shift(1)
    tr = pd.concat([h - lo, (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    atr = float(tr.tail(14).mean())
    last_close = float(c.iloc[-1])

    # 末尾 (=直近確定足) を除いた直前 20 本のレンジ。ブレイク判定の基準
    hi20 = float(h.iloc[-21:-1].max())
    lo20 = float(lo.iloc[-21:-1].min())
    span = hi20 - lo20
    return {
        "atr": atr,
        "atr_pct": atr / last_close * 100 if last_close else 0.0,
        "high20": hi20,
        "low20": lo20,
        "range_pos": (last_close - lo20) / span if span else 0.5,
        "turnover_avg20": float((c * v).tail(20).mean()),
        "closed_bars": len(df),
    }
