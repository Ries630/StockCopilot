"""スクリーニング対象ユニバースとパラメータ。

crypto (swing/screen.py) は Hyperliquid の 1 リクエストで全銘柄の出来高が揃うため
動的ユニバースにできたが、yfinance は 1 銘柄 = 1 リクエストなので
**明示的なリスト管理** にする。初回は狭く始め、運用しながら追加する方針
(候補が出ない日は「候補なし」でよい。母集団を無理に広げない)。

母集団は 3 層:

1. **ウォッチリスト** (保有検討中) — config/watchlist.py。常に含める
2. **探索ユニバース** (下記 UNIVERSE_JP / UNIVERSE_US) — 知らない銘柄を拾う枠。
   広げるほど実行時間が延びるので、候補ゼロの頻度を実測してから拡張する
3. **保有** — 既定で除外

保有銘柄はスクリーニングの対象ではない。screen.py が lib/holdings.py の
held_tickers() を除外フィルタとして使い、既定で母集団から落とす
(--include-held で含められる)。既に持っている銘柄が候補に出ても行動が変わらず、
保有側の判断は analyze.py / stock-check が担うため。
"""

# ウォッチリスト (保有検討中の銘柄) は config/watchlist.py に置く。
# public リポジトリに購入意図を残さないため追跡対象外にしており、
# 未作成の環境 (CI・クリーンクローン) では空リストとして扱う。
# 雛形は config/watchlist.example.py
try:
    from config.watchlist import WATCHLIST_JP, WATCHLIST_US
except ImportError:
    WATCHLIST_JP: list[str] = []
    WATCHLIST_US: list[str] = []

# ウォッチリストの日本語名 (任意)。雛形にある WATCHLIST_NAMES_JP を作っていれば使う
try:
    from config.watchlist import WATCHLIST_NAMES_JP
except ImportError:
    WATCHLIST_NAMES_JP: dict[str, str] = {}

# --- 日本株: 流動性の高い主要銘柄 (編集して拡張) ---
# public リポジトリなので、ここに実際の保有銘柄を書き足さないこと。
# 保有は screen.py が既定で除外するので、そもそも書く意味がない
UNIVERSE_JP = [
    "7203",  # トヨタ自動車
    "6758",  # ソニーグループ
    "8306",  # 三菱UFJ
    "9984",  # ソフトバンクグループ
    "6501",  # 日立製作所
    "8035",  # 東京エレクトロン
    "6098",  # リクルートHD
    "7974",  # 任天堂
    "6861",  # キーエンス
    "9983",  # ファーストリテイリング
]

# --- 日本株の銘柄名 ---
# **4 桁コードだけでは何の会社か分からない**ため、出力には必ず名前を併記する。
# yfinance も名前を返すが英語なので (7203 → "Toyota Motor Corporation")、
# 日本語で出したい銘柄はここに書く。ここに無い銘柄は yfinance の英語名に落ちる
# (→ docs/adr/0023-japanese-stock-display-names.md)。
#
# 保有銘柄はここに書かない。保有の名前は Investment の生成物から来る
# (public リポジトリに保有を残さないため → docs/adr/0008)。
NAMES_JP: dict[str, str] = {
    "7203": "トヨタ自動車",
    "6758": "ソニーグループ",
    "8306": "三菱UFJフィナンシャル・グループ",
    "9984": "ソフトバンクグループ",
    "6501": "日立製作所",
    "8035": "東京エレクトロン",
    "6098": "リクルートホールディングス",
    "7974": "任天堂",
    "6861": "キーエンス",
    "9983": "ファーストリテイリング",
    # ウォッチリスト側の名前を重ねる (追跡対象外のファイルに書ける)
    **WATCHLIST_NAMES_JP,
}

# --- 米国株: 主要大型株 (編集して拡張) ---
UNIVERSE_US = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META",
    "VTI",  # 米国株全体の ETF。地合いの基準として置いている
]

# --- 機械条件のパラメータ ---
# 流動性フロア: 20 日平均売買代金。板の薄い銘柄を落とす
# (crypto 版の MIN_DAY_VOLUME_USD に相当。閾値は運用しながら調整する)
MIN_TURNOVER_JPY = 1e9   # JP: 10 億円/日
MIN_TURNOVER_USD = 1e8   # US: $100M/日

# 「動いている」の判定: 直近確定足の変化率がその銘柄自身の日次 ATR の何倍か。
# 生の % だと高ボラ銘柄が常に上位に来るため ATR 正規化する (crypto 版と同じ思想)
MIN_MOVE_IN_ATR = 1.5

# 「レンジを抜けた」の判定: 確定終値が 20 日レンジをどれだけ超えたか (ATR 単位)。
# 下限が無いと 0.02 ATR のような幅でも通り、実質「レンジの端にいる」銘柄を拾うのと
# 変わらなくなる。状態ではなく事象を条件にするという設計に反するので閾値を置く。
# 運用しながら調整する前提の値 (ATR の 1/4〜1/2 が目安)
MIN_BREAK_IN_ATR = 0.3

# 出力する候補の最大数。多すぎる候補は分析工数を食うだけ
MAX_CANDIDATES = 5
