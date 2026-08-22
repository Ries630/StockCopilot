"""市場別結果を最終JSONへ合流するテスト。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import finalize_report
from finalize_report import finalize
from tests.test_report import base_data, candidate, holding_state, market_screen, position


def test_finalize_freezes_unchanged_market_and_keeps_updated_market() -> None:
    previous = base_data(
        holdings=[position(ticker="1111")],
        candidates=[candidate(ticker="JP-OLD", market="jp", currency="JPY", name="国内候補")],
    )
    current = base_data(
        holdings=[
            holding_state(ticker="1111", shares=50),
            position(ticker="US-H", currency="USD", name="US Holding"),
        ],
        candidates=[candidate(ticker="US-NEW")],
        screen={
            "jp": market_screen(),
            "us": market_screen(matched=1, selected=1),
        },
    )
    current["bar_status"]["jp"] = {
        "status": "unchanged",
        "previous": "2026-08-20",
    }

    result = finalize(current, previous)

    assert [item["ticker"] for item in result["holdings"]] == ["1111", "US-H"]
    assert result["holdings"][0]["shares"] == 50
    assert result["holdings"][0]["analysis_status"] == "carried"
    assert [item["ticker"] for item in result["candidates"]] == ["JP-OLD", "US-NEW"]
    assert any("JP: 確定足は前回から更新なし" in warning for warning in result["warnings"])


def test_finalize_unavailable_market_continues_other_market() -> None:
    previous = base_data(holdings=[position(ticker="1111")])
    current = base_data(
        bars={"us": "2026-08-19"},
        holdings=[holding_state(ticker="1111", shares=75)],
        screen={"jp": market_screen(), "us": market_screen()},
    )
    current["bar_status"]["jp"] = {
        "status": "unavailable",
        "previous": "2026-08-20",
    }

    result = finalize(current, previous)

    assert [item["ticker"] for item in result["holdings"]] == ["1111"]
    assert result["holdings"][0]["shares"] == 75
    assert any("JP: 確定足日を取得できず" in warning for warning in result["warnings"])


def test_both_unchanged_is_not_a_new_actionable_observation() -> None:
    previous = base_data(
        holdings=[
            position(
                verdict="売却",
                signals={"weekly": "bad", "daily": "bad", "overheat": "bad", "volume": "bad"},
            )
        ]
    )
    current = base_data(holdings=[holding_state()])
    current["bar_status"] = {
        "jp": {"status": "unchanged", "previous": "2026-08-20"},
        "us": {"status": "unchanged", "previous": "2026-08-19"},
    }

    result = finalize(current, previous)

    assert result["holdings"][0]["verdict"] == "売却"
    assert result["holdings"][0]["analysis_status"] == "carried"
    assert len(result["warnings"]) == 2


def test_finalize_rejects_missing_current_required_collection() -> None:
    current = base_data()
    del current["holdings"]

    with pytest.raises(KeyError, match="holdings"):
        finalize(current, None)


@pytest.mark.parametrize("missing", ["holdings", "candidates"])
def test_finalize_rejects_broken_previous_schema_v2(missing: str) -> None:
    previous = base_data()
    del previous[missing]

    with pytest.raises(KeyError, match=missing):
        finalize(base_data(), previous)


def test_finalize_drops_sold_holding_and_keeps_new_state_without_analysis() -> None:
    previous = base_data(holdings=[position(ticker="OLD")])
    current = base_data(
        holdings=[holding_state(ticker="NEW", currency="USD", name="New Holding")]
    )
    current["bar_status"] = {
        "jp": {"status": "unchanged", "previous": "2026-08-20"},
        "us": {"status": "unchanged", "previous": "2026-08-19"},
    }

    result = finalize(current, previous)

    assert [item["ticker"] for item in result["holdings"]] == ["NEW"]
    assert result["holdings"][0]["analysis_status"] == "unavailable"


def test_cli_writes_valid_final_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft = tmp_path / "brief.draft.json"
    out = tmp_path / "brief.json"
    draft.write_text(json.dumps(base_data()), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["finalize_report.py", str(draft), "-o", str(out)],
    )

    finalize_report.main()

    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == 2
