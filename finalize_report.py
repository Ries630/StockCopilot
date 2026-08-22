# /// script
# requires-python = ">=3.10"
# dependencies = ["jsonschema>=4.25"]
# ///
"""夕方ブリーフの市場別結果を合流し、契約検証済みJSONを書き出す。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lib.contract import validate
from lib.market_observation import MARKETS, merge_market_results


def finalize(current: dict, previous: dict | None) -> dict:
    """市場別状態に従って前回結果を合流し、警告と契約を確定する。

    Args:
        current: 更新市場だけを分析して作った今回の中間表現。
        previous: 前回の中間表現。初回はNone。

    Returns:
        合流・検証済みの中間表現。
    """
    status = current["bar_status"]
    merged = merge_market_results(previous, current, status)
    warnings = list(merged.get("warnings") or [])
    for market in MARKETS:
        state = status[market]["status"]
        prefix = market.upper()
        if state == "unchanged":
            warnings.append(
                f"{prefix}: 確定足は前回から更新なし（休場またはデータ遅延を区別できない）"
            )
        elif state == "unavailable":
            warnings.append(f"{prefix}: 確定足日を取得できず、この市場の分析を更新しない")
    if warnings:
        merged["warnings"] = list(dict.fromkeys(warnings))
    validate(merged)
    return merged


def main() -> None:
    """CLI引数から今回・前回JSONを読み、最終JSONを書き出す。"""
    parser = argparse.ArgumentParser(description="市場別結果を合流して夕方ブリーフを確定する")
    parser.add_argument("source", help="更新市場だけを含む今回のJSON")
    parser.add_argument("-o", "--out", required=True, help="合流後JSONの出力先")
    parser.add_argument("--previous", help="前回JSON（既定: 出力先と同じ場所のlatest.json）")
    args = parser.parse_args()

    source = Path(args.source)
    out = Path(args.out)
    previous_path = Path(args.previous) if args.previous else out.with_name("latest.json")
    current = json.loads(source.read_text(encoding="utf-8"))
    try:
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        previous = None
    result = finalize(current, previous)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[finalize] {out}")


if __name__ == "__main__":
    main()
