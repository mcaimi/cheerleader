"""
Virtualized disassembly grid for the Dear PyGui front-end.

One :class:`~cheerleader.libs.types.DisasmInstruction` per row — fixed-width
text cells (address, bytes, mnemonic) plus ``OPERAND_SEGS`` operand segments
in a horizontal group.
Rendering is *virtualized* exactly like :class:`~cheerleader.gui.hexgrid.HexGrid`:
a fixed pool of row widgets covers only the visible window; sections with
10⁵–10⁶ instructions cost the same as a 20-instruction one.

Row layout (monospace font, zero item spacing — the HexGrid theme trick):

    0x0000000100000000  1f 20 03 d5 …           pacibsp      x29, sp
    └ addr (18 ch)      └ bytes (24 ch)         └ mnemonic   └ operand segments
                                                  (10 ch)

The bytes cell shows at most 8 bytes (a ``~`` marks longer instructions) so
the mnemonic column stays aligned; addresses are cyan-dim (``COL_ADDR``),
bytes dim (``COL_SEP``), cursor row bright (``COL_CURSOR``).

Syntax highlighting: the mnemonic is colored by its
:func:`~cheerleader.libs.highlight.mnemonic_kind` (``MNEM_COLORS``) and the
operands are split into ``(text, kind)`` pieces by
:func:`~cheerleader.libs.highlight.operand_spans`, one colored text item per
piece (``OPERAND_COLORS``). Zero item spacing + a monospace font make the
segments read as one string; pieces beyond ``OPERAND_SEGS`` are folded into
the last segment. The cursor row overrides every color with ``COL_CURSOR``.

Pool, scrollbar, wheel and repaint machinery come from
:class:`~cheerleader.gui.virtualgrid.VirtualGrid` (shared with HexGrid):
callbacks only mutate plain Python state and mark pool slots dirty; *all*
widget mutations happen in :meth:`tick` on the main thread. Click/wheel are
pointer-over-guarded; navigation keys (↑ ↓ PgUp PgDn Home End) follow the
HexGrid bindings. Row hit-testing is y-only (whole-row selection).
Geometry (row height, origin) is measured from the live pool like
HexGrid's; headless tests drive the grid through its state methods and the
same geometry-simulation monkeypatching used by ``tests/test_gui_hexgrid.py``.
"""

from functools import lru_cache

from cheerleader.libs.highlight import mnemonic_kind, operand_spans
from cheerleader.libs.types import DisasmInstruction

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

from cheerleader.gui.const import (
    FALLBACK_CHAR_H,
    KEY_PAGE_DOWN,
    KEY_PAGE_UP,
    MAX_POOL_ROWS,
)
from cheerleader.gui.theme import (
    COL_ADDR,
    COL_ADDR_CURSOR,
    COL_CURSOR,
    COL_SEP,
    COL_TEXT,
    MNEM_COLORS,
    OPERAND_COLORS,
)
from cheerleader.gui.virtualgrid import VirtualGrid

# Fixed char widths of the addr/bytes/mnemonic cells (operands take the rest).
ADDR_W = 18  # "0x" + 16 hex digits
BYTES_W = 26  # 8 bytes "de ad be ef 00 11 22 33" + truncation marker
MNEM_W = 10
MAX_BYTES_SHOWN = 8
OPERAND_SEGS = 8  # colored text items per row for the operand pieces


def bytes_text(raw: bytes) -> str:
    """Hex byte string for the bytes cell (≤ 8 bytes, ``~`` if truncated)."""
    text = " ".join(f"{b:02x}" for b in raw[:MAX_BYTES_SHOWN])
    if len(raw) > MAX_BYTES_SHOWN:
        text += " ~"
    return text


def row_texts(insn: DisasmInstruction) -> tuple[str, str, str, str]:
    """The four fixed-width cell texts of one instruction row."""
    return (
        f"0x{insn.addr:016x}",
        bytes_text(insn.raw).ljust(BYTES_W),
        insn.mnemonic.ljust(MNEM_W),
        insn.op_str,
    )


@lru_cache(maxsize=8192)
def operand_cells(op_str: str, arch: str) -> tuple[tuple[str, str], ...]:
    """Exactly ``OPERAND_SEGS`` ``(text, kind)`` operand cells.

    Pieces past the last segment are folded into it (keeping the last
    piece's kind); unused segments are ``("", "plain")``. Cached: the same
    operand strings recur constantly and repaints happen per frame.
    """
    pieces = operand_spans(op_str, arch)
    if len(pieces) > OPERAND_SEGS:
        head = pieces[: OPERAND_SEGS - 1]
        tail = pieces[OPERAND_SEGS - 1 :]
        pieces = [*head, ("".join(t for t, _ in tail), tail[-1][1])]
    pieces += [("", "plain")] * (OPERAND_SEGS - len(pieces))
    return tuple(pieces)


class DisasmGeometry:
    """Measured row geometry (HexGrid's :class:`GridGeometry`, row-level)."""

    def __init__(self, cw: float, row_h: float, origin_y: float) -> None:
        self.cw = cw
        self.row_h = row_h
        self.origin_y = origin_y

    def is_sane(self) -> bool:
        return self.cw > 2.0 and self.row_h > 4.0 and self.origin_y >= 0.0


def _blank_row() -> tuple[str, ...]:
    return (" " * ADDR_W, " " * BYTES_W, " " * MNEM_W) + ("",) * OPERAND_SEGS


class DisasmGrid(VirtualGrid):
    """Virtualized instruction grid; data arrives via :meth:`set_instrs`."""

    TAG_KIND = "disasm"

    def __init__(self, *, name: str = "disasm") -> None:
        super().__init__(name=name)
        self._instrs: list[DisasmInstruction] = []
        self._arch = ""  # selects the register set for operand highlighting

    # ── widget construction ───────────────────────────────────────────────

    def _build_row(self, base: str) -> tuple[str, tuple[int | str, ...]]:
        addr = dpg.add_text(" " * ADDR_W, tag=f"{base}-addr", color=COL_ADDR)
        cells = tuple(
            dpg.add_text(" ", tag=f"{base}-c{i}", color=COL_TEXT) for i in range(2)
        )
        segs = tuple(
            dpg.add_text("", tag=f"{base}-o{i}", color=COL_TEXT)
            for i in range(OPERAND_SEGS)
        )
        return (base, (addr, *cells, *segs))

    def _key_bindings(self):
        """HexGrid navigation parity, one row per instruction."""
        return [
            (dpg.mvKey_Up, self._key_up),
            (dpg.mvKey_Down, self._key_down),
            *((k, self._key_page_up) for k in {KEY_PAGE_UP, dpg.mvKey_Prior}),
            *((k, self._key_page_down) for k in {KEY_PAGE_DOWN, dpg.mvKey_Next}),
            (dpg.mvKey_Home, self._key_home),
            (dpg.mvKey_End, self._key_end),
        ]

    # ── data ───────────────────────────────────────────────────────────────

    def set_instrs(self, instrs: list[DisasmInstruction], *, arch: str = "") -> None:
        """Replace the instruction list (resets cursor + scroll).

        *arch* (``BinaryInfo.arch``) picks the register set highlighted in
        the operands.
        """
        self._instrs = instrs
        self._arch = arch
        self._cursor = 0
        self._top = 0
        self._dirty_all = True
        self._push_slider()

    def instrs(self) -> list[DisasmInstruction]:
        return self._instrs

    def _total_rows(self) -> int:
        return len(self._instrs)

    def move_to(self, index: int) -> None:
        """Select instruction *index*, scrolling only as needed."""
        n = self._total_rows()
        if n == 0:
            return
        new = max(0, min(int(index), n - 1))
        old = self._cursor
        self._cursor = new
        scrolled = False
        if new < self._top:
            self._top = new
            scrolled = True
        elif new >= self._top + self._vis:
            self._top = min(new - self._vis + 1, self._max_top())
            scrolled = True
        if scrolled:
            self._dirty_all = True
            self._push_slider()
        else:
            self._mark_rows_dirty((old, new))

    # ── keyboard ───────────────────────────────────────────────────────────

    def _key_up(self, *_args) -> None:
        if self.input_enabled:
            self.move_to(self._cursor - 1)

    def _key_down(self, *_args) -> None:
        if self.input_enabled:
            self.move_to(self._cursor + 1)

    def _key_page_up(self, *_args) -> None:
        if self.input_enabled:
            self.move_to(self._cursor - self._vis)

    def _key_page_down(self, *_args) -> None:
        if self.input_enabled:
            self.move_to(self._cursor + self._vis)

    def _key_home(self, *_args) -> None:
        if self.input_enabled:
            self.move_to(0)

    def _key_end(self, *_args) -> None:
        if self.input_enabled:
            self.move_to(max(self._total_rows() - 1, 0))

    # ── pointer (whole-row selection: y-only hit test) ─────────────────────

    def _on_click(self, *_args) -> None:
        geom = self._geometry
        if geom is None or not self.input_enabled or not self._pointer_over_grid():
            return
        _, my = dpg.get_mouse_pos(local=False)
        row = int((my - geom.origin_y) / geom.row_h) + self._top
        if 0 <= row < self._total_rows():
            self.move_to(row)

    # ── per-frame maintenance (main thread) ────────────────────────────────

    def _layout_for(self, grid_w: int, grid_h: int) -> bool:
        """Pool rows from the height (rows have a fixed column layout)."""
        new_vis = min(max(int(grid_h // self._row_h_hint()), 1), MAX_POOL_ROWS)
        if new_vis == self._vis:
            return False
        self._vis = new_vis
        return True

    def _anchor_item(self) -> int | str | None:
        return self._pool[0][1][0] if self._pool else None

    def _row_h_hint(self) -> float:
        if self._geometry is not None and self._geometry.row_h > 0:
            return self._geometry.row_h
        ts = self._text_size()
        return ts[1] if ts and ts[1] > 0 else FALLBACK_CHAR_H

    def _measure_geometry(self) -> None:
        """Measure row height once per pool build (needs a rendered frame)."""
        if self._geometry is not None or len(self._pool) < 2:
            return
        try:
            y0 = float(dpg.get_item_rect_min(self._pool[0][0])[1])
            y1 = float(dpg.get_item_rect_min(self._pool[1][0])[1])
        except (SystemError, ValueError, TypeError, KeyError):
            return
        ts = self._text_size()
        cw = ts[0] if ts and ts[0] > 0 else 9.0
        geom = DisasmGeometry(cw=cw, row_h=y1 - y0, origin_y=y0)
        if geom.is_sane():
            self._geometry = geom

    def _paint_slot(self, slot: int) -> None:
        _base, tags = self._pool[slot]  # tags: addr, bytes, mnemonic, op segs
        index = self._top + slot
        if index >= self._total_rows():
            texts = _blank_row()
            colors = (COL_ADDR, COL_SEP) + (COL_TEXT,) * (1 + OPERAND_SEGS)
        else:
            insn = self._instrs[index]
            addr, byts, mnem, _ops = row_texts(insn)
            ops = operand_cells(insn.op_str, self._arch)
            texts = (addr, byts, mnem, *(t for t, _ in ops))
            if index == self._cursor:
                colors = (COL_ADDR_CURSOR,) + (COL_CURSOR,) * (2 + OPERAND_SEGS)
            else:
                colors = (
                    COL_ADDR,
                    COL_SEP,
                    MNEM_COLORS[mnemonic_kind(insn.mnemonic)],
                    *(OPERAND_COLORS[k] for _, k in ops),
                )
        for tag, text, color in zip(tags, texts, colors, strict=True):
            self._emit(tag, text, color)
