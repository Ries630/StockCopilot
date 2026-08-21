# ADR-0027: JSON Schema 検証に表示欠落の severity を重ねる

- ステータス: 承認済み
- 日付: 2026-08-21
- 関連: [#60](https://github.com/Ries630/StockCopilot/issues/60) / [#64](https://github.com/Ries630/StockCopilot/pull/64) / [#71](https://github.com/Ries630/StockCopilot/issues/71) / [#72](https://github.com/Ries630/StockCopilot/pull/72) / [ADR-0021](0021-json-schema-for-report-contract.md) / [ADR-0026](0026-centralised-contract-validation.md)

## 背景

[ADR-0021](0021-json-schema-for-report-contract.md) により、中間表現のキー・型・必須・語彙は
[`report-contract.schema.json`](../report-contract.schema.json) を正として検証している。
一方、契約違反には、判断を成立させない欠落と、表示だけを不完全にする欠落がある。

たとえば候補の `verdict` や `signals` が無ければ、資金を動かすかどうかを安全に読めない。
対して `bars.us` や `summary` の欠落はレポートの情報量を下げるが、残った判断を消す理由には
ならない。ただし欠落を空文字や0へ置き換えると、正常値との区別がつかなくなる。

旧提案の [ADR-0026](0026-centralised-contract-validation.md) はseverityの境界を示したが、
JSON Schemaを比較しておらず、手書きの構造検証へ置き換える前提だった。最新の`main`へ
積み直すには、Schemaを構造の単一情報源として維持したままseverityだけを重ねる必要がある。

## 決定

`lib/contract.py`はJSON Schemaの全エラーを受け取り、次の2段階で扱う。

| 段階 | 対象 | 挙動 |
|---|---|---|
| 例外 | 判断項目の欠落、および型・語彙・形式・未知キー・組み合わせの違反 | その場で停止する |
| 警告 | 表示項目の`required`または`minItems`による欠落 | HTMLとSlackへ載せ、「不明」と表示して続行する |

構造の必須指定はSchemaから外さない。Schemaが検出した違反をPythonでseverity分類することで、
「契約上は必須だが、出力は劣化継続できる」という両方の事実を保持する。
具体的な項目分類は[`docs/report-contract.md`](../report-contract.md)を正とする。

`validate()`は警告文のリストを返す。`report.py`と`notify.py`は既存の`warnings`へ必ず合流し、
欠落箇所を空欄や0ではなく「不明」として表示する。

## 検討した代替

### 手書きvalidatorへ置き換える

不採用。JSON Schemaと同じキー・型・語彙をPythonへ写すことになり、構造の正が二重化する。
最新`main`で追加された市場と通貨、数値範囲などの制約を落とす後退にもなる。

### 表示項目をSchemaで任意にする

不採用。欠落がSchema違反として検出されなくなり、警告を出すために別の必須一覧が必要になる。
Schema単体を読んだときにも、本来必要な表示項目が分からなくなる。

### strict用と表示用の2つのSchemaを持つ

不採用。同じ構造を2ファイルへ書くため、変更時に制約がずれる。単一のSchemaが返すエラーを
分類する方が、構造の定義を複製しない。

### すべての契約違反で停止する

不採用。判断が残っているのに表示項目1つの欠落でHTMLとSlackの両方が失われ、日次処理の
観測可能性が下がる。表示欠落は隠さず警告することで劣化継続を許す。

## 結果

表示項目の欠落があっても日次出力を継続できる一方、警告を読み飛ばすと不完全な出力を
使い続けられる。型や形式が壊れた表示値は警告へ降格しないため、欠落以外の表示不備では
処理が止まる。

表示項目を増やすときは、Schemaの構造定義に加えてPythonのseverity分類と表示側の
「不明」フォールバックを揃えて変更する必要がある。構造そのものはSchemaだけに置くが、
severityという別の関心はPythonと意味契約に追加される。

## 再評価のサイン

- 同じ警告が繰り返され、表示欠落が定常化したとき
- 警告の見落としによって利用者の判断へ影響が出たとき
- severity分類の項目追加漏れが繰り返されたとき
- JSON Schemaのannotationや別の標準機構でseverityを一元化できるようになったとき
