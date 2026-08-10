# ADR-0012: 決算注記を `lib/earnings.py` に置き、両エントリポイントから共用する

- ステータス: 承認済み
- 日付: 2026-08-09
- 関連: `7cc6694`、[#18](https://github.com/Ries630/StockCopilot/issues/18)、[#13](https://github.com/Ries630/StockCopilot/pull/13)

## 背景

候補が決算直前だと、確定足ベースのトリガーがギャップで飛ばされて執行できない
（[ADR-0005](0005-completed-bars-only.md) / [ADR-0009](0009-earnings-date-as-trigger-validity.md)）。
`screen.py` の段階で決算日が見えないと、「この候補を分析に回すか、決算後まで待つか」を
判断できなかった。

`stock-screen` スキルは `uv run screen.py --earnings` を既定の実行方法として書いていたが、
この引数は実装されておらず `unrecognized arguments: --earnings` で落ちていた。

`earnings_note()` と `EARNINGS_ALERT_DAYS` の実体は `analyze.py` の中にあった。
`MAX_CANDIDATES = 5`、yfinance は 1 銘柄 1 リクエスト
（[ADR-0004](0004-yfinance-as-data-source.md)）。

## 決定

`earnings_note()` と `EARNINGS_ALERT_DAYS` を `lib/earnings.py` に切り出し、`analyze.py` と
`screen.py` の両方から使う。`screen.py` に `--earnings` を追加し、**既定はオフ**。
取得は候補に絞ってから行う。

## 検討した代替

- **`screen.py` から `analyze.py` を import する** — エントリポイント同士の逆依存になる
- **`lib/datasource.py` に置く** — 「データ源の差し替えはこのファイルに閉じる」という
  同ファイルの宣言（[ADR-0004](0004-yfinance-as-data-source.md)）と食い違う
- **コピーする** — 片方だけ文言や警告期間が変わったときに「同じ銘柄なのに分析と
  スクリーニングで決算の扱いが違う」状態になる
- **母集団全体に決算取得をかける** — 通過しない銘柄 (大半) の分まで待つことになる。
  候補だけなら追加コストは最大 5 リクエスト
- **既定でオンにする** — 決算の取得はスクリーニングの通過判定に一切関与しない。
  必要なときだけ払うコストにした

## 結果

- **`--earnings` を付け忘れると決算直前の候補を見落とす。** 既定の出力には決算が出ない
- 警告期間 (`EARNINGS_ALERT_DAYS`) を変えると `analyze` と `screen` の出力が同時に変わる。
  意図した性質だが、**片方だけ変えることはできない**
- `lib/` が「データ源アダプタと指標」以外の関心事、すなわち表示文言を持ち始めた
- `earnings_note()` の実体が移ったため、`monkeypatch.setattr(analyze, "fetch_next_earnings", ...)`
  が効かなくなり、テストの差し替え先を実体側に直す必要が生じた
