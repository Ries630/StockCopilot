# 中間表現の意味（`reports/*_evening.json`）

夕方ブリーフの分析結果を、HTMLレポート（`report.py`）とSlack通知（後続PR）が
読むための中間表現。

- **キー・型・必須・語彙の正**: [`report-contract.schema.json`](report-contract.schema.json)
- **項目の意味と組み合わせ規則の正**: この文書
- **`screen.py` / `analyze.py`の出力の読み方の正**:
  [`output-contract.md`](output-contract.md)

同じ構造をこの文書へ写経しない。構造を変えるときはJSON Schemaを変更し、
意味が変わるときだけこの文書を変更する（→
[ADR-0020](adr/0020-intermediate-report-json.md) /
[ADR-0021](adr/0021-json-schema-for-report-contract.md)）。

## 位置づけ

```text
screen.py --json / analyze.py  ──┐
                                 ├→ reports/YYYY-MM-DD_evening.json
LLMの判断・シナリオ・散文 ──────┘         │
                                          ├→ report.py → HTML
                                          └→ notify.py → Slack（後続PR）
```

- 価格・指標・score・決算注記は`screen.py --json`が中間表現と同じキー・単位へ
  決定的に変換する。LLMは値や単位を変えずに合流させる
- 判断と散文はLLMが書く。判断ラベルの定義は下記（この文書が正）
- `reports/`には保有情報が入るため、追跡・公開しない（→
  [ADR-0008](adr/0008-no-holdings-in-repo.md)）

## トップレベルの意味

`schema`
: 中間表現の互換性を示す番号。構造を互換性なく変えるときに上げる。

`date` / `generated_at` / `bars`
: 分析日、JSTの生成時刻、今回取得できたJP/USそれぞれの確定足日。
  `bar_status`が`unavailable`の市場だけは`bars`の市場キーを省略する。

`bar_status`
: JP/USそれぞれの前回確定足日と更新状態。`updated`は前回より進んだ、`unchanged`は
  同じ、`initial`は比較対象が無い初回、`unavailable`は今回の確定足日を取得できなかった
  状態を示す。`updated`と`initial`だけを新しい市場観測として分析する。
  `unchanged`と`unavailable`は前回の判断・水準・トリガーを引き継ぐ。
  → [ADR-0029](adr/0029-market-specific-bar-observation.md)

`holdings_as_of`
: 資産クラスごとの保有データ基準日。配列を空にするとデータ鮮度を確認できない。
`count`はその基準日に含まれる銘柄数で、取得できる場合だけ入れる。

`effective_holdings`
: 基準日時点の保有とジャーナルの執行記録を合成した結果。`executions`は反映した執行数、
`lines`は読み手に見せる計算過程である。執行0件でも`lines`へ
「執行記録なし（as_of時点のまま）」を入れる。空にすると、記録が無いのか
拾い忘れたのかを区別できない。

`market_tone`
: 地合いの短い評価と説明。地合いを評価しない日はオブジェクトごと省略する。

`holdings` / `candidates`
: 保有分析とスクリーニング候補。空配列は正常だが、キーの省略は書き漏らしなので許さない。
  市場別合流では候補の`market`を使い、保有は`lib/market_observation.py`の
  `market_from_currency()`でJPY→JP、USD→USと決定する。この対応を他の場所へ複製しない。
  `market_tone`は市場別合流の対象にせず、今回値を維持する。

`screen`
: JP/US別の母集団と評価結果。`evaluated`は条件を判定できた件数、`failures`は取得例外、
  `matched`は上限適用前の通過件数、`selected`は更新市場の選別と上限適用後の件数。
  候補ゼロを独立した観測として数えるのは、状態が`updated`または`initial`で、かつ
  `evaluated > 0`、`matched == 0`の市場だけ。母集団0、全件取得失敗、確定足取得不能は
  候補ゼロへ数えない。

`summary` / `assumptions` / `warnings`
: 総括、確認を取らずに置いた前提、取得・解釈上の警告。空白だけの文は情報がないため許さない。

## 保有銘柄（Position）の意味

- `ticker`は生ティッカー。空白だけの値では対象を特定できない
- `name`は日本株では必須。米国株では取得できない場合に省略できる
- `shares`は実効保有株数、`price`は確定足終値で、指定する場合はいずれも正値。
  `change_pct`は前回エントリ比なので負値を取り得る
- `scenario`は前進・停滞・否定接近のいずれか
- `levels`は支持・抵抗・無効化水準、`closes`は古い順の終値。価格水準はすべて正値
- `earnings`は決算注記。決算情報が無い場合はオブジェクトごと省略する
- `prose.change`と`prose.scenario`は、HTML単独で判断を再構成するための本文

`reference_only: true`は自動運用口座など判断対象外の銘柄を示す。この場合、入力の
`verdict`は省略するか`"—"`にし、HTMLでは`"—"`を表示する。それ以外の保有銘柄には
判断ラベルが必要である。

## 候補（Candidate）の意味

- `market`は候補を抽出した市場、`currency`は表示通貨。JPはJPY、USはUSDに対応する
- `price`と価格水準は正値。`score_atr`と`atr_pct`は0以上で、`screen.py`が返した値を移す
- `pass_reason`は`screen.py`が返した値を移す
- `range`は直前20日レンジの安値・高値と、その中での終値位置。終値位置はレンジ外なら
  0〜100%を超えてよい。`pos_pct`は`screen.py --json`が元の比率を100倍して出力し、
  `price`・`low`・`high`から求めた値と0.5ポイント以内で一致する必要がある
- `atr_pct`、`turnover`、`levels`、`closes`、`earnings`は取得できる場合だけ入れる
- `prose.strong`と`prose.weak`は強弱の根拠、`prose.check`は次に確認する条件

`verdict: "買い"`では`prose.weak`を省略・空配列にできない。弱点の無い候補は、
弱点を確認していない候補だからである。

## 判断ラベル

**このファイルが定義の正。** スキルや ADR に写経しない
（→ [ADR-0025](adr/0025-journal-as-ledger-and-memo.md) でジャーナルから移した）。

**2 系統あり、別体系。** 同じ日の中間表現に両方が並ぶので取り違えないこと。
共通するのは「保留」だけで、これは両方とも「データ不足で判定が成立しない」を指す。

### 保有（`holdings[].verdict`）— 5 種

| ラベル | 意味 |
|---|---|
| `ホールド` | そのまま持ち続ける。動かないことも判断 |
| `積増し` | 買い増す |
| `部分利確` | 一部だけ売って利益を確定し、残りは持ち続ける |
| `売却` | 持ち高を手仕舞う |
| `保留` | **データ不足で判定が成立しない場合のみ** |

`reference_only: true`（自動運用口座など）の銘柄はラベルを付けず `"—"` を入れる。

### 候補（`candidates[].verdict`）— 4 種

| ラベル | 意味 |
|---|---|
| `買い` | 今の判断材料からは買い。執行するかはりーすさんが決める |
| `見送り` | **`prose.weak` に理由を必ず書く。** 後から「なぜ見送ったか」を追えないと検証できない |
| `決算後に再判定` | 決算ギャップで確定足ベースのトリガーが飛びうる（`earnings.note` に日付を併記） |
| `保留` | データ不足で判定が成立しない |

→ [ADR-0017](adr/0017-screen-report-writes-verdict.md)

### 共通の規範

- **信頼度の星は付けない。** 現物のホールド判断は perp より単純で、毎回付けると形骸化する
- **「保留」はデータ不足のときだけ。** 週足が `insufficient` を返す、`ema200` が `null` に
  なる銘柄がこれにあたる。上位トレンドの基準線が無い状態で「ホールド」と書くと、
  根拠のある継続保有と見分けがつかなくなる。この状態を表すためのラベル
- **散文は断定して書く。** 「構造は強いが勢いは中立」のような濁し方をせず、
  強い / 弱い / 伸びきっている をはっきり書く。免責の但し書き（「買い推奨ではない」等）は付けない
- コードから参照する形は `lib/verdicts.py`（`HOLDING_VERDICTS` / `CANDIDATE_VERDICTS`）

## 銘柄名（`name`）

日本株では必須。4桁コードだけでは会社を判別しづらく、名前が無いとレポートとして
読めないためである。米国株はティッカーで判別できるため、省略を許す。

出所は経路で異なる（→ [ADR-0023](adr/0023-japanese-stock-display-names.md)）。

- 保有: Investmentの生成物（`lib/holdings.py`）にある日本語名
- 候補: `config/universe.py`の`NAMES_JP`にある日本語名
- 辞書に無い日本株候補: yfinanceの`longName`（英語名）

`screen.py`が候補へ`name`を付けるため、中間表現にはその値を写す。日本語名を補う場合は
`config/universe.py`の`NAMES_JP`、または追跡対象外の`config/watchlist.py`にある
`WATCHLIST_NAMES_JP`へ追加する。

## 4軸シグナル

`weekly`（週足）、`daily`（日足）、`overheat`（過熱）、`volume`（出来高・OBV）の
4軸を、`good` / `warn` / `bad` / `unknown`で評価する。色ではなく評価を入れ、
表示色は`report.py`が決める。

`unknown`は書き漏らしではなく、実際のデータ不足にだけ使う。資金が動く判断
（買い・積増し・売却）とは併存できない。ホールド・見送り・保留など、資金を
動かさない判断までは一律に禁止しない。

短い根拠を表示したい場合は`signals.labels`へ軸ごとのラベルを入れる。

## 資金が動く判断

判定の正は`lib/verdicts.py`の`ACTIONABLE_VERDICTS`で、現在は買い・積増し・売却。
HTMLのヒーローと後続のSlackメンションは同じ判定だけを見る。散文から判断を抽出しない。

## 用語の説明を `prose` に書かない

指標や判断ラベルの**説明**は `report.py` の `GLOSSARY` にあり、HTML では用語ラベルの
ポップオーバーとして出る（→ [ADR-0024](adr/0024-glossary-popovers.md)）。

`prose` に書くのは**その日の観測と判断**だけにする。「RSI とは〜」を毎日書くと、
日々変わる情報と一度読めば済む情報が同じ密度で並び、読み飛ばす作業が生まれる。
用語の説明を足したくなったら `GLOSSARY` に書く。

## 契約違反の扱い

`lib/contract.py`の`validate()`を、JSONを利用する前に必ず1回呼ぶ。

severityは次の2段階（→ [ADR-0027](adr/0027-contract-validation-severity.md)）。

| 段階 | 対象 | 挙動 |
|---|---|---|
| 例外 | 判断を成立させる項目の欠落、型・語彙・形式・空・未知キー・組み合わせ違反 | `KeyError` / `ValueError`で停止 |
| 警告 | 表示項目の欠落 | HTMLとSlackの警告へ載せ、「不明」と表示して続行 |

表示項目として警告へ降格できる欠落は次のとおり。ここに無いSchema違反は例外にする。

- トップレベル: `date`、`generated_at`、`holdings_as_of`、`screen`、`summary`
- `holdings_as_of[].as_of` / `label`
- `effective_holdings.executions`
- 保有: `prose`、`prose.change` / `scenario`
- 候補: `score_atr`、`pass_reason`、`range`とその3要素、`prose`、`prose.check`

ただし`verdict: "買い"`で`prose`全体が無い場合は、必須の`weak`も確認できないため例外。
型や形式が壊れた値、空白文字、`null`は「欠落」へ読み替えず例外のままにする。

警告へ降格した欠落を空文字や0へ置き換えると、正常値と書き漏らしを区別できない。
`validate()`の警告を既存の`warnings`へ合流し、表示側でも「不明」を残す。
