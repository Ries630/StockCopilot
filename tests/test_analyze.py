"""analyze.py の保有ヘッダのテスト (ネットワークアクセスなし)。

保有データの鮮度警告は「黙って進めない」ための仕掛け。文言が変わると
判断の前提が伝わらなくなるので、警告の有無を明示的に固定する。
決算注記のテストは test_earnings.py にある。
"""

from __future__ import annotations

import datetime as dt

import pytest

import analyze


def test_holdings_header_fresh(capsys: pytest.CaptureFixture[str]) -> None:
    """鮮度が十分なら警告を出さない。"""
    as_of = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    analyze.print_holdings_header([{"as_of": as_of}, {"as_of": as_of}])
    out = capsys.readouterr().out
    assert "保有 2 銘柄" in out
    assert "(2 日前)" in out
    assert "⚠" not in out


def test_holdings_header_stale_warns(capsys: pytest.CaptureFixture[str]) -> None:
    """STALE_DAYS を超えたら増減判定を保留するよう警告する。"""
    days = analyze.STALE_DAYS + 1
    as_of = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    analyze.print_holdings_header([{"as_of": as_of}])
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "増減の判定には古い" in out


@pytest.mark.parametrize("holding", [{"as_of": ""}, {"as_of": None}, {}])
def test_holdings_header_unparseable_as_of(
    holding: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    """as_of が読めない場合も黙って進めず、保留を促す。"""
    analyze.print_holdings_header([holding])
    out = capsys.readouterr().out
    assert "不明" in out
    assert "増減の判定は保留すること" in out
