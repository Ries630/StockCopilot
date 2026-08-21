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

- 価格・指標・score・決算注記はスクリプトの出力を加工せず移す
- 判断と散文はLLMが書く。判断ラベルの意味は[`journal/README.md`](../journal/README.md)が正
- `reports/`には保有情報が入るため、追跡・公開しない（→
  [ADR-0008](adr/0008-no-holdings-in-repo.md)）

## トップレベルの意味

`schema`
: 中間表現の互換性を示す番号。構造を互換性なく変えるときに上げる。

`date` / `generated_at` / `bars`
: 分析日、JSTの生成時刻、JP/USそれぞれの確定足日。片方の確定足日を省略すると、
  対象市場のデータ鮮度を確認できない。

`stale_bars`
: 確定足が前回エントリと同じなら`true`。同じ足を独立した観測として数えないために使う。

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

`screen`
: 実際に調べた母集団、市場、取得失敗数。取得失敗は「条件を満たさなかった」のではなく
「判定できなかった」ので、候補ゼロと分けて表示する。

`summary` / `assumptions` / `warnings`
: 総括、確認を取らずに置いた前提、取得・解釈上の警告。空白だけの文は情報がないため許さない。

## 保有銘柄（Position）の意味

- `ticker`は生ティッカー。空白だけの値では対象を特定できない
- `name`は任意。JP/USどちらも同じ扱いで、取得できない場合は省略する
- `shares`は実効保有株数、`price`は確定足終値、`change_pct`は前回エントリ比
- `scenario`は前進・停滞・否定接近のいずれか
- `levels`は支持・抵抗・無効化水準、`closes`は古い順の終値
- `earnings`は決算注記。決算情報が無い場合はオブジェクトごと省略する
- `prose.change`と`prose.scenario`は、HTML単独で判断を再構成するための本文

`reference_only: true`は自動運用口座など判断対象外の銘柄を示す。この場合、入力の
`verdict`は省略するか`"—"`にし、HTMLでは`"—"`を表示する。それ以外の保有銘柄には
判断ラベルが必要である。

## 候補（Candidate）の意味

- `market`は候補を抽出した市場、`currency`は表示通貨
- `score_atr`と`pass_reason`は`screen.py`が返した値を移す
- `range`は直前20日レンジの安値・高値と、その中での終値位置。終値位置はレンジ外なら
  0〜100%を超えてよい
- `atr_pct`、`turnover`、`levels`、`closes`、`earnings`は取得できる場合だけ入れる
- `prose.strong`と`prose.weak`は強弱の根拠、`prose.check`は次に確認する条件

`verdict: "買い"`では`prose.weak`を省略・空配列にできない。弱点の無い候補は、
弱点を確認していない候補だからである。

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

## 契約違反の扱い

`lib/contract.py`の`validate()`を、JSONを利用する前に必ず1回呼ぶ。

- 必須キー欠落は`KeyError`
- 型・語彙・形式・空・未知キー・組み合わせ違反は`ValueError`
- 任意項目は省略できるが、`null`で代用しない
- 契約にない追加キーは、表記揺れや未描画データを見逃さないため拒否する
- 候補レンジで`low > high`は意味上の矛盾として拒否する

既定値で埋めて進むと、「判断が無かった日」と「LLMが書き漏らした日」が
区別できなくなる。生成に失敗させ、入力側を修正する。
