"""screen.py の母集団構築のテスト (ネットワークアクセスなし)。

母集団の組み立ては「何を取りに行くか」を決めており、
ここが崩れると保有銘柄の混入 (public リポジトリ規範に関わる) や
同一銘柄の二重取得 (yfinance は 1 銘柄 1 リクエスト) が起きる。
"""

from __future__ import annotations

import pytest

import screen


def _patch_lists(
    monkeypatch: pytest.MonkeyPatch,
    *,
    watch_jp: list[str] | None = None,
    watch_us: list[str] | None = None,
    uni_jp: list[str] | None = None,
    uni_us: list[str] | None = None,
    held: set[str] | None = None,
) -> None:
    """母集団の入力 4 層と保有を差し替える (実ファイル・実保有に依存させない)。"""
    monkeypatch.setattr(screen, "WATCHLIST_JP", watch_jp or [])
    monkeypatch.setattr(screen, "WATCHLIST_US", watch_us or [])
    monkeypatch.setattr(screen, "UNIVERSE_JP", uni_jp or [])
    monkeypatch.setattr(screen, "UNIVERSE_US", uni_us or [])
    monkeypatch.setattr(screen, "held_tickers", lambda: held or set())


def test_watchlist_is_included(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストは探索ユニバースと並んで母集団に入る。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"])
    assert screen.build_universe("jp", False) == [("1111", "jp"), ("2222", "jp")]


def test_watchlist_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストを先に取りに行く (優先度の高い層から到達させる)。"""
    _patch_lists(monkeypatch, watch_us=["WWW"], uni_us=["UUU"])
    assert [t for t, _ in screen.build_universe("us", False)] == ["WWW", "UUU"]


def test_duplicates_are_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリストと探索ユニバースの重複は 1 回だけ取りに行く。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["1111", "2222"])
    assert screen.build_universe("jp", False) == [("1111", "jp"), ("2222", "jp")]


def test_held_excluded_from_watchlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """ウォッチリスト銘柄も保有になれば既定で落ちる (買い済みは候補にしない)。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"], held={"1111"})
    assert screen.build_universe("jp", False) == [("2222", "jp")]


def test_include_held_keeps_watchlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """--include-held では保有と重なるウォッチリスト銘柄も残る。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], uni_jp=["2222"], held={"1111"})
    assert screen.build_universe("jp", True) == [("1111", "jp"), ("2222", "jp")]


def test_market_filter_splits_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    """--market jp は JP 側のウォッチリストと探索ユニバースだけを組む。"""
    _patch_lists(monkeypatch, watch_jp=["1111"], watch_us=["WWW"], uni_us=["UUU"])
    assert screen.build_universe("jp", False) == [("1111", "jp")]


def test_missing_holdings_does_not_break(monkeypatch: pytest.MonkeyPatch) -> None:
    """Investment の生成物が無くても母集団は組める (保有の除外だけ効かない)。"""
    _patch_lists(monkeypatch, watch_jp=["1111"])

    def _raise() -> set[str]:
        raise FileNotFoundError("report_data_*.json が見つからない")

    monkeypatch.setattr(screen, "held_tickers", _raise)
    assert screen.build_universe("jp", False) == [("1111", "jp")]
