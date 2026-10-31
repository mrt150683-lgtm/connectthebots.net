"""Bounded exact arithmetic and deterministic local JSON, without float coercion."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
from fractions import Fraction
from functools import lru_cache
import json
import re
from typing import Any

MAX_DECIMAL_DIGITS = 24
MAX_DECIMAL_SCALE = 12
MAX_ATOMS_PER_FAMILY = 64
MAX_JSON_BYTES = 4 * 1024 * 1024
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,2})?$")


def exact(value: str | int | Decimal | Fraction, *, low: Fraction | int | None = None,
          high: Fraction | int | None = None) -> Fraction:
    """Convert bounded decimal text directly to a rational; floats are forbidden."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("INVALID_NUMBER: use bounded decimal text, not bool or float")
    if isinstance(value, Fraction):
        if max(len(str(abs(value.numerator))), len(str(value.denominator))) > 2048:
            raise ValueError("NUMBER_TOO_LARGE: rational exceeds internal bound")
        result = value
    else:
        if isinstance(value, int):
            if abs(value) > 10**9:
                raise ValueError("NUMBER_TOO_LARGE: integer magnitude exceeds bound")
            value = str(value)
        if isinstance(value, str):
            if len(value) > 40 or not _DECIMAL.fullmatch(value):
                raise ValueError("INVALID_NUMBER: expected bounded decimal text")
            try:
                value = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError("INVALID_NUMBER: invalid decimal") from exc
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("INVALID_NUMBER: expected finite decimal")
        parts = value.as_tuple()
        if len(parts.digits) > MAX_DECIMAL_DIGITS or not isinstance(parts.exponent, int) or abs(parts.exponent) > MAX_DECIMAL_SCALE:
            raise ValueError("NUMBER_TOO_LARGE: decimal digits or exponent exceed bound")
        if abs(value) > Decimal("1000000000"):
            raise ValueError("NUMBER_TOO_LARGE: magnitude exceeds bound")
        result = Fraction(value)
    if low is not None and result < low or high is not None and result > high:
        raise ValueError("NUMBER_OUT_OF_RANGE: value is outside permitted bounds")
    return result


@lru_cache(maxsize=1)
def _display_precision() -> int:
    from .config import load_defaults
    return load_defaults()["scoring_fixture_policy"]["display_precision_digits"]


def decimal_text(value: Fraction, precision: int | None = None) -> str:
    with localcontext() as context:
        context.prec = _display_precision() if precision is None else precision
        context.rounding = ROUND_HALF_EVEN
        rendered = format(Decimal(value.numerator) / Decimal(value.denominator), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def fraction_record(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator), "display": decimal_text(value)}


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY: duplicate object key")
        result[key] = value
    return result


def strict_json_loads(text: str | bytes, *, max_bytes: int = MAX_JSON_BYTES) -> Any:
    if isinstance(text, bytes):
        if len(text) > max_bytes:
            raise ValueError("MESSAGE_TOO_LARGE: JSON exceeds limit")
        try:
            text = text.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError("INVALID_UNICODE: invalid UTF-8") from exc
    if len(text.encode("utf-8", errors="surrogatepass")) > max_bytes:
        raise ValueError("MESSAGE_TOO_LARGE: JSON exceeds limit")
    def invalid_constant(_: str) -> None:
        raise ValueError("INVALID_NUMBER: non-finite JSON number")
    try:
        result = json.loads(text, parse_float=Decimal, object_pairs_hook=_unique, parse_constant=invalid_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("INVALID_JSON: malformed or excessively nested JSON") from exc
    def check(value: Any, depth: int = 0) -> None:
        if depth > 24:
            raise ValueError("JSON_TOO_DEEP: nesting exceeds limit")
        if isinstance(value, str):
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise ValueError("INVALID_UNICODE: unpaired surrogate") from exc
        elif isinstance(value, list):
            for item in value:
                check(item, depth + 1)
        elif isinstance(value, dict):
            for key, item in value.items():
                check(key, depth + 1)
                check(item, depth + 1)
    check(result)
    return result


def canonical_json(value: Any) -> bytes:
    """Canonical R0 restricted JSON: strings, booleans, safe integers, lists, maps.

    This excludes decimal numbers and non-BMP object keys. Within that subset,
    sorting follows the same order in UTF-16 and Unicode codepoints. It is a
    consistency encoding, not a production JCS implementation or authentication.
    """
    def check(item: Any, depth: int = 0) -> None:
        if depth > 24:
            raise ValueError("JSON_TOO_DEEP: canonical input too deep")
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, int):
            if abs(item) > 9007199254740991:
                raise ValueError("INTEGER_OUT_OF_RANGE: unsafe JSON integer")
            return
        if isinstance(item, str):
            item.encode("utf-8", errors="strict")
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                check(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or any(ord(ch) > 0xFFFF for ch in key):
                    raise ValueError("INVALID_KEY: canonical keys must be BMP strings")
                check(key, depth + 1)
                check(child, depth + 1)
            return
        raise ValueError("INVALID_CANONICAL_VALUE: floats and arbitrary objects are prohibited")
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
