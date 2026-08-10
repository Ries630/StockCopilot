"""lib/holdings.py の as_of 解決のテスト (ネットワークアクセス・実保有に依存しない)。

Investment の生成物は資産クラスごとに別の基準日を持つ。これを一律で
stock.as_of に丸めると、別系統で管理されている銘柄が実際より新しく見える。
どの基準日をどの口座に割り当てるかは保有の鮮度判定の前提なので、明示的に固定する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import holdings


def _report(**overrides) -> dict:
    """テスト用の report_data 相当の dict を組み立てる。"""
    data = {
        "market": {
            "stock_as_of": "2026-07-22",
            "wealthnavi_as_of": "2026-06-30",
            "usdjpy": 150.0,
        },
        "stock": {
            "as_of": "2026-07-22",
            "holdings": [
                {
                    "ticker": "9999",
                    "name": "テスト銘柄",
                    "region": "日本",
                    "quantity": 100,
                    "account": "楽天 NISA",
                    "class": "国内株式",
                },
                {
                    "ticker": "VTI",
                    "name": "テスト ETF",
                    "region": "米国",
                    "quantity": 1.5,
                    "account": "WealthNavi",
                    "class": "海外ETF",
                },
            ],
        },
    }
    data.update(overrides)
    return data


@pytest.fixture
def report_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """latest_report_path() を差し替え、任意の内容を読ませる。"""

    def _write(data: dict) -> Path:
        path = tmp_path / "report_data_20260731.json"
        path.write_text(json.dumps(data, ensure_ascii=False))
        monkeypatch.setattr(holdings, "latest_report_path", lambda: path)
        return path

    return _write


def test_resolve_as_of_uses_class_specific_value() -> None:
    """口座名がキー名を含むなら、その資産クラスの as_of を使う。"""
    by_key = {"stock_as_of": "2026-07-22", "wealthnavi_as_of": "2026-06-30"}
    assert holdings._resolve_as_of("WealthNavi", by_key, "2026-07-22") == (
        "2026-06-30",
        "wealthnavi_as_of",
    )


def test_resolve_as_of_falls_back_to_default() -> None:
    """対応するキーが無い口座は既定 (stock.as_of) に落ちる。"""
    by_key = {"stock_as_of": "2026-07-22", "wealthnavi_as_of": "2026-06-30"}
    assert holdings._resolve_as_of("楽天 NISA", by_key, "2026-07-22") == (
        "2026-07-22",
        "stock_as_of",
    )


def test_resolve_as_of_ignores_empty_override() -> None:
    """値が空のキーは上書きに使わない (空文字で既定を潰さない)。"""
    by_key = {"stock_as_of": "2026-07-22", "wealthnavi_as_of": ""}
    assert holdings._resolve_as_of("WealthNavi", by_key, "2026-07-22") == (
        "2026-07-22",
        "stock_as_of",
    )


def test_load_holdings_assigns_per_class_as_of(report_file) -> None:
    """銘柄ごとに、その口座の資産クラスの as_of が付く。"""
    report_file(_report())
    by_ticker = {h["ticker"]: h for h in holdings.load_holdings()}
    assert by_ticker["9999"]["as_of"] == "2026-07-22"
    assert by_ticker["9999"]["as_of_source"] == "stock_as_of"
    assert by_ticker["VTI"]["as_of"] == "2026-06-30"
    assert by_ticker["VTI"]["as_of_source"] == "wealthnavi_as_of"


def test_load_holdings_without_market_section(report_file) -> None:
    """market セクションが無くても stock.as_of で動く (後方互換)。"""
    data = _report()
    del data["market"]
    report_file(data)
    assert {h["as_of"] for h in holdings.load_holdings()} == {"2026-07-22"}
