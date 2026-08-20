"""判断ラベルの定義と、資金が動く判断の判定。

ラベル体系そのものの正は `journal/README.md`、中間表現での載せ方の正は
`docs/report-contract.md` にある。ここに置くのは **コードが参照する形**だけで、
HTML レポート (report.py) と Slack 通知 (notify.py) の両方から使う。

`ACTIONABLE_VERDICTS` を 1 か所に閉じてあるのは、Slack のメンションを鳴らす条件と
HTML のヒーローに出す内容を必ず一致させるため。片方だけ変えると、通知が鳴ったのに
レポートには何も無い (逆も) という状態が起こる。
"""

# 保有分析 (stock-check) の 5 ラベル。順序は journal/README.md の記載順
HOLDING_VERDICTS = ("ホールド", "積増し", "部分利確", "売却", "保留")

# スクリーニング (stock-screen) の 4 ラベル。保有側とは別体系で、共通するのは「保留」だけ
CANDIDATE_VERDICTS = ("買い", "見送り", "決算後に再判定", "保留")

# 判断対象外 (自動運用口座など) に入れる値。ラベルではないので上の 2 組には含めない
NOT_APPLICABLE = "—"

# 資金が動く判断。Slack のメンションと HTML のヒーローはこの集合だけを見る。
#
# 「部分利確」を外しているのは、りーすさんが 2026-08-20 に対象を
# 買い / 積増し / 売却 の 3 つと決めたため。増やすときはここだけを変える。
ACTIONABLE_VERDICTS = frozenset({"買い", "積増し", "売却"})


def is_actionable(verdict: str | None) -> bool:
    """その判断ラベルで資金が動くか。

    Args:
        verdict: 判断ラベル。None や未知の値は False。

    Returns:
        資金が動く判断なら True。
    """
    return verdict in ACTIONABLE_VERDICTS


def check_verdict(verdict: str, allowed: tuple, where: str) -> str:
    """判断ラベルが契約の語彙に含まれることを確かめる。

    値の検証を欠かすと、契約外のラベル ("買い " や "購入" のような揺れ) が
    **カードには表示されるのに `actionable_items()` には拾われない**状態になる。
    買い判断がヒーローからも Slack のメンションからも静かに消えるため、
    キーの存在だけでなく値まで見る。

    Args:
        verdict: 判断ラベル。
        allowed: 許される語彙 (HOLDING_VERDICTS / CANDIDATE_VERDICTS)。
        where: エラー文に出す位置の説明。

    Returns:
        検証済みの判断ラベル。

    Raises:
        ValueError: 語彙に無い値の場合。
    """
    if verdict not in allowed:
        raise ValueError(
            f"{where}: 判断ラベル {verdict!r} は契約外 "
            f"(使えるのは {' / '.join(allowed)} / docs/report-contract.md)"
        )
    return verdict


def actionable_items(data: dict) -> list[dict]:
    """中間表現から、資金が動く判断だけを取り出す。

    保有と候補を 1 本のリストに混ぜて返す。呼び出し側 (ヒーロー・メンション) は
    どちらに由来するかを区別せず「今日動くものがあるか」だけを見るため。
    判断対象外 (reference_only) の銘柄は、ラベルの値によらず除外する。

    Args:
        data: `docs/report-contract.md` に従う中間表現 dict。

    Returns:
        `{"ticker", "name", "verdict", "kind"}` の dict のリスト
        (`kind` は "holding" / "candidate")。該当なしなら空リスト。
    """
    found: list[dict] = []
    for kind, key in (("holding", "holdings"), ("candidate", "candidates")):
        for item in data.get(key) or []:
            if item.get("reference_only"):
                continue
            if is_actionable(item.get("verdict")):
                found.append(
                    {
                        "ticker": item.get("ticker", ""),
                        "name": item.get("name", ""),
                        "verdict": item["verdict"],
                        "kind": kind,
                    }
                )
    return found
