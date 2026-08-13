"""lib/journal.py の執行記録パーサのテスト (ネットワークアクセス・実ジャーナルに依存しない)。

ジャーナルは人が書く Markdown なので、書式ゆれで保有の除外が静かに不完全になるのが
最悪の失敗になる。ここでは「読める書式」と「読めなかったときに警告が出ること」の
両方を固定する。読める書式の正は journal/README.md にあり、このファイルはその実装の検証。

実ジャーナル (journal/journal.md) は追跡対象外で CI には存在しないため、
テストは常に文字列か tmp_path のファイルを入力にする。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from lib import journal

# 執行の節を含む最小のジャーナル。書式は journal/README.md のテンプレートに合わせる。
_JOURNAL = """\
## 2026-08-05 (Investment as_of=2026-07-22)

- **1111** テスト銘柄 | 現値 ¥1,000 | 判断: ホールド

### スクリーニング (母集団 10 銘柄 / market=all)

- **3333** | ¥500 | score 1.2 ATR | 20 日レンジを上に突破

### 執行

- 2026-08-05 | **1111** テスト銘柄 | 売却 100株 @ ¥1,000 | 約定代金 ¥100,000 | **残 200株**
- 2026-08-05 | **AAPL** テスト ETF | 購入 5株 @ $200 | 約定代金 $1,000 | **残 5株**
"""


def test_reads_date_ticker_and_remaining() -> None:
    """執行の行から約定日・銘柄・残株数を読む。"""
    executions, warnings = journal.parse_executions(_JOURNAL)
    assert warnings == []
    assert [(e.date, e.ticker, e.remaining) for e in executions] == [
        (dt.date(2026, 8, 5), "1111", 200.0),
        (dt.date(2026, 8, 5), "AAPL", 5.0),
    ]


def test_ignores_lines_outside_execution_section() -> None:
    """`### 執行` の外の行は拾わない (候補や保有分析の行を執行と誤認しない)。"""
    executions, _ = journal.parse_executions(_JOURNAL)
    assert {e.ticker for e in executions} == {"1111", "AAPL"}


def test_next_heading_ends_the_section() -> None:
    """次の見出しに入った時点で執行の節は終わる。"""
    text = "### 執行\n\n- 2026-08-05 | **1111** X | 売却 | **残 0株**\n\n## 2026-08-06\n\n- 2026-08-06 | **2222** Y | 売却 | **残 0株**\n"
    executions, _ = journal.parse_executions(text)
    assert [e.ticker for e in executions] == ["1111"]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("- 2026-08-05 | **1111** X | 売却 | **残 1,000株**", 1000.0),  # コンマ区切り
        ("- 2026-08-05 | **1111** X | 売却 | 残 0株", 0.0),  # 売り切り・装飾なし
        ("- 2026-08-05 | **VTI** X | 売却 | **残 1.5株**", 1.5),  # 小数株 (ロボアド)
        ("- 2026-08-05 | **1111** X | 売却 | **残200株**", 200.0),  # 空白なし
    ],
)
def test_remaining_shares_variants(line: str, expected: float) -> None:
    """残株数はコンマ・小数・空白の有無を許容する。"""
    executions, warnings = journal.parse_executions(f"### 執行\n{line}\n")
    assert warnings == []
    assert executions[0].remaining == expected


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("**1111** テスト銘柄", "1111"),
        ("1111 テスト銘柄", "1111"),  # 太字なし
        ("**1111**テスト銘柄", "1111"),  # 銘柄名が続く
        ("**aapl** テスト", "AAPL"),  # 小文字は正規化する
        ("**BRK.B** テスト", "BRK.B"),  # ピリオドを含むティッカー
    ],
)
def test_ticker_variants(field: str, expected: str) -> None:
    """銘柄欄は装飾と銘柄名の有無にかかわらず先頭のティッカーを読む。"""
    executions, warnings = journal.parse_executions(
        f"### 執行\n- 2026-08-05 | {field} | 売却 | **残 1株**\n"
    )
    assert warnings == []
    assert executions[0].ticker == expected


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("- 2026-08-05 | **1111** X | 売却 100株 @ ¥1,000", "残株数"),
        ("- **1111** X | 売却 | **残 0株**", "約定日"),
        ("- 2026-08-05 | テストファンド | 売却 | **残 0株**", "ティッカー"),
        ("- 2026-13-45 | **1111** X | 売却 | **残 0株**", "日付"),
    ],
)
def test_unparsable_lines_become_warnings(line: str, reason: str) -> None:
    """解釈できない行は例外にせず警告にする (書式ゆれでスクリーナーを止めない)。"""
    executions, warnings = journal.parse_executions(f"### 執行\n{line}\n")
    assert executions == []
    assert len(warnings) == 1
    assert reason in warnings[0]
    assert warnings[0].startswith("2 行目")


def test_date_comes_from_the_section_heading() -> None:
    """約定日は節の見出し (`#### 執行 (YYYY-MM-DD)`) からも取る。

    節に 1 日ぶんしか書かないなら日付は見出しに 1 回でよく、実際そう書かれた
    エントリがある。過去エントリは書き換えない規約なので読む側で吸収する。
    """
    text = (
        "#### 執行 (2026-08-10)\n\n"
        "- **1111** テスト銘柄 | **100株を ¥1,000 で売却**\n"
        "  - **残 200株。** 集中は緩んだが依然として最大\n"
    )
    executions, warnings = journal.parse_executions(text)
    assert warnings == []
    assert (executions[0].date, executions[0].ticker, executions[0].remaining) == (
        dt.date(2026, 8, 10),
        "1111",
        200.0,
    )


def test_bullet_without_pipe_is_not_an_execution() -> None:
    """`|` 区切りでない箇条書きは執行の行ではない (節に書いた補足)。警告にもしない。"""
    text = (
        "### 執行\n\n"
        "- **本日の執行報告なし。** 実効保有は 2026-08-10 の\n"
        "  1111 テスト銘柄 100株売却 (残 200株) の時点から変わっていない\n"
    )
    assert journal.parse_executions(text) == ([], [])


def test_planned_execution_without_remaining_warns() -> None:
    """残株数の無い行は警告にする (執行の節に書かれた「予定」を執行にしない)。"""
    text = (
        "#### 執行 (2026-08-10)\n\n"
        "- **1111** テスト銘柄 | **整理予定**\n"
        "  - 執行後は判断対象から外す\n"
    )
    executions, warnings = journal.parse_executions(text)
    assert executions == []
    assert "残株数" in warnings[0]


def test_remaining_does_not_leak_into_the_next_item() -> None:
    """続きの行の残株数を次の 1 件に持ち越さない。"""
    text = (
        "#### 執行 (2026-08-10)\n\n"
        "- **1111** A | 売却\n"
        "  - **残 200株**\n"
        "- **2222** B | 売却\n"
    )
    executions, warnings = journal.parse_executions(text)
    assert [e.ticker for e in executions] == ["1111"]
    assert "残株数" in warnings[0]


def test_warning_does_not_contain_the_line_itself() -> None:
    """警告に行の内容を載せない (保有情報を warning 経由で持ち回さない)。"""
    _, warnings = journal.parse_executions(
        "### 執行\n- 2026-08-05 | テスト投信 秘密のファンド | 売却 | **残 0株**\n"
    )
    assert "ファンド" not in warnings[0]


def test_valid_lines_survive_an_unparsable_one() -> None:
    """1 行が壊れても、他の行の執行は落とさない。"""
    text = (
        "### 執行\n"
        "- 2026-08-05 | **1111** X | 売却 100株 @ ¥1,000\n"
        "- 2026-08-06 | **2222** Y | 売却 | **残 0株**\n"
    )
    executions, warnings = journal.parse_executions(text)
    assert [e.ticker for e in executions] == ["2222"]
    assert len(warnings) == 1


def test_prose_in_the_section_is_not_a_warning() -> None:
    """箇条書きでない行 (補足の散文) は執行ではないので警告にしない。"""
    executions, warnings = journal.parse_executions("### 執行\n\nNISA 口座での売却。\n")
    assert executions == []
    assert warnings == []


def test_load_executions_reads_file(tmp_path: Path) -> None:
    """ファイルから読める。"""
    path = tmp_path / "journal.md"
    path.write_text(_JOURNAL, encoding="utf-8")
    executions, warnings = journal.load_executions(path)
    assert len(executions) == 2
    assert warnings == []


def test_missing_journal_is_not_an_error(tmp_path: Path) -> None:
    """ジャーナルが無いのは正常 (追跡対象外で、初回運用や CI には存在しない)。"""
    assert journal.load_executions(tmp_path / "no_such_journal.md") == ([], [])
