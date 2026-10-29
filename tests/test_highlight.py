"""Tests for the shared formatting helpers: human_size and raw_hex.

human_size replaces the two previous byte formatters (_human_size in tabs
and _fmt_size in highlight) with one implementation using correct binary
unit labels (bytes/KiB/MiB/GiB).
"""

from __future__ import annotations

import pytest

from cheerleader.tui.highlight import human_size, raw_hex


class TestHumanSize:
    @pytest.mark.parametrize(
        ("size", "expected"),
        [
            (0, "0 bytes"),
            (1, "1 bytes"),
            (299, "299 bytes"),
            (1023, "1023 bytes"),
            (1024, "1.0 KiB"),
            (1536, "1.5 KiB"),
            (1024 * 1024 - 1, "1024.0 KiB"),
            (1024 * 1024, "1.0 MiB"),
            (150 * 1024 * 1024, "150.0 MiB"),
            (1024 * 1024 * 1024 - 1, "1024.0 MiB"),
            (1024 * 1024 * 1024, "1.0 GiB"),
            (5 * 1024 * 1024 * 1024, "5.0 GiB"),
        ],
    )
    def test_format(self, size: int, expected: str) -> None:
        assert human_size(size) == expected

    def test_binary_units_not_decimal(self):
        # 150 * 10^6 (a "150 MB" file in SI units) is 143.1 MiB — the old
        # _fmt_size labeled ÷1024 results as "KB"/"MB"; the unified formatter
        # uses the correct binary labels
        assert human_size(150_000_000) == "143.1 MiB"


class TestRawHex:
    def test_basic(self):
        assert raw_hex(b"\xde\xad\xbe\xef") == "de ad be ef"

    def test_empty(self):
        assert raw_hex(b"") == ""

    def test_single_byte(self):
        assert raw_hex(b"\x00") == "00"

    def test_limit_truncates(self):
        assert raw_hex(b"\xde\xad\xbe\xef", limit=2) == "de ad"

    def test_limit_larger_than_input(self):
        assert raw_hex(b"\xde\xad", limit=10) == "de ad"

    def test_limit_zero(self):
        assert raw_hex(b"\xde\xad", limit=0) == ""

    def test_matches_legacy_join(self):
        # byte-identical to the previously-duplicated inline comprehensions
        raw = bytes(range(12))
        assert raw_hex(raw) == " ".join(f"{b:02x}" for b in raw)
        assert raw_hex(raw, limit=6) == " ".join(f"{b:02x}" for b in raw[:6])
