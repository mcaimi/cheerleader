"""Tests for user-input parsing: byte sequences and offsets.

The parsers live in ``cheerleader.tui.hexparse`` — TUI-layer input parsing
with user-facing error messages (moved out of ``libs.hexdoc``).
"""

from __future__ import annotations

import pytest

from cheerleader.tui.hexparse import parse_byte_sequence, parse_offset


class TestParseByteSequence:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # literal text (opening quote, closing quote optional)
            ('"hello', b"hello"),
            ('"hello"', b"hello"),
            ("'abc'", b"abc"),
            ('"café', "café".encode("utf-8")),
            # hex bytes separated by spaces / commas / mixed whitespace
            ("de ad be ef", b"\xde\xad\xbe\xef"),
            ("de,ad,be,ef", b"\xde\xad\xbe\xef"),
            ("DE AD BE EF", b"\xde\xad\xbe\xef"),
            ("de  ad\tbe\nef", b"\xde\xad\xbe\xef"),
            ("  de ad  ", b"\xde\xad"),
            # packed hex (even number of digits)
            ("deadbeef", b"\xde\xad\xbe\xef"),
            ("deadbeef00", b"\xde\xad\xbe\xef\x00"),
            # single hex byte with optional 0x prefix
            ("0x7f", b"\x7f"),
            ("7f", b"\x7f"),
            ("7", b"\x07"),
            ("0xFF", b"\xff"),
            # decimal byte
            ("#127", b"\x7f"),
            ("#0", b"\x00"),
            ("#255", b"\xff"),
            # mixed forms
            ("#10 0x20 ff", b"\x0a\x20\xff"),
        ],
    )
    def test_valid(self, text: str, expected: bytes) -> None:
        assert parse_byte_sequence(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",            # empty input
            "   ",         # whitespace only
            '"',           # quote with empty string
            "''",          # empty single-quoted string
            "#256",        # decimal out of range
            "#-1",         # negative decimal
            "#abc",        # bad decimal value
            "xyz",         # not a hex byte
            "0x",          # prefix without digits
            "abc",         # odd number of hex digits
            "deadbeef0",   # odd number of hex digits (packed)
            "1.5",         # not a hex byte
        ],
    )
    def test_invalid(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_byte_sequence(text)


class TestParseOffset:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1a2b", 0x1A2B),
            ("0x1a2b", 0x1A2B),
            ("0X1A2B", 0x1A2B),
            ("ff", 0xFF),
            ("#6699", 6699),
            ("#0", 0),
            ("1_000", 0x1000),   # underscores stripped
            ("  0xff  ", 0xFF),  # surrounding whitespace stripped
        ],
    )
    def test_valid(self, text: str, expected: int) -> None:
        assert parse_offset(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",     # empty offset
            "   ",  # whitespace only
            "zz",   # not hex
            "#zz",  # bad decimal
        ],
    )
    def test_invalid(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_offset(text)
