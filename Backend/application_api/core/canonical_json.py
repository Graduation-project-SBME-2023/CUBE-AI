"""RFC 8785 (JSON Canonicalization Scheme) and the graph-change content digest.

`graph_changes.content_digest` is the SHA-256 of the RFC 8785 canonical JSON of
`content`, computed by the Application API. A user decision
must quote it, so every writer of that column (the graph-change repository and the
migration backfill) calls `content_digest` here and nowhere else.

RFC 8785 differs from `json.dumps(sort_keys=True)` in two places: object keys
sort by UTF-16 code units (not code points), and numbers use the ECMAScript
`Number.prototype.toString` form (`1e+21`, `5e-7`, `100` for `100.0`).
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

# RFC 8785 numbers are IEEE 754 doubles; a larger integer would be silently
# rounded by every other implementation, so it is refused instead.
_MAX_EXACT_INTEGER = 2**53

_SHORT_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _string(value: str) -> str:
    out = ['"']
    for char in value:
        escaped = _SHORT_ESCAPES.get(char)
        if escaped is not None:
            out.append(escaped)
        elif char < " ":
            out.append(f"\\u{ord(char):04x}")
        elif "\ud800" <= char <= "\udfff":
            raise ValueError("lone surrogates cannot be canonicalized (RFC 8785 3.2.2.2)")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _number(value: float) -> str:
    """ECMAScript Number.prototype.toString for a finite double."""
    if math.isnan(value) or math.isinf(value):
        raise ValueError("NaN and Infinity are not JSON (RFC 8785 3.2.2.3)")
    if value == 0:
        return "0"  # also -0
    sign = "-" if value < 0 else ""
    # repr() is the shortest string that round-trips, the same digits ES uses.
    text = repr(abs(value))
    mantissa, _, exponent = text.partition("e")
    whole, _, fraction = mantissa.partition(".")
    digits = whole + fraction
    stripped = digits.lstrip("0")
    # n: the position of the decimal point relative to the first significant digit.
    n = len(whole) + (int(exponent) if exponent else 0) - (len(digits) - len(stripped))
    digits = stripped.rstrip("0")
    k = len(digits)
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    exp = n - 1
    exp_text = ("+" if exp >= 0 else "-") + str(abs(exp))
    if k == 1:
        return sign + digits + "e" + exp_text
    return sign + digits[0] + "." + digits[1:] + "e" + exp_text


def _utf16_key(key: str) -> bytes:
    return key.encode("utf-16-be", "surrogatepass")


def _serialize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > _MAX_EXACT_INTEGER:
            raise ValueError(
                f"integer {value} is outside the exact IEEE 754 range (RFC 8785 3.2.2.3)"
            )
        return str(value)
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise TypeError(f"JSON object keys must be strings, got {type(key).__name__}")
        return (
            "{"
            + ",".join(
                _string(key) + ":" + _serialize(value[key])
                for key in sorted(value, key=_utf16_key)
            )
            + "}"
        )
    raise TypeError(f"{type(value).__name__} is not a JSON value")


def canonicalize(value: Any) -> bytes:
    """The RFC 8785 canonical UTF-8 bytes of a JSON value."""
    return _serialize(value).encode("utf-8")


def content_digest(value: Any) -> str:
    """SHA-256 hex of the canonical JSON: the `graph_changes.content_digest` rule."""
    return hashlib.sha256(canonicalize(value)).hexdigest()
