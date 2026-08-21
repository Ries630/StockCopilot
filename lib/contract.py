"""中間表現（`reports/*_evening.json`）の検証。

構造（キー・型・必須・語彙）の正は `docs/report-contract.schema.json`。
このモジュールはJSON Schemaを適用し、Schemaだけでは読みづらくなる業務上の
組み合わせ規則を追加で検証する。
"""

import json
import pathlib
from collections.abc import Iterable

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from lib.verdicts import ACTIONABLE_VERDICTS

SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[1] / "docs" / "report-contract.schema.json"
"""中間表現JSON Schemaの絶対パス。"""

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
"""読み込み済みのDraft 2020-12 JSON Schema。"""

Draft202012Validator.check_schema(SCHEMA)
_VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())

SIGNAL_AXES = ("weekly", "daily", "overheat", "volume")
"""資金移動判断との組み合わせを確認する4軸。"""


def _path(parts: Iterable[object]) -> str:
    """JSON Schemaのパスを読みやすい文字列へ変換する。

    Args:
        parts: ValidationErrorが持つパスの各要素。

    Returns:
        `root.candidates[0].price`形式の位置。
    """
    result = "root"
    for part in parts:
        result += f"[{part}]" if isinstance(part, int) else f".{part}"
    return result


def _raise_contract_error(error: ValidationError) -> None:
    """jsonschemaの例外をプロジェクトの契約例外へ変換する。

    必須キー欠落は従来どおりKeyError、それ以外の構造違反はValueErrorにする。
    呼び出し側が「書き漏らし」と「値の不正」を区別できる契約を維持するため。

    Args:
        error: jsonschemaが返した検証エラー。

    Raises:
        KeyError: 必須キーが欠けている場合。
        ValueError: 型・語彙・形式・未知キーなど、それ以外の契約違反。
    """
    where = _path(error.absolute_path)
    if error.validator == "required":
        missing = next(key for key in error.validator_value if key not in error.instance)
        raise KeyError(
            f"{where}: 必須キー '{missing}' が無い "
            "(docs/report-contract.schema.json)"
        ) from None
    field = error.absolute_path[-1] if error.absolute_path else None
    label = {"currency": "通貨", "verdict": "判断ラベル"}.get(field)
    detail = f"{label}は契約外。" if label else "契約外。"
    raise ValueError(
        f"{where}: {detail}{error.message} (docs/report-contract.schema.json)"
    ) from None


def _validate_actionable_consistency(item: dict, where: str) -> None:
    """資金が動く判断とデータ不足の併存を拒否する。

    Args:
        item: 構造検証済みのPositionまたはCandidate。
        where: エラー文に出す位置。

    Raises:
        ValueError: actionableな判断と`unknown`シグナルが併存する場合。
    """
    verdict = item.get("verdict")
    if verdict not in ACTIONABLE_VERDICTS:
        return
    missing = [axis for axis in SIGNAL_AXES if item["signals"][axis] == "unknown"]
    if missing:
        raise ValueError(
            f"{where}: 判断 {verdict!r} と signals の {' / '.join(missing)} = 'unknown' が併存。"
            "データ不足のまま資金が動く判断は出さない (docs/report-contract.md)"
        )


def _validate_range(candidate: dict, where: str) -> None:
    """候補の20日レンジが逆転していないことを検証する。

    Args:
        candidate: 構造検証済みのCandidate。
        where: エラー文に出す位置。

    Raises:
        ValueError: `range.low`が`range.high`を上回る場合。
    """
    low = candidate["range"]["low"]
    high = candidate["range"]["high"]
    if low > high:
        raise ValueError(
            f"{where}.range: low {low!r} が high {high!r} を上回っている "
            "(docs/report-contract.md)"
        )


def validate(data: dict) -> dict:
    """中間表現の構造と業務上の組み合わせを検証する。

    Args:
        data: `docs/report-contract.schema.json`に従うdict。

    Returns:
        検証済みの同じdict。

    Raises:
        KeyError: 必須キーが欠けている場合。
        ValueError: 構造・値・組み合わせが契約外の場合。
    """
    error = next(_VALIDATOR.iter_errors(data), None)
    if error is not None:
        _raise_contract_error(error)

    for index, position in enumerate(data["holdings"]):
        if not position.get("reference_only"):
            _validate_actionable_consistency(position, f"holdings[{index}]")
    for index, candidate in enumerate(data["candidates"]):
        where = f"candidates[{index}]"
        _validate_actionable_consistency(candidate, where)
        _validate_range(candidate, where)

    return data
