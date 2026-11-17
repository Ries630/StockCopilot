"""lib/earnings.py の決算注記のテスト (ネットワークアクセスなし)。

決算注記は「黙って進めない」ための仕掛け。文言が変わると判断の前提が
伝わらなくなるので、警告の有無を明示的に固定する。
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from lib import earnings


@pytest.fixture(autouse=True)
def _isolate_seen(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """決算日の記録を実行環境の data/ に書かせない (結果を実行環境に依存させない)。"""
    monkeypatch.setattr(earnings, "EARNINGS_SEEN_PATH", tmp_path / "earnings_seen.json")


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


# --- 決算日の記録: 決算直後に次回の予定日へ切り替わっても警告を落とさない ---

NOV10 = dt.date(2026, 11, 10)
FEB10 = dt.date(2027, 2, 10)


def test_rollover_after_earnings_keeps_reaction_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """前日に見た決算日が、翌日の取得で次回の予定日に変わっても直近決算の警告を出す。"""
    _patch_earnings(monkeypatch, NOV10)
    earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 9))
    _patch_earnings(monkeypatch, FEB10)
    note = earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 11))
    assert note == (
        "⚠ 直近決算 2026-11-10 (1 日前) — 直近の値動きは決算反応の可能性 (次回 2027-02-10)"
    )


def test_rollover_warning_lasts_through_alert_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """切り替わった後の実行でも、警告期間の終わりまで記録から警告を出し続ける。"""
    _patch_earnings(monkeypatch, NOV10)
    earnings.earnings_note("9999", "jp", today=NOV10)
    _patch_earnings(monkeypatch, FEB10)
    last = NOV10 + dt.timedelta(days=earnings.EARNINGS_ALERT_DAYS)
    earnings.earnings_note("9999", "jp", today=NOV10 + dt.timedelta(days=1))
    assert earnings.earnings_note("9999", "jp", today=last).startswith("⚠ 直近決算 2026-11-10")
    after = earnings.earnings_note("9999", "jp", today=last + dt.timedelta(days=1))
    assert after.startswith("決算 2027-02-10")


def test_rollover_when_date_becomes_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """決算直後に取得できなくなっても、「不明」ではなく記録した決算日で警告する。"""
    _patch_earnings(monkeypatch, NOV10)
    earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 9))
    _patch_earnings(monkeypatch, None)
    _patch_instrument_type(monkeypatch, "equity")
    note = earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 12))
    assert note == "⚠ 直近決算 2026-11-10 (2 日前) — 直近の値動きは決算反応の可能性"


def test_upcoming_earnings_wins_over_recent_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """取得値自体が警告期間内なら取得値で注記する (記録はその取りこぼしを補うだけ)。"""
    _patch_earnings(monkeypatch, NOV10)
    earnings.earnings_note("9999", "jp", today=NOV10)
    _patch_earnings(monkeypatch, dt.date(2026, 11, 13))
    note = earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 11))
    assert note.startswith("⚠ 決算 2026-11-13 (あと 2 日)")


def test_records_are_per_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    """記録は市場とティッカーごとに分け、別の銘柄の決算日で警告しない。"""
    _patch_earnings(monkeypatch, NOV10)
    earnings.earnings_note("9999", "jp", today=NOV10)
    _patch_earnings(monkeypatch, FEB10)
    assert earnings.earnings_note("8888", "jp", today=dt.date(2026, 11, 11)).startswith("決算 2027")


def test_old_records_are_pruned(monkeypatch: pytest.MonkeyPatch) -> None:
    """警告期間より前に過ぎた決算日は記録から消す (記録を際限なく増やさない)。"""
    _patch_earnings(monkeypatch, NOV10)
    earnings.earnings_note("9999", "jp", today=NOV10)
    _patch_earnings(monkeypatch, FEB10)
    earnings.earnings_note("9999", "jp", today=dt.date(2026, 12, 1))
    recorded = json.loads(earnings.EARNINGS_SEEN_PATH.read_text(encoding="utf-8"))
    assert recorded == {"jp:9999": ["2027-02-10"]}


def test_broken_record_does_not_stop_the_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """記録が壊れていても注記は止めず、取得値で判定したことを警告する。"""
    earnings.EARNINGS_SEEN_PATH.write_text("{broken", encoding="utf-8")
    _patch_earnings(monkeypatch, FEB10)
    note = earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 11))
    assert note.startswith("決算 2027-02-10")
    assert "決算日の記録を読めない" in capsys.readouterr().err


def test_moved_up_schedule_is_not_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    """推定日が前倒しの確定日に置き換わったら、推定日では警告しない (実在しない決算日)。"""
    _patch_earnings(monkeypatch, dt.date(2026, 11, 20))
    earnings.earnings_note("9999", "jp", today=dt.date(2026, 10, 20))
    _patch_earnings(monkeypatch, dt.date(2026, 11, 4))
    earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 1))
    _patch_earnings(monkeypatch, FEB10)
    assert earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 21)).startswith(
        "決算 2027-02-10"
    )


def test_postponed_schedule_is_not_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    """延期で置き換わった元の予定日を、過ぎたあとに「直近決算」と扱わない (まだ決算前)。"""
    _patch_earnings(monkeypatch, NOV10)
    earnings.earnings_note("9999", "jp", today=dt.date(2026, 10, 20))
    _patch_earnings(monkeypatch, dt.date(2026, 11, 25))
    earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 1))
    note = earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 12))
    assert note == "決算 2026-11-25 (あと 13 日)"


def test_same_day_rollover_keeps_gap_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """決算の当日に取得値が次回日へ切り替わっても、当日のギャップ警告を出す。"""
    _patch_earnings(monkeypatch, NOV10)
    earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 9))
    _patch_earnings(monkeypatch, FEB10)
    note = earnings.earnings_note("9999", "jp", today=NOV10)
    assert note == "⚠ 決算 2026-11-10 (本日) — ギャップでトリガーが飛びうる (次回 2027-02-10)"


def test_same_day_unavailable_keeps_gap_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """決算の当日に取得できなくなっても、「不明」ではなく当日の警告を出す。"""
    _patch_earnings(monkeypatch, NOV10)
    earnings.earnings_note("9999", "jp", today=dt.date(2026, 11, 9))
    _patch_earnings(monkeypatch, None)
    _patch_instrument_type(monkeypatch, "equity")
    note = earnings.earnings_note("9999", "jp", today=NOV10)
    assert note == "⚠ 決算 2026-11-10 (本日) — ギャップでトリガーが飛びうる"
