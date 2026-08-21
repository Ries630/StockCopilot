"""中間表現 (`reports/*_evening.json`) の検証。

**仕様の正は `docs/report-contract.md`。** ここはその実装であって仕様ではない。

## なぜ 1 箇所にまとめるか

以前は `report.py` の中で、フィールドごとに使う場所でその都度検証していた。
契約には 30 近い項目があり、この形だと **1 項目ごとに検証を書き忘れる機会がある**。
実際にレビューで 4 ラウンド・計 15 件の漏れが 1 件ずつ指摘された。

宣言的な表を 1 つ持てば、「何を検証しているか」と「何が漏れているか」が
コードを読めば分かる。契約に項目を足したときも、ここに足し忘れれば
テスト (`tests/test_contract.py`) が落ちる。

## 落とし方

**契約違反はその場で落とす。既定値で埋めない。** 埋めて進むと、LLM が書き漏らした
項目が静かに空欄になり、「判断が無かった日」と「書き漏らした日」が出力から
区別できなくなる。

- `KeyError` — 必須のキーが無い
- `ValueError` — キーはあるが値が契約外 (語彙違反・空・組み合わせ違反)
"""

from lib.verdicts import ACTIONABLE_VERDICTS, CANDIDATE_VERDICTS, HOLDING_VERDICTS

# この実装が読める中間表現のバージョン。契約を変えたら上げる
SCHEMA_VERSION = 1

# 語彙。値まで検証しないと、表記揺れ ("jpy" / "goood") が既定値に倒れて
# もっともらしい出力になる
CURRENCIES = ("JPY", "USD")
MARKETS = ("jp", "us")
SIGNAL_VALUES = ("good", "warn", "bad", "unknown")

# 4 軸のシグナル。キーは固定
SIGNAL_AXES = ("weekly", "daily", "overheat", "volume")

# 確定足を出す市場。片方だけだと、その市場のデータ鮮度を確認できないまま読むことになる
BAR_MARKETS = ("jp", "us")


def _require(data: dict, key: str, where: str):
    """必須キーを取り出す。無ければ落とす。

    Args:
        data: 対象の dict。
        key: 必須キー。
        where: エラー文に出す位置の説明。

    Returns:
        キーの値。

    Raises:
        KeyError: キーが無い場合。
    """
    if not isinstance(data, dict) or key not in data:
        raise KeyError(f"{where}: 必須キー '{key}' が無い (docs/report-contract.md)")
    return data[key]


def _require_choice(value, allowed: tuple, where: str, label: str):
    """値が契約の語彙に含まれることを確かめる。

    Args:
        value: 検証する値。
        allowed: 許される語彙。
        where: エラー文に出す位置の説明。
        label: エラー文に出す項目名。

    Returns:
        検証済みの値。

    Raises:
        ValueError: 語彙に無い値の場合。
    """
    if value not in allowed:
        raise ValueError(
            f"{where}: {label} {value!r} は契約外 "
            f"(使えるのは {' / '.join(str(a) for a in allowed)} / docs/report-contract.md)"
        )
    return value


def _require_nonempty(data: dict, key: str, where: str, why: str) -> list:
    """必須の非空リストを取り出す。

    Args:
        data: 対象の dict。
        key: 必須キー。
        where: エラー文に出す位置の説明。
        why: 空を許さない理由 (エラー文に出す)。

    Returns:
        非空のリスト。

    Raises:
        KeyError: キーが無い場合。
        ValueError: 空の場合。
    """
    value = _require(data, key, where)
    if not value:
        raise ValueError(f"{where}: '{key}' が空。{why} (docs/report-contract.md)")
    return value


def _is_jp(item: dict) -> bool:
    """日本株かどうか。

    Args:
        item: Position または Candidate。

    Returns:
        日本株なら True。
    """
    return item.get("market") == "jp" or item.get("currency") == "JPY"


def _validate_signals(item: dict, where: str) -> dict:
    """4 軸のシグナルを検証する。

    軸の欠落も表記揺れも `unknown` に倒さない。`unknown` は契約上
    「データ不足」を意味し、その銘柄の判断が `保留` になる根拠でもあるため、
    書き漏らしから偽の根拠が作られることになる。

    Args:
        item: Position または Candidate。
        where: エラー文に出す位置の説明。

    Returns:
        検証済みの signals dict。
    """
    signals = _require(item, "signals", where)
    for axis in SIGNAL_AXES:
        _require(signals, axis, f"{where}.signals")
        _require_choice(signals[axis], SIGNAL_VALUES, f"{where}.signals", f"{axis} の評価")
    return signals


def _validate_actionable_consistency(verdict: str, signals: dict, where: str) -> None:
    """資金が動く判断とデータ不足の併存を拒否する。

    `unknown` はデータ不足を指す。それを抱えたまま 買い / 積増し / 売却 を出すと、
    **判断材料が欠けたまま資金を動かす提案**がヒーローと Slack に載る。

    軸 1 本でも `unknown` なら常に `保留` とまでは強制しない。出来高だけが
    取れない日に保有の判断が一切出せなくなり、運用が止まるため。強制するのは
    資金が動く判断との併存だけにする。

    Args:
        verdict: 判断ラベル。
        signals: 検証済みの signals dict。
        where: エラー文に出す位置の説明。

    Raises:
        ValueError: 資金が動く判断と unknown が併存する場合。
    """
    if verdict not in ACTIONABLE_VERDICTS:
        return
    missing = [axis for axis in SIGNAL_AXES if signals[axis] == "unknown"]
    if missing:
        raise ValueError(
            f"{where}: 判断 {verdict!r} と signals の {' / '.join(missing)} = 'unknown' が併存。"
            "データ不足のまま資金が動く判断は出さない (docs/report-contract.md)"
        )


def _validate_name(item: dict, where: str) -> None:
    """日本株に表示名があることを確かめる。

    4 桁コードだけでは何の会社か分からない (→ docs/adr/0022)。

    Args:
        item: Position または Candidate。
        where: エラー文に出す位置の説明。

    Raises:
        KeyError: 日本株なのに名前が無い場合。
    """
    if _is_jp(item) and not str(item.get("name") or "").strip():
        raise KeyError(
            f"{where}: 日本株には 'name' が要る "
            f"(4 桁コードだけでは判別できない / docs/report-contract.md)"
        )


def _validate_position(pos: dict, index: int) -> None:
    """保有銘柄 1 件を検証する。

    Args:
        pos: Position。
        index: 位置 (エラー文用)。
    """
    where = f"holdings[{index}] ({pos.get('ticker', '?')})"
    _require(pos, "ticker", where)
    _validate_name(pos, where)
    _require_choice(_require(pos, "currency", where), CURRENCIES, where, "通貨")
    _require(pos, "price", where)
    signals = _validate_signals(pos, where)

    prose = _require(pos, "prose", where)
    for key in ("change", "scenario"):
        _require(prose, key, f"{where}.prose")

    # 判断対象外 (自動運用口座) だけがラベルを持たない
    if pos.get("reference_only"):
        return
    verdict = _require_choice(
        _require(pos, "verdict", where), HOLDING_VERDICTS, where, "判断ラベル"
    )
    _validate_actionable_consistency(verdict, signals, where)


def _validate_candidate(cand: dict, index: int) -> None:
    """候補 1 件を検証する。

    Args:
        cand: Candidate。
        index: 位置 (エラー文用)。
    """
    where = f"candidates[{index}] ({cand.get('ticker', '?')})"
    _require(cand, "ticker", where)
    _require_choice(_require(cand, "market", where), MARKETS, where, "market")
    _validate_name(cand, where)
    _require_choice(_require(cand, "currency", where), CURRENCIES, where, "通貨")
    for key in ("price", "score_atr", "pass_reason"):
        _require(cand, key, where)

    rng = _require(cand, "range", where)
    for key in ("low", "high"):
        _require(rng, key, f"{where}.range")

    signals = _validate_signals(cand, where)
    verdict = _require_choice(
        _require(cand, "verdict", where), CANDIDATE_VERDICTS, where, "判断ラベル"
    )
    _validate_actionable_consistency(verdict, signals, where)

    prose = _require(cand, "prose", where)
    _require(prose, "check", f"{where}.prose")
    if verdict == "買い":
        # 弱点の無い「買い」は、見ていないだけである
        _require_nonempty(
            prose, "weak", f"{where}.prose", "「買い」には弱い点を必ず書く (弱点の無い候補は無い)"
        )


def validate(data: dict) -> dict:
    """中間表現の全体を検証する。

    **描画の前にここを通す。** 通ったあとは、契約で必須と定めた項目が
    揃っていることを前提にしてよい。

    Args:
        data: `docs/report-contract.md` に従う dict。

    Returns:
        検証済みの同じ dict (呼び出し側が続けて使えるように返す)。

    Raises:
        KeyError: 必須キーが欠けている場合。
        ValueError: 値が契約外の場合。
    """
    # 契約のバージョンを見ずに描くと、構造の違う将来の版を v1 として部分的に描いてしまう
    _require_choice(_require(data, "schema", "root"), (SCHEMA_VERSION,), "root", "schema")

    for key in ("date", "generated_at", "summary"):
        _require(data, key, "root")

    bars = _require(data, "bars", "root")
    for market in BAR_MARKETS:
        _require(bars, market, "bars")

    _require_nonempty(
        data, "holdings_as_of", "root", "保有データの基準日が無いとデータ鮮度を確認できない"
    )

    eff = _require(data, "effective_holdings", "root")
    _require_nonempty(
        eff,
        "lines",
        "effective_holdings",
        "執行 0 件でも「執行記録なし (as_of 時点のまま)」を入れる "
        "(記録が無いのか拾い忘れたのかを区別するため)",
    )

    screen = _require(data, "screen", "root")
    for key in ("universe", "market", "failures"):
        _require(screen, key, "screen")

    # 空配列は正常 (候補ゼロ・保有なし)。キーの欠落だけを落とす
    for index, pos in enumerate(_require(data, "holdings", "root")):
        _validate_position(pos, index)
    for index, cand in enumerate(_require(data, "candidates", "root")):
        _validate_candidate(cand, index)

    return data
