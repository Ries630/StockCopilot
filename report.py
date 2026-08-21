# /// script
# requires-python = ">=3.10"
# dependencies = ["jsonschema>=4.25"]
# ///
"""中間表現 JSON から、自己完結した HTML レポートを組み立てる。

入力構造の正は`docs/report-contract.schema.json`、意味の正は`docs/report-contract.md`
（→ `docs/adr/0020-intermediate-report-json.md`）。
このスクリプトは **判断をしない**。JSON に書かれた判断とデータを描画するだけで、
指標の再計算もネットワークアクセスもしない。入力はJSON Schemaで検証する。

    uv run report.py reports/2026-08-20_evening.json
    uv run report.py reports/2026-08-20_evening.json -o /tmp/preview.html

出力は既定で入力と同じ場所の `.html`。**保有情報が入るので `reports/` の外に
既定で書き出さない** (→ docs/adr/0008-no-holdings-in-repo.md)。

あわせて入力と同じ場所に `latest.json` を複製する。**次回実行がシリーズ分析の起点に
使う**もので、スキルの手作業にすると 1 回の書き忘れで前回との差分が静かに切れる
(→ docs/adr/0025-journal-as-ledger-and-memo.md)。TradingCopilot の
`order_plans/latest.json` と同じ形。

グラフィックだけでなく散文 (`prose`) を必ず描画する。HTML 単独でレポートとして
成立させるためで、図だけ並べても読み手は判断を再構成できない。
"""

import argparse
import html
import json
import pathlib

from lib.contract import validate
from lib.verdicts import NOT_APPLICABLE, actionable_items

# 4 軸シグナルの評価 → 配色と表示ラベル。
# 中間表現には色ではなく評価 (good/warn/bad/unknown) が入る。軸ごとに「良い」の
# 向きが違う (過熱度は高いほど悪い) ため、色の決定はここに閉じる。
SIGNAL_STYLE = {
    "good": ("var(--good)", "良"),
    "warn": ("var(--warn)", "中"),
    "bad": ("var(--bad)", "悪"),
    "unknown": ("var(--muted)", "?"),
}

# シグナルの軸名 → 見出し。順序が表示順になる
SIGNAL_AXES = (
    ("weekly", "週足"),
    ("daily", "日足"),
    ("overheat", "過熱"),
    ("volume", "出来高"),
)

# 判断ラベル → ヒーローとバッジの色。ここに無いラベルは中立色で描く
VERDICT_COLOR = {
    "買い": "var(--good)",
    "積増し": "var(--good)",
    "ホールド": "var(--accent)",
    "部分利確": "var(--warn)",
    "決算後に再判定": "var(--warn)",
    "見送り": "var(--muted)",
    "売却": "var(--bad)",
    "保留": "var(--muted)",
    NOT_APPLICABLE: "var(--muted)",
}


# 用語の説明。**本文に毎日同じ説明を書かない**ための逃がし先で、小さなラベルを
# 押したときだけ出す (→ docs/adr/0024-glossary-popovers.md)。
#
# 説明は専門用語を使わずに書く。ここで RSI を「RSI が高い状態」と説明しても、
# 読み手が知りたいこと (だから何なのか) は埋まらない。
GLOSSARY: dict[str, tuple[str, str]] = {
    "weekly": (
        "週足",
        "1 本のローソクが 1 週間ぶんの値動き。長い目で見た方向 (上位トレンド) を"
        "見るために使う。日足より反応は遅いが、一時的な上下に振り回されにくい。",
    ),
    "daily": (
        "日足",
        "1 本が 1 日ぶんの値動き。いつ買う・売るかのタイミング判断に使う。"
        "週足で方向を決め、日足でタイミングを測る、の 2 段構え。",
    ),
    "overheat": (
        "過熱",
        "短期間に上がりすぎ・下がりすぎていないか。過熱していると、方向が合っていても"
        "いったん反対に振れやすいので、押し目を待つ判断につながる。",
    ),
    "volume": (
        "出来高",
        "その値動きにどれだけの売買が伴ったか。売買を伴わない値上がりは続きにくい。"
        "OBV は出来高の増減を積み上げた線で、上げに買いが伴っているかを見る。",
    ),
    "score": (
        "score (ATR 単位)",
        "候補の強さ。その銘柄自身の 1 日の平均的な値幅 (ATR) の何倍動いた／突破したかで測る。"
        "動きやすい銘柄と静かな銘柄を同じ土俵で並べるための単位。"
        "向きは持たないので、大きく下げた銘柄も高い score になる。",
    ),
    "atr": (
        "ATR",
        "1 日でだいたいどれくらい動くか、の平均値。値動きの荒さを表す。"
        "同じ 3% の変化でも、荒い銘柄では普通、静かな銘柄では異常な動きになる。",
    ),
    "range20": (
        "20 日レンジ",
        "直近 20 営業日の高値と安値の幅。終値位置が 100% を超えていればその上限を"
        "上に抜けたこと、0% 未満なら下に抜けたことを意味する。",
    ),
    "closed_bar": (
        "確定足",
        "取引が終わって値が確定したローソク。まだ動いている途中の足で判断すると、"
        "同じ分析をあとからやり直したときに結果が変わってしまうため使わない。",
    ),
    "effective_holdings": (
        "実効保有",
        "保有データの基準日 (as_of) 時点の株数に、そのあとジャーナルへ記録した売買を"
        "足し合わせた、いまの実際の株数。基準日の更新が低頻度なので、この形で追う。",
    ),
    "support": (
        "支持",
        "下げが止まりやすい価格帯。ここを終値で割ると、見立てが下向きに切り替わる。",
    ),
    "resistance": (
        "抵抗",
        "上げが止まりやすい価格帯。ここを終値で超えられると、上向きが続きやすい。",
    ),
    "invalidation": (
        "無効化",
        "そこを割ったら今の見立てが成り立たなくなる価格。持ち続ける根拠が消える線で、"
        "手仕舞いや判断のやり直しの目安になる。",
    ),
    "earnings": (
        "決算注記",
        "決算の前後は寄り付きで価格が飛ぶ (ギャップ) ことがあり、"
        "「終値で◯◯円を割ったら」という条件が飛び越えられて実行できなくなる。"
        "⚠ はその期間に入っている印で、日程のお知らせではない。",
    ),
    "scenario": (
        "シナリオ進捗",
        "前回立てた見立てが今どうなっているか。前進 (近づいた) / 停滞 (動いていない) / "
        "否定接近 (崩れかけ) の 3 つで示す。",
    ),
    "sparkline": (
        "推移の線",
        "直近の終値の並びを小さく描いたもの。目盛りは無く、形と向きだけを見る。"
        "上昇なら緑、下落なら赤。",
    ),
    "market_tone": (
        "地合い",
        "相場全体の空気。株・債券・金などの ETF の強弱の並びから、資金がリスクを"
        "取りに行っているか、退避しているかを読む。個別銘柄の強弱を相対で見るために使う。",
    ),
    "v_buy": ("買い", "いまの判断材料からは買い。執行するかはりーすさんが決める。"),
    "v_add": ("積増し", "すでに持っている銘柄を買い増す判断。"),
    "v_hold": ("ホールド", "そのまま持ち続ける。動かないことも判断のひとつ。"),
    "v_trim": ("部分利確", "一部だけ売って利益を確定し、残りは持ち続ける。"),
    "v_sell": ("売却", "持ち高を手仕舞う判断。"),
    "v_pass": ("見送り", "今回は買わない。理由を必ず 1 行添えてある。"),
    "v_after_earnings": (
        "決算後に再判定",
        "決算のギャップで価格の水準が飛びうるので、決算を通過してから判断し直す。",
    ),
    "v_pending": (
        "保留",
        "データが足りず判定が成立しない。上場から日が浅く、週足や長期の平均線が"
        "計算できない銘柄がこれにあたる。根拠なく「ホールド」と書かないための札。",
    ),
    "v_na": (
        "判断対象外",
        "自動でリバランスされる口座の銘柄。分析しても執行されないので判断を付けない。"
        "ただし地合いを読む材料としては使う。",
    ),
}

# 判断ラベル → 用語キー。ラベルの文字列を id に使えないので対応表を持つ
VERDICT_TERM = {
    "買い": "v_buy",
    "積増し": "v_add",
    "ホールド": "v_hold",
    "部分利確": "v_trim",
    "売却": "v_sell",
    "見送り": "v_pass",
    "決算後に再判定": "v_after_earnings",
    "保留": "v_pending",
    NOT_APPLICABLE: "v_na",
}

# ダークテーマ想定。外部リソースは読み込まない (フォント・スクリプト・画像すべて)。
#
# 背景を透過にせず明示的に塗るのは、morning brief と違ってこの HTML が
# artifact の iframe ではなく **ローカルファイルとして直接開かれる**ため。
# 透過のままだとブラウザの既定の白地に明色の文字が乗って読めない。
CSS = """
:root {
  color-scheme: dark;
  --bg: #0d1017; --panel: #14171f; --panel2: #1a1e28; --line: #272c38;
  --fg: #e6e8ec; --muted: #8b93a3;
  --good: #3fb950; --warn: #d6a534; --bad: #f0625a; --accent: #5aa2f0;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px; background: var(--bg); color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif;
  font-size: 14px; line-height: 1.7;
}
.num { font-variant-numeric: tabular-nums; }
h1 { font-size: 18px; margin: 0; font-weight: 600; letter-spacing: .02em; }
h2 { font-size: 13px; margin: 28px 0 10px; font-weight: 600; color: var(--muted);
     letter-spacing: .08em; }
h3 { font-size: 15px; margin: 0; font-weight: 600; }
p  { margin: 6px 0; }
.wrap { max-width: 1080px; margin: 0 auto; }
.head { display: flex; justify-content: space-between; align-items: baseline;
        gap: 16px; flex-wrap: wrap; border-bottom: 1px solid var(--line); padding-bottom: 12px; }
.head .meta { color: var(--muted); font-size: 12px; }
.hero { margin-top: 16px; padding: 20px; border-radius: 12px;
        border: 1px solid var(--line); background: var(--panel); }
.hero.quiet { color: var(--muted); }
.hero .lead { font-size: 22px; font-weight: 650; line-height: 1.4; }
.hero .sub { color: var(--muted); font-size: 13px; margin-top: 4px; }
.banner { margin-top: 12px; padding: 12px 16px; border-radius: 10px;
          background: var(--panel2); border: 1px solid var(--line); font-size: 13px; }
.banner .k { color: var(--muted); font-size: 12px; letter-spacing: .04em; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.card { padding: 16px; border-radius: 12px; border: 1px solid var(--line);
        background: var(--panel); }
.card.ref { opacity: .72; }
.card .top { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.card .price { font-size: 20px; font-weight: 650; }
.card .sub { color: var(--muted); font-size: 12px; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px;
         font-weight: 600; border: 1px solid currentColor; white-space: nowrap; }
.chip { display: inline-block; padding: 1px 8px; border-radius: 6px; font-size: 11px;
        background: var(--panel2); color: var(--muted); border: 1px solid var(--line); }
.chip.warn { color: var(--warn); border-color: var(--warn); }
.sig { display: flex; gap: 6px; margin: 12px 0 4px; }
.sig .ax { flex: 1; text-align: center; }
.sig .bar { height: 5px; border-radius: 3px; background: currentColor; }
.sig .nm { font-size: 10px; color: var(--muted); margin-top: 4px; letter-spacing: .04em; }
.sig .lb { font-size: 10px; margin-top: 1px; }
.prose { margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--line); font-size: 13px; }
.prose .k { color: var(--muted); font-size: 11px; letter-spacing: .06em; }
.prose ul { margin: 4px 0; padding-left: 18px; }
.prose li { margin: 2px 0; }
.trigger { margin-top: 8px; padding: 8px 10px; border-radius: 8px; background: var(--panel2);
           border-left: 3px solid var(--accent); font-size: 12.5px; }
.axis { margin: 10px 0 2px; }
.axis .track { position: relative; height: 22px; }
.axis .line { position: absolute; top: 10px; left: 0; right: 0; height: 2px;
             background: var(--line); }
.axis .mk { position: absolute; top: 3px; width: 2px; height: 16px; transform: translateX(-1px); }
.axis .dot { position: absolute; top: 4px; width: 14px; height: 14px; border-radius: 50%;
             transform: translateX(-7px); border: 2px solid var(--bg); background: var(--accent); }
.axis .legend { display: flex; justify-content: space-between;
                color: var(--muted); font-size: 11px; }
.empty { padding: 18px; border: 1px dashed var(--line); border-radius: 12px;
         color: var(--muted); font-size: 13px; }
.notes { margin-top: 12px; padding: 14px 16px; border-radius: 10px;
         background: var(--panel); border: 1px solid var(--line); font-size: 13px; }
.notes ul { margin: 4px 0; padding-left: 18px; }
.warn-box { border-color: var(--warn); }
.foot { margin-top: 28px; padding-top: 12px; border-top: 1px solid var(--line);
        color: var(--muted); font-size: 11px; }
/* 用語ラベル: 押すと説明が出る。本文に毎日同じ説明を書かないための逃がし先 */
.term { font: inherit; color: inherit; background: none; border: 0; padding: 0;
        cursor: help; border-bottom: 1px dotted currentColor; }
.term:hover, .term:focus-visible { color: var(--accent); }
.term::after { content: "?"; font-size: .7em; vertical-align: super;
               margin-left: 1px; opacity: .65; }
.term.plain::after { content: none; }
[popover] { display: none; margin: auto; max-width: 420px; padding: 18px 20px; border-radius: 12px;
            border: 1px solid var(--line); background: var(--panel2); color: var(--fg);
            font-size: 13px; line-height: 1.8; }
[popover]:popover-open { display: block; }
[popover]::backdrop { background: rgba(0, 0, 0, .55); }
[popover] b { color: var(--accent); display: block; margin-bottom: 6px; font-size: 14px; }
[popover] p { margin: 0; }
[popover] dl { margin: 0; }
[popover] dt { color: var(--accent); font-weight: 600; margin-top: 10px; }
[popover] dd { margin: 0; color: var(--fg); }
.gloss-all { max-width: 560px; max-height: 78vh; overflow: auto; }
"""


def esc(value) -> str:
    """テキストを HTML エスケープする。

    Args:
        value: 任意の値。None は空文字になる。

    Returns:
        エスケープ済みの文字列。
    """
    return html.escape("" if value is None else str(value))


def require(data: dict, key: str, where: str):
    """必須キーを取り出す。無ければ落とす。

    既定値で埋めて進むと、LLM が書き漏らした項目が静かに空欄になり、
    「判断が無かった日」と「書き漏らした日」が出力から区別できなくなる
    (→ docs/report-contract.md の「契約違反の扱い」)。

    Args:
        data: 対象の dict。
        key: 必須キー。
        where: エラー文に出す位置の説明。

    Returns:
        キーの値。

    Raises:
        KeyError: キーが無い場合。
    """
    if key not in data:
        raise KeyError(f"{where}: 必須キー '{key}' が無い (docs/report-contract.md)")
    return data[key]


def money(value, currency: str) -> str:
    """価格を通貨記号付きで整形する。

    Args:
        value: 数値。None なら "—"。
        currency: "JPY" または "USD"。

    Returns:
        表示用の文字列 (例: "¥1,234" / "$123.45")。
    """
    if value is None:
        return "—"
    if currency == "JPY":
        return f"¥{value:,.0f}"
    return f"${value:,.2f}"


def shares_text(shares) -> str:
    """株数を表示用に整形する。

    Args:
        shares: 株数。None なら空文字。

    Returns:
        表示用の文字列 (例: "100株")。
    """
    if shares is None:
        return ""
    num = int(shares) if float(shares) == int(shares) else shares
    return f"{num:,}株"


def signed_pct(value) -> str:
    """変化率を符号付きで整形する。

    Args:
        value: パーセント値。None なら "—"。

    Returns:
        表示用の文字列 (例: "+1.2%")。
    """
    return "—" if value is None else f"{value:+.1f}%"


def sparkline(closes: list, width: int = 300, height: int = 40) -> str:
    """終値の並びを SVG のスパークラインにする。

    値の絶対水準ではなく形だけを見せる図なので、目盛りは付けない。
    上昇なら緑、下落なら赤で塗り、始点と終点の比較を色で読めるようにする。

    Args:
        closes: 終値のリスト (古い順)。2 点未満なら描かない。
        width: SVG の幅 (px)。
        height: SVG の高さ (px)。

    Returns:
        SVG の文字列。描けない場合は空文字。
    """
    pts = [float(c) for c in (closes or []) if c is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    pad = 3
    step = width / (len(pts) - 1)
    coords = [
        f"{i * step:.1f},{pad + (height - 2 * pad) * (1 - (p - lo) / span):.1f}"
        for i, p in enumerate(pts)
    ]
    color = "var(--good)" if pts[-1] >= pts[0] else "var(--bad)"
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'preserveAspectRatio="none" aria-hidden="true" style="display:block;margin-top:10px">'
        f'<polyline fill="none" stroke="{color}" stroke-width="1.6" '
        f'stroke-linejoin="round" points="{" ".join(coords)}"/></svg>'
    )


def signal_bars(signals: dict) -> str:
    """4 軸のシグナルを横並びのミニバーにする。

    Args:
        signals: `{"weekly": "good", ..., "labels": {...}}` (契約の signals)。

    Returns:
        HTML の断片。
    """
    labels = (signals or {}).get("labels") or {}
    cells = []
    for key, name in SIGNAL_AXES:
        color, mark = SIGNAL_STYLE.get((signals or {}).get(key), SIGNAL_STYLE["unknown"])
        extra = esc(labels.get(key, "")) or mark
        cells.append(
            f'<div class="ax" style="color:{color}">'
            f'<div class="bar"></div><div class="nm">{term(key, name)}</div>'
            f'<div class="lb">{extra}</div></div>'
        )
    return f'<div class="sig">{"".join(cells)}</div>'


def level_axis(price, levels: dict, currency: str) -> str:
    """現値とキーレベルの位置関係を 1 本の横軸に描く。

    数値を並べただけでは「無効化ラインまであとどれくらいか」が読み取れない。
    支持・抵抗・無効化と現値を同じスケールに乗せて、距離を見た目で示す。

    Args:
        price: 現値。
        levels: `{"support": …, "resistance": …, "invalidation": …}`。
        currency: 通貨コード。

    Returns:
        HTML の断片。描く材料が無ければ空文字。
    """
    marks = [
        ("invalidation", "無効化", "var(--bad)"),
        ("support", "支持", "var(--warn)"),
        ("resistance", "抵抗", "var(--accent)"),
    ]
    values = [(k, n, c, float(levels[k])) for k, n, c in marks if (levels or {}).get(k) is not None]
    if price is None or not values:
        return ""
    pool = [v for *_, v in values] + [float(price)]
    lo, hi = min(pool), max(pool)
    span = (hi - lo) or 1.0
    # 端に張り付くと読めないので、両側に 8% ぶんの余白を作る
    lo, hi = lo - span * 0.08, hi + span * 0.08
    span = hi - lo

    def pos(v: float) -> float:
        return (v - lo) / span * 100

    bars = "".join(
        f'<div class="mk" style="left:{pos(v):.1f}%;background:{c}" title="{esc(n)}"></div>'
        for _, n, c, v in values
    )
    legend = " / ".join(f"{term(k, n)} {money(v, currency)}" for k, n, _, v in values)
    return (
        f'<div class="axis"><div class="track"><div class="line"></div>{bars}'
        f'<div class="dot" style="left:{pos(float(price)):.1f}%"></div></div>'
        f'<div class="legend"><span>{legend}</span><span>現値 {money(price, currency)}</span>'
        f"</div></div>"
    )


def range_axis(rng: dict, price, currency: str) -> str:
    """直前 20 日レンジの中で終値がどこにいるかを描く。

    Args:
        rng: `{"low": …, "high": …, "pos_pct": …}`。
        price: 現値。
        currency: 通貨コード。

    Returns:
        HTML の断片。材料が無ければ空文字。
    """
    if not rng or rng.get("low") is None or rng.get("high") is None:
        return ""
    lo, hi = float(rng["low"]), float(rng["high"])
    span = (hi - lo) or 1.0
    pct = rng.get("pos_pct")
    if pct is None and price is not None:
        pct = (float(price) - lo) / span * 100
    pct = float(pct if pct is not None else 50)
    # マーカーの座標だけを軸内に丸める。**表示する % は丸めない** —
    # レンジをどれだけ上抜けたかは候補の強さそのもので、丸めると突破の度合いが消える
    marker = max(-8.0, min(108.0, pct))
    return (
        f'<div class="axis"><div class="track"><div class="line"></div>'
        f'<div class="mk" style="left:0;background:var(--warn)"></div>'
        f'<div class="mk" style="left:100%;background:var(--accent)"></div>'
        f'<div class="dot" style="left:{marker:.1f}%"></div></div>'
        f'<div class="legend">'
        f"<span>{term('range20', '20日レンジ')} "
        f"{money(lo, currency)} 〜 {money(hi, currency)}</span>"
        f"<span>終値位置 {pct:.0f}%</span></div></div>"
    )


def score_bar(score: float) -> str:
    """score (ATR 単位) を横バーにする。

    3.0 ATR で満杯にする。score は上限が無いが、実運用で 3 を超える候補は稀で、
    それ以上を線形に伸ばすと日々の差が潰れて見えなくなる。

    Args:
        score: `screen.py` の score。

    Returns:
        HTML の断片。
    """
    pct = max(0.0, min(100.0, float(score) / 3.0 * 100))
    return (
        f'<div style="margin:10px 0 2px"><div style="height:6px;border-radius:3px;'
        f'background:var(--panel2);overflow:hidden">'
        f'<div style="height:100%;width:{pct:.0f}%;background:var(--accent)"></div></div>'
        f'<div class="legend" style="display:flex;justify-content:space-between;'
        f'color:var(--muted);font-size:11px"><span>{term("score", "score")}</span>'
        f'<span class="num">{score:.1f} ATR</span></div></div>'
    )


def earnings_chip(earnings: dict) -> str:
    """決算注記をチップにする。

    Args:
        earnings: `{"note": …, "warn": bool}`。None 可。

    Returns:
        HTML の断片。注記が無ければ空文字。
    """
    note = (earnings or {}).get("note")
    if not note:
        return ""
    cls = "chip warn" if (earnings or {}).get("warn") else "chip"
    return f'<div style="margin-top:8px"><span class="{cls}">{term("earnings", note)}</span></div>'


def term(key: str, text: str | None = None) -> str:
    """用語に説明のポップオーバーを付けたラベルを返す。

    ポップオーバー本体は `glossary_html()` が末尾にまとめて 1 つずつ出す。
    同じ用語が何度出てきても実体は 1 つで、ここは参照だけを作る。

    `title` 属性を併記するのは、Popover API に未対応のブラウザでも
    ホバーで説明が出るようにするため (JavaScript は使わない)。

    Args:
        key: GLOSSARY のキー。
        text: 表示する文字列 (省略時は用語名そのもの)。

    Returns:
        HTML の断片。キーが未定義なら素のテキストを返す。
    """
    if key not in GLOSSARY:
        return esc(text or key)
    name, desc = GLOSSARY[key]
    shown = name if text is None else text
    return (
        f'<button class="term" type="button" popovertarget="g-{key}" '
        f'title="{esc(desc)}">{esc(shown)}</button>'
    )


def glossary_html() -> str:
    """全用語のポップオーバー本体と、一覧を開くボタンを出す。

    Returns:
        HTML の断片。
    """
    pops = "".join(
        f'<div popover id="g-{key}"><b>{esc(name)}</b><p>{esc(desc)}</p></div>'
        for key, (name, desc) in GLOSSARY.items()
    )
    items = "".join(
        f"<dt>{esc(name)}</dt><dd>{esc(desc)}</dd>" for name, desc in GLOSSARY.values()
    )
    return (
        f"{pops}"
        f'<div popover id="g-all" class="gloss-all"><b>用語</b><dl>{items}</dl></div>'
    )


def verdict_badge(verdict: str) -> str:
    """判断ラベルをバッジにする。

    Args:
        verdict: 判断ラベル。

    Returns:
        HTML の断片。
    """
    color = VERDICT_COLOR.get(verdict, "var(--muted)")
    key = VERDICT_TERM.get(verdict)
    inner = term(key, verdict) if key else esc(verdict)
    return f'<span class="badge" style="color:{color}">{inner}</span>'


def position_card(pos: dict) -> str:
    """保有銘柄のカードを組む。

    Args:
        pos: 契約の Position。

    Returns:
        HTML の断片。
    """
    where = f"holdings[{pos.get('ticker', '?')}]"
    ticker = pos["ticker"]
    currency = pos["currency"]
    name = pos.get("name") or ""
    price = require(pos, "price", where)
    prose = require(pos, "prose", where)
    # 判断対象外の銘柄だけが「—」を持つ。それ以外で verdict が欠けていたら落とす。
    # 既定値に潰すと、書き漏らした「売却」が「判断なし」として表示され、
    # ヒーローからも actionable_items() からも消える
    verdict = NOT_APPLICABLE if pos.get("reference_only") else pos["verdict"]

    card_cls = "card ref" if pos.get("reference_only") else "card"
    scenario = ""
    if pos.get("scenario"):
        scenario = (
            f'<span class="chip">{term("scenario", "シナリオ")} {esc(pos["scenario"])}</span>'
        )

    reasons = ""
    if prose.get("reasons"):
        lis = "".join(f"<li>{esc(r)}</li>" for r in prose["reasons"])
        reasons = f'<div class="k">根拠</div><ul>{lis}</ul>'
    trigger = ""
    if prose.get("trigger"):
        trigger = f'<div class="trigger">トリガー: {esc(prose["trigger"])}</div>'

    return (
        f'<div class="{card_cls}">'
        f'<div class="top"><div>'
        f'<h3>{esc(ticker)} {esc(name)}</h3>'
        f'<div class="sub num">{esc(shares_text(pos.get("shares")))}</div></div>'
        f"<div>{verdict_badge(verdict)}</div></div>"
        f'<div class="price num" style="margin-top:6px">{money(price, currency)}'
        f'<span class="sub num"> {signed_pct(pos.get("change_pct"))}</span></div>'
        f'<div style="margin-top:6px">{scenario}</div>'
        f"{signal_bars(pos['signals'])}"
        f"{sparkline(pos.get('closes') or [])}"
        f"{level_axis(price, pos.get('levels') or {}, currency)}"
        f"{earnings_chip(pos.get('earnings'))}"
        f'<div class="prose">'
        f'<div class="k">前回からの変化</div><p>{esc(prose.get("change"))}</p>'
        f'<div class="k">シナリオ進捗</div><p>{esc(prose.get("scenario"))}</p>'
        f"{reasons}{trigger}</div></div>"
    )


def candidate_card(cand: dict) -> str:
    """スクリーニング候補のカードを組む。

    Args:
        cand: 契約の Candidate。

    Returns:
        HTML の断片。
    """
    where = f"candidates[{cand.get('ticker', '?')}]"
    ticker = cand["ticker"]
    currency = cand["currency"]
    name = cand.get("name") or ""
    price = require(cand, "price", where)
    prose = require(cand, "prose", where)
    verdict = cand["verdict"]

    strong = ""
    if prose.get("strong"):
        lis = "".join(f"<li>{esc(s)}</li>" for s in prose["strong"])
        strong = f'<div class="k">強い点</div><ul>{lis}</ul>'
    weak = ""
    if prose.get("weak"):
        lis = "".join(f"<li>{esc(w)}</li>" for w in prose["weak"])
        weak = f'<div class="k">弱い点</div><ul>{lis}</ul>'
    check = ""
    if prose.get("check"):
        check = f'<div class="trigger">確認点: {esc(prose["check"])}</div>'

    parts = []
    if cand.get("atr_pct") is not None:
        parts.append(f"{term('atr', 'ATR')} {cand['atr_pct']:.1f}%")
    if cand.get("turnover"):
        parts.append(f"売買代金 {esc(cand['turnover'])}")
    # parts は term() が返す HTML を含むので、ここから先はエスケープしない
    meta = " · ".join(parts)
    return (
        f'<div class="card"><div class="top"><div>'
        f'<h3>{esc(ticker)} {esc(name)}</h3>'
        f'<div class="sub">{esc(require(cand, "pass_reason", where))}</div></div>'
        f"<div>{verdict_badge(verdict)}</div></div>"
        f'<div class="price num" style="margin-top:6px">{money(price, currency)}'
        f'<span class="sub num"> {signed_pct(cand.get("change_pct"))}</span></div>'
        f'<div class="sub num" style="margin-top:2px">{meta}</div>'
        f"{score_bar(float(require(cand, 'score_atr', where)))}"
        f"{signal_bars(cand['signals'])}"
        f"{sparkline(cand.get('closes') or [])}"
        f"{range_axis(require(cand, 'range', where), price, currency)}"
        f"{level_axis(price, cand.get('levels') or {}, currency)}"
        f"{earnings_chip(cand.get('earnings'))}"
        f'<div class="prose">{strong}{weak}{check}</div></div>'
    )


def hero(data: dict) -> str:
    """本日、資金が動く判断があるかを最上部に出す。

    無い日を小さく扱わない。「候補ゼロは正常」であることを毎回同じ場所に
    同じ大きさで出すことが、埋め草の候補を作らない運用の裏返しになる。

    Args:
        data: 中間表現 dict。

    Returns:
        HTML の断片。
    """
    items = actionable_items(data)
    if not items:
        return (
            '<div class="hero quiet"><div class="lead">本日、資金が動く判断なし</div>'
            '<div class="sub">候補ゼロ・ホールドのみは正常な結果。埋め草の候補は作らない</div>'
            "</div>"
        )
    rows = "".join(
        f'<div style="margin-top:6px">{verdict_badge(i["verdict"])} '
        f'<span class="num">{esc(i["ticker"])}</span> {esc(i["name"])}</div>'
        for i in items
    )
    return (
        f'<div class="hero"><div class="lead">資金が動く判断 {len(items)} 件</div>'
        f'<div class="sub">執行するかはりーすさんが決める。以下は判断であって指示ではない</div>'
        f"{rows}</div>"
    )


def notes_box(title: str, items: list, warn: bool = False) -> str:
    """箇条書きのボックスを組む。

    Args:
        title: 見出し。
        items: 行のリスト。空なら描かない。
        warn: 警告色の枠にするか。

    Returns:
        HTML の断片。
    """
    if not items:
        return ""
    lis = "".join(f"<li>{esc(x)}</li>" for x in items)
    cls = "notes warn-box" if warn else "notes"
    return f'<div class="{cls}"><div class="k">{esc(title)}</div><ul>{lis}</ul></div>'


def render(data: dict) -> str:
    """中間表現から HTML 全体を組み立てる。

    Args:
        data: `docs/report-contract.md` に従う dict。

    Returns:
        完成した HTML 文字列。

    Raises:
        KeyError: 必須キーが欠けている場合。
    """
    # **描画の前に契約を一括検証する。** 通ったあとは必須項目が揃っている前提でよい。
    # 使う場所ごとに検証していた頃は、項目ごとに書き忘れる機会があった
    validate(data)

    date = require(data, "date", "root")
    bars = require(data, "bars", "root")
    screen = require(data, "screen", "root")
    eff = require(data, "effective_holdings", "root")

    as_of_parts = []
    for a in require(data, "holdings_as_of", "root"):
        text = f"{a.get('label', '保有')} {a.get('as_of', '不明')}"
        if a.get("count") is not None:
            text += f" ({a['count']}銘柄)"
        as_of_parts.append(text)
    as_of = " / ".join(as_of_parts)
    bar_line = " / ".join(f"{k.upper()} {v}" for k, v in bars.items())
    if data.get("stale_bars"):
        bar_line += "（前回から変わらず・独立した観測として数えない）"

    tone = data.get("market_tone") or {}
    tone_html = ""
    if tone.get("label") or tone.get("prose"):
        tone_html = (
            f'<div class="banner"><div class="k">'
            f'{term("market_tone", "地合い")} — {esc(tone.get("label"))}</div>'
            f'<p>{esc(tone.get("prose"))}</p></div>'
        )

    # どちらも契約上の必須キー。get(..., []) で潰すと、LLM が書き漏らした日が
    # 「保有なし・候補なし」という正常な出力に化けて区別できなくなる
    holdings = require(data, "holdings", "root")
    candidates = require(data, "candidates", "root")
    failures = require(screen, "failures", "screen")
    if candidates:
        cand_html = f'<div class="grid">{"".join(candidate_card(c) for c in candidates)}</div>'
    else:
        # 取得に失敗した銘柄は「条件を満たさなかった」のではなく「判定できていない」。
        # 混ぜると、取得障害の日を静かな日として読んでしまう
        note = ""
        if failures:
            note = (
                f"ただし {esc(failures)} 銘柄は取得に失敗しており、判定できていない。"
                "候補ゼロと取得失敗は別の事象として扱うこと。"
            )
        cand_html = (
            '<div class="empty">候補なし。条件を満たす事象が起きた銘柄が無かった。'
            f"閾値も母集団もこの場では変えない。{note}</div>"
        )
    hold_html = (
        f'<div class="grid">{"".join(position_card(p) for p in holdings)}</div>'
        if holdings
        else '<div class="empty">保有銘柄なし。</div>'
    )

    # 生成時刻は ISO8601 の "HH:MM" だけを見出しに出す (日付は左に既に出ている)
    gen_time = str(require(data, "generated_at", "root"))[11:16]
    lines = require(eff, "lines", "effective_holdings")
    if not lines:
        raise ValueError(
            "effective_holdings.lines が空。執行 0 件でも「執行記録なし (as_of 時点のまま)」を"
            "入れる (docs/report-contract.md)"
        )
    eff_lines = "".join(f"<p class='num'>{esc(line)}</p>" for line in lines)
    # 母集団と market も契約上の必須キー。空欄にすると、どの母集団を正常に調べたのか
    # 分からないまま「候補なし」と報告することになる
    universe = require(screen, "universe", "screen")
    market = require(screen, "market", "screen")
    cand_head = f"候補 {len(candidates)} 件 — 母集団 {esc(universe)} 銘柄 (market={esc(market)})"
    if failures:
        cand_head += f" / 取得失敗 {esc(failures)} 件"
    eff_head = (
        f"{term('effective_holdings', '実効保有')} — Investment {esc(as_of)} "
        f"+ 執行記録 {esc(eff.get('executions', 0))} 件"
    )
    ref_count = sum(1 for p in holdings if p.get("reference_only"))
    hold_head = f"保有 {len(holdings)} 銘柄"
    if ref_count:
        hold_head += f"（うち判断対象外 {ref_count} 銘柄）"

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>StockCopilot Evening Brief — {esc(date)}</title>
<style>{CSS}</style></head><body><div class="wrap">
<div class="head">
  <h1>StockCopilot — Evening Brief</h1>
  <div class="meta num">{esc(date)} 生成 {esc(gen_time)} JST</div>
</div>
{hero(data)}
<div class="banner">
  <div class="k">{eff_head}</div>
  {eff_lines}
</div>
<div class="banner"><div class="k">{term("closed_bar", "確定足")}</div>
  <p class="num">{esc(bar_line)}</p></div>
{tone_html}
<h2>{hold_head}</h2>
{hold_html}
<h2>{cand_head}</h2>
{cand_html}
<h2>総括</h2>
<div class="notes"><p>{esc(require(data, "summary", "root"))}</p></div>
{notes_box("確認を取らずに置いた前提", data.get("assumptions") or [])}
{notes_box("警告", data.get("warnings") or [], warn=True)}
<div class="foot">
  分析・提案のみ。このプロジェクトに発注機能は無い。判断の最終決定はりーすさん。<br>
  点線の付いた語は押すと説明が出る →
  <button class="term plain" type="button" popovertarget="g-all">用語をまとめて見る</button><br>
  生成: report.py（中間表現の仕様は docs/report-contract.md）
</div>
{glossary_html()}
</div></body></html>"""


def main() -> None:
    """コマンドライン引数を読み、HTML を書き出す。"""
    ap = argparse.ArgumentParser(description="中間表現 JSON から HTML レポートを生成する")
    ap.add_argument("source", help="中間表現 JSON のパス")
    ap.add_argument("-o", "--out", help="出力先 (既定: 入力と同じ場所の .html)")
    ap.add_argument(
        "--no-latest", action="store_true", help="latest.json を更新しない (プレビュー用)"
    )
    args = ap.parse_args()

    src = pathlib.Path(args.source)
    raw = src.read_text(encoding="utf-8")
    data = json.loads(raw)
    out = pathlib.Path(args.out) if args.out else src.with_suffix(".html")
    # HTML を先に組む。契約違反ならここで落ち、latest.json は更新されない
    html = render(data)
    out.write_text(html, encoding="utf-8")
    print(f"[report] {out}")

    if not args.no_latest and src.name != "latest.json":
        latest = src.with_name("latest.json")
        latest_date = ""
        if latest.exists():
            latest_date = json.loads(latest.read_text(encoding="utf-8")).get("date", "")
        if data["date"] >= latest_date:
            latest.write_text(raw, encoding="utf-8")
            print(f"[report] {latest} (次回のシリーズ分析の起点)")
        else:
            print(f"[report] {latest} はより新しいため更新しない")


if __name__ == "__main__":
    main()
