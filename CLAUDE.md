# StockCopilot — Claude 向けプロジェクトガイド

株式 (日本株・米国株、現物) のスクリーニングと保有分析。**発注機能は持たない** (分析・提案のみ)。
TradingCopilot (仮想通貨) の兄弟プロジェクト。設計判断の経緯は
`~/.claude/plans/tradingcopilot-trading-copilot-morning-b-toasty-duckling.md` を参照。

## 環境前提

このプロジェクトは **uv + PEP 723** で Python 環境を管理する。
**`pip install` / `python -m venv` を使わないこと。**

```bash
cd ~/Repositories/StockCopilot
uv run screen.py            # スクリーナー
uv run analyze.py 9433 AAPL # 保有分析 (引数省略で保有全銘柄)
uv run run_tests.py         # テスト (ネットワークアクセスなし)
uv run --with ruff ruff check .  # lint
```

`pyproject.toml` は置かない。置くと uv がパッケージプロジェクトとして扱い、
PEP 723 のインライン依存で動かす運用と食い違う。lint 設定は `ruff.toml` に切り出している。

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
- `journal/README.md` — ジャーナルの書式仕様 (本体 `journal/journal.md` は **git 追跡対象外**)
- `tests/` + `run_tests.py` — テスト。**ネットワークにアクセスしない** (yfinance を叩くと
  実行日と市場の状態で結果が変わり CI が不安定になる)。時刻依存のロジックは
  判定時刻を注入して検証する (`drop_forming_bar(..., now=...)`)
- `.github/workflows/ci.yml` — PR と main への push で lint・テスト・**保有情報の追跡チェック**を実行

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

### public リポジトリ前提の規範

このリポジトリは公開されている。**保有情報 (銘柄・株数・口座名・売買判断) を
リポジトリに残さない**こと。保有は実行時に `lib/holdings.py` が
Investment の生成物から読むだけで、リポジトリ側には持たない設計になっている。

- `journal/journal.md` / `reports/` / `data/` はコミットしない (`.gitignore` 済み)
- `config/universe.py` に**実際の保有銘柄を書き足さない**。保有は screen.py 実行時に
  `held_tickers()` から動的にマージされるので、書く必要がない
- Issue・PR・コミットメッセージに保有銘柄や株数を書かない。銘柄に触れる必要がある場合は
  「保有銘柄A」等に置き換える
- 分析結果の貼り付け (screen.py / analyze.py の出力) も同様に扱う

## 関連スキル

- `stock-check` — 保有株のテクニカル + シナリオ追跡 (analyze.py + journal)。
  実体は `~/.claude/skills/stock-check/SKILL.md`
- `stock-screen` (**未作成**) — ユニバースから候補を機械抽出 (screen.py)
