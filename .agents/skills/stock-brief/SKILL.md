---
name: stock-brief
description: 株式現物の夕方ブリーフ。候補スクリーニング (stock-screen) と保有分析 (stock-check) を通しで実行し、中間表現 JSON → HTML レポート → Slack 通知 → ジャーナル追記までを完結させる。平日夕方の定期タスクから呼ばれるほか、「夕方ブリーフ」「イブニングブリーフ」「今日の株をまとめて」で起動
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
- **LLM は Slack ツールを呼ばない。** 投稿は STEP 6 の `notify.py` が完結させる
  (→ [`docs/adr/0022-slack-webhook-notification.md`](../../../docs/adr/0022-slack-webhook-notification.md))
- **保有情報をリポジトリに書かない。** 書いてよいのは `.gitignore` 済みの
  `journal/journal.md` `journal/lessons.md` `reports/` `config/watchlist.py` だけ
  (→ [`docs/adr/0008-no-holdings-in-repo.md`](../../../docs/adr/0008-no-holdings-in-repo.md))
- **git のコミット・push はしない**

## 流れ

```
STEP 1  stock-screen   候補スクリーニングと候補分析
STEP 2  stock-check    保有分析
STEP 3  中間表現 JSON   更新市場の結果を下書きへまとめる
STEP 4  finalize        停滞市場を前回結果から合流して契約検証する
STEP 5  report.py      HTML レポート
STEP 6  notify.py      Slack 通知 (メンションは更新市場の資金移動判断だけ)
STEP 7  記録            執行の台帳と運用メモ (定型の分析は書かない)
STEP 8  チャット本文     短いサマリー
STEP 9  教訓の昇格       再発防止ルールを追跡対象外のlessonsへ置く
```

STEP 3 で中間表現を挟むのは、**Slack のメンションの発火条件を LLM の裁量から外す**ため
(→ [`docs/adr/0020-intermediate-report-json.md`](../../../docs/adr/0020-intermediate-report-json.md))。

## STEP 1. 候補スクリーニングと候補分析

`stock-screen` スキルの手順を実行する (実体は
[`.agents/skills/stock-screen/SKILL.md`](../stock-screen/SKILL.md))。
候補の出力まで完了してから STEP 2 の保有分析へ進む。記録だけは STEP 7 に回す。

ただし `stock-screen` の STEP 1 は、人間向け表示ではなく次の機械出力で実行する。

```bash
uv run screen.py --json --earnings
```

この JSON は STEP 3 まで保持し、`screen` と `candidates` の機械データへそのまま合流させる。
価格・score・ATR・レンジ位置などを人間向けの丸め出力から復元しない。夕方ブリーフは
JP/US両市場を扱うため `--market` で限定せず、`--json --earnings` を外さない。

`bar_status`が`updated`または`initial`の市場だけを候補分析へ渡す。`screen.py`が選別済みの
`candidates`以外を足さない。市場状態と候補ゼロの成立条件は`docs/report-contract.md`を正とする。

候補数によって次のように分岐する。

- **候補ゼロ:** 正常な結果として `stock-screen` の候補分析を飛ばす。埋め草の候補を作らず、
  `candidates: []` と機械データを保持したまま STEP 2 の保有分析へ進む
- **候補あり:** `stock-screen` の責務として候補を `analyze.py` で分析し、候補用の判断ラベルを
  確定する。候補分析が完了してから STEP 2 の保有分析へ進む

どちらの場合も候補側の処理だけでブリーフを終えない。

## STEP 2. 保有分析

`stock-check` スキルの手順をそのまま実行する (実体は
[`.agents/skills/stock-check/SKILL.md`](../stock-check/SKILL.md))。
STEP 0 の `journal/lessons.md` の読み込みから STEP 4 の出力フォーマットまでを行い、
**記録 (あちらの STEP 5) はここでは行わない** — STEP 7 でまとめて扱う。

保有の読み込みと実効保有の組み立ては通常どおり行うが、`analyze.py`は更新市場だけに限定する。

```bash
uv run analyze.py --market jp  # JPがupdated / initialのときだけ
uv run analyze.py --market us  # USがupdated / initialのときだけ
```

`unchanged` / `unavailable`市場では`analyze.py`を呼ばない。両市場とも該当する場合は保有の
分析呼び出しをすべて飛ばし、STEP 4で前回結果を引き継ぐ。

シリーズ分析の起点は `reports/latest.json` (前回の中間表現)。**無ければ
`journal/journal.md` の最終エントリを読む** — 2026-08-20 までのエントリは定型の分析を
含んでいる。この fallback は移行期のためのもので、JSON が溜まったら消す
(→ [`docs/adr/0025-journal-as-ledger-and-memo.md`](../../../docs/adr/0025-journal-as-ledger-and-memo.md))。

## STEP 3. 中間表現 JSON

```bash
mkdir -p reports
```

STEP 1・2 の更新市場の結果を `reports/YYYY-MM-DD_evening.draft.json` に書く。

**キー・型・必須・語彙の正は
[`docs/report-contract.schema.json`](../../../docs/report-contract.schema.json)、意味と組み合わせの正は
[`docs/report-contract.md`](../../../docs/report-contract.md)。** このファイルに写経しないこと
(二重管理になり、必ずどちらかが先に古くなる)。

書くときの要点だけ挙げる:

- **`prose` を必ず埋める。** HTML 単独でレポートとして成立させるための本文で、
  図だけ並べても読み手は判断を再構成できない。`prose` を欠くと `report.py` が落ちる
- **日本株には `name` を必ず入れる。** 4 桁コードだけでは何の会社か分からない。
  保有は Investment の生成物、候補は `screen.py` が付けて返すので、その値を写す。
  欠けていると `report.py` が落ちる → [ADR-0023](../../../docs/adr/0023-japanese-stock-display-names.md)
- **用語の説明を `prose` に書かない。** 説明は `report.py` の `GLOSSARY` にあり、
  HTML ではポップオーバーとして出る。`prose` に書くのはその日の観測と判断だけ
  → [ADR-0024](../../../docs/adr/0024-glossary-popovers.md)
- **判断ラベルは 2 系統を取り違えない。** 保有は ホールド / 積増し / 部分利確 / 売却 / 保留、
  候補は 買い / 見送り / 決算後に再判定 / 保留。定義の正は
  [`docs/report-contract.md`](../../../docs/report-contract.md) の「判断ラベル」
- **自動運用口座の銘柄は `reference_only: true`** にし、`verdict` は `"—"` を入れる
- **`bars` / `bar_status` / `screen` は `screen.py --json` の値をそのまま移す。** 実行日や
  平日から推測せず、LLMが更新状態や件数を再計算しない
- **`holdings`の集合とidentity/stateは今回の実効保有を正にする。** 更新市場は分析済みの
  Positionを`analysis_status: current`で入れる。非更新市場は現在の`ticker` / `name` /
  `shares` / `currency` / `reference_only`だけを`analysis_status: unavailable`で入れ、前回分析を
  手でコピーしない。STEP 4が同一銘柄の分析だけを決定的に合流する
- **`signals` には色ではなく評価** (`good` / `warn` / `bad` / `unknown`) を入れる。
  `unknown` は実際のデータ不足にだけ使い、資金が動く判断とは併存させない。ホールド・見送り
  など非資金移動の判断は一律に `保留` へ変えず、分析自体が判断を確立できない場合だけ
  `保留` にする

## STEP 4. 市場別結果の確定

```bash
uv run finalize_report.py reports/YYYY-MM-DD_evening.draft.json \
  -o reports/YYYY-MM-DD_evening.json
```

この処理が`unchanged` / `unavailable`市場を`reports/latest.json`から合流し、警告追加と
契約検証を行う。LLMが前回結果を手でコピーしない。失敗したらHTML・Slackへ進まない。

## STEP 5. HTML レポート

```bash
uv run report.py reports/YYYY-MM-DD_evening.json
```

同じ場所に `.html` が出る。**必須キーが欠けていれば落ちる** — 既定値で埋めずに
落とすのは、書き漏らした日と判断が無かった日を区別するため。落ちたら JSON を直して
やり直す。

## STEP 6. Slack 通知

```bash
uv run notify.py reports/YYYY-MM-DD_evening.json
```

**このコマンドの結果 (標準出力の 1 行) を STEP 8 に必ず載せる。**
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

## STEP 7. 記録

**定型の分析をジャーナルに書かない。** 分析・判断・前提・Slack 投稿の結果はすべて
中間表現 JSON に入っており、人が読むのは HTML レポート
(→ [`docs/adr/0025-journal-as-ledger-and-memo.md`](../../../docs/adr/0025-journal-as-ledger-and-memo.md))。

`journal/journal.md` に書くのは、**その日に該当があったときだけ**の次の 4 種類。
毎日は発生しない。**書式の正は [`journal/README.md`](../../../journal/README.md)。**

| 書くもの | いつ |
|---|---|
| `### 執行` | 売買の報告を受けたとき |
| 訂正 | 過去の分析の誤りが分かったとき (**原因を必ず添える**) |
| 昇格候補の 1 回目の観測 | 「もう一度起きたら運用ルールへ」の保留状態 |
| 規約の例外 | 規約に反する扱いをしたとき |

**該当が無ければ何も書かない。** 「本日は特記なし」も書かない — 空振りの行が積むと、
例外の記録という節の意味が薄れる。

定期実行では外部参照を行わないため、`### 対話実行での追補と出典` はこのスキルからは
書かない (対話実行のときだけ)。

## STEP 8. チャット本文

```
🌆 Evening Brief — 2026-08-20 (木) 17:30 JST

📦 保有 4 銘柄（今回更新 2 銘柄）: ホールド 1 / 部分利確 1 / 保留 1 / 対象外 1
🔍 候補 2 件（今回更新 1 件）: 買い 1 / 決算後に再判定 1
🎯 資金が動く判断 1 件: 買い 4444 (候補)

優先アクションを 1 つだけ、1〜2 行で。

📄 reports/2026-08-20_evening.html
📮 Slack: sent (メンションあり)
```

ジャーナルに書いたものがあれば最後に 1 行足す (`📝 ジャーナル: 執行 1 件を記録`)。
**何も書かなかった日は何も足さない。**

総数、今回更新数、候補ゼロ、新規市場観測なしの使い分けは`docs/report-contract.md`の
「市場別表示の意味」に従う。**無理に候補を出さない。**

## STEP 9. 教訓の昇格

`stock-check` / `stock-screen` の規定どおり、再発防止のルールとして使えるものを
`journal/lessons.md` に書き足す。**この SKILL.md には書かない** — 教訓は実際の保有に
ついての観測を含み、このファイルは公開リポジトリで追跡されている
(→ [`docs/adr/0018-bundle-skills-in-repo.md`](../../../docs/adr/0018-bundle-skills-in-repo.md))。

## 注意

- **判断の最終決定はりーすさん。** 「買い」も「売却」も判断であって執行の指示ではない
- **候補ゼロを失敗と扱わない。** 母集団が狭いので出ない日のほうが多い
- **`reports/` の中身をリポジトリの追跡対象ファイルに貼らない。** Issue・PR・
  コミットメッセージに銘柄や株数を書かない (必要なら「保有銘柄A」に置き換える)
- **外部参照 (Web / IR) を行わない。** 定期実行は無人で、外部情報が判断に入る前に
  人が検証できないため。決算日が取得できない銘柄は「不明」と明記して進み、判断は
  `決算後に再判定` に倒す。**これは劣化ではなく設計どおりの挙動**
  (→ [`docs/adr/0016-surface-unavailable-earnings-date.md`](../../../docs/adr/0016-surface-unavailable-earnings-date.md))。
  可否の正は [`docs/output-contract.md`](../../../docs/output-contract.md) の
  「実行モードと外部参照」で、**対話実行では引いてよい**
