# Architecture Decision Records

コードから理由を読み取れない長期的な設計判断を、判断した時点の記録として残す。

## 運用

ADR の作成基準・書式・作成・更新・置換・廃止の手順は `adr` skill を正とする。
このファイルには、このリポジトリの ADR 一覧と動かない結論だけを置く。
用語とコンテキスト境界は `domain-modeling` skill を正とする。`CONTEXT.md` は同 skill が
必要時に作成・更新し、ADR には用語定義を重複して書かない。

## 意図的に書いていないもの

- **例示ティッカーをユニバース内の銘柄に統一する**（[#5](https://github.com/Ries630/StockCopilot/issues/5)）
  — `tests/test_datasource.py` がパラメータで固定している
- **ETF の 404 ログをスコープ限定で抑制する**（[#15](https://github.com/Ries630/StockCopilot/issues/15)）
  — 抑制と復帰のテスト 2 件が固定している
- **`ruff.toml` の lint 設定と DTZ 除外** — 破れば lint が落ち、理由もファイル内にある
- **`MIN_MOVE_IN_ATR` / 流動性フロア / `MAX_CANDIDATES` の値** — 定数 1 個で差し替えられ、
  `tests/test_screen.py` が挙動を固定している
- **テストがネットワークにアクセスしないこと** — 代替（カセット・実データ）を比較した記録が
  なく、取り消しコストも低い。理由は `AGENTS.md` と `ruff.toml` に置いてある
- **発注機能を持たないこと** — 規範としては最上位だが、代替を検討して落とした記録が無い。
  隔離の理由は [ADR-0001](0001-separate-sibling-project.md) の結果節にある
- **StockCopilot と CryptoTradingCopilot の現行機能差** — 正は
  [`docs/sibling-project-comparison.md`](../sibling-project-comparison.md)。
  将来の設計判断ではなく、実装に合わせて更新する比較表として管理する
- **兄弟プロジェクトの表示名への追随**（[#89](https://github.com/Ries630/StockCopilot/issues/89)）
  — 外部プロジェクトの名称変更であり、設計判断ではない。現行名の正は
  [`docs/sibling-project-comparison.md`](../sibling-project-comparison.md) とする
- **ジャーナルの書式**（[#19](https://github.com/Ries630/StockCopilot/issues/19)）
  — 正は `journal/README.md` にあり、ADR にすると二重管理になる
- **Investment 生成物の既定入力先**（[#87](https://github.com/Ries630/StockCopilot/issues/87)）
  — 可逆的なローカルパス変更であり、`tests/test_holdings.py` が移転後の既定値を固定している

## 一覧

これらは ADR 運用の開始（2026-08-10）より前に決まっていた判断を、コミット本文・Issue・PR と
設計メモ `~/.claude/plans/tradingcopilot-trading-copilot-morning-b-toasty-duckling.md` から
遡って復元したもの。日付は判断した時点を指す。

### 骨格（2026-08-06）

| # | 決定 | ステータス |
|---|---|---|
| [0001](0001-separate-sibling-project.md) | 独立した兄弟プロジェクトとして立て、指標エンジンはコピー流用する | 承認済み |
| [0002](0002-journal-tracked-in-repo.md) | 分析ジャーナル本体をリポジトリで追跡する | 廃止（[0008](0008-no-holdings-in-repo.md)） |
| [0003](0003-uv-pep723-inline-deps.md) | 依存は PEP 723 のインライン宣言に書き、uv で走らせる | 承認済み |
| [0004](0004-yfinance-as-data-source.md) | JP/US とも yfinance を使い、差し替え点を 2 関数に閉じる | 承認済み |
| [0005](0005-completed-bars-only.md) | 判定は確定足のみで行う | 承認済み |
| [0006](0006-screener-does-not-decide-buys.md) | スクリーナーは買いを判定せず、候補を絞るだけにする | 承認済み |
| [0007](0007-split-technical-and-fundamental-research.md) | 機械スクリーニングは決定的な Python コードで行い、dexter-jp の LLM 入りツールを使わない | 承認済み |
| [0008](0008-no-holdings-in-repo.md) | 保有情報と売買意図をリポジトリに残さない | 承認済み |

### 運用に載せてからの調整（2026-08-07〜09）

| # | 決定 | ステータス |
|---|---|---|
| [0009](0009-earnings-date-as-trigger-validity.md) | 決算日はトリガーの有効性を判断するために取得する | 承認済み |
| [0010](0010-three-layer-universe.md) | 母集団を 3 層にし、ウォッチリストは追跡対象外にする | 承認済み |
| [0011](0011-event-based-screen-thresholds.md) | 通過条件は状態ではなく事象にし、突破幅に下限を置く | 承認済み |
| [0012](0012-shared-earnings-module.md) | 決算注記を `lib/earnings.py` に置き、両エントリポイントから共用する | 承認済み |

### 保有の定義の統一（2026-08-12）

| # | 決定 | ステータス |
|---|---|---|
| [0015](0015-journal-executions-machine-read.md) | ジャーナルの執行記録を決定的コードで読み、母集団の除外を実効保有で行う | 承認済み |

### 決算日の取得不能の可視化（2026-08-13）

| # | 決定 | ステータス |
|---|---|---|
| [0016](0016-surface-unavailable-earnings-date.md) | 決算日を取得できなかったことを銘柄種別で切り分けて出力に出す | 承認済み |

### レポートと Slack 通知（2026-08-20〜21）

| # | 決定 | ステータス |
|---|---|---|
| [0017](0017-screen-report-writes-verdict.md) | スクリーニングのレポートには買いの判断を書く（コード側の分離は維持する） | 承認済み |
| [0020](0020-intermediate-report-json.md) | 夕方ブリーフの出力に中間表現 JSON を挟む | 承認済み |
| [0021](0021-json-schema-for-report-contract.md) | 中間表現の構造をJSON Schema、業務上の組み合わせをPythonで検証する | 承認済み |
| [0022](0022-slack-webhook-notification.md) | Slack 通知をスクリプトの Incoming Webhook に移す | 承認済み |
| [0023](0023-japanese-stock-display-names.md) | 日本株の表示名は手書きの辞書を正にし、yfinanceを落ち先にする | 承認済み |
| [0024](0024-glossary-popovers.md) | 用語の説明は本文ではなくポップオーバーに置く | 承認済み |
| [0025](0025-journal-as-ledger-and-memo.md) | ジャーナルの役割を「執行台帳 + 運用メモ」に絞る | 廃止（ADR-0028 により置換） |
| [0026](0026-centralised-contract-validation.md) | 中間表現の検証を集約し、severityを2段階にする | 廃止（ADR-0027により置換） |
| [0027](0027-contract-validation-severity.md) | JSON Schema検証に表示欠落のseverityを重ねる | 承認済み |
| [0028](0028-standalone-analysis-journal-history.md) | 単体分析はジャーナルに履歴を残す | 承認済み |
| [0029](0029-market-specific-bar-observation.md) | 確定足の更新状態を市場別に判定し、取得不能市場だけ停止する | 承認済み |
| [0030](0030-current-holding-state-with-carried-analysis.md) | 現在の保有状態を正にし、市場状態に応じて分析だけを合流する | 承認済み |

### スキル定義の置き場所（2026-08-14）

| # | 決定 | ステータス |
|---|---|---|
| [0018](0018-bundle-skills-in-repo.md) | スキル定義を汎用化してリポジトリに同封し、個人の運用教訓は追跡対象外に分離する | 承認済み |

### エージェント非依存化（2026-08-18）

| # | 決定 | ステータス |
|---|---|---|
| [0019](0019-agent-agnostic-instructions.md) | 指示ファイルとスキルをエージェント非依存の場所に置き、Claude Code へは橋渡しする | 承認済み |

### 未決・進行中

| # | 決定 | ステータス |
|---|---|---|
| [0013](0013-no-full-backtest.md) | シグナルの事後検証までを作り、フルバックテストは作らない | 提案（[#17](https://github.com/Ries630/StockCopilot/issues/17)） |
| [0014](0014-defer-machine-readable-log.md) | 機械可読ログは閾値と母集団が固まるまで作らない | 提案（[#9](https://github.com/Ries630/StockCopilot/issues/9)） |

## テンプレート

[`template.md`](template.md) をコピーして使う。作成・更新は `adr` skill から。
