# StockCopilot — Claude 向けプロジェクトガイド

株式 (日本株・米国株、現物) のスクリーニングと保有分析。**発注機能は持たない** (分析・提案のみ)。
TradingCopilot (仮想通貨) の兄弟プロジェクト。設計判断の経緯は
`~/.claude/plans/tradingcopilot-trading-copilot-morning-b-toasty-duckling.md` を参照。

## 環境前提

このプロジェクトは **uv + PEP 723** で Python 環境を管理する。
**`pip install` / `python -m venv` を使わないこと。**

```bash
cd ~/Documents/Claude/Projects/StockCopilot
uv run screen.py            # スクリーナー
uv run analyze.py 9433 AAPL # 保有分析 (引数省略で保有全銘柄)
```

## データ源 (2026-08-06 決定)

- **JP/US とも yfinance** (`lib/datasource.py` のアダプタに閉じ込め)。JP は `{4桁コード}.T` に正規化。
- J-Quants は検証済み・現状不可: dexter-jp (`~/Repositories/dexter-jp`) の無料キーは約 12 週遅延
  (取得可能期間 2024-05-14〜2026-05-14 を実測)。Light プラン契約時のみ JP アダプタを差し替える。
  実装仕様は dexter-jp `src/tools/finance/stock-price.ts` (J-Quants V2, `x-api-key`, 調整済み OHLCV)。
- **確定足のみ使用** (look-ahead 防止)。JP=15:30 JST 引け / US=16:00 ET 引けのカレンダーで
  形成中の足を落とす。ロジックは `lib/datasource.py` の `drop_forming_bar()`。

## 構成

- `lib/datasource.py` — 株価取得アダプタ (yfinance)。差し替えはこのファイルに閉じる
- `lib/indicators.py` — 指標エンジン (TradingCopilot `swing/_analyze.py` から移植。pandas + ta)
- `lib/holdings.py` — Investment プロジェクトの生成物から株式保有を読む (**read-only**)
- `config/universe.py` — スクリーニング対象ユニバースとパラメータ
- `screen.py` — 候補の機械スクリーニング (買い判定はしない。候補を絞るだけ)
- `analyze.py` — 保有/指定銘柄のテクニカル分析
- `journal/journal.md` — 分析の継続記録 (swing/journal.md 方式)

## 他プロジェクトとの関係

- **Investment** (`~/Documents/Claude/Projects/Investment`): 保有銘柄は
  `output/report_data_*.json` の `stock.holdings` を読むだけ。module import はしない (疎結合)。
  Investment 側のファイルを書き換えないこと。
- **TradingCopilot**: コード共有はしない (コピー流用の慣習)。指標エンジンの移植元。
- **dexter-jp** (`~/Repositories/dexter-jp`): ファンダ特化の日本株リサーチエージェント (TS/Bun)。
  役割分担 = テクニカル・スクリーニングは本プロジェクト (決定的コード)、
  候補銘柄の財務深掘り (健全性・決算・有報) は dexter-jp。
  dexter-jp の LLM 入りツール (`company_screener` 等) を機械スクリーニングに使わないこと。

## 安全規範

- 発注コード・証券会社の取引 API を追加しない (このプロジェクトのスコープ外)
- `.env` を作る場合 (J-Quants 移行時) は `.gitignore` 登録を確認し、コミットしない
- スクリーナーに裁量的な「買い判定」ロジックを足さない。候補の採否は分析 (analyze) を通す

## 関連スキル (予定)

- `stock-screen` — ユニバースから候補を機械抽出 (screen.py)
- `stock-check` — 保有株のテクニカル + シナリオ追跡 (analyze.py + journal)
