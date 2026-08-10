# ADR-0007: 機械スクリーニングは決定的な Python コードで行い、dexter-jp の LLM 入りツールを使わない

- ステータス: 承認済み
- 日付: 2026-08-06
- 関連: 設計メモ `~/.claude/plans/tradingcopilot-trading-copilot-morning-b-toasty-duckling.md`

## 背景

`~/Repositories/dexter-jp` は日本株リサーチ用の LLM エージェント (TS/Bun、LangChain ループ +
Claude Agent SDK モード)。データ源は J-Quants (株価) と EDINET DB (財務) で、
**ファンダ特化・テクニカル指標は皆無**。

`company_screener` / `get_financials` といったツールは、内部で LLM を呼ぶ。

こちらが必要としているのは数百銘柄規模の機械スクリーニングで、指標計算は
[ADR-0001](0001-separate-sibling-project.md) で移植した決定的な Python コードが担う。

## 決定

指標計算と機械スクリーニングは本プロジェクトの決定的な Python コードで行う。
dexter-jp の LLM 入りツールをスクリーニングに使わない。dexter-jp から借りるのは
J-Quants の API キーと、クライアント実装 (仕様書として) だけ。

役割分担は、テクニカル・スクリーニング = 本プロジェクト、候補銘柄の財務深掘り
(健全性・決算・有報) = dexter-jp。

## 検討した代替

- **dexter-jp の `company_screener` を機械スクリーニングに使う** — 内部で LLM を呼ぶため、
  数百銘柄の機械スクリーニングにはコスト・速度・**再現性**の面で不適
- **本プロジェクトに財務取得を実装する** — （記録に無い。EDINET 対応は dexter-jp 側に
  既にあり、重複実装を検討した記録は残っていない）

## 結果

- **財務データを機械スクリーニングの条件に入れられない。** 通過条件はテクニカルと
  流動性だけになる
- 候補 → ファンダ深掘りが 1 つのコマンドで完結しない。候補を人が dexter-jp に渡す
- **米国株のファンダ深掘りの経路が無い。** dexter-jp は日本株専用

## 再評価のサイン

- フェーズ 2 (低優先) として、スクリーナー通過銘柄の深掘りに dexter-jp を呼ぶ導線を
  SKILL.md に記す案がある
- 米国株のファンダ深掘りが必要になったら、本家 dexter (Financial Datasets API 鍵が必要) の
  セットアップを別途検討する
