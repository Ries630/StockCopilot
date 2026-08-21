# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest", "yfinance>=0.2.40", "pandas", "numpy", "ta", "jsonschema>=4.25"]
# ///
"""テストランナー。

このプロジェクトは uv + PEP 723 で依存を管理し、requirements.txt を作らない
(AGENTS.md の環境前提)。pytest も同じ流儀で動かせるよう、ランナー自身に
インライン依存を持たせている。

    uv run run_tests.py            # 全テスト
    uv run run_tests.py -k datasource -v

テストはすべてネットワークアクセスなしで完結する。yfinance を叩くと
実行日と市場の状態で結果が変わり、CI が不安定になるため。
"""

import pathlib
import sys

import pytest

if __name__ == "__main__":
    root = pathlib.Path(__file__).parent
    # プロジェクトルートを import 起点にする (lib / analyze を解決するため)
    sys.path.insert(0, str(root))
    sys.exit(pytest.main([str(root / "tests"), "-q", *sys.argv[1:]]))
