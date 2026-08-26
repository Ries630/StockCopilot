"""lib/holdings.py の as_of 解決と実効保有の合成のテスト (ネットワーク・実保有に依存しない)。

Investment の生成物は資産クラスごとに別の基準日を持つ。これを一律で
stock.as_of に丸めると、別系統で管理されている銘柄が実際より新しく見える。
どの基準日をどの口座に割り当てるかは保有の鮮度判定の前提なので、明示的に固定する。

実効保有 (= as_of 時点の保有 + as_of 以降の執行記録) はスクリーナーの母集団除外に
効くので、どの執行を反映しどれを無視するかを境界ごとに固定する。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from lib import holdings, journal


def test_investment_output_uses_agent_projects_default() -> None:
    """Investment の移転後ディレクトリを既定の入力先にする。"""
    assert holdings.INVESTMENT_OUTPUT == (
        Path.home() / "AgentProjects" / "Investment" / "output"
    )


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


# --- held_tickers: 実効保有の合成 ---


@pytest.fixture(autouse=True)
def _isolate_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """実ジャーナルを読ませない。

    journal.md は追跡対象外で、開発機には存在し CI には存在しない。
    既定のパスのまま走らせると結果が実行環境で変わる。
    """
    monkeypatch.setattr(journal, "JOURNAL_PATH", tmp_path / "absent_journal.md")


def _patch_executions(
    monkeypatch: pytest.MonkeyPatch, *records: tuple[str, str, float], warnings: list[str] | None = None
) -> None:
    """執行記録を差し替える (パースは test_journal.py の担当)。

    Args:
        monkeypatch: pytest の monkeypatch。
        records: (約定日, ティッカー, 残株数) をジャーナルの出現順に並べたもの。
        warnings: 解釈できなかった行の警告。
    """
    executions = [
        journal.Execution(
            date=dt.date.fromisoformat(day), ticker=ticker, remaining=remaining, line_no=i
        )
        for i, (day, ticker, remaining) in enumerate(records, start=1)
    ]
    monkeypatch.setattr(holdings, "load_executions", lambda: (executions, list(warnings or [])))


def test_held_tickers_without_executions(report_file) -> None:
    """執行記録が無ければ Investment の保有そのものになる。"""
    report_file(_report())
    held = holdings.held_tickers()
    assert held.tickers == {"9999", "VTI"}
    assert (held.executions_read, held.executions_applied) == (0, 0)


def test_execution_adds_ticker_bought_after_as_of(
    report_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    """as_of 以降に買った銘柄は保有に入る (実害のある方向の修正)。"""
    report_file(_report())
    _patch_executions(monkeypatch, ("2026-07-25", "5555", 100))
    assert "5555" in holdings.held_tickers().tickers


def test_execution_removes_ticker_sold_out(report_file, monkeypatch: pytest.MonkeyPatch) -> None:
    """残 0 株の執行があれば保有から外れる (母集団に戻る)。"""
    report_file(_report())
    _patch_executions(monkeypatch, ("2026-07-25", "9999", 0))
    assert holdings.held_tickers().tickers == {"VTI"}


def test_execution_before_as_of_is_ignored(report_file, monkeypatch: pytest.MonkeyPatch) -> None:
    """as_of より前の執行は Investment の保有に含まれているので反映しない。"""
    report_file(_report())
    _patch_executions(monkeypatch, ("2026-07-20", "9999", 0))
    held = holdings.held_tickers()
    assert "9999" in held.tickers
    assert (held.executions_read, held.executions_applied) == (1, 0)


def test_execution_on_the_as_of_day_is_applied(
    report_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    """基準日と同日の執行はジャーナルを優先する (誤っても低実害側に倒れる)。"""
    report_file(_report())
    _patch_executions(monkeypatch, ("2026-07-22", "9999", 0))
    assert "9999" not in holdings.held_tickers().tickers


def test_execution_uses_per_class_as_of(report_file, monkeypatch: pytest.MonkeyPatch) -> None:
    """基準日は銘柄ごとに見る。

    VTI の基準日は 2026-06-30 (WealthNavi) なので、stock.as_of (2026-07-22) より
    前の執行でも反映する。既定の基準日で一律に判定すると、別系統で管理されている
    資産クラスの執行を落とすことになる。
    """
    report_file(_report())
    _patch_executions(monkeypatch, ("2026-07-01", "VTI", 0))
    assert holdings.held_tickers().tickers == {"9999"}


def test_latest_execution_wins(report_file, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一銘柄に複数件あれば日付が新しい行が効く (残株数は絶対値)。"""
    report_file(_report())
    _patch_executions(monkeypatch, ("2026-07-26", "9999", 0), ("2026-07-25", "9999", 100))
    assert "9999" not in holdings.held_tickers().tickers


def test_same_day_last_line_wins(report_file, monkeypatch: pytest.MonkeyPatch) -> None:
    """同日に複数件あれば後に書かれた行が効く (ジャーナルは追記専用)。"""
    report_file(_report())
    _patch_executions(monkeypatch, ("2026-07-25", "9999", 0), ("2026-07-25", "9999", 100))
    assert "9999" in holdings.held_tickers().tickers


def test_journal_warnings_are_passed_through(
    report_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    """解釈できなかった行は呼び出し側まで届ける (黙って除外を不完全にしない)。"""
    report_file(_report())
    _patch_executions(monkeypatch, ("2026-07-25", "9999", 0), warnings=["84 行目: 残株数が読めない"])
    held = holdings.held_tickers()
    assert held.warnings == ["84 行目: 残株数が読めない"]
    assert (held.executions_read, held.executions_applied) == (1, 1)


def test_held_tickers_reads_the_journal_file(
    report_file, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既定のパスのジャーナルを実際に読んで合成する (パーサとの結線の確認)。"""
    report_file(_report())
    path = tmp_path / "journal.md"
    path.write_text(
        "## 2026-07-25\n\n### 執行\n\n"
        "- 2026-07-25 | **9999** テスト銘柄 | 売却 100株 @ ¥1,000 | 約定代金 ¥100,000 | **残 0株**\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(journal, "JOURNAL_PATH", path)
    held = holdings.held_tickers()
    assert held.tickers == {"VTI"}
    assert held.executions_applied == 1
