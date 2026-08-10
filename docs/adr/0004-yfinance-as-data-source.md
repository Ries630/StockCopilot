# ADR-0004: JP/US とも yfinance を使い、差し替え点を 2 関数に閉じる

- ステータス: 承認済み
- 日付: 2026-08-06
- 関連: `4fa1e92`、設計メモ `~/.claude/plans/tradingcopilot-trading-copilot-morning-b-toasty-duckling.md`

## 背景

データ源は当初**未確定**で、アダプタ層で吸収する方針だけが決まっていた。

J-Quants は隣のプロジェクト dexter-jp (`~/Repositories/dexter-jp`) が既に API キーを持ち、
クライアント実装も読める状態にあった。そのキーを実測した結果 (2026-08-06):

| 項目 | 実測 |
|---|---|
| プラン | 無料 |
| 取得可能期間 | 2024-05-14 〜 2026-05-14 |
| 遅延 | 約 12 週 |
| Light プラン | 約 ¥1,650/月 |

12 週遅れのデータはライブのスクリーニング・保有分析に使えない。

yfinance は無料・鍵不要で JP と US の両方を返す。JP は 4 桁コードを `{code}.T` に
正規化して渡す。

## 決定

JP/US とも yfinance を既定のデータ源にする。差し替え点は `lib/datasource.py` の
`fetch_ohlcv()` / `fetch_next_earnings()` の 2 関数に閉じ込め、データ源を変えても
`screen.py` / `analyze.py` とスキル本体を触らずに済む形にする。

## 検討した代替

- **J-Quants (無料プラン)** — 約 12 週遅延。ライブ用途に使えない
- **J-Quants (Light プラン)** — 遅延は解消するが月額が発生する。実装仕様は dexter-jp の
  `src/tools/finance/stock-price.ts` にある
  (`GET https://api.jquants.com/v2/equities/bars/daily`、`x-api-key` ヘッダ、
  分割調整済みの `AdjO/AdjH/AdjL/AdjC/AdjVo`)。契約したときに JP アダプタだけを差し替える
- **Stooq** — 差し替え候補として名前が挙がっただけで、比較した記録は無い

## 結果

- **yfinance が持たないものは取れない。** ETF は fundamentals を持たないため決算日が
  常に取得できず、後に 404 ログの抑制が必要になった
  ([#15](https://github.com/Ries630/StockCopilot/issues/15)、`f471da7`)
- 分割調整は yfinance の調整に委ねる。J-Quants のように調整済み列を明示的に受け取る形ではない
- 鍵不要の非公式 API なので、仕様変更・レート制限に対する保証が無い
- **1 銘柄 = 1 リクエスト。** 母集団の大きさがそのまま実行時間になる。
  crypto 版 (Hyperliquid は 1 リクエストで全銘柄の出来高が揃う) のような動的ユニバースに
  できず、明示的なリスト管理になった
  → [ADR-0010](0010-three-layer-universe.md)。決算日の取得を候補だけに絞る制約にもなっている
  → [ADR-0012](0012-shared-earnings-module.md)

## 再評価のサイン

- **J-Quants Light プランを契約したとき。** JP アダプタだけを差し替える
- 差し替えは上記 2 関数に閉じているので、スクリプトとスキルは触らずに済む見込み
