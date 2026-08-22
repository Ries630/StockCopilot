"""中間表現（`reports/*_evening.json`）の検証。

構造（キー・型・必須・語彙）の正は `docs/report-contract.schema.json`。
このモジュールはJSON Schemaを適用し、Schemaだけでは読みづらくなる業務上の
組み合わせ規則と、表示項目の欠落だけを警告へ降格するseverityを追加する。
"""

import datetime as dt
import json
import math
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

BAR_MARKETS = ("jp", "us")
"""確定足の日付を表示する市場。"""

ROOT_DISPLAY_KEYS = frozenset(
    {"date", "generated_at", "holdings_as_of", "summary"}
)
"""トップレベルで欠落を警告へ降格できる表示項目。"""


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
        missing = _missing_key(error)
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


def _missing_key(error: ValidationError) -> str | None:
    """`required`違反から欠落キーを取り出す。

    Args:
        error: jsonschemaが返した検証エラー。

    Returns:
        欠落キー。`required`違反でなければNone。
    """
    if error.validator != "required" or not isinstance(error.instance, dict):
        return None
    # jsonschemaは欠落1件につき1エラーを返す一方、validator_valueには同じ階層の
    # 必須キー全体が入る。今回の欠落キーはエラーメッセージから特定する。
    if error.message.startswith("'") and "' is a required property" in error.message:
        return error.message.split("'", 2)[1]
    return next((key for key in error.validator_value if key not in error.instance), None)


def _is_indexed(path: tuple[object, ...], collection: str, *tail: str) -> bool:
    """配列要素以下のJSONパスかを判定する。

    Args:
        path: `ValidationError.absolute_path`のタプル。
        collection: トップレベルの配列名。
        *tail: 配列indexより後ろに期待するキー。

    Returns:
        `collection[index].tail...`の形ならTrue。
    """
    return (
        len(path) == len(tail) + 2
        and path[0] == collection
        and isinstance(path[1], int)
        and path[2:] == tail
    )


def _is_display_gap(error: ValidationError) -> bool:
    """Schema違反が表示項目の欠落だけかを判定する。

    型・語彙・形式・未知キーは、表示項目であっても入力の破損なので例外のままにする。
    severityを下げるのは`required`と`minItems`による欠落だけ。

    Args:
        error: jsonschemaが返した検証エラー。

    Returns:
        警告へ降格できる欠落ならTrue。
    """
    path = tuple(error.absolute_path)
    if error.validator == "minItems" and path == ("holdings_as_of",):
        return True

    missing = _missing_key(error)
    if missing is None:
        return False
    if path == ():
        return missing in ROOT_DISPLAY_KEYS
    if path == ("effective_holdings",):
        return missing == "executions"
    if _is_indexed(path, "holdings_as_of"):
        return missing in {"as_of", "label"}
    if _is_indexed(path, "holdings"):
        return missing == "prose"
    if _is_indexed(path, "holdings", "prose"):
        return missing in {"change", "scenario"}
    if _is_indexed(path, "candidates"):
        if missing in {"score_atr", "pass_reason", "range"}:
            return True
        if missing == "prose":
            candidate = error.instance
            return candidate.get("verdict") != "買い"
    if _is_indexed(path, "candidates", "range"):
        return missing in {"low", "high", "pos_pct"}
    if _is_indexed(path, "candidates", "prose"):
        return missing == "check"
    return False


def _display_warning(error: ValidationError) -> str:
    """表示項目の欠落を利用者向け警告へ変換する。

    Args:
        error: `_is_display_gap()`がTrueを返したエラー。

    Returns:
        HTMLとSlackへ載せる警告文。
    """
    where = _path(error.absolute_path)
    missing = _missing_key(error)
    if missing is not None:
        return f"契約: {where} の '{missing}' が無い — 表示は「不明」"
    return f"契約: {where} が空 — 表示は「不明」"


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
    """候補の20日レンジと終値位置が矛盾しないことを検証する。

    Args:
        candidate: 構造検証済みのCandidate。
        where: エラー文に出す位置。

    Raises:
        ValueError: レンジが逆転しているか、終値位置と価格が矛盾する場合。
    """
    low = candidate["range"]["low"]
    high = candidate["range"]["high"]
    if low > high:
        raise ValueError(
            f"{where}.range: low {low!r} が high {high!r} を上回っている "
            "(docs/report-contract.md)"
        )
    expected = 50.0 if low == high else (candidate["price"] - low) / (high - low) * 100
    actual = candidate["range"]["pos_pct"]
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=0.5):
        raise ValueError(
            f"{where}.range.pos_pct: {actual!r} は price / low / high から求めた "
            f"{expected:.2f}% と一致しない (許容差 0.5 ポイント / docs/report-contract.md)"
        )


def _validate_bar_status(data: dict) -> None:
    """確定足日と市場別更新状態の組み合わせを検証する。

    Args:
        data: 構造検証済みの中間表現。

    Raises:
        KeyError: 状態に必要な日付が無い場合。
        ValueError: 状態と日付の関係が矛盾する場合。
    """
    bars = data["bars"]
    for market in BAR_MARKETS:
        item = data["bar_status"][market]
        status = item["status"]
        current = bars.get(market)
        previous = item.get("previous")
        where = f"bar_status.{market}"
        if status == "unavailable":
            if current is not None:
                raise ValueError(f"{where}: unavailable なのに bars.{market} がある")
            continue
        if current is None:
            raise KeyError(f"{where}: {status} には bars.{market} が必要")
        if status == "initial":
            if previous is not None:
                raise ValueError(f"{where}: initial に previous は置かない")
            continue
        if previous is None:
            raise KeyError(f"{where}: {status} には previous が必要")
        current_day = dt.date.fromisoformat(current)
        previous_day = dt.date.fromisoformat(previous)
        if status == "unchanged" and current_day != previous_day:
            raise ValueError(f"{where}: unchanged だが現在日と前回日が一致しない")
        if status == "updated" and current_day <= previous_day:
            raise ValueError(f"{where}: updated だが現在日が前回日より新しくない")


def _validate_screen(data: dict) -> None:
    """市場別screen件数と更新状態の整合を検証する。

    Args:
        data: 構造検証済みの中間表現。

    Raises:
        ValueError: 件数の大小関係か市場ゲートが矛盾する場合。
    """
    for market in BAR_MARKETS:
        item = data["screen"][market]
        where = f"screen.{market}"
        if item["evaluated"] + item["failures"] > item["universe"]:
            raise ValueError(f"{where}: evaluated + failures が universe を上回る")
        if item["matched"] > item["evaluated"]:
            raise ValueError(f"{where}: matched が evaluated を上回る")
        if item["selected"] > item["matched"]:
            raise ValueError(f"{where}: selected が matched を上回る")
        details = item.get("failure_details")
        if details is not None and len(details) != item["failures"]:
            raise ValueError(f"{where}: failure_details 件数が failures と一致しない")
        status = data["bar_status"][market]["status"]
        if status not in {"updated", "initial"} and item["selected"]:
            raise ValueError(f"{where}: {status} 市場から候補を選択している")


def validate(data: dict) -> list[str]:
    """中間表現の構造と業務上の組み合わせを検証する。

    Args:
        data: `docs/report-contract.schema.json`に従うdict。

    Returns:
        表示項目の欠落に対する警告。問題が無ければ空リスト。

    Raises:
        KeyError: 必須キーが欠けている場合。
        ValueError: 構造・値・組み合わせが契約外の場合。
    """
    warnings: list[str] = []
    for error in _VALIDATOR.iter_errors(data):
        if _is_display_gap(error):
            warnings.append(_display_warning(error))
            continue
        _raise_contract_error(error)

    _validate_bar_status(data)
    _validate_screen(data)

    for index, position in enumerate(data["holdings"]):
        if not position.get("reference_only"):
            _validate_actionable_consistency(position, f"holdings[{index}]")
    for index, candidate in enumerate(data["candidates"]):
        where = f"candidates[{index}]"
        _validate_actionable_consistency(candidate, where)
        candidate_range = candidate.get("range") or {}
        if all(key in candidate_range for key in ("low", "high", "pos_pct")):
            _validate_range(candidate, where)

    return warnings
