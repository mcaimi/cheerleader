"""Shared hex-row layout and rendering for the hex views.

Single source of truth for the row geometry used by the hex editor grid,
the side-by-side compare view, and the plain hex dump pane:

    offset(8) + 2 + hex bytes in 3-char slots (one extra gap column before
    each subsequent 8-byte group) + "  |" + ascii(1 char per byte) + "|"

"""

from __future__ import annotations

from collections.abc import Sequence

from rich.segment import Segment
from rich.style import Style

BPL = 16  # default bytes per row
OFFSET_W = 8  # width of the "%08x" offset field


def ascii_char(b: int) -> str:
    """Printable ASCII character for byte *b*, '.' otherwise."""
    return chr(b) if 0x20 <= b < 0x7F else "."


# ── row geometry ──────────────────────────────────────────────────────────────


def hex_col_x(bpl: int, i: int) -> int:
    """X column of the first hex digit of byte *i* (0 = offset field).

    Each byte occupies a 3-char slot (two digits + one separator); the
    separator before each 8-byte group after the first is two chars wide,
    contributing one extra column per preceding group.
    """
    return OFFSET_W + 2 + i * 3 + i // 8


def group_gap_x(bpl: int) -> int | None:
    """X of the blank gap column between the two 8-byte groups.

    ``None`` when *bpl* fits in a single group (``bpl <= 8``).
    """
    return hex_col_x(bpl, 8) - 1 if bpl > 8 else None


def ascii_col_x(bpl: int) -> int:
    """X column of the first ASCII character cell."""
    # last byte's two digits + the "  |" separator (3 chars)
    return hex_col_x(bpl, bpl - 1) + 5


def row_width(bpl: int) -> int:
    """Total width of one rendered row, including the trailing '|'."""
    return ascii_col_x(bpl) + bpl + 1


def x_to_col(bpl: int, x: int) -> int | None:
    """Byte column at row-relative *x*, or ``None`` for non-byte cells.

    Maps the hex digit cells (either digit of a byte selects it) and the
    ASCII cells; returns ``None`` for the offset field, byte separators,
    the inter-group gap column, and the outer '|' cells.
    """
    hex_start = OFFSET_W + 2
    if hex_start <= x < hex_start + bpl * 3:
        rel = x - hex_start
        rel -= rel // 24  # collapse the extra gap column before each 8-byte group
        if rel % 3 == 2:
            return None  # separator cell
        return rel // 3
    a = ascii_col_x(bpl)
    if a <= x < a + bpl:
        return x - a
    return None


# ── row rendering ─────────────────────────────────────────────────────────────


def row_segments(
    offset: int,
    data: bytes,
    styles: Sequence[Style | None],
    bpl: int = BPL,
    addr_style: Style | None = None,
) -> list[Segment]:
    """Segments for one hex row: offset, hex bytes, |ascii|.

    Rows are always padded to *bpl* bytes; bytes past the end of *data*
    render blank. *styles* holds one entry per byte in *data* (``None`` =
    default style).
    """
    segs: list[Segment] = [Segment(f"{offset:08x}", addr_style), Segment("  ")]
    for i in range(bpl):
        if i:
            segs.append(Segment("  " if i % 8 == 0 else " "))
        if i < len(data):
            segs.append(Segment(f"{data[i]:02x}", styles[i]))
        else:
            segs.append(Segment("  "))
    segs.append(Segment("  |"))
    for i in range(bpl):
        if i < len(data):
            segs.append(Segment(ascii_char(data[i]), styles[i]))
        else:
            segs.append(Segment(" "))
    segs.append(Segment("|"))
    return segs


def hex_line(offset: int, data: bytes, bpl: int = BPL) -> str:
    """One hex row as plain text (no styling), padded to the full row width."""
    return "".join(
        s.text for s in row_segments(offset, data, [None] * len(data), bpl)
    )


def build_hex_dump(data: bytes, base_addr: int) -> str:
    """Whole-buffer hex dump as plain text, one row per 16 bytes."""
    return "\n".join(
        hex_line(base_addr + i, data[i : i + 16]) for i in range(0, len(data), 16)
    )
