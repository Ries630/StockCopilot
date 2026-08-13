"""ジャーナル (journal/journal.md) の執行記録を読む。

**書式の正は `journal/README.md` の「執行の記録」**。ここはその実装であって
仕様ではないので、書式の説明をこのファイルに写経しない。

機械が読むのは各行の **約定日 / 銘柄 / 残株数の 3 つだけ** ([ADR-0015](
../docs/adr/0015-journal-executions-machine-read.md))。売買別・単価・約定代金は読まない。
除外判定に必要なのは「残っているか」だけで、読む項目を増やすほど書式ゆれで
壊れる箇所が増えるため。残株数は「1 件につき必ず書く / その行だけで残高が確定する」と
書式側で定められており、最も規律が効いている項目でもある。

**既に書かれた揺れは読む側で吸収する。** 約定日は行頭に無ければ節の見出し
(`#### 執行 (2026-08-10)`) から取り、残株数は字下げされた続きの行まで探す。
ジャーナルは追記専用で過去エントリを書き換えない規約があるため、
書式の正 (journal/README.md) に合わない過去の記録も読めなければ台帳として使えない。

ジャーナル本体は追跡対象外 ([ADR-0008](../docs/adr/0008-no-holdings-in-repo.md)) なので、
**ファイルが存在しないのは正常**。その場合は執行 0 件として扱う。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

# cwd に依存させない (screen.py はリポジトリ外から呼ばれうる)。
JOURNAL_PATH = Path(__file__).resolve().parent.parent / "journal" / "journal.md"

# 見出し行。`### 執行` の配下の行だけを対象にする。
# 行だけを見て拾うと、保有分析やスクリーニングの行を執行と誤認しうる。
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*$")
_EXECUTION_HEADING = "執行"

# 執行の見出しに日付が付く書き方 (`#### 執行 (2026-08-10)`) を許容する。
# 節に 1 日ぶんしか書かないなら日付は見出しに 1 回でよく、実際そう書かれた
# エントリがある。過去エントリは書き換えない規約なので、読む側で吸収する
_HEADING_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# 執行 1 件の始まり。インデントの無い箇条書きだけが 1 件の開始で、
# 字下げされた箇条書きは同じ 1 件の続き (残株数を子の行に書く書き方がある)。
_ITEM_RE = re.compile(r"^[-*]\s+(.*)$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\b")

# 銘柄欄の先頭 (`**9999** 銘柄名` / `AAPL`)。装飾の `*` は落とす。
# 末尾の否定先読みは「ティッカーがそこで終わっている」ことの確認で、
# 銘柄名が続いていても (`**9999**銘柄名`) ティッカーだけを取る。
_TICKER_RE = re.compile(r"^\**([A-Za-z0-9][A-Za-z0-9.\-]{0,9})\**(?![A-Za-z0-9.\-])")

# `**残 200株**` / `残 1,000 株` / `残0株`。小数株 (ロボアド等) も取る。
_REMAIN_RE = re.compile(r"残\s*([\d,]+(?:\.\d+)?)\s*株")


@dataclass(frozen=True)
class Execution:
    """執行 1 件。機械が読む 3 項目のみを持つ。

    Attributes:
        date: 約定日。
        ticker: 銘柄 (大文字に正規化済み)。
        remaining: 執行後の残株数。0 なら売り切り。
        line_no: ジャーナル内の行番号 (1 始まり)。警告の指し示し用。
    """

    date: dt.date
    ticker: str
    remaining: float
    line_no: int


def parse_executions(text: str) -> tuple[list[Execution], list[str]]:
    """ジャーナルの本文から執行記録を取り出す。

    解釈できない行で例外を投げない。黙って捨てるのでもなく警告として返す。
    ジャーナルは人が書く Markdown なので、書式ゆれで保有の除外が静かに
    不完全になるのが最悪の失敗になるため。

    Args:
        text: journal.md の全文。

    Returns:
        (執行記録のリスト, 警告のリスト)。記録はファイル内の出現順
        (= 追記順)。警告は "N 行目: 理由" の形で、行の内容は含めない
        (保有情報を warning 経由で持ち回さないため)。
    """
    executions: list[Execution] = []
    warnings: list[str] = []
    in_execution = False
    section_date: dt.date | None = None
    block: list[str] = []
    block_line_no = 0

    def flush() -> None:
        """溜めた 1 件ぶんを解釈して結果に積む。"""
        if not block:
            return
        parsed, reason = _parse_block(block, block_line_no, section_date)
        if parsed is not None:
            executions.append(parsed)
        elif reason:
            warnings.append(f"{block_line_no} 行目: {reason}")
        block.clear()

    for line_no, line in enumerate(text.splitlines(), start=1):
        heading = _HEADING_RE.match(line)
        if heading:
            flush()  # 節をまたぐ前に、溜めている 1 件を閉じる
            title = heading.group(1)
            # 別の見出しに入った時点で執行の節は終わる (同階層でも上位でも)
            in_execution = title.startswith(_EXECUTION_HEADING)
            found = _HEADING_DATE_RE.search(title) if in_execution else None
            section_date = _to_date(found.group(1)) if found else None
            continue
        if not in_execution:
            continue
        item = _ITEM_RE.match(line)
        if item:
            flush()
            block.append(item.group(1))
            block_line_no = line_no
        elif block:
            block.append(line.strip())  # 字下げされた続きの行 (残株数を子に書く書き方)
    flush()

    return executions, warnings


def _parse_block(
    block: list[str], line_no: int, section_date: dt.date | None
) -> tuple[Execution | None, str]:
    """執行 1 件 (項目行 + 続きの行) を解釈する。

    約定日は行頭に無ければ節の見出しから取る。残株数は続きの行まで探す。
    書式の正は `journal/README.md` だが、**過去エントリは書き換えない**規約が
    あるため、既に書かれた揺れは読む側で吸収する。

    Args:
        block: 項目行 (行頭の `- ` を除いた本文) と、それに続く行。
        line_no: 項目行の行番号。
        section_date: 節の見出しから取れた日付 (無ければ None)。

    Returns:
        (Execution, "") または (None, 理由)。理由が空文字なら執行の行ではないので
        警告にもしない (節に書かれた補足の箇条書きがこれにあたる)。
    """
    head = block[0]
    if "|" not in head:
        return None, ""  # 執行の行は `|` 区切り。そうでない箇条書きは補足の散文
    fields = [f.strip() for f in head.split("|")]

    date_match = _DATE_RE.match(fields[0])
    if date_match:
        day = _to_date(date_match.group(1))
        if day is None:
            return None, "約定日が日付として解釈できない"
        ticker_field = fields[1] if len(fields) > 1 else ""
    else:
        day = section_date
        if day is None:
            return None, "約定日が読めない (行頭か、節の見出しに書く)"
        ticker_field = fields[0]

    ticker_match = _TICKER_RE.match(ticker_field)
    if not ticker_match:
        return None, "銘柄欄の先頭からティッカーを読めない"

    remain_match = _REMAIN_RE.search("\n".join(block))
    if not remain_match:
        return None, "残株数 (`残 N株`) が読めない"

    return Execution(
        date=day,
        ticker=ticker_match.group(1).upper(),
        remaining=float(remain_match.group(1).replace(",", "")),
        line_no=line_no,
    ), ""


def _to_date(value: str) -> dt.date | None:
    """ISO 形式の文字列を date にする。解釈できなければ None。

    Args:
        value: "2026-08-05" 形式の文字列。

    Returns:
        date、または解釈できなければ None。
    """
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def load_executions(path: Path | None = None) -> tuple[list[Execution], list[str]]:
    """ジャーナルのファイルから執行記録を読む。

    Args:
        path: ジャーナルのパス。省略時は JOURNAL_PATH。

    Returns:
        (執行記録のリスト, 警告のリスト)。ファイルが無ければ ([], [])。
        無いことは異常ではない (追跡対象外で、初回運用や CI では存在しない)。
    """
    target = path or JOURNAL_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], []
    return parse_executions(text)
