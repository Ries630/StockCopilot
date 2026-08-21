"""銘柄の表示名の解決。

**4 桁コードだけでは何の会社か分からない**日本株のために、出力へ必ず名前を
併記できるようにする (→ docs/adr/0023-japanese-stock-display-names.md)。

解決の順序は 1 か所に閉じてある。screen.py と analyze.py の両方から使うため、
片方だけ違う順序になると同じ銘柄が別の名前で出る。

    1. 呼び出し側が渡した名前の辞書 (config の NAMES_JP など) — 日本語
    2. yfinance の history_metadata — 英語しか返らない
    3. 解決できなければ None

米国株は既定で問い合わせない。ティッカーだけで判別でき、1 銘柄 1 リクエストの
コストに見合わないため (辞書に載っていれば使う)。
"""

from lib.datasource import detect_market, fetch_display_name

# 同じ実行の中で同じ銘柄を二度取りに行かないためのキャッシュ。
# 解決できなかった銘柄も None のまま覚える (再試行しても同じ結果になるため)
_cache: dict[str, str | None] = {}


def display_name(
    ticker: str,
    market: str | None = None,
    names: dict | None = None,
    fetch: bool = True,
) -> str | None:
    """銘柄の表示名を返す。

    Args:
        ticker: 生ティッカー。
        market: "jp" / "us"。省略時は detect_market() で推定。
        names: 手書きの名前の辞書 (config の NAMES_JP を想定)。
        fetch: False なら辞書だけを見てネットワークに出ない。

    Returns:
        表示名。解決できなければ None。
    """
    market = market or detect_market(ticker)
    if names and (name := names.get(ticker)):
        return name
    # US はティッカーだけで判別できるので、辞書に無ければ問い合わせない
    if not fetch or market != "jp":
        return None
    if ticker not in _cache:
        _cache[ticker] = fetch_display_name(ticker, market)
    return _cache[ticker]


def label(ticker: str, name: str | None) -> str:
    """「コード 銘柄名」の 1 行を組む。

    名前が無いときにコードだけを返すのは、"7203 不明" のような
    実体のない文字列を出力に混ぜないため。

    Args:
        ticker: 生ティッカー。
        name: 表示名 (None 可)。

    Returns:
        表示用の文字列。
    """
    return f"{ticker} {name}" if name else ticker
