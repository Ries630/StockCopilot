# ADR-0003: 依存は PEP 723 のインライン宣言に書き、uv で走らせる

- ステータス: 承認済み
- 日付: 2026-08-06
- 関連: `4fa1e92`、`564ad38`、[#2](https://github.com/Ries630/StockCopilot/pull/2)、`ruff.toml`

## 背景

TradingCopilot が **uv + PEP 723 インライン依存**で運用されており、`requirements.txt` を
作らない慣習があった。Investment は python3 運用で系統が違う
（[ADR-0001](0001-separate-sibling-project.md) で隔離した相手）。

依存は `pandas` / `numpy` / `ta` / `yfinance` の 4 つ。実行するスクリプトは
`screen.py` と `analyze.py` の 2 本で、いずれも単体で走るエントリポイント。
ライブラリとして配布する予定は無い。

## 決定

依存は各スクリプト先頭の PEP 723 インライン宣言に書き、`uv run <script>` で実行する。
`requirements.txt` も `pyproject.toml` も置かない。`pip install` / `python -m venv` は使わない。

## 検討した代替

- **`requirements.txt` + venv** — TradingCopilot の慣習に反する。
  （これ以上の比較の記録は無い）
- **`pyproject.toml` を置く** — 置くと uv がこのディレクトリを**パッケージプロジェクトとして
  扱い**、PEP 723 のインライン依存でスクリプトを走らせる運用と食い違う

## 結果

- **lint 設定を `pyproject.toml` に置けない。** `ruff.toml` に切り出している
- **テストランナーも PEP 723 で書くことになった。** `pytest` を依存ファイルに足せないので、
  `run_tests.py` が自前のインライン依存で pytest を起動する
- 依存の宣言が各スクリプトのヘッダに重複する。バージョンを上げるときは全ヘッダを直す
- このプロジェクトを package として import する経路が無い。
  跨プロジェクトの module 共有を避ける [ADR-0001](0001-separate-sibling-project.md) と
  方向は一致している
