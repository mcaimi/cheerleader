"""
Virtualized hex + ASCII grid widget for the Dear PyGui front-end.

Renders a :class:`~cheerleader.libs.hexdoc.HexDocument` in the classic hex row
layout shared with the terminal UI (``tui.hexrender``)::

    offset(8) + 2 + hex bytes in 3-char slots (one extra gap column before
    each subsequent 8-byte group) + "  |" + ascii(1 char per byte) + "|"

Rendering is *virtualized*: a fixed pool of Dear PyGui text items covers only
the visible window (one offset + bpl hex cells + bpl ASCII cells per row).
The pool is re-painted only when the visible state changes (scroll, cursor,
matches, edits) — never per frame — so a multi-GB file costs the same as a
4 KiB one.

Design notes:

* **Pure geometry** — the row layout math and pixel hit-testing live in
module-level pure functions (:func:`hit_test`, :func:`hex_col_x`, ...),
unit-tested without a display. Parity with the terminal renderer is
enforced by a test composing the cell texts against
``tui.hexrender.hex_line``.
* **Measured hit-testing** — clicks resolve against cell positions
*measured from the live widgets* (:class:`GridGeometry`), so hit-testing
stays correct even when font metrics or spacing differ from the ideal
character grid. ``get_item_rect_min`` and ``get_mouse_pos(local=False)``
are assumed to share one coordinate frame (DPG "screen" space); if that
ever proves wrong on-screen, only :meth:`HexGrid._measure_geometry` and
:meth:`HexGrid._on_click` need adjusting.
* **Threading / pool / scrollbar** — shared with the disassembly grid in
:class:`~cheerleader.gui.virtualgrid.VirtualGrid`: callbacks only mutate
plain Python state and mark pool slots dirty; *all* Dear PyGui item
mutations happen in :meth:`HexGrid.tick` on the main thread.
* **Colors** — Dear PyGui text items carry no background. The editor's
cursor gets a drawn background (:meth:`HexGrid.enable_cursor_highlight`,
TUI white-on-blue); elsewhere (and for the black-on-magenta search match)
bright foreground colors stand in.
* **PageUp/PageDown** — handlers are registered under both the correct
ImGui codes (517/518) and the stale ``mvKey_Prior``/``mvKey_Next``
constants (266/267 — dearpygui 2.3.1 bug, upstream issue #2658); the wrong
spelling is inert.
"""

import bisect
from collections.abc import Callable
from dataclasses import dataclass

from cheerleader.libs.hexdoc import HexDocument

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

from cheerleader.gui.const import (
    BPL,
    DEFAULT_VISIBLE_ROWS,
    FALLBACK_CHAR_H,
    FALLBACK_CHAR_W,
    KEY_PAGE_DOWN,
    KEY_PAGE_UP,
    MAX_POOL_ROWS,
    OFFSET_W,
)
from cheerleader.gui.theme import (
    COL_ADDR,
    COL_ADDR_CURSOR,
    COL_BRIGHT,
    COL_CURSOR,
    COL_CURSOR_BG,
    COL_EDITED,
    COL_MATCH,
    COL_SEP,
    COL_TEXT,
)
from cheerleader.gui.tooling import ctrl_down
from cheerleader.gui.virtualgrid import VirtualGrid


def hex_col_x(bpl: int, i: int) -> int:
    """Char column of the first hex digit of byte *i* (mirrors tui.hexrender)."""
    return OFFSET_W + 2 + i * 3 + i // 8


def ascii_col_x(bpl: int) -> int:
    """Char column of the first ASCII character cell."""
    return hex_col_x(bpl, bpl - 1) + 5


def row_width(bpl: int) -> int:
    """Total char width of one rendered row, including the trailing '|'."""
    return ascii_col_x(bpl) + bpl + 1


def hex_cell_width(bpl: int, i: int) -> int:
    """Char width of hex cell *i*: two digits plus padding to the next cell."""
    if i == bpl - 1:
        return 2  # the "  |" separator is its own pooled item
    return hex_col_x(bpl, i + 1) - hex_col_x(bpl, i)


def offset_text(offset: int, bpl: int = BPL) -> str:
    """Offset field text: ``'%08x'`` plus the two-column gap."""
    return f"{offset:0{OFFSET_W}x}" + " " * (hex_col_x(bpl, 0) - OFFSET_W)


def hex_cell_text(bpl: int, i: int, value: int | None) -> str:
    """Hex cell *i* text: two digits (blank past EOF) padded to its slot."""
    digits = "  " if value is None else f"{value:02x}"
    return digits + " " * (hex_cell_width(bpl, i) - 2)


def ascii_cell_text(value: int | None) -> str:
    """Printable ASCII character for *value*, ``'.'`` for non-printables."""
    if value is None:
        return " "
    return chr(value) if 0x20 <= value < 0x7F else "."


def bpl_for_cols(cols: int) -> int:
    """Bytes per row fitting in *cols* character columns (16, else 8)."""
    return BPL if cols >= row_width(BPL) else 8


def bpl_for_width(width_px: float, char_w: float) -> int:
    """Bytes per row fitting in *width_px* pixels at *char_w* px per char."""
    if char_w <= 0:
        return BPL
    return bpl_for_cols(int(width_px // char_w))


def visible_rows_for(height_px: float, row_h: float) -> int:
    """Conservative visible-row count for *height_px* at *row_h* px per row.

    Row heights are under-estimated by design so the pool always covers every
    on-screen row — an under-sized pool would hide the last rows from
    scrolling. ``row_h <= 0`` (no metrics yet) falls back to the default.
    """
    if row_h <= 0:
        return DEFAULT_VISIBLE_ROWS
    return max(1, int(height_px // row_h))


@dataclass(frozen=True)
class GridGeometry:
    """Cell positions measured from the live widgets (DPG screen coordinates).

    ``cell_x``/``ascii_x`` hold the left edge of each hex/ASCII cell of the
    top pool row; ``cw`` is the character width and ``row_h`` the distance
    between consecutive rows.
    """

    cw: float
    row_h: float
    origin_y: float
    cell_x: tuple[float, ...]
    ascii_x: tuple[float, ...]

    def is_sane(self, bpl: int) -> bool:
        """True when the measurement is usable for hit-testing.

        Floors on ``cw``/``row_h`` reject degenerate (unlaid-out or scaled)
        measurements that would otherwise explode the visible-row estimate.
        """
        if self.cw < 3.0 or self.row_h < 3.0:
            return False
        if len(self.cell_x) != bpl or len(self.ascii_x) != bpl:
            return False
        return (
            all(self.cell_x[i] < self.cell_x[i + 1] for i in range(bpl - 1))
            and all(self.ascii_x[i] < self.ascii_x[i + 1] for i in range(bpl - 1))
            and self.cell_x[-1] < self.ascii_x[0]
        )


def hit_test(
    geom: GridGeometry,
    *,
    bpl: int,
    top_row: int,
    max_offset: int,
    x: float,
    y: float,
) -> int | None:
    """Pure pixel→byte mapping; ``None`` for non-byte cells and past EOF.

    *x*/*y* share :class:`GridGeometry`'s coordinate frame. Hex digits select
    their byte, ASCII cells select their byte; the offset field, digit
    separators, inter-group gap, pipes and the area between the panes select
    nothing. ``max_offset`` is the document size (exclusive).
    """
    row = int((y - geom.origin_y) // geom.row_h)
    if row < 0:
        return None
    if geom.cell_x[0] <= x < geom.cell_x[-1] + 2 * geom.cw:
        i = bisect.bisect_right(geom.cell_x, x) - 1
        if x >= geom.cell_x[i] + 2 * geom.cw:
            return None  # separator / inter-group gap column
        col = i
    elif geom.ascii_x[0] <= x < geom.ascii_x[-1] + geom.cw:
        col = bisect.bisect_right(geom.ascii_x, x) - 1
    else:
        return None
    offset = (top_row + row) * bpl + col
    if offset >= max_offset:
        return None
    return offset


@dataclass
class _PoolRow:
    """Widget tags of one pooled row slot."""

    group: int | str
    offset: int | str
    hexes: list[int | str]
    pipe: int | str
    asciis: list[int | str]
    end: int | str


class HexGrid(VirtualGrid):
    """Virtualized hex+ASCII grid over a :class:`HexDocument`.

    The widget tree is built once by :meth:`build` and re-painted on demand by
    :meth:`tick` (called once per frame by the app shell). Callbacks only
    mutate plain state; see :mod:`cheerleader.gui.virtualgrid`.
    """

    TAG_KIND = "hexgrid"

    def __init__(
        self,
        doc: HexDocument,
        *,
        name: str = "hex",
        on_cursor: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(name=name)
        self._doc = doc
        self._bpl = BPL
        self._match: tuple[int, int] | None = None
        self.on_cursor = on_cursor
        # Compare mode: per-byte color override, shared scroll
        # range and row-scrolling keys. All default to the editor behavior.
        self.color_for: Callable[[int], tuple[int, int, int, int]] | None = None
        self.max_top_rows: Callable[[], int] | None = None
        self.scroll_mode = False
        # Cursor background (see enable_cursor_highlight): two rectangles
        # (hex cell, ASCII cell) drawn on the tool window behind the grid.
        self._hl_rects: tuple[int | str, int | str] | None = None
        self._hl_window: int | str | None = None
        self._hl_origin: int | str | None = None
        self._hl_last: object = None

    # ── widget construction ───────────────────────────────────────────────

    def _build_row(self, base: str) -> _PoolRow:
        off = dpg.add_text(" " * (OFFSET_W + 2), tag=f"{base}-off", color=COL_ADDR)
        hexes = [
            dpg.add_text("  ", tag=f"{base}-h{i}", color=COL_TEXT)
            for i in range(self._bpl)
        ]
        pipe = dpg.add_text("  |", tag=f"{base}-pipe", color=COL_SEP)
        asciis = [
            dpg.add_text(" ", tag=f"{base}-a{i}", color=COL_TEXT)
            for i in range(self._bpl)
        ]
        end = dpg.add_text("|", tag=f"{base}-end", color=COL_SEP)
        return _PoolRow(
            group=base, offset=off, hexes=hexes, pipe=pipe, asciis=asciis, end=end
        )

    def _anchor_item(self) -> int | str | None:
        return self._pool[0].offset if self._pool else None

    def enable_cursor_highlight(self, window: int | str, origin: int | str) -> None:
        """Paint the cursor cell's *background* (TUI white-on-blue).

        DPG text has no background, so two rectangles are drawn on the
        top-level tool *window*: window draw items land on the window's own
        draw list, which ImGui renders underneath its (transparent) child
        windows — i.e. behind the grid's text. Draw-item coordinates are
        relative to the window's content start: the window's ``pos`` plus
        that of *origin*, the window's first content child (windows report
        ``pos``, not ``rect_min``).
        """
        self._hl_window = window
        self._hl_origin = origin
        self._hl_rects = tuple(  # type: ignore[assignment]
            dpg.draw_rectangle(
                (0, 0),
                (0, 0),
                parent=window,
                fill=COL_CURSOR_BG,
                color=(0, 0, 0, 0),
                show=False,
            )
            for _ in range(2)
        )
        self.refresh()  # cursor text color changes with the highlight

    def _after_paint(self) -> None:
        if self._hl_rects is None:
            return
        place = self._highlight_rects()
        if place == self._hl_last:
            return
        self._hl_last = place
        for tag, rect in zip(self._hl_rects, place or (None, None), strict=True):
            if not dpg.does_item_exist(tag):
                return
            if rect is None:
                dpg.configure_item(tag, show=False)
            else:
                dpg.configure_item(tag, pmin=rect[0], pmax=rect[1], show=True)

    def _highlight_rects(self):
        """``((pmin, pmax) hex, (pmin, pmax) ascii)`` of the cursor cell in
        window draw coordinates, or ``None`` when it is not on screen."""
        geom = self._geometry
        if geom is None or self._doc.size == 0:
            return None
        slot = self._cursor // self._bpl - self._top
        if not 0 <= slot < self._vis:
            return None
        try:
            wx, wy = dpg.get_item_pos(self._hl_window)
            cx, cy = dpg.get_item_pos(self._hl_origin)
        except (SystemError, ValueError, TypeError, KeyError):
            return None
        ox, oy = wx + cx, wy + cy
        col = self._cursor % self._bpl
        y0 = geom.origin_y + slot * geom.row_h - oy
        y1 = y0 + geom.row_h
        hx = geom.cell_x[col] - ox
        ax = geom.ascii_x[col] - ox
        return (
            ((hx - 1, y0), (hx + 2 * geom.cw + 1, y1)),
            ((ax, y0), (ax + geom.cw, y1)),
        )

    def _key_bindings(self):
        """The TUI editor view's navigation bindings."""
        return [
            (dpg.mvKey_Left, self._key_left),
            (dpg.mvKey_Right, self._key_right),
            (dpg.mvKey_Up, self._key_up),
            (dpg.mvKey_Down, self._key_down),
            # Both code spellings: see KEY_PAGE_UP/KEY_PAGE_DOWN (#2658).
            *((k, self._key_page_up) for k in {KEY_PAGE_UP, dpg.mvKey_Prior}),
            *((k, self._key_page_down) for k in {KEY_PAGE_DOWN, dpg.mvKey_Next}),
            (dpg.mvKey_Home, self._key_home),
            (dpg.mvKey_End, self._key_end),
        ]

    # ── keyboard (parity: HexEditorView bindings; scroll_mode = compare) ───

    def _key_left(self, *_args) -> None:
        # left/right have no row meaning in compare (scroll) mode
        if self.input_enabled and not self.scroll_mode:
            self.move_to(self._cursor - 1)

    def _key_right(self, *_args) -> None:
        if self.input_enabled and not self.scroll_mode:
            self.move_to(self._cursor + 1)

    def _key_up(self, *_args) -> None:
        if not self.input_enabled:
            return
        if self.scroll_mode:
            self.scroll_by(-1)
        else:
            self.move_to(self._cursor - self._bpl)

    def _key_down(self, *_args) -> None:
        if not self.input_enabled:
            return
        if self.scroll_mode:
            self.scroll_by(1)
        else:
            self.move_to(self._cursor + self._bpl)

    def _key_page_up(self, *_args) -> None:
        if not self.input_enabled:
            return
        if self.scroll_mode:
            self.scroll_by(-self._vis)
        else:
            self.move_to(self._cursor - self._vis * self._bpl)

    def _key_page_down(self, *_args) -> None:
        if not self.input_enabled:
            return
        if self.scroll_mode:
            self.scroll_by(self._vis)
        else:
            self.move_to(self._cursor + self._vis * self._bpl)

    def _key_home(self, *_args) -> None:
        if not self.input_enabled:
            return
        if self.scroll_mode:
            self.set_top(0)
        elif ctrl_down():
            self.move_to(0)  # Ctrl+Home: start of file
        else:
            self.move_to(self._cursor - self._cursor % self._bpl)

    def _key_end(self, *_args) -> None:
        if not self.input_enabled:
            return
        if self.scroll_mode:
            self.set_top(self._max_top())  # bottom of the (shared) range
        elif ctrl_down():
            self.move_to(max(self._doc.size - 1, 0))  # Ctrl+End: end of file
        else:
            self.move_to(self._cursor - self._cursor % self._bpl + self._bpl - 1)

    # ── pointer ───────────────────────────────────────────────────────────

    def _on_click(self, *_args) -> None:
        geom = self._geometry
        if geom is None or not self.input_enabled or not self._pointer_over_grid():
            return
        mx, my = dpg.get_mouse_pos(local=False)
        offset = hit_test(
            geom,
            bpl=self._bpl,
            top_row=self._top,
            max_offset=self._doc.size,
            x=mx,
            y=my,
        )
        if offset is not None:
            self.move_to(offset)

    # ── public state ────────────────────────────────────────────────────────

    @property
    def bpl(self) -> int:
        return self._bpl

    def _total_rows(self) -> int:
        return (self._doc.size + self._bpl - 1) // self._bpl

    def _max_top(self) -> int:
        if self.max_top_rows is not None:
            # Compare mode: both panes share the longest document's range
            # (the shorter pane renders blank tail rows).
            return max(0, self.max_top_rows() - self._vis)
        return max(0, self._total_rows() - self._vis)

    def move_to(self, offset: int, *, top: bool = False) -> None:
        """Move the cursor, scrolling only as needed (or placing its row on top)."""
        size = self._doc.size
        if size == 0:
            return
        new = max(0, min(int(offset), size - 1))
        old = self._cursor
        old_row, new_row = old // self._bpl, new // self._bpl
        self._cursor = new
        scrolled = False
        if top:
            self._top = min(new_row, self._max_top())
            scrolled = True
        elif new_row < self._top:
            self._top = new_row
            scrolled = True
        elif new_row >= self._top + self._vis:
            self._top = min(new_row - self._vis + 1, self._max_top())
            scrolled = True
        if scrolled:
            self._dirty_all = True
            self._push_slider()
        else:
            self._mark_rows_dirty((old_row, new_row))
        if self.on_cursor is not None:
            self.on_cursor(new)

    def set_match(self, match: tuple[int, int] | None) -> None:
        """Highlight a ``(start, length)`` byte range (search hit)."""
        old, self._match = self._match, match
        for m in (old, match):
            if not m:
                continue
            start, length = m
            first = start // self._bpl
            last = max(start, start + length - 1) // self._bpl
            self._mark_rows_dirty(range(first, last + 1))

    def set_scrollbar_visible(self, visible: bool) -> None:
        """Show/hide the vertical scrollbar (compare hides the right pane's)."""
        dpg.configure_item(self._scroll_tag, show=visible)

    def invalidate_range(self, start: int, stop: int) -> None:
        """Mark pool slots covering the byte range ``[start, stop)`` dirty.

        Used after document writes (edits change both the cell texts and the
        edited-byte colors); cheaper than :meth:`refresh` for spot changes.
        """
        if stop <= start:
            stop = start + 1
        self._mark_rows_dirty(range(start // self._bpl, (stop - 1) // self._bpl + 1))

    # ── per-frame maintenance (main thread) ────────────────────────────────

    def _layout_for(self, grid_w: int, grid_h: int) -> bool:
        """Bytes per row from the width, pool rows from the height."""
        new_bpl = bpl_for_width(grid_w, self._char_w())
        new_vis = min(visible_rows_for(grid_h, self._char_h()), MAX_POOL_ROWS)
        if new_bpl == self._bpl and new_vis == self._vis:
            return False
        self._bpl, self._vis = new_bpl, new_vis
        return True

    def _char_w(self) -> float:
        if self._geometry is not None and self._geometry.cw > 0:
            return self._geometry.cw
        ts = self._text_size()
        return float(ts[0]) if ts and ts[0] > 0 else FALLBACK_CHAR_W

    def _char_h(self) -> float:
        if self._geometry is not None and self._geometry.row_h > 0:
            return self._geometry.row_h
        ts = self._text_size()
        return float(ts[1]) if ts and ts[1] > 0 else FALLBACK_CHAR_H

    def _measure_geometry(self) -> None:
        """Measure cell positions once per pool build (needs a rendered frame)."""
        if self._geometry is not None or len(self._pool) < 2:
            return
        try:
            r0, r1 = self._pool[0], self._pool[1]
            cell_x = tuple(float(dpg.get_item_rect_min(t)[0]) for t in r0.hexes)
            ascii_x = tuple(float(dpg.get_item_rect_min(t)[0]) for t in r0.asciis)
            y0 = float(dpg.get_item_rect_min(r0.offset)[1])
            y1 = float(dpg.get_item_rect_min(r1.offset)[1])
        except (SystemError, ValueError, TypeError, KeyError):
            return
        ts = self._text_size()
        if ts and ts[0] > 0:
            cw = float(ts[0])
        elif len(ascii_x) >= 2:
            cw = ascii_x[1] - ascii_x[0]  # zero-spacing pitch between cells
        else:
            cw = FALLBACK_CHAR_W
        geom = GridGeometry(
            cw=cw, row_h=y1 - y0, origin_y=y0, cell_x=cell_x, ascii_x=ascii_x
        )
        if geom.is_sane(self._bpl):
            self._geometry = geom

    def _byte_color(self, offset: int) -> tuple[int, int, int, int]:
        if self.color_for is not None:
            # Compare mode: the controller decides per byte (live same/diff
            # comparison of both documents).
            return self.color_for(offset)
        if offset == self._cursor:
            # On the drawn background: white-on-blue, like the TUI.
            return COL_BRIGHT if self._hl_rects is not None else COL_CURSOR
        m = self._match
        if m and m[0] <= offset < m[0] + m[1]:
            return COL_MATCH
        if self._doc.is_edited(offset):
            return COL_EDITED
        return COL_TEXT

    def _paint_slot(self, slot: int) -> None:
        row = self._pool[slot]
        doc_row = self._top + slot
        bpl = self._bpl
        if doc_row >= self._total_rows():
            # Past EOF: TUI parity — the row renders fully blank.
            self._emit(row.offset, " " * (OFFSET_W + 2), COL_ADDR)
            for i in range(bpl):
                self._emit(row.hexes[i], " " * hex_cell_width(bpl, i), COL_TEXT)
                self._emit(row.asciis[i], " ", COL_TEXT)
            self._emit(row.pipe, "   ", COL_SEP)
            self._emit(row.end, " ", COL_SEP)
            return
        offset = doc_row * bpl
        data = self._doc.read(offset, bpl)
        cursor_row = offset <= self._cursor < offset + bpl
        self._emit(
            row.offset,
            offset_text(offset, bpl),
            COL_ADDR_CURSOR if cursor_row else COL_ADDR,
        )
        for i in range(bpl):
            value = data[i] if i < len(data) else None
            color = self._byte_color(offset + i)
            self._emit(row.hexes[i], hex_cell_text(bpl, i, value), color)
            self._emit(row.asciis[i], ascii_cell_text(value), color)
        self._emit(row.pipe, "  |", COL_SEP)
        self._emit(row.end, "|", COL_SEP)
