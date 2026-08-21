# /// script
# requires-python = ">=3.10"
# dependencies = []
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
from lib.verdicts import actionable_items

# .env の置き場所 (リポジトリルート)。gitignore 済み
ENV_PATH = pathlib.Path(__file__).parent / ".env"

WEEKDAY_JA = ("月", "火", "水", "木", "金", "土", "日")

# Slack Block Kit の 1 ブロックあたりの文字数上限は 3000。総括はそれより手前で切る
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
        label = "対象外" if item.get("reference_only") else (item.get(key) or "?")
        counts[label] = counts.get(label, 0) + 1
    return " / ".join(f"{label} {n}" for label, n in counts.items())


def build_message(data: dict, report_path: str, user_id: str) -> tuple[str, list, bool]:
    """投稿する本文と Block Kit を組み立てる。

    Args:
        data: 中間表現 dict。
        report_path: HTML レポートのパス (本文に出す)。
        user_id: 自己メンションに使う Slack ユーザー ID。空ならメンションしない。

    Returns:
        `(fallback テキスト, blocks, メンションしたか)` のタプル。
    """
    date = data.get("date", "")
    weekday = ""
    try:
        year, month, day = (int(x) for x in date.split("-"))
        weekday = f" ({WEEKDAY_JA[dt.date(year, month, day).weekday()]})"
    except (ValueError, TypeError):
        pass
    clock = str(data.get("generated_at", ""))[11:16]

    items = actionable_items(data)
    mentioned = bool(items and user_id)

    lines = []
    if mentioned:
        lines.append(f"<@{user_id}>")
    lines.append(f"🌆 *StockCopilot Evening Brief* — {date}{weekday} {clock} JST")

    if items:
        lines.append(f"🎯 *資金が動く判断 {len(items)} 件*")
        for i in items:
            kind = "保有" if i["kind"] == "holding" else "候補"
            lines.append(f"　*{i['verdict']}* `{i['ticker']}` {i['name']} ({kind})")
    else:
        lines.append("🎯 資金が動く判断なし (候補ゼロ・ホールドのみは正常)")

    holdings = data.get("holdings") or []
    candidates = data.get("candidates") or []
    lines.append(f"📦 保有 {len(holdings)} 銘柄: {verdict_tally(holdings) or '—'}")
    if candidates:
        lines.append(f"🔍 候補 {len(candidates)} 件: {verdict_tally(candidates)}")
    else:
        screen = data.get("screen") or {}
        lines.append(f"🔍 候補なし (母集団 {screen.get('universe', '?')} 銘柄)")

    if data.get("stale_bars"):
        lines.append("🕘 確定足は前回から変わらず (独立した観測として数えない)")

    for warning in data.get("warnings") or []:
        lines.append(f"⚠️ {warning}")

    summary = str(data.get("summary") or "")
    if len(summary) > SUMMARY_LIMIT:
        summary = summary[:SUMMARY_LIMIT] + "…"

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]
    if summary:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": summary}})
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"📄 `{report_path}`（ブラウザで開く）"}],
        }
    )

    # fallback テキストはモバイルの通知プレビューに出る。メンションもここに含める
    fallback = "\n".join(lines[: 3 if items else 2])
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
    data = validate(json.loads(src.read_text(encoding="utf-8")))
    report_path = args.report or str(src.with_suffix(".html"))
    print(f"[Slack] {notify(data, report_path, dry_run=args.dry_run)}")


if __name__ == "__main__":
    main()
