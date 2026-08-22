"""市場別の確定足比較と合流のテスト。"""

from pathlib import Path

import pytest

from lib.market_observation import (
    active_markets,
    candidate_zero_markets,
    compare_bars,
    load_previous_bars,
    market_from_currency,
    merge_market_results,
    parse_legacy_bar_dates,
)


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [
        (
            {"jp": "2026-08-21", "us": "2026-08-20"},
            {"jp": "2026-08-20", "us": "2026-08-19"},
            {"jp": "updated", "us": "updated"},
        ),
        (
            {"jp": "2026-08-20", "us": "2026-08-20"},
            {"jp": "2026-08-20", "us": "2026-08-19"},
            {"jp": "unchanged", "us": "updated"},
        ),
        (
            {"jp": "2026-08-21", "us": "2026-08-19"},
            {"jp": "2026-08-20", "us": "2026-08-19"},
            {"jp": "updated", "us": "unchanged"},
        ),
        (
            {"jp": "2026-08-20", "us": "2026-08-19"},
            {"jp": "2026-08-20", "us": "2026-08-19"},
            {"jp": "unchanged", "us": "unchanged"},
        ),
    ],
)
def test_compare_bars_four_update_patterns(current, previous, expected) -> None:
    status = compare_bars(current, previous)
    assert {market: item["status"] for market, item in status.items()} == expected


def test_compare_bars_initial_and_unavailable() -> None:
    status = compare_bars({"jp": "2026-08-20"}, {})
    assert status == {
        "jp": {"status": "initial"},
        "us": {"status": "unavailable"},
    }
    assert active_markets(status) == {"jp"}


def test_compare_bars_rejects_regression() -> None:
    with pytest.raises(ValueError, match="後退"):
        compare_bars({"jp": "2026-08-19"}, {"jp": "2026-08-20"})


def test_candidate_zero_needs_active_market_and_evaluated_population() -> None:
    screen = {
        "jp": {"evaluated": 0, "matched": 0},
        "us": {"evaluated": 3, "matched": 0},
    }
    status = {
        "jp": {"status": "updated"},
        "us": {"status": "unchanged", "previous": "2026-08-19"},
    }
    assert candidate_zero_markets(screen, status) == []

    status["us"] = {"status": "updated", "previous": "2026-08-19"}
    assert candidate_zero_markets(screen, status) == ["us"]


def test_holding_market_is_decided_from_currency_in_one_place() -> None:
    assert market_from_currency("JPY") == "jp"
    assert market_from_currency("USD") == "us"
    with pytest.raises(ValueError, match="決定できない"):
        market_from_currency("EUR")


def test_merge_keeps_updated_market_and_freezes_unchanged_market() -> None:
    previous = {
        "holdings": [
            {"ticker": "JP-OLD", "currency": "JPY"},
            {"ticker": "US-OLD", "currency": "USD"},
        ],
        "candidates": [
            {"ticker": "JP-OLD-C", "market": "jp"},
            {"ticker": "US-OLD-C", "market": "us"},
        ],
    }
    current = {
        "holdings": [
            {"ticker": "JP-NEW", "currency": "JPY"},
            {"ticker": "US-NEW", "currency": "USD"},
        ],
        "candidates": [
            {"ticker": "JP-NEW-C", "market": "jp"},
            {"ticker": "US-NEW-C", "market": "us"},
        ],
        "summary": "今回",
    }
    status = {
        "jp": {"status": "unchanged", "previous": "2026-08-20"},
        "us": {"status": "updated", "previous": "2026-08-19"},
    }

    merged = merge_market_results(previous, current, status)

    assert [item["ticker"] for item in merged["holdings"]] == ["JP-OLD", "US-NEW"]
    assert [item["ticker"] for item in merged["candidates"]] == ["JP-OLD-C", "US-NEW-C"]
    assert merged["summary"] == "今回"


def test_latest_takes_priority_over_legacy_journal(tmp_path: Path) -> None:
    latest = tmp_path / "latest.json"
    latest.write_text('{"bars":{"jp":"2026-08-20","us":"2026-08-19"}}')
    journal = tmp_path / "journal.md"
    journal.write_text("## 2026-08-18\n確定足 JP 2026-08-18 / US 2026-08-17")
    assert load_previous_bars(latest, journal) == {
        "jp": "2026-08-20",
        "us": "2026-08-19",
    }


def test_latest_keeps_previous_bar_after_unavailable_market(tmp_path: Path) -> None:
    latest = tmp_path / "latest.json"
    latest.write_text(
        '{"bars":{"us":"2026-08-20"},'
        '"bar_status":{"jp":{"status":"unavailable","previous":"2026-08-19"}}}'
    )

    assert load_previous_bars(latest, tmp_path / "journal.md") == {
        "jp": "2026-08-19",
        "us": "2026-08-20",
    }


def test_legacy_journal_fallback_reads_market_dates(tmp_path: Path) -> None:
    journal = tmp_path / "journal.md"
    journal.write_text(
        "## 2026-08-10\n確定足は JP・US とも 8/7 引け。\n"
        "## 2026-08-13\n確定足 JP 2026-08-12 / US 2026-08-11。\n"
    )
    assert load_previous_bars(tmp_path / "missing.json", journal) == {
        "jp": "2026-08-12",
        "us": "2026-08-11",
    }


def test_legacy_parser_reads_old_month_day_shape() -> None:
    assert parse_legacy_bar_dates(
        "## 2026-08-07\nJP は 8/6 引け、US は 8/5 引けの確定足\n"
    ) == {"jp": "2026-08-06", "us": "2026-08-05"}
