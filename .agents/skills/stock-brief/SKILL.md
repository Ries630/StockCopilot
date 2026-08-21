---
name: stock-brief
description: 株式現物の夕方ブリーフ。保有分析 (stock-check) と候補スクリーニング (stock-screen) を通しで実行し、中間表現 JSON → HTML レポート → Slack 通知 → ジャーナル追記までを完結させる。平日夕方の定期タスクから呼ばれるほか、「夕方ブリーフ」「イブニングブリーフ」「今日の株をまとめて」で起動
---
# Stock Brief — 株式現物の夕方ブリーフ

## When to Use

平日夕方の定期タスク `stock-copilot-evening-brief` の実体。個別に呼ぶ場合は
「夕方ブリーフ」「イブニングブリーフ」で起動する。

**保有分析だけ / 候補探しだけを頼まれたときはこのスキルを使わない。**
`stock-check` / `stock-screen` を単独で使う。こちらは両方を通しで回し、
レポートと通知まで出すための束ね役である。

## 大原則

- **確認を求めて止まらない。** 応答できる相手がいない。前提が不確かなら、質問ではなく
  「何を前提に置いたか」を出力と JSON の `assumptions` に明記して進める
- **発注しない。** 分析と提案のみ
- **LLM は Slack ツールを呼ばない。** 投稿は STEP 5 の `notify.py` が完結させる
  (→ [`docs/adr/0022-slack-webhook-notification.md`](../../../docs/adr/0022-slack-webhook-notification.md))
- **保有情報をリポジトリに書かない。** 書いてよいのは `.gitignore` 済みの
  `journal/journal.md` `journal/lessons.md` `reports/` `config/watchlist.py` だけ
  (→ [`docs/adr/0008-no-holdings-in-repo.md`](../../../docs/adr/0008-no-holdings-in-repo.md))
- **git のコミット・push はしない**

## 流れ

```
STEP 1  stock-check    保有分析
STEP 2  stock-screen   候補スクリーニング
STEP 3  中間表現 JSON   判断と機械データを 1 つの器にまとめる
STEP 4  report.py      HTML レポート
STEP 5  notify.py      Slack 通知 (メンションは資金が動く判断がある日だけ)
STEP 6  ジャーナル追記   同じ日付エントリに保有 → スクリーニングの順で
STEP 7  チャット本文     短いサマリー
```

STEP 3 で中間表現を挟むのは、**Slack のメンションの発火条件を LLM の裁量から外す**ため
(→ [`docs/adr/0020-intermediate-report-json.md`](../../../docs/adr/0020-intermediate-report-json.md))。

## STEP 1. 保有分析

`stock-check` スキルの手順をそのまま実行する (実体は
[`.agents/skills/stock-check/SKILL.md`](../stock-check/SKILL.md))。
STEP 0 の `journal/lessons.md` の読み込みから STEP 4 の出力フォーマットまでを行い、
**ジャーナル追記 (あちらの STEP 5) はここでは行わない** — STEP 6 でスクリーニングと
まとめて書く。

## STEP 2. 候補スクリーニング

同様に `stock-screen` スキルの手順を実行する (実体は
[`.agents/skills/stock-screen/SKILL.md`](../stock-screen/SKILL.md))。
こちらもジャーナル追記は STEP 6 に回す。

**候補ゼロは正常な結果。** 埋め草の候補を作らない。

## STEP 3. 中間表現 JSON

STEP 1・2 の結果を `reports/YYYY-MM-DD_evening.json` に書く。

**キー・型・必須・語彙の正は
[`docs/report-contract.schema.json`](../../../docs/report-contract.schema.json)、意味と組み合わせの正は
[`docs/report-contract.md`](../../../docs/report-contract.md)。** このファイルに写経しないこと
(二重管理になり、必ずどちらかが先に古くなる)。

書くときの要点だけ挙げる:

- **`prose` を必ず埋める。** HTML 単独でレポートとして成立させるための本文で、
  図だけ並べても読み手は判断を再構成できない。`prose` を欠くと `report.py` が落ちる
- **判断ラベルは 2 系統を取り違えない。** 保有は ホールド / 積増し / 部分利確 / 売却 / 保留、
  候補は 買い / 見送り / 決算後に再判定 / 保留。定義の正は
  [`journal/README.md`](../../../journal/README.md)
- **自動運用口座の銘柄は `reference_only: true`** にし、`verdict` は `"—"` を入れる
- **確定足が前回エントリと同じなら `stale_bars: true`**
- **`signals` には色ではなく評価** (`good` / `warn` / `bad` / `unknown`) を入れる。
  データ不足は `unknown` で、その銘柄の `verdict` は `保留` になる

## STEP 4. HTML レポート

```bash
uv run report.py reports/YYYY-MM-DD_evening.json
```

同じ場所に `.html` が出る。**必須キーが欠けていれば落ちる** — 既定値で埋めずに
落とすのは、書き漏らした日と判断が無かった日を区別するため。落ちたら JSON を直して
やり直す。

## STEP 5. Slack 通知

```bash
uv run notify.py reports/YYYY-MM-DD_evening.json
```

**このコマンドの結果 (標準出力の 1 行) を STEP 6・7 の両方に必ず載せる。**
黙って落とすと「実行されなかった」のか「投稿だけ失敗した」のかが区別できない。

| 出力 | 意味 |
|---|---|
| `sent (メンションあり)` | 資金が動く判断があり、モバイル push を鳴らした |
| `sent (メンションなし)` | 投稿のみ。静穏日の正常な結果 |
| `sent (メンションなし: SLACK_USER_ID 未設定)` | 鳴らすべき日に鳴らせなかった。**報告すること** |
| `skip: SLACK_WEBHOOK_URL 未設定 …` | `.env` の設定漏れ。**報告すること** |
| `fail: …` | 送信失敗。ブリーフ全体は失敗扱いにせず、**理由を報告する** |

メンションの発火条件と対象ラベルは `lib/verdicts.py` の `ACTIONABLE_VERDICTS` が正。
**ここで裁量を挟まない。**

## STEP 6. ジャーナル追記

`journal/journal.md` の**同じ日付エントリ**に、保有分析 → スクリーニングの順で書く。
**書式の正は [`journal/README.md`](../../../journal/README.md)。**

加えてこのスキルからは次の 2 節を残す:

- `### 本エントリでの前提` — 確認を取らずに置いた前提 (JSON の `assumptions` と同じ内容)
- `### Slack 投稿` — STEP 5 の結果 1 行と、生成した HTML のパス

## STEP 7. チャット本文

```
🌆 Evening Brief — 2026-08-20 (木) 17:30 JST

📦 保有 4 銘柄: ホールド 1 / 部分利確 1 / 保留 1 / 対象外 1
🔍 候補 2 件: 買い 1 / 決算後に再判定 1
🎯 資金が動く判断 1 件: 買い 4444 (候補)

優先アクションを 1 つだけ、1〜2 行で。

📄 reports/2026-08-20_evening.html
📮 Slack: sent (メンションあり)
```

資金が動く判断が無い日は `🎯 資金が動く判断なし (候補ゼロ・ホールドのみは正常)` と書く。
**これは正常であり、無理に候補を出さない。**

## STEP 8. 教訓の昇格

`stock-check` / `stock-screen` の規定どおり、再発防止のルールとして使えるものを
`journal/lessons.md` に書き足す。**この SKILL.md には書かない** — 教訓は実際の保有に
ついての観測を含み、このファイルは公開リポジトリで追跡されている
(→ [`docs/adr/0018-bundle-skills-in-repo.md`](../../../docs/adr/0018-bundle-skills-in-repo.md))。

## 注意

- **判断の最終決定はりーすさん。** 「買い」も「売却」も判断であって執行の指示ではない
- **候補ゼロを失敗と扱わない。** 母集団が狭いので出ない日のほうが多い
- **`reports/` の中身をリポジトリの追跡対象ファイルに貼らない。** Issue・PR・
  コミットメッセージに銘柄や株数を書かない (必要なら「保有銘柄A」に置き換える)
- **決算日が取得できない銘柄は「不明」と明記して進む。** Web 検索で補完しない
