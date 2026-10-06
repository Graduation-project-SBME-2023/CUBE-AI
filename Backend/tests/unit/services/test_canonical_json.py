"""RFC 8785 canonical JSON: the rule behind graph_changes.content_digest."""

from __future__ import annotations

import hashlib
import json
import struct

import pytest

from application_api.core.canonical_json import canonicalize, content_digest

pytestmark = pytest.mark.unit


def _double(bits: str) -> float:
    return struct.unpack(">d", bytes.fromhex(bits))[0]


# RFC 8785 Appendix B: IEEE 754 bit patterns and their ECMAScript forms.
@pytest.mark.parametrize(
    ("bits", "expected"),
    [
        ("0000000000000000", "0"),
        ("8000000000000000", "0"),
        ("0000000000000001", "5e-324"),
        ("8000000000000001", "-5e-324"),
        ("7fefffffffffffff", "1.7976931348623157e+308"),
        ("ffefffffffffffff", "-1.7976931348623157e+308"),
        ("4340000000000000", "9007199254740992"),
        ("c340000000000000", "-9007199254740992"),
        ("4430000000000000", "295147905179352830000"),
        ("44b52d02c7e14af5", "9.999999999999997e+22"),
        ("44b52d02c7e14af6", "1e+23"),
        ("44b52d02c7e14af7", "1.0000000000000001e+23"),
        ("444b1ae4d6e2ef4e", "999999999999999700000"),
        ("444b1ae4d6e2ef4f", "999999999999999900000"),
        ("444b1ae4d6e2ef50", "1e+21"),
        ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
        ("3eb0c6f7a0b5ed8d", "0.000001"),
        ("41b3de4355555553", "333333333.3333332"),
        ("41b3de4355555554", "333333333.33333325"),
        ("41b3de4355555555", "333333333.3333333"),
        ("41b3de4355555556", "333333333.3333334"),
        ("41b3de4355555557", "333333333.33333343"),
        ("becbf647612f3696", "-0.0000033333333333333333"),
        ("43143ff3c1cb0959", "1424953923781206.2"),
    ],
)
def test_numbers_use_the_ecmascript_form(bits: str, expected: str):
    assert canonicalize(_double(bits)) == expected.encode()


def test_object_keys_sort_by_utf16_code_units():
    """RFC 8785 3.2.3: an astral character (a surrogate pair) sorts before U+FB33."""
    keys = [chr(0x20AC), "\r", chr(0xFB33), "1", chr(0x1F600), chr(0x80), chr(0xF6)]
    canonical = canonicalize({key: index for index, key in enumerate(keys)})
    # json.loads keeps the serialized key order.
    assert list(json.loads(canonical)) == [
        "\r", "1", chr(0x80), chr(0xF6), chr(0x20AC), chr(0x1F600), chr(0xFB33)
    ]


def test_the_rfc_sample_canonicalizes_byte_for_byte():
    """RFC 8785 3.2.2 sample: whitespace, number forms and string escapes."""
    value = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
        "string": chr(0x20AC) + "$" + chr(0x0F) + "\nA'B\"\\\\\"/",
        "literals": [None, True, False],
    }
    assert canonicalize(value).decode("utf-8") == (
        '{"literals":[null,true,false],'
        '"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"' + chr(0x20AC) + '$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )


def test_integral_floats_and_ints_agree():
    assert canonicalize({"a": 100.0, "b": 100}) == b'{"a":100,"b":100}'


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), 2**53 + 1, chr(0xD800), {1: "non-string key"}, {"set"}],
)
def test_values_rfc_8785_cannot_represent_are_refused(value):
    with pytest.raises((ValueError, TypeError)):
        canonicalize(value)


def test_the_digest_is_sha256_of_the_canonical_bytes():
    content = {"v": 1, "nodes": [{"node_id": "n", "params": {"b": 1, "a": 0.5}}]}
    assert content_digest(content) == hashlib.sha256(
        b'{"nodes":[{"node_id":"n","params":{"a":0.5,"b":1}}],"v":1}'
    ).hexdigest()
    # Key order and insignificant formatting never change the digest.
    reordered = {"nodes": [{"params": {"a": 0.5, "b": 1.0}, "node_id": "n"}], "v": 1}
    assert content_digest(reordered) == content_digest(content)
