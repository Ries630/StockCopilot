"""notify.py のテスト。

**ネットワークにアクセスしない。** Slack への POST は `notify.post` を差し替えて
呼び出し内容だけを検証する (AGENTS.md の環境前提)。
"""

import json
import pathlib
import sys

import pytest

import notify
from tests.test_report import base_data, candidate, position


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """実行環境の `.env` と環境変数から切り離す。

    ここを外すと、開発機の `.env` の有無でテストの結果が変わる。

    Args:
        monkeypatch: pytest の monkeypatch。
        tmp_path: テスト用の一時ディレクトリ。

    Returns:
        差し替えた `.env` のパス (既定では存在しない)。
    """
    env_path = tmp_path / ".env"
    monkeypatch.setattr(notify, "ENV_PATH", env_path)
    for key in ("SLACK_WEBHOOK_URL", "SLACK_USER_ID"):
        monkeypatch.delenv(key, raising=False)
    return env_path


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list:
    """`notify.post` を差し替え、送信内容を記録する。

    Args:
        monkeypatch: pytest の monkeypatch。

    Returns:
        `(webhook, payload)` が積まれるリスト。
    """
    calls: list = []

    def fake_post(webhook: str, payload: dict, timeout: int = 15) -> str:
        calls.append((webhook, payload))
        return "sent"

    monkeypatch.setattr(notify, "post", fake_post)
    return calls


def text_of(payload: dict) -> str:
    """payload に含まれる文字列をすべて連結する (検索用)。

    Args:
        payload: Slack へ送る dict。

    Returns:
        本文と全ブロックのテキストを連結した文字列。
    """
    parts = [payload.get("text", "")]
    for block in payload.get("blocks", []):
        if "text" in block:
            parts.append(block["text"]["text"])
        for element in block.get("elements", []):
            parts.append(element.get("text", ""))
    return "\n".join(parts)


# ─── メンションのゲート ───────────────────────────────────


def test_mentions_only_when_money_moves(
    monkeypatch: pytest.MonkeyPatch, isolated_env: pathlib.Path, sent: list
) -> None:
    """買い候補がある日はメンションが付く。"""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    monkeypatch.setenv("SLACK_USER_ID", "U123")

    result = notify.notify(base_data(candidates=[candidate(verdict="買い")]), "reports/x.html")

    assert result == "sent (メンションあり)"
    assert "<@U123>" in text_of(sent[0][1])


def test_no_mention_on_quiet_day(
    monkeypatch: pytest.MonkeyPatch, isolated_env: pathlib.Path, sent: list
) -> None:
    """ホールドだけの日はメンションしない。投稿自体は行う。"""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    monkeypatch.setenv("SLACK_USER_ID", "U123")

    result = notify.notify(base_data(holdings=[position(verdict="ホールド")]), "reports/x.html")

    assert result == "sent (メンションなし)"
    body = text_of(sent[0][1])
    assert "<@U123>" not in body
    assert "資金が動く判断なし" in body


def test_quiet_day_still_posts(
    monkeypatch: pytest.MonkeyPatch, isolated_env: pathlib.Path, sent: list
) -> None:
    """静穏日も投稿する。

    何も出さないと「実行されて静穏だった」のか「実行されなかった」のかが
    Slack だけでは区別できない (→ docs/adr/0022)。
    """
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    notify.notify(base_data(), "reports/x.html")
    assert len(sent) == 1


def test_mention_lost_is_reported(
    monkeypatch: pytest.MonkeyPatch, isolated_env: pathlib.Path, sent: list
) -> None:
    """鳴らすべき日に SLACK_USER_ID が無いことを黙って通さない。"""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    result = notify.notify(base_data(candidates=[candidate(verdict="買い")]), "reports/x.html")
    assert result == "sent (メンションなし: SLACK_USER_ID 未設定)"


def test_missing_webhook_is_reported(isolated_env: pathlib.Path, sent: list) -> None:
    """Webhook 未設定はスキップ理由を返す。黙って落とさない。"""
    result = notify.notify(base_data(), "reports/x.html")
    assert result.startswith("skip: SLACK_WEBHOOK_URL 未設定")
    assert sent == []


def test_dry_run_does_not_post(
    monkeypatch: pytest.MonkeyPatch, isolated_env: pathlib.Path, sent: list
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    result = notify.notify(base_data(), "reports/x.html", dry_run=True)
    assert result.startswith("dry-run: mention=no")
    # 確認用なので、抜粋ではなく投稿される本文全体が出る
    assert "reports/x.html" in result
    assert sent == []


# ─── 本文の組み立て ───────────────────────────────────────


def test_fallback_text_carries_the_mention() -> None:
    """モバイルの通知プレビューは fallback テキストから作られる。"""
    fallback, _, mentioned = notify.build_message(
        base_data(candidates=[candidate(verdict="買い")]), "reports/x.html", "U123"
    )
    assert mentioned
    assert "<@U123>" in fallback
    assert "資金が動く判断 1 件" in fallback


def test_body_lists_actionable_items_with_source() -> None:
    data = base_data(
        holdings=[position(verdict="積増し")], candidates=[candidate(verdict="買い")]
    )
    _, blocks, _ = notify.build_message(data, "reports/x.html", "U123")
    body = text_of({"blocks": blocks})
    assert "*積増し* `9999` テスト銘柄 (保有)" in body
    assert "*買い* `AAAA` 候補銘柄 (候補)" in body


def test_verdict_tally_marks_reference_only() -> None:
    """判断対象外の銘柄をラベルの内訳に混ぜない。"""
    items = [
        position(verdict="ホールド"),
        position(verdict="保留"),
        position(verdict="—", reference_only=True),
    ]
    assert notify.verdict_tally(items) == "ホールド 1 / 保留 1 / 対象外 1"


def test_warnings_and_stale_bars_are_included() -> None:
    data = base_data(stale_bars=True, warnings=["取得に失敗した銘柄が 1 件"])
    _, blocks, _ = notify.build_message(data, "reports/x.html", "")
    body = text_of({"blocks": blocks})
    assert "確定足は前回から変わらず" in body
    assert "取得に失敗した銘柄が 1 件" in body


def test_main_adds_contract_warnings_to_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    isolated_env: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """単体実行でも表示項目の欠落をSlack本文へ載せる。"""
    data = base_data()
    del data["summary"]
    src = tmp_path / "brief.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["notify.py", str(src), "--dry-run"])

    notify.main()

    output = capsys.readouterr().out
    assert "'summary' が無い" in output
    assert "不明" in output


def test_main_rejects_decision_contract_violation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    isolated_env: pathlib.Path,
) -> None:
    """判断項目の欠落はSlack単体実行でも停止する。"""
    data = base_data()
    del data["candidates"]
    src = tmp_path / "brief.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["notify.py", str(src), "--dry-run"])

    with pytest.raises(KeyError, match="candidates"):
        notify.main()


def test_dynamic_mrkdwn_is_escaped_except_for_the_controlled_mention() -> None:
    """分析文に含まれるメンション記法で通知条件を迂回させない。"""
    data = base_data(
        candidates=[candidate(ticker="<@U999>", name="<!channel>")],
        summary="<@U888> & <https://example.test>",
        warnings=["<!here> & <@U777>"],
    )
    fallback, blocks, mentioned = notify.build_message(data, "reports/<x>.html", "U123")
    body = text_of({"blocks": blocks})

    assert mentioned
    assert "<@U123>" in fallback
    for value in ("<@U999>", "<!channel>", "<@U888>", "<!here>", "<@U777>"):
        assert value not in body
    assert "&lt;@U999&gt;" in body
    assert "&amp;" in body


def test_every_block_stays_within_slack_text_limits() -> None:
    """契約に件数・文字数上限がなくても通知全体を失敗させない。"""
    data = base_data(
        holdings=[position(ticker=f"H{index}", name="名" * 500, verdict="売却") for index in range(20)],
        candidates=[candidate(ticker=f"C{index}", name="名" * 500) for index in range(20)],
        warnings=["警告" * 1000 for _ in range(20)],
        summary="総括" * 2000,
    )
    _, blocks, _ = notify.build_message(data, "reports/" + "x" * 3000, "U123")

    assert len(blocks) <= 5
    for block in blocks:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= notify.SECTION_TEXT_LIMIT
        if block["type"] == "context":
            assert len(block["elements"][0]["text"]) <= notify.CONTEXT_TEXT_LIMIT
    assert "…他" in text_of({"blocks": blocks})


def test_report_path_is_explicitly_local() -> None:
    """ローカルファイルは Slack から直接開ける URL ではないと明示する。"""
    _, blocks, _ = notify.build_message(base_data(), "reports/2026-08-20_evening.html", "")
    body = text_of({"blocks": blocks})
    assert "ローカルレポート" in body
    assert "この端末で開く" in body
    assert "reports/2026-08-20_evening.html" in body


def test_long_summary_is_truncated() -> None:
    """Slack の 1 ブロック 3000 文字上限に当たる前に切る。"""
    _, blocks, _ = notify.build_message(base_data(summary="あ" * 5000), "x.html", "")
    body = text_of({"blocks": blocks})
    assert "…" in body
    assert len(body) < 3000


def test_notify_script_declares_contract_dependency() -> None:
    """単体起動用の PEP 723 依存に jsonschema を含める。"""
    source = pathlib.Path(notify.__file__).read_text(encoding="utf-8")
    assert 'dependencies = ["jsonschema>=4.25"]' in source


def test_weekday_is_derived_from_date() -> None:
    fallback, _, _ = notify.build_message(base_data(date="2026-08-20"), "x.html", "")
    assert "2026-08-20 (木)" in fallback


def test_broken_date_does_not_crash() -> None:
    """日付が壊れていても通知そのものは通す (通知は最後の砦なので落とさない)。"""
    fallback, _, _ = notify.build_message(base_data(date="not-a-date"), "x.html", "")
    assert "StockCopilot Evening Brief" in fallback


# ─── .env の読み取り ──────────────────────────────────────


def test_load_env_parses_and_skips_comments(tmp_path: pathlib.Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# コメント\n\nSLACK_WEBHOOK_URL="https://hooks.example/x"\nSLACK_USER_ID=U9\nごみ行\n',
        encoding="utf-8",
    )
    env = notify.load_env(env_file)
    assert env == {"SLACK_WEBHOOK_URL": "https://hooks.example/x", "SLACK_USER_ID": "U9"}


def test_load_env_missing_file_is_empty(tmp_path: pathlib.Path) -> None:
    assert notify.load_env(tmp_path / "none") == {}


def test_env_var_wins_over_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """実行環境からの注入を .env より優先する。"""
    monkeypatch.setenv("SLACK_USER_ID", "UENV")
    assert notify.setting("SLACK_USER_ID", {"SLACK_USER_ID": "UFILE"}) == "UENV"
