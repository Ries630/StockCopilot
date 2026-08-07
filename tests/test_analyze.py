"""analyze.py の表示ロジックのテスト (ネットワークアクセスなし)。

決算日と保有データ鮮度の注記は、どちらも「黙って進めない」ための仕掛け。
文言が変わると判断の前提が伝わらなくなるので、警告の有無を明示的に固定する。
"""

from __future__ import annotations

import datetime as dt

import pytest

import analyze


def _patch_earnings(monkeypatch: pytest.MonkeyPatch, day: dt.date | None) -> None:
    """fetch_next_earnings を差し替える (ネットワークに出ないようにする)。"""
    monkeypatch.setattr(analyze, "fetch_next_earnings", lambda ticker, market: day)


def test_earnings_note_empty_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """決算日が取れなければ行を出さない (ETF など)。"""
    _patch_earnings(monkeypatch, None)
    assert analyze.earnings_note("VTI", "us") == ""


def test_earnings_note_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """当日決算は警告付きで「本日」と出る。"""
    today = dt.date.today()
    _patch_earnings(monkeypatch, today)
    note = analyze.earnings_note("9999", "jp")
    assert note.startswith("⚠")
    assert "(本日)" in note
    assert "ギャップでトリガーが飛びうる" in note


def test_earnings_note_upcoming_within_alert_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """警告期間内の決算前はギャップ注意を出す。"""
    day = dt.date.today() + dt.timedelta(days=analyze.EARNINGS_ALERT_DAYS)
    _patch_earnings(monkeypatch, day)
    note = analyze.earnings_note("9999", "jp")
    assert note.startswith("⚠")
    assert f"あと {analyze.EARNINGS_ALERT_DAYS} 日" in note
    assert "ギャップでトリガーが飛びうる" in note


def test_earnings_note_far_future_has_no_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """警告期間外は日付だけを出す (毎回警告すると警告が効かなくなる)。"""
    _patch_earnings(monkeypatch, dt.date.today() + dt.timedelta(days=89))
    note = analyze.earnings_note("AAPL", "us")
    assert not note.startswith("⚠")
    assert "あと 89 日" in note


def test_earnings_note_recent_past_warns_about_reaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直近の決算後は、値動きを決算反応として疑うよう促す。"""
    _patch_earnings(monkeypatch, dt.date.today() - dt.timedelta(days=1))
    note = analyze.earnings_note("9999", "jp")
    assert note.startswith("⚠")
    assert "直近決算" in note
    assert "1 日前" in note
    assert "決算反応の可能性" in note


def test_earnings_note_old_past_has_no_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """十分過去の決算は警告しない。"""
    _patch_earnings(monkeypatch, dt.date.today() - dt.timedelta(days=30))
    note = analyze.earnings_note("9999", "jp")
    assert not note.startswith("⚠")
    assert "30 日前" in note


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
