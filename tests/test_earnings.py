"""lib/earnings.py の決算注記のテスト (ネットワークアクセスなし)。

決算注記は「黙って進めない」ための仕掛け。文言が変わると判断の前提が
伝わらなくなるので、警告の有無を明示的に固定する。
"""

from __future__ import annotations

import datetime as dt

import pytest

from lib import earnings


def _patch_earnings(monkeypatch: pytest.MonkeyPatch, day: dt.date | None) -> None:
    """fetch_next_earnings を差し替える (ネットワークに出ないようにする)。

    注記の実体は lib/earnings.py にあり、analyze.py は再エクスポートしているだけ。
    差し替えるのは実体側でなければ効かない。
    """
    monkeypatch.setattr(earnings, "fetch_next_earnings", lambda ticker, market: day)


def _patch_instrument_type(monkeypatch: pytest.MonkeyPatch, kind: str | None) -> None:
    """fetch_instrument_type を差し替える (ネットワークに出ないようにする)。

    Args:
        kind: "etf" / "equity" / 判定できなかった場合の None。
    """
    monkeypatch.setattr(earnings, "fetch_instrument_type", lambda ticker, market: kind)


def test_earnings_note_empty_for_etf(monkeypatch: pytest.MonkeyPatch) -> None:
    """ETF は決算の概念が無いので行を出さない。"""
    _patch_earnings(monkeypatch, None)
    _patch_instrument_type(monkeypatch, "etf")
    assert earnings.earnings_note("VTI", "us") == ""


def test_earnings_note_unknown_for_equity(monkeypatch: pytest.MonkeyPatch) -> None:
    """個別株で決算日が取れなければ「不明」を出す。

    ここが空文字だと、読み手は「決算が無い」と「取得できなかった」を
    区別できない。2026-08-10 に決算反応をテクニカルの進捗として読んだ事故の再発防止。
    """
    _patch_earnings(monkeypatch, None)
    _patch_instrument_type(monkeypatch, "equity")
    note = earnings.earnings_note("9999", "jp")
    assert note == earnings.UNAVAILABLE_NOTE
    assert note.startswith("⚠")
    assert "不明" in note


def test_earnings_note_unknown_when_type_undetermined(monkeypatch: pytest.MonkeyPatch) -> None:
    """種別を判定できなかった場合も「不明」を出す (ETF と確定していない側に倒す)。"""
    _patch_earnings(monkeypatch, None)
    _patch_instrument_type(monkeypatch, None)
    assert earnings.earnings_note("9999", "jp") == earnings.UNAVAILABLE_NOTE


def test_earnings_note_skips_instrument_type_when_date_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """決算日が取れたら種別は取りに行かない (1 銘柄 1 リクエスト増えるため)。"""

    def _boom(ticker: str, market: str) -> str:
        raise AssertionError("決算日が取れているのに種別を取得した")

    _patch_earnings(monkeypatch, dt.date.today())
    monkeypatch.setattr(earnings, "fetch_instrument_type", _boom)
    assert earnings.earnings_note("9999", "jp").startswith("⚠")


def test_earnings_note_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """当日決算は警告付きで「本日」と出る。"""
    today = dt.date.today()
    _patch_earnings(monkeypatch, today)
    note = earnings.earnings_note("9999", "jp")
    assert note.startswith("⚠")
    assert "(本日)" in note
    assert "ギャップでトリガーが飛びうる" in note


def test_earnings_note_upcoming_within_alert_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """警告期間内の決算前はギャップ注意を出す。"""
    day = dt.date.today() + dt.timedelta(days=earnings.EARNINGS_ALERT_DAYS)
    _patch_earnings(monkeypatch, day)
    note = earnings.earnings_note("9999", "jp")
    assert note.startswith("⚠")
    assert f"あと {earnings.EARNINGS_ALERT_DAYS} 日" in note
    assert "ギャップでトリガーが飛びうる" in note


def test_earnings_note_far_future_has_no_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """警告期間外は日付だけを出す (毎回警告すると警告が効かなくなる)。"""
    _patch_earnings(monkeypatch, dt.date.today() + dt.timedelta(days=89))
    note = earnings.earnings_note("AAPL", "us")
    assert not note.startswith("⚠")
    assert "あと 89 日" in note


def test_earnings_note_recent_past_warns_about_reaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直近の決算後は、値動きを決算反応として疑うよう促す。"""
    _patch_earnings(monkeypatch, dt.date.today() - dt.timedelta(days=1))
    note = earnings.earnings_note("9999", "jp")
    assert note.startswith("⚠")
    assert "直近決算" in note
    assert "1 日前" in note
    assert "決算反応の可能性" in note


def test_earnings_note_old_past_has_no_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """十分過去の決算は警告しない。"""
    _patch_earnings(monkeypatch, dt.date.today() - dt.timedelta(days=30))
    note = earnings.earnings_note("9999", "jp")
    assert not note.startswith("⚠")
    assert "30 日前" in note
