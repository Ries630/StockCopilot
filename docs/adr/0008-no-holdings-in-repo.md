# ADR-0008: 保有情報と売買意図をリポジトリに残さない

- ステータス: 承認済み
- 日付: 2026-08-06
- 関連: `71476a6`、[#2](https://github.com/Ries630/StockCopilot/pull/2)、[#6](https://github.com/Ries630/StockCopilot/issues/6)、[ADR-0002](0002-journal-tracked-in-repo.md) を置換

## 背景

2026-08-06、プロジェクトを `~/Documents/Claude/Projects/StockCopilot` から
`~/Repositories/StockCopilot` へ移し、public リポジトリ
https://github.com/Ries630/StockCopilot として公開した。
（**公開そのものを決めた理由は記録に無い。** 設計メモは移動と公開の事実だけを書いている）

公開時点の状態:

- `journal/journal.md` が追跡されていた（[ADR-0002](0002-journal-tracked-in-repo.md)）。
  保有銘柄・株数・売買判断の時系列が入るファイル
- `reports/` は未指定。保有モードで実行すると銘柄・株数が入りうる
- `.env.*` も未指定
- 保有そのものは `lib/holdings.py` が Investment の生成物
  (`output/report_data_*.json` の `stock.holdings`) を read-only で読む設計で、
  リポジトリ側には持っていなかった

## 決定

保有銘柄・株数・口座名・売買判断をリポジトリに残さない。守るのは**銘柄名の秘匿ではなく、
資産と売買意図をリポジトリに残さないこと**。

- 該当ファイルは `.gitignore` に置き、追跡されていないことを CI で検査する
- Issue・PR・コミットメッセージでも銘柄に触れず、「保有銘柄A」等に置き換える
- 分析結果 (`screen.py` / `analyze.py` の出力) の貼り付けも同じ扱いにする

## 検討した代替

- **目視レビューで担保する** — public リポジトリでの漏洩を目視レビューだけに頼らないため、
  CI に追跡チェックを入れた。`journal/journal.md` / `reports` / `data` / `.env` が
  追跡されていたら落とす
- **リポジトリを private にする** — （記録に無い）

## 結果

- **ジャーナル本体がバックアップも履歴管理もされない。** ローカルのファイル 1 つで、
  他マシンから同じ記録を見られない
- **Issue・PR で具体的な銘柄を挙げて議論できない。**
  [#3](https://github.com/Ries630/StockCopilot/issues/3) のように、実測ケースは銘柄名を
  伏せて価格と ATR だけで書くことになる
- 分析結果を貼れないので、不具合の再現手順を記録に残しにくい
- `config/universe.py` に保有銘柄を書き足せない。もっとも保有は既定で母集団から
  除外されるので書く意味がそもそも無い。この理由は当初「実行時に `held_tickers()` から
  動的にマージされる」と誤記されており、
  [#6](https://github.com/Ries630/StockCopilot/issues/6) / `00ac7f1` で
  「除外フィルタである」に訂正した
- 購入意図であるウォッチリストも同じ扱いになった → [ADR-0010](0010-three-layer-universe.md)
