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
uv run report.py reports/YYYY-MM-DD_evening.json           # HTML レポート
uv run notify.py reports/YYYY-MM-DD_evening.json --dry-run # Slack 通知 (確認)
uv run run_tests.py         # テスト (ネットワークアクセスなし)
uv run --with ruff ruff check .  # lint
```

Slack 通知には `.env` が要る (`cp .env.example .env`)。未設定でも落ちず、
スキップ理由が出る。

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
- **外部参照 (Web / IR) の可否は実行モードで決まる。** 定期実行は引かない / 対話実行は引いてよい。
  正は `docs/output-contract.md` の「実行モードと外部参照」。**手動確認リストで埋めない**

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
- `lib/market_observation.py` — 前回と今回の確定足を市場別に比較し、更新市場だけを分析して
  停滞・取得不能市場を前回結果から引き継ぐ。保有は現在のidentity/stateを正にして分析だけを
  合流する → [ADR-0029](docs/adr/0029-market-specific-bar-observation.md) /
  [ADR-0030](docs/adr/0030-current-holding-state-with-carried-analysis.md)
- `config/universe.py` — 探索ユニバースとパラメータ。母集団は
  ウォッチリスト / 探索ユニバース / 保有 (除外) の 3 層
  → [ADR-0010](docs/adr/0010-three-layer-universe.md)。
  **日本株の日本語名 `NAMES_JP` もここが正** → [ADR-0023](docs/adr/0023-japanese-stock-display-names.md)
- `lib/names.py` — 銘柄名の解決 (辞書 → yfinance の英語名 → None)。
  **日本株は 4 桁コードだけでは判別できないので出力に必ず名前を併記する**
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
  JSON Schemaで検証し、業務上の組み合わせとseverityは`lib/contract.py`で検証する
  → [ADR-0021](docs/adr/0021-json-schema-for-report-contract.md) /
  [ADR-0027](docs/adr/0027-contract-validation-severity.md)
- `docs/report-contract.md` — **中間表現の意味・組み合わせ規則・判断ラベル定義の正**。
  判断と機械データの境界をここで定義する
  → [ADR-0020](docs/adr/0020-intermediate-report-json.md)
- `lib/verdicts.py` — 判断ラベルの定義と「資金が動く判断」の判定。
  `ACTIONABLE_VERDICTS` が **Slack のメンションを鳴らす条件の正** (買い / 積増し / 売却)
- `finalize_report.py` — 市場別の更新状態に従って今回結果と前回結果を決定的に合流し、
  中間表現を確定する。市場判定と合流規則は `lib/market_observation.py` に集約する
- `report.py` — 中間表現 → 自己完結 HTML (`reports/*.html`)。判断も指標計算もしない。
  外部リソースを読み込まない。**用語の説明は本文に書かず `GLOSSARY` のポップオーバーに置く**
  → [ADR-0024](docs/adr/0024-glossary-popovers.md)
  あわせて `reports/latest.json` を更新する（次回のシリーズ分析の起点）
- `notify.py` — 中間表現 → Slack (Incoming Webhook)。**LLM は Slack ツールを呼ばない**。
  毎日投稿し、メンションは資金が動く判断がある日だけ
  → [ADR-0022](docs/adr/0022-slack-webhook-notification.md)
- `journal/README.md` — **ジャーナルの役割と書式の正**。執行の台帳・運用メモに加え、
  中間表現を作らない単体分析の履歴を持つ
  → [ADR-0028](docs/adr/0028-standalone-analysis-journal-history.md)。
  本体 `journal/journal.md` は **git 追跡対象外**
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
- `.env` (Slack の資格情報 / J-Quants 移行時) は `.gitignore` 済み。コミットしない。
  雛形 `.env.example` にも実際の値を書かない
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

- `stock-brief` — 平日夕方の定期ブリーフ。stock-screen → stock-check → 中間表現 JSON →
  HTML → Slack → ジャーナルを通しで回す束ね役。実体は `.agents/skills/stock-brief/SKILL.md`
- `stock-check` — 保有株のテクニカル + シナリオ追跡 (analyze.py + journal)。
  実体は `.agents/skills/stock-check/SKILL.md`
- `stock-screen` — ウォッチリスト + 探索ユニバースから候補を機械抽出し (screen.py)、
  テクニカル分析で `買い / 見送り / 決算後に再判定 / 保留` を判断する (analyze.py + journal)。
  実体は `.agents/skills/stock-screen/SKILL.md`
  → [ADR-0017](docs/adr/0017-screen-report-writes-verdict.md)

## Code Review Rules

- レビューコメントは日本語で記載する
