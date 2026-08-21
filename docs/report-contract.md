# 中間表現の仕様（`reports/*_evening.json`）

夕方ブリーフの分析結果を、HTML レポート（`report.py`）と Slack 通知（`notify.py`）が
読める形にしたもの。**この書式の正はこのファイル。** スキル定義や ADR に写経しない
（→ [ADR-0020](adr/0020-intermediate-report-json.md)）。

`screen.py` / `analyze.py` の**出力の読み方**は
[`docs/output-contract.md`](output-contract.md) が正。こちらは、その読み方を通した
**判断を載せる器**の仕様である。

## 位置づけ

```
screen.py --json / analyze.py  ──┐
                                 ├→ reports/YYYY-MM-DD_evening.json
LLM の判断・シナリオ・散文 ──────┘         │
                                          ├→ report.py → reports/YYYY-MM-DD_evening.html
                                          └→ notify.py → Slack
```

- **機械データ**（価格・指標・score・決算注記）はスクリプトの出力をそのまま写す
- **判断と散文**は LLM が書く。判断ラベルの定義の正は
  [`journal/README.md`](../journal/README.md)
- `reports/` は `.gitignore` 済み。保有情報が入るのでここ以外に書かない
  （→ [ADR-0008](adr/0008-no-holdings-in-repo.md)）

## トップレベル

| キー | 型 | 必須 | 内容 |
|---|---|---|---|
| `schema` | int | ✓ | 契約のバージョン。現在は `1`。**`report.py` が一致を検証する** |
| `date` | str | ✓ | 分析日 `YYYY-MM-DD`（ファイル名と一致させる） |
| `generated_at` | str | ✓ | 生成時刻 ISO8601（JST オフセット付き） |
| `bars` | obj | ✓ | 確定足の日付。`{"jp": "YYYY-MM-DD", "us": "YYYY-MM-DD"}` |
| `stale_bars` | bool | | 確定足が前回エントリと同じなら `true`（既定 `false`） |
| `holdings_as_of` | list | ✓ | 保有データの基準日。下記 |
| `effective_holdings` | obj | ✓ | 実効保有の計算結果。下記 |
| `market_tone` | obj | | 地合い。`{"label": "リスクオン", "prose": "…"}` |
| `holdings` | list | ✓ | 保有銘柄。下記 `Position`（空配列可） |
| `candidates` | list | ✓ | スクリーニング候補。下記 `Candidate`（空配列可） |
| `screen` | obj | ✓ | `{"universe": 25, "market": "all", "failures": 0}`。3 キーとも必須 |
| `summary` | str | ✓ | 総括の散文。全銘柄に共通するリスクと優先アクション 1 つ |
| `assumptions` | list[str] | | スケジュール実行で確認を取らずに置いた前提 |
| `warnings` | list[str] | | 警告（保有データの鮮度、取得失敗、ジャーナルの解釈不能行など） |

`holdings_as_of` は資産クラスごとに基準日が異なりうるため配列にする
（→ [`output-contract.md`](output-contract.md) の「保有データの鮮度」）。

```json
[{"as_of": "2026-07-22", "label": "株式", "count": 12},
 {"as_of": "2026-06-30", "label": "自動運用", "count": 6}]
```

`effective_holdings` は `stock-check` が冒頭に置くと定めているものをそのまま入れる。
**執行が 0 件でも `lines` に「執行記録なし（as_of 時点のまま）」を入れる。** 空にすると、
記録が無いのか拾い忘れたのかが読み手に区別できない。

```json
{"executions": 2, "lines": ["9999 100株 → 50株 (08/18 に -50株 @¥1,234)", "他 11 銘柄は変更なし"]}
```

## `Position`（保有銘柄）

| キー | 型 | 必須 | 内容 |
|---|---|---|---|
| `ticker` | str | ✓ | 生ティッカー（JP は 4 桁） |
| `name` | str | | 銘柄名 |
| `shares` | num | | 実効保有の株数 |
| `currency` | str | ✓ | `"JPY"` / `"USD"` の**どちらか**（値も検証する） |
| `price` | num | ✓ | 確定足の終値 |
| `change_pct` | num | | 前回エントリ比の変化率（%） |
| `verdict` | str | ✓ | ホールド / 積増し / 部分利確 / 売却 / 保留 |
| `scenario` | str | | 前進 / 停滞 / 否定接近 |
| `signals` | obj | ✓ | 4 軸のシグナル。下記 |
| `levels` | obj | | `{"support": …, "resistance": …, "invalidation": …}` |
| `closes` | list[num] | | スパークライン用の終値（古い順、30〜90 本） |
| `earnings` | obj | | `{"note": "決算 2026-08-25 (あと5日)", "warn": true}` |
| `reference_only` | bool | | 自動運用口座など判断対象外なら `true`（既定 `false`） |
| `prose` | obj | ✓ | 散文。下記 |

`prose`:

```json
{"change": "前回からの変化 (1〜2 行)",
 "scenario": "シナリオ進捗の説明",
 "reasons": ["判断の根拠 2〜3 点"],
 "trigger": "日足終値で ¥1,100 を割ったら…"}
```

**`prose` は必須。** HTML 単独でレポートとして成立させるための本文で、これが無いと
グラフィックだけが並んで読み手が判断を再構成できない。

`reference_only: true` の銘柄には `verdict` を付けない（`"—"` を入れる）。
自動運用口座は分析しても執行されないため、判断ラベルを付けると実行可能な判断と
区別できなくなる。地合いの材料としては使う。

## `Candidate`（スクリーニング候補）

| キー | 型 | 必須 | 内容 |
|---|---|---|---|
| `ticker` / `name` / `market` | str | ✓ / / ✓ | `market` は `"jp"` / `"us"` |
| `currency` | str | ✓ | `"JPY"` / `"USD"` の**どちらか**（値も検証する） |
| `price` | num | ✓ | 確定足の終値 |
| `change_pct` | num | | 直近足の変化率（%） |
| `score_atr` | num | ✓ | `screen.py` の `score`（ATR 単位） |
| `pass_reason` | str | ✓ | 通過理由。`screen.py` の `reasons` を連結したもの |
| `range` | obj | ✓ | `{"low": …, "high": …, "pos_pct": 82}` — 直前 20 日レンジと終値位置 |
| `atr_pct` | num | | 日次 ATR（%） |
| `turnover` | str | | 20 日平均売買代金（表示用に整形した文字列） |
| `earnings` | obj | | `Position` と同じ |
| `verdict` | str | ✓ | 買い / 見送り / 決算後に再判定 / 保留 |
| `signals` | obj | ✓ | 4 軸のシグナル |
| `levels` | obj | | `{"support": …, "resistance": …}` |
| `closes` | list[num] | | スパークライン用の終値 |
| `prose` | obj | ✓ | 下記 |

`prose`:

```json
{"strong": ["強い点 (数字を添える)"],
 "weak": ["弱い点"],
 "check": "日足終値で ¥XXX を維持できるか"}
```

**`weak` が空の「買い」を書かない。** 弱点の無い候補は、見ていないだけである。

## `signals`（4 軸のシグナル）

保有・候補で共通。キーは固定で、値は評価。

| キー | 軸 |
|---|---|
| `weekly` | 週足トレンド（上位トレンド） |
| `daily` | 日足モメンタム（執行判断） |
| `overheat` | 過熱度 |
| `volume` | 出来高・OBV |

値は `"good"` / `"warn"` / `"bad"` / `"unknown"` の 4 値。
**色ではなく評価を入れる** — 軸ごとに「良い」の向きが違う（過熱度は高いほど悪い）ため、
色を直接書くと軸ごとの意味が読み手に伝わらない。配色は `report.py` が決める。

`unknown` はデータ不足を指す。週足が `insufficient` を返す銘柄、`ema200` が `null` の
銘柄がこれにあたり、`verdict` は `保留` になる。

任意で短いラベルを添えられる:

```json
{"weekly": "good", "daily": "warn", "overheat": "bad", "volume": "unknown",
 "labels": {"weekly": "EMA20>50>200", "overheat": "RSI 78"}}
```

## 資金が動く判断（メンションのゲート）

`lib/verdicts.py` の `ACTIONABLE_VERDICTS` が正。現在は **買い / 積増し / 売却** の 3 つ。

- HTML のヒーローと Slack のメンションは、**どちらもこの判定だけを見る**
- 判定は JSON の `verdict` の値で機械的に決まる。散文からの抽出はしない
  （→ [ADR-0021](adr/0021-slack-webhook-notification.md)）

## 契約違反の扱い

**検証の実装は [`lib/contract.py`](../lib/contract.py) の `validate()` に集約してある。**
`report.py` は描画の前にこれを 1 回呼び、通ったあとは必須項目が揃っている前提で描く。

以前は使う場所ごとに検証していたが、その形だと**項目ごとに書き忘れる機会がある**
（実際にレビューで 4 ラウンド・計 15 件の漏れが 1 件ずつ指摘された）。1 箇所に
まとめると、何を検証していて何が漏れているかがコードを読めば分かる。
検証している項目の一覧は `tests/test_contract.py` が持つ。

必須キーが欠けていたら**その場で落ちる**。既定値で埋めて進むと、LLM が
書き漏らした項目が静かに空欄になり、「判断が無かった日」と「書き漏らした日」が
出力から区別できなくなる。

- `KeyError` — 必須のキーが無い
- `ValueError` — キーはあるが値が契約外（語彙違反・空・組み合わせ違反）

**キーの存在だけでなく値も見る。**

- **`verdict` は語彙に含まれる値でなければならない**（`lib/verdicts.py` の
  `HOLDING_VERDICTS` / `CANDIDATE_VERDICTS`）。`"買い "` のような揺れを通すと、
  カードには表示されるのに `actionable_items()` が拾わず、買い判断がヒーローからも
  Slack のメンションからも静かに消える
- **`effective_holdings.lines` は空にできない。** 執行 0 件でも
  「執行記録なし（as_of 時点のまま）」を入れる
- **`screen.failures` は候補ゼロと混ぜない。** 取得に失敗した銘柄は
  「条件を満たさなかった」のではなく「判定できていない」。HTML では見出しと
  候補ゼロの文言の両方に件数を出す。`universe` / `market` も同じく必須
- **`currency` は `JPY` / `USD` のどちらか。** 未知の値を `USD` に倒すと、
  日本株の価格が `$3,120.00` のように表示される
- **`signals` は 4 軸すべてが必要で、値も語彙に含まれること。** 軸の欠落や
  表記揺れ（`"goood"`）を `unknown` に倒すと、**契約上「データ不足」を意味する値**が
  書き漏らしから作られる。`unknown` は `verdict: 保留` の根拠でもあるため、偽の根拠になる
- **`ticker` は空にできない。** 銘柄を特定できない判断がヒーローと Slack に並ぶ
- **`schema` は一致を検証する。** 見ないと、構造の違う将来の版を v1 として
  部分的に描いてしまう
- **`bars` は `jp` / `us` の両方が必要。** 片方だけだと、その市場のデータ鮮度を
  確認できないまま読むことになる
- **`candidates[].market` は `jp` / `us` のどちらか。** どの市場のスクリーニング結果か
  失った候補を通さない
- **`holdings_as_of` は空にできない。** 基準日が無いとデータ鮮度を確認できない

### 組み合わせの規則

単独では正しくても、組み合わせると契約違反になるものがある。

- **`verdict: "買い"` には `prose.weak` が必要**（非空）。弱点の無い候補は、
  見ていないだけである
- **資金が動く判断（買い / 積増し / 売却）と `signals` の `unknown` は併存できない。**
  データ不足のまま資金を動かす提案がヒーローと Slack に載るため。
  ただし**軸 1 本の `unknown` で常に `保留` とまでは強制しない** — 出来高だけが
  取れない日に保有の判断が一切出せなくなり、運用が止まる。強制するのは
  資金が動く判断との併存だけ
