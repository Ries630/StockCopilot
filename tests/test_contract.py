"""lib/contract.py のテスト。

契約の検証を 1 箇所に集めたので、**何を検証しているかの一覧はこのファイルが持つ**。
契約に項目を足したらここにも足す (足し忘れれば漏れが見えないままになる)。

ネットワークにアクセスしない。
"""

import pytest

from lib.contract import validate
from tests.test_report import base_data, candidate, position


def test_valid_document_passes() -> None:
    data = base_data(holdings=[position()], candidates=[candidate()])
    assert validate(data) is data


def test_empty_lists_are_valid() -> None:
    """候補ゼロ・保有なしは正常な入力。"""
    validate(base_data())


# ─── ルート ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "key", ["schema", "date", "generated_at", "summary", "bars", "holdings_as_of",
            "effective_holdings", "screen", "holdings", "candidates"]
)
def test_root_required_keys(key: str) -> None:
    data = base_data()
    del data[key]
    with pytest.raises(KeyError, match=key):
        validate(data)


def test_schema_version_is_checked() -> None:
    with pytest.raises(ValueError, match="schema"):
        validate(base_data(schema=2))


@pytest.mark.parametrize("market", ["jp", "us"])
def test_both_bar_markets_are_required(market: str) -> None:
    """片方だけだと、その市場のデータ鮮度を確認できないまま読むことになる。"""
    bars = {"jp": "2026-08-20", "us": "2026-08-19"}
    del bars[market]
    with pytest.raises(KeyError, match=market):
        validate(base_data(bars=bars))


def test_effective_holdings_lines_cannot_be_empty() -> None:
    with pytest.raises(ValueError, match="lines"):
        validate(base_data(effective_holdings={"executions": 0, "lines": []}))


def test_holdings_as_of_cannot_be_empty() -> None:
    with pytest.raises(ValueError, match="holdings_as_of"):
        validate(base_data(holdings_as_of=[]))


@pytest.mark.parametrize("key", ["universe", "market", "failures"])
def test_screen_required_keys(key: str) -> None:
    screen = {"universe": 25, "market": "all", "failures": 0}
    del screen[key]
    with pytest.raises(KeyError, match=key):
        validate(base_data(screen=screen))


# ─── 保有 ────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["ticker", "currency", "price", "signals", "prose", "verdict"])
def test_position_required_keys(key: str) -> None:
    pos = position()
    del pos[key]
    with pytest.raises(KeyError, match=key):
        validate(base_data(holdings=[pos]))


@pytest.mark.parametrize("key", ["change", "scenario"])
def test_position_prose_required_keys(key: str) -> None:
    pos = position()
    del pos["prose"][key]
    with pytest.raises(KeyError, match=key):
        validate(base_data(holdings=[pos]))


def test_reference_only_position_needs_no_verdict() -> None:
    ref = position(reference_only=True)
    del ref["verdict"]
    validate(base_data(holdings=[ref]))


def test_position_verdict_vocabulary() -> None:
    """候補側のラベルを保有に入れたら落ちる。"""
    with pytest.raises(ValueError, match="判断ラベル"):
        validate(base_data(holdings=[position(verdict="買い")]))


# ─── 候補 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key", ["ticker", "market", "currency", "price", "score_atr", "pass_reason",
            "range", "signals", "prose", "verdict"]
)
def test_candidate_required_keys(key: str) -> None:
    cand = candidate()
    del cand[key]
    with pytest.raises(KeyError, match=key):
        validate(base_data(candidates=[cand]))


@pytest.mark.parametrize("market", ["JP", "usa", ""])
def test_candidate_market_vocabulary(market: str) -> None:
    with pytest.raises(ValueError, match="market"):
        validate(base_data(candidates=[candidate(market=market)]))


def test_candidate_range_needs_low_and_high() -> None:
    for key in ("low", "high"):
        cand = candidate()
        del cand["range"][key]
        with pytest.raises(KeyError, match=key):
            validate(base_data(candidates=[cand]))


def test_candidate_prose_check_is_required() -> None:
    cand = candidate()
    del cand["prose"]["check"]
    with pytest.raises(KeyError, match="check"):
        validate(base_data(candidates=[cand]))


def test_buy_without_weak_points_is_rejected() -> None:
    """弱点の無い「買い」は、見ていないだけである。"""
    for weak in ([], None):
        cand = candidate(verdict="買い")
        cand["prose"]["weak"] = weak
        with pytest.raises(ValueError, match="weak"):
            validate(base_data(candidates=[cand]))


def test_pass_verdict_may_omit_weak_points() -> None:
    """「買い」以外では強制しない。"""
    cand = candidate(verdict="見送り")
    cand["prose"]["weak"] = []
    validate(base_data(candidates=[cand]))


# ─── 語彙と組み合わせ ──────────────────────────────────────


@pytest.mark.parametrize("currency", ["jpy", "JPY ", "EUR"])
def test_currency_vocabulary(currency: str) -> None:
    with pytest.raises(ValueError, match="通貨"):
        validate(base_data(holdings=[position(currency=currency)]))


@pytest.mark.parametrize("axis", ["weekly", "daily", "overheat", "volume"])
def test_all_signal_axes_are_required(axis: str) -> None:
    pos = position()
    del pos["signals"][axis]
    with pytest.raises(KeyError, match=axis):
        validate(base_data(holdings=[pos]))


def test_signal_value_vocabulary() -> None:
    pos = position()
    pos["signals"]["daily"] = "goood"
    with pytest.raises(ValueError, match="daily"):
        validate(base_data(holdings=[pos]))


def test_jp_stock_needs_a_name() -> None:
    with pytest.raises(KeyError, match="name"):
        validate(base_data(holdings=[position(name="")]))
    with pytest.raises(KeyError, match="name"):
        validate(base_data(candidates=[candidate(market="jp", currency="JPY", name=None)]))


def test_us_stock_may_omit_the_name() -> None:
    validate(base_data(candidates=[candidate(name="")]))


def test_actionable_verdict_with_unknown_signal_is_rejected() -> None:
    """データ不足のまま資金が動く判断は出さない。"""
    cand = candidate(verdict="買い")
    cand["signals"]["weekly"] = "unknown"
    with pytest.raises(ValueError, match="unknown"):
        validate(base_data(candidates=[cand]))

    pos = position(verdict="売却")
    pos["signals"]["volume"] = "unknown"
    with pytest.raises(ValueError, match="unknown"):
        validate(base_data(holdings=[pos]))


def test_non_actionable_verdict_may_carry_unknown() -> None:
    """軸 1 本の unknown で常に保留にはしない。

    出来高だけが取れない日に保有の判断が一切出せなくなり、運用が止まるため。
    """
    validate(base_data(holdings=[position(verdict="ホールド")]))
    cand = candidate(verdict="保留")
    cand["signals"]["weekly"] = "unknown"
    validate(base_data(candidates=[cand]))
