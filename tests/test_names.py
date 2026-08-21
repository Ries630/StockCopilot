"""lib/names.py のテスト。

**ネットワークにアクセスしない。** yfinance への問い合わせは
`lib.names.fetch_display_name` を差し替えて検証する。
"""

import pytest

from lib import names


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    """テスト間でキャッシュを持ち越さない。"""
    names._cache.clear()


@pytest.fixture
def fetched(monkeypatch: pytest.MonkeyPatch) -> list:
    """yfinance の問い合わせを差し替え、呼ばれた銘柄を記録する。

    Args:
        monkeypatch: pytest の monkeypatch。

    Returns:
        問い合わせたティッカーが積まれるリスト。
    """
    calls: list = []

    def fake_fetch(ticker: str, market: str | None = None) -> str | None:
        calls.append(ticker)
        return f"{ticker} Corporation"

    monkeypatch.setattr(names, "fetch_display_name", fake_fetch)
    return calls


def test_dictionary_wins_over_network(fetched: list) -> None:
    """辞書にある銘柄は問い合わせない (日本語名を英語名で上書きしない)。"""
    assert names.display_name("7203", "jp", {"7203": "トヨタ自動車"}) == "トヨタ自動車"
    assert fetched == []


def test_falls_back_to_yfinance_for_jp(fetched: list) -> None:
    """辞書に無い日本株は yfinance に落ちる。"""
    assert names.display_name("6902", "jp", {}) == "6902 Corporation"
    assert fetched == ["6902"]


def test_us_is_not_queried(fetched: list) -> None:
    """米国株はティッカーだけで判別できるので問い合わせない。"""
    assert names.display_name("AAPL", "us", {}) is None
    assert fetched == []


def test_us_dictionary_entry_is_still_used(fetched: list) -> None:
    assert names.display_name("AAPL", "us", {"AAPL": "アップル"}) == "アップル"
    assert fetched == []


def test_fetch_false_stays_offline(fetched: list) -> None:
    assert names.display_name("6902", "jp", {}, fetch=False) is None
    assert fetched == []


def test_same_ticker_is_fetched_once(fetched: list) -> None:
    """1 回の実行で同じ銘柄を二度取りに行かない。"""
    names.display_name("6902", "jp", {})
    names.display_name("6902", "jp", {})
    assert fetched == ["6902"]


def test_unresolved_name_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """解決できなかった銘柄も覚える (再試行しても同じ結果になるため)。"""
    calls: list = []

    def fake_fetch(ticker: str, market: str | None = None) -> str | None:
        calls.append(ticker)
        return None

    monkeypatch.setattr(names, "fetch_display_name", fake_fetch)
    assert names.display_name("6902", "jp", {}) is None
    assert names.display_name("6902", "jp", {}) is None
    assert calls == ["6902"]


def test_market_is_inferred_when_omitted(fetched: list) -> None:
    """market 省略時は detect_market() が 4 桁コードを jp と判定する。"""
    assert names.display_name("6902", names=None) == "6902 Corporation"


def test_label_omits_missing_name() -> None:
    """名前が無いときに実体のない文字列を混ぜない。"""
    assert names.label("7203", "トヨタ自動車") == "7203 トヨタ自動車"
    assert names.label("AAPL", None) == "AAPL"
    assert names.label("AAPL", "") == "AAPL"
