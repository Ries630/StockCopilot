# /// script
# requires-python = ">=3.10"
# dependencies = ["jsonschema>=4.25"]
# ///
"""中間表現 JSON を読み、夕方ブリーフの結果を Slack へ投稿する。

投稿はこのスクリプトが完結させる。**LLM は Slack ツールを呼ばない**
(→ docs/adr/0022-slack-webhook-notification.md)。理由は 2 つ:

1. Slack は自分の投稿では自分に push を鳴らさない。本人 identity で投稿しても
   自己メンションは通知にならないので、アプリ identity の Incoming Webhook を使う
2. 鳴らす条件を LLM の裁量に戻さないため。メンションの発火は中間表現の
   `verdict` の値だけで機械的に決まる

    uv run notify.py reports/2026-08-20_evening.json
    uv run notify.py reports/2026-08-20_evening.json --dry-run   # 送らずに本文を出す

投稿は**毎日行う**。静穏日に何も出ないと「実行されて静穏だった」のか
「実行されなかった」のかが Slack だけでは区別できないため。
メンションだけを、資金が動く判断がある日に絞る。
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import urllib.error
import urllib.request

from lib.contract import validate
from lib.market_observation import active_markets, candidate_observation_labels
from lib.verdicts import actionable_items, observed_items

# .env の置き場所 (リポジトリルート)。gitignore 済み
ENV_PATH = pathlib.Path(__file__).parent / ".env"

WEEKDAY_JA = ("月", "火", "水", "木", "金", "土", "日")
UNKNOWN = "不明"

# Slack の section と context に収まるよう、投稿する全テキストをここで上限化する
SECTION_TEXT_LIMIT = 3000
CONTEXT_TEXT_LIMIT = 2000
SUMMARY_LIMIT = 1200


def load_env(path: pathlib.Path | None = None) -> dict:
    """`.env` を読む (依存を増やさないための最小実装)。

    `KEY=VALUE` の行だけを読む。クォートは剥がし、`#` で始まる行と空行は飛ばす。
    環境変数の上書きはしない — 優先順位は `setting()` が決める。

    既定値をシグネチャに書かず実行時に `ENV_PATH` を見るのは、既定引数が
    定義時に固定されると差し替えが効かなくなるため。

    Args:
        path: `.env` のパス。省略時は `ENV_PATH`。無ければ空 dict を返す。

    Returns:
        キー → 値 の dict。
    """
    path = path or ENV_PATH
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("'\"")
    return env


def setting(name: str, env: dict) -> str:
    """設定値を環境変数 → `.env` の順に探す。

    Args:
        name: 設定名。
        env: `load_env()` の戻り値。

    Returns:
        値 (見つからなければ空文字)。
    """
    return (os.environ.get(name) or env.get(name) or "").strip()


def verdict_tally(items: list, key: str = "verdict") -> str:
    """判断ラベルの内訳を「ホールド 2 / 保留 1」の形にする。

    件数だけでは、静かな日と判断が割れた日が同じに見える。
    内訳を出すことで、開かずに中身の傾向が読める。

    Args:
        items: Position または Candidate のリスト。
        key: 集計するキー。

    Returns:
        内訳の文字列。空リストなら空文字。
    """
    counts: dict[str, int] = {}
    for item in items:
        if item.get("reference_only"):
            label = "対象外"
        elif item.get("analysis_status") == "unavailable":
            label = "分析なし"
        else:
            label = item.get(key) or "?"
        counts[label] = counts.get(label, 0) + 1
    return " / ".join(f"{label} {n}" for label, n in counts.items())


def escape_mrkdwn(value: object) -> str:
    """動的値を Slack の mrkdwn として安全な文字列へ変換する。

    `<@...>` や `<!channel>` を含む分析文が、メンションのゲートを迂回しないようにする。
    Slack が特別に解釈する `&`、`<`、`>` だけをエスケープし、静的に書いた強調記法は
    呼び出し側に残す。

    Args:
        value: 中間表現または設定から来た動的な値。

    Returns:
        Slack の特殊構文として解釈されない文字列。
    """
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def truncate_text(text: str, limit: int) -> str:
    """文字数上限を超えるテキストを省略記号付きで切り詰める。

    Args:
        text: 切り詰める文字列。
        limit: 切り詰め後に許容する最大文字数。

    Returns:
        最大 `limit` 文字の文字列。
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def bounded_lines(lines: list[str], limit: int = SECTION_TEXT_LIMIT) -> str:
    """複数行を Slack の文字数上限内に収める。

    行を途中まで残せる場合は、残りの行数を明示して通知全体の失敗を防ぐ。先頭行そのものが
    長すぎる場合も、その行を切り詰めてから残件数を付ける。

    Args:
        lines: 連結する行。各行は mrkdwn として安全化済みとする。
        limit: 連結後に許容する最大文字数。

    Returns:
        最大 `limit` 文字の改行区切り文字列。
    """
    text = "\n".join(lines)
    if len(text) <= limit:
        return text

    kept: list[str] = []
    for index, line in enumerate(lines):
        candidate = [*kept, line]
        remaining = len(lines) - index - 1
        marker = f"…他 {remaining} 行を省略"
        candidate_text = "\n".join(candidate)
        if not remaining:
            if len(candidate_text) <= limit:
                return candidate_text
        elif len(candidate_text) + 1 + len(marker) <= limit:
            kept = candidate
            continue

        if not kept:
            # 先頭の 1 行だけで上限を超える場合も、必ず上限内のブロックを返す。
            kept.append(truncate_text(line, limit - len(marker) - 1))
            remaining_marker = marker if remaining else "…一部を省略"
            return "\n".join([*kept, remaining_marker])

        return "\n".join([*kept, f"…他 {len(lines) - index} 行を省略"])

    return text


def build_message(data: dict, report_path: str, user_id: str) -> tuple[str, list, bool]:
    """投稿する本文と Block Kit を組み立てる。

    Args:
        data: 中間表現 dict。
        report_path: HTML レポートのパス (本文に出す)。
        user_id: 自己メンションに使う Slack ユーザー ID。空ならメンションしない。

    Returns:
        `(fallback テキスト, blocks, メンションしたか)` のタプル。
    """
    date = str(data.get("date") or UNKNOWN)
    weekday = ""
    try:
        year, month, day = (int(x) for x in date.split("-"))
        weekday = f" ({WEEKDAY_JA[dt.date(year, month, day).weekday()]})"
    except (ValueError, TypeError):
        pass
    clock = str(data.get("generated_at") or "")[11:16] or UNKNOWN

    items = actionable_items(data)
    mentioned = bool(items and user_id)

    lines = []
    if mentioned:
        lines.append(f"<@{user_id}>")
    title = (
        f"🌆 *StockCopilot Evening Brief* — {escape_mrkdwn(date)}{weekday} "
        f"{escape_mrkdwn(clock)} JST"
    )
    lines.append(title)

    if items:
        lines.append(f"🎯 *資金が動く判断 {len(items)} 件*")
    elif not active_markets(data["bar_status"]):
        lines.append("🎯 新規市場観測なし (前回結果を継続・候補ゼロには数えない)")
    else:
        lines.append("🎯 今回更新分に資金が動く判断なし")

    holdings = data["holdings"]
    candidates = data["candidates"]
    updated_holdings = observed_items(data, "holdings")
    updated_candidates = observed_items(data, "candidates")
    lines.append(
        f"📦 保有 {len(holdings)} 銘柄（今回更新 {len(updated_holdings)} 銘柄）: "
        f"{escape_mrkdwn(verdict_tally(holdings) or '—')}"
    )
    lines.append(
        f"🔍 候補 {len(candidates)} 件（今回更新 {len(updated_candidates)} 件）: "
        f"{escape_mrkdwn(verdict_tally(candidates) or '—')}"
    )
    lines.extend(candidate_observation_labels(data["screen"], data["bar_status"]).values())

    status_labels = {
        "updated": "更新",
        "unchanged": "前回と同じ",
        "initial": "初回",
        "unavailable": "取得不能",
    }
    states = data["bar_status"]
    lines.append(
        "🕘 "
        + " / ".join(
            f"{market.upper()} {status_labels[states[market]['status']]}"
            for market in ("jp", "us")
        )
    )

    action_lines = []
    for item in items:
        kind = "保有" if item["kind"] == "holding" else "候補"
        name = escape_mrkdwn(item.get("name", ""))
        name_part = f" {name}" if name else ""
        action_lines.append(
            f"　*{escape_mrkdwn(item['verdict'])}* "
            f"`{escape_mrkdwn(item['ticker'])}`{name_part} ({kind})"
        )

    warning_lines = [f"⚠️ {escape_mrkdwn(warning)}" for warning in data.get("warnings") or []]

    summary = truncate_text(escape_mrkdwn(data.get("summary") or UNKNOWN), SUMMARY_LIMIT)

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": bounded_lines(lines)}},
    ]
    if action_lines:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": bounded_lines(action_lines)}}
        )
    if warning_lines:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": bounded_lines(warning_lines)}}
        )
    if summary:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": summary}})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": truncate_text(
                        f"📄 ローカルレポート: `{escape_mrkdwn(report_path)}`（この端末で開く）",
                        CONTEXT_TEXT_LIMIT,
                    ),
                }
            ],
        }
    )

    # fallback テキストはモバイルの通知プレビューに出る。メンションもここに含める
    fallback = bounded_lines(lines[: 3 if items else 2])
    return fallback, blocks, mentioned


def preview(blocks: list) -> str:
    """Block Kit から、投稿される本文を人が読める形で組み直す。

    --dry-run の確認用。Slack に出るのと同じ文面を目で確かめるためのもの。

    Args:
        blocks: build_message() が返した blocks。

    Returns:
        本文を連結した文字列。
    """
    parts = []
    for block in blocks:
        if "text" in block:
            parts.append(block["text"]["text"])
        for element in block.get("elements", []):
            parts.append(element.get("text", ""))
    return "\n".join(parts)


def post(webhook: str, payload: dict, timeout: int = 15) -> str:
    """Webhook へ POST する。

    Args:
        webhook: Incoming Webhook の URL。
        payload: 送信する JSON。
        timeout: タイムアウト秒。

    Returns:
        結果を表す文字列 ("sent" またはエラー理由)。
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 送信先は .env で固定した Slack の URL
        webhook, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            if resp.status == 200:
                return "sent"
            return f"fail: HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return f"fail: HTTP {e.code} {e.read()[:120].decode('utf-8', 'replace')}"
    except Exception as e:  # noqa: BLE001 通知の失敗でブリーフ全体を落とさない
        return f"fail: {e}"


def notify(data: dict, report_path: str, dry_run: bool = False) -> str:
    """中間表現を Slack へ投稿する。

    送信できなかった場合もレポート全体は失敗扱いにしない。ただし**黙って落とさず**、
    理由を戻り値に載せる (呼び出し側がジャーナルに残せるようにするため)。

    Args:
        data: 中間表現 dict。
        report_path: HTML レポートのパス。
        dry_run: True なら送信せず本文だけ返す。

    Returns:
        結果を表す 1 行 (呼び出し側がそのまま出力・記録できる形)。
    """
    env = load_env()
    webhook = setting("SLACK_WEBHOOK_URL", env)
    user_id = setting("SLACK_USER_ID", env)

    fallback, blocks, mentioned = build_message(data, report_path, user_id)
    payload = {"text": fallback, "blocks": blocks}

    if dry_run:
        # fallback は通知プレビュー用の抜粋なので、確認には投稿される本文全体を出す
        return f"dry-run: mention={'yes' if mentioned else 'no'}\n{preview(blocks)}"
    if not webhook:
        return "skip: SLACK_WEBHOOK_URL 未設定 (.env を確認すること)"

    result = post(webhook, payload)
    if result != "sent":
        return result
    if actionable_items(data) and not user_id:
        # 鳴らすべき日に鳴らせなかった。投稿は成功しているので fail ではないが、
        # 黙って通すと「メンションが無い日」と区別できなくなる
        return "sent (メンションなし: SLACK_USER_ID 未設定)"
    return f"sent (メンション{'あり' if mentioned else 'なし'})"


def main() -> None:
    """コマンドライン引数を読み、Slack へ投稿する。"""
    ap = argparse.ArgumentParser(description="中間表現 JSON を Slack へ投稿する")
    ap.add_argument("source", help="中間表現 JSON のパス")
    ap.add_argument("--dry-run", action="store_true", help="送信せず本文だけ表示する")
    ap.add_argument("--report", help="本文に出す HTML のパス (既定: source の .html)")
    args = ap.parse_args()

    src = pathlib.Path(args.source)
    # 単体で走らせたときも契約を検証する。壊れた中間表現から Slack へ投稿すると、
    # 誤った内容が push まで届いてしまう (通常は report.py が先に落ちる)
    data = json.loads(src.read_text(encoding="utf-8"))
    # 判断に関わる違反はここで落ちる。表示項目の欠落はSlack本文へ混ぜる
    # 契約警告は長い運用警告で切り捨てられないよう先頭に置く
    data["warnings"] = validate(data) + list(data.get("warnings") or [])
    report_path = args.report or str(src.with_suffix(".html"))
    print(f"[Slack] {notify(data, report_path, dry_run=args.dry_run)}")


if __name__ == "__main__":
    main()
