# StockCopilot — エージェント向けプロジェクトガイド

株式 (日本株・米国株、現物) のスクリーニングと保有分析。**発注機能は持たない** (分析・提案のみ)。
TradingCopilot (仮想通貨) の兄弟プロジェクト ([ADR-0001](docs/adr/0001-separate-sibling-project.md))。

**設計判断の理由・却下した代替・その時点の測定値は [`docs/adr/`](docs/adr/README.md) にある。**
このファイルには結論とリンクだけを置く。判断を変えるときは新しい ADR を書いてから実装する
(`adr` skill)。遡り作成の一次資料になった設計メモは
`~/.claude/plans/tradingcopilot-trading-copilot-morning-b-toasty-duckling.md`。

## 環境前提

このプロジェクトは **uv + PEP 723** で Python 環境を管理する。
**`pip install` / `python -m venv` を使わないこと。**
`requirements.txt` も `pyproject.toml` も置かない (lint 設定は `ruff.toml` に切り出している)
→ [ADR-0003](docs/adr/0003-uv-pep723-inline-deps.md)

```bash
cd ~/Repositories/StockCopilot
uv run screen.py            # スクリーナー
uv run analyze.py 7203 AAPL # 保有分析 (引数省略で保有全銘柄)
uv run run_tests.py         # テスト (ネットワークアクセスなし)
uv run --with ruff ruff check .  # lint
```

## データ源

- **JP/US とも yfinance**。JP は `{4桁コード}.T` に正規化。差し替えは `lib/datasource.py` の
  `fetch_ohlcv()` / `fetch_next_earnings()` に閉じる。J-Quants は検証済み・現状不可
  → [ADR-0004](docs/adr/0004-yfinance-as-data-source.md)
- **確定足のみ使用** (look-ahead 防止)。ロジックは `lib/datasource.py` の `drop_forming_bar()`
  → [ADR-0005](docs/adr/0005-completed-bars-only.md)
- **決算日はトリガーが有効かを判断するために取る** (日程を報告するためではない)
  → [ADR-0009](docs/adr/0009-earnings-date-as-trigger-validity.md)
- **決算日を取得できなかった個別株は「不明」を出力に出す** (ETF は出さないのが正常)。
  切り分けは `lib/datasource.py` の `fetch_instrument_type()`
  → [ADR-0016](docs/adr/0016-surface-unavailable-earnings-date.md)

## 構成

- `lib/datasource.py` — 株価取得アダプタ (yfinance)。差し替えはこのファイルに閉じる
- `lib/indicators.py` — 指標エンジン (TradingCopilot `swing/_analyze.py` から移植。pandas + ta)
- `lib/earnings.py` — 決算注記。analyze.py と screen.py が共用 (警告期間と文言の正)
  → [ADR-0012](docs/adr/0012-shared-earnings-module.md)
- `lib/holdings.py` — Investment プロジェクトの生成物から株式保有を読む (**read-only**)。
  `held_tickers()` はジャーナルの執行記録と合成した**実効保有**を返す
  → [ADR-0015](docs/adr/0015-journal-executions-machine-read.md)
- `lib/journal.py` — ジャーナルの `### 執行` を読むパーサ (約定日 / 銘柄 / 残株数の 3 項目のみ)。
  書式の正は `journal/README.md`
- `config/universe.py` — 探索ユニバースとパラメータ。母集団は
  ウォッチリスト / 探索ユニバース / 保有 (除外) の 3 層
  → [ADR-0010](docs/adr/0010-three-layer-universe.md)
- `config/watchlist.py` — ウォッチリスト (保有検討中)。**追跡対象外**。
  雛形は `config/watchlist.example.py`。未作成なら空リスト扱い
- `screen.py` — 候補の機械スクリーニング。候補を絞るだけで、買い判断は analyze.py の
  分析で行う → [ADR-0006](docs/adr/0006-screener-does-not-decide-buys.md) /
  [ADR-0017](docs/adr/0017-screen-report-writes-verdict.md)。通過条件は状態ではなく事象
  → [ADR-0011](docs/adr/0011-event-based-screen-thresholds.md)
- `analyze.py` — 保有/指定銘柄のテクニカル分析
- `docs/output-contract.md` — **`screen.py` / `analyze.py` の出力の読み方の正**。
  決算注記・保有の鮮度・警告の意味はここに集約する (スキル側に写経しない)
  → [ADR-0018](docs/adr/0018-bundle-skills-in-repo.md)
- `docs/report-contract.schema.json` — **中間表現JSONの構造の正**。型・必須・語彙は
  JSON Schemaで検証し、業務上の組み合わせは`lib/contract.py`で検証する
  → [ADR-0021](docs/adr/0021-json-schema-for-report-contract.md)
- `journal/README.md` — ジャーナルの書式仕様 (本体 `journal/journal.md` は **git 追跡対象外**)
- `tests/` + `run_tests.py` — テスト。**ネットワークにアクセスしない** (yfinance を叩くと
  実行日と市場の状態で結果が変わり CI が不安定になる)。時刻依存のロジックは
  判定時刻を注入して検証する (`drop_forming_bar(..., now=...)`)
- `.github/workflows/ci.yml` — PR と main への push で lint・テスト・**保有情報の追跡チェック**を実行

## 他プロジェクトとの関係

- **Investment** (`~/Documents/Claude/Projects/Investment`): 保有銘柄は
  `output/report_data_*.json` の `stock.holdings` を読むだけ。module import はしない (疎結合)。
  Investment 側のファイルを書き換えないこと。
- **TradingCopilot**: コード共有はしない (コピー流用の慣習)。指標エンジンの移植元
  → [ADR-0001](docs/adr/0001-separate-sibling-project.md)
- **dexter-jp** (`~/Repositories/dexter-jp`): ファンダ特化の日本株リサーチエージェント (TS/Bun)。
  役割分担 = テクニカル・スクリーニングは本プロジェクト (決定的コード)、
  候補銘柄の財務深掘り (健全性・決算・有報) は dexter-jp。
  dexter-jp の LLM 入りツール (`company_screener` 等) を機械スクリーニングに使わないこと
  → [ADR-0007](docs/adr/0007-split-technical-and-fundamental-research.md)

## 安全規範

- 発注コード・証券会社の取引 API を追加しない (このプロジェクトのスコープ外)
- `.env` を作る場合 (J-Quants 移行時) は `.gitignore` 登録を確認し、コミットしない
- スクリーナーに裁量的な「買い判定」ロジックを足さない。候補の採否は分析 (analyze) を通す

### public リポジトリ前提の規範

このリポジトリは公開されている。**保有情報 (銘柄・株数・口座名・売買判断) を
リポジトリに残さない**こと。守るのは銘柄名の秘匿ではなく、資産と売買意図をリポジトリに
残さないこと → [ADR-0008](docs/adr/0008-no-holdings-in-repo.md)

- `journal/journal.md` / `reports/` / `data/` / `config/watchlist.py` はコミットしない
  (`.gitignore` 済み・CI で検査)。ウォッチリストは保有ではないが**購入意図そのもの**なので
  同じ扱いにする。雛形 `config/watchlist.example.py` だけを追跡する
- `config/universe.py` に**実際の保有銘柄を書き足さない**。保有は screen.py が
  `held_tickers()` を除外フィルタとして使い既定で母集団から落とすので、書いても意味がない
- Issue・PR・コミットメッセージに保有銘柄や株数を書かない。銘柄に触れる必要がある場合は
  「保有銘柄A」等に置き換える
- 分析結果の貼り付け (screen.py / analyze.py の出力) も同様に扱う

## 関連スキル

スキル定義は `.agents/skills/` に同封してある (汎用化済み。個人の運用教訓は
追跡対象外の `journal/lessons.md` に分離) → [ADR-0018](docs/adr/0018-bundle-skills-in-repo.md)。
`.claude/skills/` は同じ実体への symlink で、Claude Code 用の橋渡し
→ [ADR-0019](docs/adr/0019-agent-agnostic-instructions.md)

- `stock-check` — 保有株のテクニカル + シナリオ追跡 (analyze.py + journal)。
  実体は `.agents/skills/stock-check/SKILL.md`
- `stock-screen` — ウォッチリスト + 探索ユニバースから候補を機械抽出し (screen.py)、
  テクニカル分析で `買い / 見送り / 決算後に再判定 / 保留` を判断する (analyze.py + journal)。
  実体は `.agents/skills/stock-screen/SKILL.md`
  → [ADR-0017](docs/adr/0017-screen-report-writes-verdict.md)
