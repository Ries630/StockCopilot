# StockCopilot

株式 (日本株・米国株、現物) のスクリーニングと保有分析を行う個人用ツール。

**発注機能は持たない。** 分析と提案だけを行い、執行は人間が手動で判断する。
証券会社の取引 API を追加しないことをプロジェクトの規範としている。

仮想通貨向けの兄弟プロジェクト TradingCopilot から指標エンジンを移植しているが、
コードは共有せずコピー流用している (資産クラスごとにデータ源・市場時間・
リスクの前提が違うため、共通化より独立を優先する)。

## 設計の要点

### 確定足だけを使う

ブレイクアウトの判定は形成中の足を混ぜると「抜けた」が引け後に戻る誤検知になる。
`lib/datasource.py` の `drop_forming_bar()` が、市場ごとの引け時刻
(JP 15:30 JST / US 16:00 ET、いずれも 30 分のバッファ付き) で未確定の足を落とす。
24/7 の仮想通貨と違い、株式は取引所カレンダーに従う必要がある。

### 決算日をトリガーの有効性として扱う

決算はギャップ要因であり、「日足終値で X を割ったら」という確定足ベースのトリガーを
**飛び越えて執行不能にする**。`analyze.py` は決算日を併記し、前後 7 日以内なら警告する。
日程を知らせるためではなく、そのトリガーが機能するかを判断するための情報として出す。

### 保有情報をリポジトリに持たない

このリポジトリは public である。保有銘柄・株数・口座名は一切コミットしない。
保有は実行時に `lib/holdings.py` が別プロジェクト (Investment) の生成 JSON を
read-only で読むだけで、リポジトリ側には持たない。分析の継続記録である
`journal/journal.md` も `.gitignore` 済みで、書式仕様 (`journal/README.md`) だけを追跡する。

CI に追跡状況のチェックを入れ、目視レビューに頼らず機械的に担保している。

### スクリーナーは「買い」を判定しない

`screen.py` は機械条件で候補を絞るだけで、採否は必ず `analyze.py` の分析を通す。
この分離により、スクリーナーが外しても失うのは分析 1 回分の工数だけで済む。
候補ゼロは正常な出力であり、埋め草の候補を作らない。

## 環境

**uv + PEP 723** で管理する。`requirements.txt` も `pyproject.toml` も置かない
(依存は各スクリプトの先頭にインラインで書く)。`pip install` / `python -m venv` は使わない。

```bash
git clone https://github.com/Ries630/StockCopilot.git
cd StockCopilot
```

[uv](https://docs.astral.sh/uv/) が入っていれば、依存は初回実行時に自動で解決される。

## 使い方

```bash
# 候補の機械スクリーニング
uv run screen.py                  # JP + US 全ユニバース
uv run screen.py --market jp      # 日本株のみ
uv run screen.py --json           # JSON 出力

# テクニカル分析 (日足 + 週足)
uv run analyze.py 7203 MSFT       # 銘柄を指定
uv run analyze.py                 # 引数省略で保有全銘柄

# データ源の疎通確認
uv run lib/datasource.py --ticker 7203
```

`analyze.py` を引数なしで実行する保有モードは、Investment プロジェクトの生成物を
参照する。存在しない環境ではティッカーを引数で渡す。

## 構成

| パス | 役割 |
|---|---|
| `lib/datasource.py` | 株価取得アダプタ (yfinance)。データ源の差し替えはこのファイルに閉じる |
| `lib/indicators.py` | 指標エンジン (RSI / MACD / EMA / BB / ATR / StochRSI / OBV / ADX) |
| `lib/holdings.py` | 保有の読み込み (read-only) |
| `config/universe.py` | スクリーニング対象ユニバースとパラメータ |
| `screen.py` | 候補の機械スクリーニング |
| `analyze.py` | 保有・指定銘柄のテクニカル分析 |
| `journal/README.md` | 分析ジャーナルの書式仕様 (本体は追跡対象外) |
| `tests/` | テスト (ネットワークアクセスなし) |

## データ源

JP・US とも **yfinance**。JP は 4 桁コードを `{code}.T` に正規化する。

J-Quants への差し替えは検証済みだが現状見送っている。無料プランは約 12 週の
データ遅延があり、ライブのスクリーニングに使えないため (Light プラン契約時のみ
JP アダプタを差し替える)。差し替え先は `lib/datasource.py` の
`fetch_ohlcv()` / `fetch_next_earnings()` の 2 関数に閉じている。

## 開発

```bash
uv run run_tests.py                    # テスト
uv run run_tests.py -k datasource -v   # 絞り込み
uv run --with ruff ruff check .        # lint
```

テストはネットワークにアクセスしない。yfinance を叩くと実行日と市場の状態で
結果が変わり、CI が不安定になるため、時刻依存のロジックは判定時刻を注入して検証する。

CI (GitHub Actions) が pull request と main への push で lint・テスト・
保有情報の追跡チェックを実行する。

## Claude Code スキル

このリポジトリは [Claude Code](https://claude.com/claude-code) のスキルから
呼ばれることを前提にしている (スキル定義そのものはリポジトリ外)。

- `stock-check` — 保有株のテクニカル分析とシナリオ追跡。ジャーナルに継続記録を残す
- `stock-screen` — ユニバースからの候補抽出 (未作成。現状は `screen.py` を直接実行する)

スキルなしでも上記のコマンドとして単体で動作する。
