"""Tests for the shared hex-row layout and rendering (cheerleader.tui.hexrender).

These pin the geometry that the hex editor, compare view, and hex pane all
share — including the click hit-testing inverse mapping.
"""

from __future__ import annotations

import pytest
from rich.segment import Segment
from rich.style import Style

from cheerleader.tui import hexrender
from cheerleader.tui.hexrender import (
    OFFSET_W,
    ascii_char,
    ascii_col_x,
    build_hex_dump,
    group_gap_x,
    hex_col_x,
    hex_line,
    row_segments,
    row_width,
    x_to_col,
)

RED = Style(color="red")
GREEN = Style(color="green")


class TestAsciiChar:
    @pytest.mark.parametrize(
        ("b", "expected"),
        [
            (0x20, " "),   # first printable
            (0x7E, "~"),   # last printable
            (0x41, "A"),
            (0x00, "."),
            (0x1F, "."),   # just below printable range
            (0x7F, "."),   # just above printable range
            (0xFF, "."),
        ],
    )
    def test_mapping(self, b: int, expected: str) -> None:
        assert ascii_char(b) == expected


class TestGeometry:
    """Pin the column math against the pre-refactor hardcoded constants."""

    def test_editor_constants(self):
        # HexEditorView previously hardcoded _HEX_X = 10, _ASCII_X = 61
        assert hex_col_x(16, 0) == 10
        assert hex_col_x(16, 7) == 31
        assert hex_col_x(16, 8) == 35   # after the extra group-gap column
        assert hex_col_x(16, 15) == 56
        assert ascii_col_x(16) == 61
        assert row_width(16) == 78

    def test_narrow_layout(self):
        # HexCompareView at bpl=8: no group gap, bar at column 44
        assert hex_col_x(8, 0) == 10
        assert hex_col_x(8, 7) == 31
        assert ascii_col_x(8) == 36
        assert row_width(8) == 45
        assert group_gap_x(8) is None

    def test_group_gap(self):
        assert group_gap_x(16) == 34
        assert group_gap_x(8) is None

    def test_matches_legacy_pane_width(self):
        # HexCompareView._pane_width before the refactor
        def legacy_pane_width(bpl: int) -> int:
            hex_w = bpl * 3 - 1 + (bpl // 8 - 1)
            return 8 + 2 + hex_w + 2 + bpl + 2

        for bpl in (8, 16):
            assert row_width(bpl) == legacy_pane_width(bpl)


class TestXToCol:
    """Hit-testing: x_to_col must be the exact inverse of the layout."""

    @pytest.mark.parametrize("bpl", [8, 16])
    def test_hex_digits_round_trip(self, bpl: int) -> None:
        for i in range(bpl):
            first = hex_col_x(bpl, i)
            assert x_to_col(bpl, first) == i       # first hex digit
            assert x_to_col(bpl, first + 1) == i   # second hex digit

    @pytest.mark.parametrize("bpl", [8, 16])
    def test_non_byte_cells_return_none(self, bpl: int) -> None:
        # offset field
        for x in range(0, OFFSET_W + 2):
            assert x_to_col(bpl, x) is None
        # inter-group gap
        gap = group_gap_x(bpl)
        if gap is not None:
            assert x_to_col(bpl, gap) is None
        # byte separators: cell right before each byte's first digit
        for i in range(1, bpl):
            sep = hex_col_x(bpl, i) - 1
            if sep != gap:
                assert x_to_col(bpl, sep) is None
        # trailing '|' and beyond
        end = ascii_col_x(bpl) + bpl
        for x in (end, end + 5):
            assert x_to_col(bpl, x) is None

    @pytest.mark.parametrize("bpl", [8, 16])
    def test_ascii_cells(self, bpl: int) -> None:
        a = ascii_col_x(bpl)
        for j in range(bpl):
            assert x_to_col(bpl, a + j) == j

    @pytest.mark.parametrize("bpl", [8, 16])
    def test_exhaustive_partition(self, bpl: int) -> None:
        """Every x inside the hex area maps to a byte digit cell or a gap —
        exactly two digit cells per byte column."""
        hex_area = range(OFFSET_W + 2, OFFSET_W + 2 + bpl * 3)
        cols: dict[int, int] = {}
        for x in hex_area:
            col = x_to_col(bpl, x)
            if col is not None:
                cols[col] = cols.get(col, 0) + 1
        assert set(cols) == set(range(bpl))
        assert all(n == 2 for n in cols.values())  # two digits per byte


class TestRowSegments:
    def test_full_row_structure(self):
        data = bytes(range(16))
        segs = row_segments(0x100, data, [RED] * 16, 16, GREEN)
        text = "".join(s.text for s in segs)
        assert len(text) == row_width(16)
        # offset field + addr style
        assert segs[0].text == "00000100"
        assert segs[0].style is GREEN
        # hex digits at their layout positions
        for i in range(16):
            x = hex_col_x(16, i)
            assert text[x : x + 2] == f"{data[i]:02x}"
            assert segs_style_at(segs, x) is RED
        # group gap is blank
        assert text[group_gap_x(16)] == " "
        # ascii column
        a = ascii_col_x(16)
        for i in range(16):
            assert text[a + i] == ascii_char(data[i])
        # enclosing bars
        assert text[a - 1] == "|"
        assert text[-1] == "|"

    def test_styles_only_cover_data_length(self):
        data = b"\xde\xad"
        segs = row_segments(0, data, [RED, GREEN], 16)
        text = "".join(s.text for s in segs)
        assert segs_style_at(segs, hex_col_x(16, 0)) is RED
        assert segs_style_at(segs, hex_col_x(16, 1)) is GREEN
        # bytes past the end of data render blank (padded to bpl)
        assert text[hex_col_x(16, 2) : hex_col_x(16, 2) + 2] == "  "
        assert len(text) == row_width(16)

    def test_default_addr_style_is_none(self):
        segs = row_segments(0, b"\x00", [None])
        assert segs[0].style is None

    def test_ascii_style_matches_byte_style(self):
        segs = row_segments(0, b"A\x01", [RED, GREEN], 16)
        a = ascii_col_x(16)
        assert segs_style_at(segs, a) is RED
        assert segs_style_at(segs, a + 1) is GREEN


def segs_style_at(segs: list[Segment], x: int) -> Style | None:
    """Style of the segment covering column *x* in a row's segment list."""
    pos = 0
    for seg in segs:
        if pos <= x < pos + len(seg.text):
            return seg.style
        pos += len(seg.text)
    raise AssertionError(f"column {x} outside row of width {pos}")


class TestPlainText:
    def test_hex_line_matches_legacy_format_full_rows(self):
        # the segment-based renderer must produce byte-identical text to the
        # original f-string implementation for full 16-byte rows
        data = bytes(range(16)) + b"HELLO WORLD"
        for start in range(0, 16, 16):  # the first row is full
            offset = start
            chunk = data[start : start + 16]
            hex_left = " ".join(f"{b:02x}" for b in chunk[:8])
            hex_right = " ".join(f"{b:02x}" for b in chunk[8:])
            ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
            legacy = f"{offset:08x}  {hex_left:<23}  {hex_right:<23}  |{ascii_part}|"
            assert hex_line(offset, chunk) == legacy

    def test_build_hex_dump_full_rows(self):
        data = bytes(range(16)) * 2
        dump = build_hex_dump(data, 0x1000)
        lines = dump.split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("00001000  00 01 02 03 04 05 06 07  08 09 0a 0b")
        assert lines[0].endswith("|................|")
        assert lines[1].startswith("00001010  00 01 02 03 04 05 06 07")

    def test_build_hex_dump_line_count(self):
        assert build_hex_dump(b"", 0) == ""
        assert len(build_hex_dump(b"A" * 33, 0).split("\n")) == 3

    def test_ascii_rendering(self):
        line = hex_line(0, b"A\x01 ~\x7f")
        a = ascii_col_x(16)
        assert line[a : a + 5] == "A. ~."
