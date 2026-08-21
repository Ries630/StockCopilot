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
`screen.market`が`all`以外なら、すべての候補の`market`が一致しなければならない。

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

- 必須キー欠落は`KeyError`
- 型・語彙・形式・空・未知キー・組み合わせ違反は`ValueError`
- 任意項目は省略できるが、`null`で代用しない
- 契約にない追加キーは、表記揺れや未描画データを見逃さないため拒否する
- 候補レンジで`low > high`は意味上の矛盾として拒否する

既定値で埋めて進むと、「判断が無かった日」と「LLMが書き漏らした日」が
区別できなくなる。生成に失敗させ、入力側を修正する。
