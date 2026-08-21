"""中間表現JSON Schemaと業務上の組み合わせ規則のテスト。

構造の正は`docs/report-contract.schema.json`に置く。必須キーのテストはSchemaの
`required`を読み、実装と同じ項目一覧をテストへ写経しない。
"""

import pytest
from jsonschema import Draft202012Validator

from lib.contract import SCHEMA, validate
from tests.test_report import base_data, candidate, position


def full_data() -> dict:
    """任意項目も含む有効な中間表現を作る。

    Returns:
        Schemaの全プロパティを検査できる代表データ。
    """
    pos = position(
        earnings={"note": "決算 2026-08-25", "warn": True},
        reference_only=False,
    )
    pos["signals"]["labels"] = {"weekly": "EMA20>50>200"}
    cand = candidate(
        turnover="$12.3M",
        earnings={"note": "決算日不明", "warn": False},
        levels={"support": 110.0, "resistance": 130.0},
        closes=[100.0, 110.0, 123.45],
    )
    cand["signals"]["labels"] = {"daily": "RSI 62"}
    return base_data(
        stale_bars=False,
        market_tone={"label": "中立", "prose": "方向感は限定的。"},
        holdings=[pos],
        candidates=[cand],
        assumptions=["前提。"],
        warnings=["警告。"],
    )


def set_path(data: dict, path: tuple, value) -> None:
    """ネストしたテストデータの値を差し替える。

    Args:
        data: 変更する中間表現。
        path: dictキーまたはlist indexの並び。
        value: 設定する値。
    """
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


def test_schema_itself_is_valid_draft_2020_12() -> None:
    """Schemaファイル自体の書き間違いを検出する。"""
    Draft202012Validator.check_schema(SCHEMA)


def test_valid_document_passes_without_copying() -> None:
    data = full_data()
    assert validate(data) is data


def test_empty_holdings_and_candidates_are_valid() -> None:
    """候補ゼロ・保有なしは正常な入力。"""
    validate(base_data())


def test_every_declared_required_key_is_rejected_when_removed() -> None:
    """各オブジェクトの`required`をSchemaから読み、欠落を機械的に検査する。"""
    data = full_data()
    defs = SCHEMA["$defs"]
    cases = (
        (SCHEMA, data),
        (defs["bars"], data["bars"]),
        (defs["holdingAsOf"], data["holdings_as_of"][0]),
        (defs["effectiveHoldings"], data["effective_holdings"]),
        (defs["screen"], data["screen"]),
        (defs["position"], data["holdings"][0]),
        (defs["positionProse"], data["holdings"][0]["prose"]),
        (defs["signals"], data["holdings"][0]["signals"]),
        (defs["candidate"], data["candidates"][0]),
        (defs["candidateRange"], data["candidates"][0]["range"]),
        (defs["candidateProse"], data["candidates"][0]["prose"]),
    )
    for schema, target in cases:
        for key in schema["required"]:
            removed = target.pop(key)
            try:
                with pytest.raises(KeyError, match=key):
                    validate(data)
            finally:
                target[key] = removed


@pytest.mark.parametrize(
    "path",
    [
        ("schema",),
        ("date",),
        ("generated_at",),
        ("bars", "jp"),
        ("holdings_as_of", 0, "as_of"),
        ("holdings_as_of", 0, "label"),
        ("holdings_as_of", 0, "count"),
        ("effective_holdings", "executions"),
        ("screen", "universe"),
        ("screen", "failures"),
        ("summary",),
        ("holdings", 0, "ticker"),
        ("holdings", 0, "price"),
        ("holdings", 0, "shares"),
        ("candidates", 0, "ticker"),
        ("candidates", 0, "price"),
        ("candidates", 0, "score_atr"),
        ("candidates", 0, "range", "low"),
        ("candidates", 0, "range", "high"),
        ("candidates", 0, "range", "pos_pct"),
    ],
)
def test_null_is_not_a_substitute_for_an_omitted_or_typed_value(path: tuple) -> None:
    """任意項目も含め、`null`を既定値として受理しない。"""
    data = full_data()
    set_path(data, path, None)
    with pytest.raises(ValueError, match="契約外"):
        validate(data)


@pytest.mark.parametrize(
    ("path", "wrong"),
    [
        (("bars",), []),
        (("holdings_as_of",), "2026-08-20"),
        (("effective_holdings",), []),
        (("effective_holdings", "lines"), "執行記録なし"),
        (("holdings",), {}),
        (("candidates",), {}),
        (("screen",), []),
        (("holdings", 0, "signals"), []),
        (("holdings", 0, "prose"), []),
        (("candidates", 0, "range"), []),
        (("candidates", 0, "prose", "weak"), "弱い点"),
        (("assumptions",), "前提"),
    ],
)
def test_container_types_are_enforced(path: tuple, wrong) -> None:
    data = full_data()
    set_path(data, path, wrong)
    with pytest.raises(ValueError, match="契約外"):
        validate(data)


@pytest.mark.parametrize(
    "path",
    [
        ("effective_holdings", "lines", 0),
        ("assumptions", 0),
        ("warnings", 0),
        ("holdings", 0, "prose", "reasons", 0),
        ("candidates", 0, "prose", "strong", 0),
        ("candidates", 0, "prose", "weak", 0),
    ],
)
def test_string_list_items_must_be_nonempty_strings(path: tuple) -> None:
    data = full_data()
    set_path(data, path, 123)
    with pytest.raises(ValueError, match="契約外"):
        validate(data)


@pytest.mark.parametrize(
    "path",
    [
        ("summary",),
        ("holdings", 0, "ticker"),
        ("holdings", 0, "prose", "change"),
        ("candidates", 0, "ticker"),
        ("candidates", 0, "pass_reason"),
        ("candidates", 0, "prose", "check"),
    ],
)
def test_required_text_rejects_empty_or_whitespace(path: tuple) -> None:
    for value in ("", "   ", "\n\t"):
        data = full_data()
        set_path(data, path, value)
        with pytest.raises(ValueError, match="契約外"):
            validate(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("date",), "2026-02-30"),
        (("bars", "jp"), "2026/08/20"),
        (("generated_at",), "2026-08-20T17:30:00Z"),
    ],
)
def test_dates_and_generated_time_formats(path: tuple, value: str) -> None:
    data = full_data()
    set_path(data, path, value)
    with pytest.raises(ValueError, match="契約外"):
        validate(data)


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("bars",),
        ("holdings_as_of", 0),
        ("effective_holdings",),
        ("screen",),
        ("holdings", 0),
        ("holdings", 0, "signals"),
        ("holdings", 0, "prose"),
        ("candidates", 0),
        ("candidates", 0, "range"),
        ("candidates", 0, "prose"),
    ],
)
def test_unknown_keys_are_rejected_at_every_object_level(path: tuple) -> None:
    data = full_data()
    target = data
    for part in path:
        target = target[part]
    target["typo_field"] = "見落とさない"
    with pytest.raises(ValueError, match="契約外"):
        validate(data)


@pytest.mark.parametrize("currency", ["jpy", "JPY ", "EUR", ""])
def test_currency_vocabulary(currency: str) -> None:
    with pytest.raises(ValueError, match="通貨.*契約外"):
        validate(base_data(holdings=[position(currency=currency)]))


@pytest.mark.parametrize("market", ["JP", "usa", ""])
def test_market_vocabulary(market: str) -> None:
    with pytest.raises(ValueError, match="契約外"):
        validate(base_data(candidates=[candidate(market=market)]))


@pytest.mark.parametrize("axis", ["weekly", "daily", "overheat", "volume"])
def test_signal_vocabulary(axis: str) -> None:
    cand = candidate()
    cand["signals"][axis] = "goood"
    with pytest.raises(ValueError, match=axis):
        validate(base_data(candidates=[cand]))


def test_jp_and_us_names_are_optional_but_nonempty_when_present() -> None:
    """`name`の有無は市場で変えない。"""
    jp_position = position(currency="JPY")
    jp_position.pop("name")
    jp_candidate = candidate(market="jp", currency="JPY")
    jp_candidate.pop("name")
    us_candidate = candidate()
    us_candidate.pop("name")
    validate(
        base_data(
            holdings=[jp_position],
            candidates=[jp_candidate, us_candidate],
        )
    )

    jp_candidate["name"] = " "
    with pytest.raises(ValueError, match="name"):
        validate(base_data(candidates=[jp_candidate]))


def test_reference_only_position_verdict_rule() -> None:
    ref = position(reference_only=True)
    ref.pop("verdict")
    validate(base_data(holdings=[ref]))

    ref["verdict"] = "—"
    validate(base_data(holdings=[ref]))

    ref["verdict"] = "売却"
    with pytest.raises(ValueError, match="verdict"):
        validate(base_data(holdings=[ref]))


def test_normal_position_requires_holding_verdict() -> None:
    pos = position()
    pos.pop("verdict")
    with pytest.raises(KeyError, match="verdict"):
        validate(base_data(holdings=[pos]))
    with pytest.raises(ValueError, match="判断ラベル.*契約外"):
        validate(base_data(holdings=[position(verdict="買い")]))


def test_buy_requires_a_nonempty_weak_points_list() -> None:
    for weak in (None, [], "弱い点", [" "]):
        cand = candidate(verdict="買い")
        if weak is None:
            cand["prose"].pop("weak")
        else:
            cand["prose"]["weak"] = weak
        error = KeyError if weak is None else ValueError
        with pytest.raises(error, match="weak"):
            validate(base_data(candidates=[cand]))


def test_non_buy_candidate_may_omit_weak_points() -> None:
    cand = candidate(verdict="見送り")
    cand["prose"].pop("weak")
    validate(base_data(candidates=[cand]))


def test_actionable_verdict_with_unknown_signal_is_rejected() -> None:
    cand = candidate(verdict="買い")
    cand["signals"]["weekly"] = "unknown"
    with pytest.raises(ValueError, match="unknown"):
        validate(base_data(candidates=[cand]))

    pos = position(verdict="売却")
    pos["signals"]["volume"] = "unknown"
    with pytest.raises(ValueError, match="unknown"):
        validate(base_data(holdings=[pos]))


def test_non_actionable_verdict_may_carry_unknown() -> None:
    validate(base_data(holdings=[position(verdict="ホールド")]))
    cand = candidate(verdict="保留")
    cand["signals"]["weekly"] = "unknown"
    validate(base_data(candidates=[cand]))


def test_candidate_range_cannot_be_reversed() -> None:
    cand = candidate()
    cand["range"] = {"low": 130.0, "high": 120.0, "pos_pct": 50.0}
    with pytest.raises(ValueError, match="low.*high"):
        validate(base_data(candidates=[cand]))


def test_candidate_market_must_match_a_single_market_screen() -> None:
    with pytest.raises(ValueError, match="screen.market"):
        validate(
            base_data(
                screen={"universe": 25, "market": "jp", "failures": 0},
                candidates=[candidate(market="us")],
            )
        )

    validate(
        base_data(
            screen={"universe": 25, "market": "all", "failures": 0},
            candidates=[candidate(market="us")],
        )
    )


@pytest.mark.parametrize(
    ("market", "currency"),
    [("jp", "USD"), ("us", "JPY")],
)
def test_candidate_currency_must_match_market(market: str, currency: str) -> None:
    """候補の市場と表示通貨の矛盾を拒否する。"""
    with pytest.raises(ValueError, match="currency|JPY|USD"):
        validate(base_data(candidates=[candidate(market=market, currency=currency)]))


@pytest.mark.parametrize(
    ("market", "currency"),
    [("jp", "JPY"), ("us", "USD")],
)
def test_candidate_currency_accepts_market_pair(market: str, currency: str) -> None:
    """候補の正しい市場・表示通貨の組み合わせを受理する。"""
    validate(base_data(candidates=[candidate(market=market, currency=currency)]))


def test_candidate_position_percent_must_match_price_and_range() -> None:
    cand = candidate()
    cand["range"]["pos_pct"] = 1.1725
    with pytest.raises(ValueError, match="pos_pct"):
        validate(base_data(candidates=[cand]))

    cand["range"]["pos_pct"] = 117.7
    validate(base_data(candidates=[cand]))

    cand["range"]["pos_pct"] = 117.76
    with pytest.raises(ValueError, match="pos_pct"):
        validate(base_data(candidates=[cand]))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("effective_holdings", "executions"), -1),
        (("screen", "universe"), -1),
        (("screen", "failures"), -1),
        (("screen", "failures"), 1.5),
        (("holdings", 0, "price"), True),
        (("candidates", 0, "score_atr"), True),
    ],
)
def test_numeric_boundaries_and_booleans(path: tuple, value) -> None:
    data = full_data()
    set_path(data, path, value)
    with pytest.raises(ValueError, match="契約外"):
        validate(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("holdings", 0, "shares"), 0),
        (("holdings", 0, "price"), 0),
        (("holdings", 0, "levels", "support"), -1),
        (("holdings", 0, "closes", 0), 0),
        (("candidates", 0, "price"), 0),
        (("candidates", 0, "score_atr"), -0.1),
        (("candidates", 0, "range", "low"), 0),
        (("candidates", 0, "range", "high"), -1),
        (("candidates", 0, "atr_pct"), -0.1),
        (("candidates", 0, "levels", "resistance"), 0),
        (("candidates", 0, "closes", 0), -1),
    ],
)
def test_price_values_are_positive_and_atr_values_are_nonnegative(
    path: tuple, value: float
) -> None:
    """価格系の非正値とATR系の負値を拒否する。"""
    data = full_data()
    set_path(data, path, value)
    with pytest.raises(ValueError, match="契約外"):
        validate(data)


def test_zero_atr_values_and_negative_range_position_are_valid() -> None:
    """ATRのゼロとレンジ下抜けを過剰に拒否しない。"""
    cand = candidate(score_atr=0, atr_pct=0, price=90)
    cand["range"] = {"low": 100, "high": 120, "pos_pct": -50}
    validate(base_data(candidates=[cand]))
