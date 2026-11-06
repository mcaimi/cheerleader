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
* **Threading** — key/mouse/slider callbacks may run on Dear PyGui's
callback thread; they only mutate plain Python state and mark pool slots
dirty. *All* Dear PyGui item mutations happen in :meth:`HexGrid.tick`,
which the app shell calls once per frame on the main thread.
* **Colors** — Dear PyGui text items carry no background, so the TUI's
white-on-blue cursor and black-on-magenta match map to bright foreground
colors for now; a drawlist background underlay is deferred until
its z-order can be verified on screen.
* **Scrollbar mapping** — DPG's vertical slider puts min at the bottom, so
the scrollbar stores ``max_top - _top`` (thumb at the widget top ≙ top of
the file) and its value text is hidden (``format=" "``). PageUp/PageDown
handlers are registered under both the correct ImGui codes (517/518) and
the stale ``mvKey_Prior``/``mvKey_Next`` constants (266/267 — dearpygui
2.3.1 bug, upstream issue #2658); the wrong spelling is inert.
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
    INIT_GRID_H,
    INIT_GRID_W,
    KEY_PAGE_DOWN,
    KEY_PAGE_UP,
    MAX_POOL_ROWS,
    OFFSET_W,
    SLIDER_RESERVE,
    WHEEL_ROWS,
)


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


# ── palette (foreground only; see module docstring) ───────────────────────────

COL_TEXT = (216, 216, 222, 255)
COL_ADDR = (140, 140, 148, 255)
COL_ADDR_CURSOR = (255, 255, 255, 255)
COL_CURSOR = (110, 170, 255, 255)  # stands in for the TUI's white-on-blue
COL_EDITED = (250, 210, 80, 255)
COL_MATCH = (255, 110, 255, 255)  # stands in for black-on-magenta
COL_SEP = (110, 110, 118, 255)


@dataclass
class _PoolRow:
    """Widget tags of one pooled row slot."""

    group: int | str
    offset: int | str
    hexes: list[int | str]
    pipe: int | str
    asciis: list[int | str]
    end: int | str


class HexGrid:
    """Virtualized hex+ASCII grid over a :class:`HexDocument`.

    The widget tree is built once by :meth:`build` and re-painted on demand by
    :meth:`tick` (called once per frame by the app shell). Callbacks only
    mutate plain state; see the module docstring for the threading model.
    """

    def __init__(
        self,
        doc: HexDocument,
        *,
        name: str = "hex",
        on_cursor: Callable[[int], None] | None = None,
    ) -> None:
        self._doc = doc
        self._name = name
        self._bpl = BPL
        self._cursor = 0
        self._top = 0
        self._vis = DEFAULT_VISIBLE_ROWS
        self._match: tuple[int, int] | None = None
        self.on_cursor = on_cursor
        # While a modal dialog is open the editor controller turns the grid's
        # key/click/wheel handlers off so popup typing never leaks through.
        self.input_enabled = True
        # Compare mode: per-byte color override, shared scroll
        # range and row-scrolling keys. All default to the editor behavior.
        self.color_for: Callable[[int], tuple[int, int, int, int]] | None = None
        self.max_top_rows: Callable[[], int] | None = None
        self.scroll_mode = False
        self._pool: list[_PoolRow] = []
        self._dirty_all = True
        self._dirty: set[int] = set()
        self._cache: dict[object, tuple[str, tuple[int, int, int, int]]] = {}
        self._geometry: GridGeometry | None = None
        self._last_parent_size: tuple[int, int] | None = None
        self._slider_max = -1
        self._wheel_remain = 0.0  # fractional (trackpad) wheel accumulation
        self._parent: int | str | None = None
        self._area_tag = f"{name}-hexgrid-area"
        self._grid_tag = f"{name}-hexgrid"
        self._scroll_tag = f"{name}-hexgrid-scroll"
        self._input_tag = f"{name}-hexgrid-input"

    # ── widget construction ───────────────────────────────────────────────

    def build(self, parent: int | str) -> None:
        """Create the grid inside *parent* (a window or child window tag)."""
        if dpg.does_item_exist(self._area_tag):
            return
        self._parent = parent
        with dpg.group(horizontal=True, parent=parent, tag=self._area_tag):
            # no_scroll_with_mouse: the wheel belongs to our virtualized
            # scrolling (_top); the built-in child scroll would otherwise
            # slide the whole pool inside the window and desync everything.
            dpg.add_child_window(
                tag=self._grid_tag,
                border=False,
                no_scrollbar=True,
                no_scroll_with_mouse=True,
                width=INIT_GRID_W,
                height=INIT_GRID_H,
            )
            dpg.add_slider_int(
                tag=self._scroll_tag,
                vertical=True,
                min_value=0,
                max_value=0,
                format=" ",  # hide the value text (the mapping is inverted)
                callback=self._on_scroll,
                drag_callback=self._on_scroll,
            )
        self._bind_grid_theme()
        self._rebuild_pool()
        self._install_input()

    def _bind_grid_theme(self) -> None:
        """Zero item spacing inside the grid so cells align on the char grid."""
        with dpg.theme() as theme, dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0)
        dpg.bind_item_theme(self._grid_tag, theme)

    def _rebuild_pool(self) -> None:
        """(Re)create the pooled row widgets for the current vis/bpl."""
        if dpg.does_item_exist(self._grid_tag):
            dpg.delete_item(self._grid_tag, children_only=True)
        self._pool.clear()
        self._cache.clear()
        prefix = self._grid_tag
        for s in range(self._vis + 1):
            base = f"{prefix}-r{s}"
            with dpg.group(horizontal=True, parent=self._grid_tag, tag=base):
                off = dpg.add_text(
                    " " * (OFFSET_W + 2), tag=f"{base}-off", color=COL_ADDR
                )
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
            self._pool.append(
                _PoolRow(
                    group=base,
                    offset=off,
                    hexes=hexes,
                    pipe=pipe,
                    asciis=asciis,
                    end=end,
                )
            )
        self._dirty_all = True
        self._geometry = None

    def _install_input(self) -> None:
        """Global pointer/keyboard input for this grid.

        Clicks and wheel use the global registry plus a
        :meth:`_pointer_over_grid` guard (item-level handlers on child
        windows are less predictable); the key handlers mirror the TUI editor
        view's navigation bindings.
        """
        with dpg.handler_registry(tag=self._input_tag):
            dpg.add_mouse_click_handler(
                button=dpg.mvMouseButton_Left, callback=self._on_click
            )
            dpg.add_mouse_wheel_handler(callback=self._on_wheel)
            dpg.add_key_press_handler(key=dpg.mvKey_Left, callback=self._key_left)
            dpg.add_key_press_handler(key=dpg.mvKey_Right, callback=self._key_right)
            dpg.add_key_press_handler(key=dpg.mvKey_Up, callback=self._key_up)
            dpg.add_key_press_handler(key=dpg.mvKey_Down, callback=self._key_down)
            # Both code spellings: see KEY_PAGE_UP/KEY_PAGE_DOWN (upstream #2658).
            for key in {KEY_PAGE_UP, dpg.mvKey_Prior}:
                dpg.add_key_press_handler(key=key, callback=self._key_page_up)
            for key in {KEY_PAGE_DOWN, dpg.mvKey_Next}:
                dpg.add_key_press_handler(key=key, callback=self._key_page_down)
            dpg.add_key_press_handler(key=dpg.mvKey_Home, callback=self._key_home)
            dpg.add_key_press_handler(key=dpg.mvKey_End, callback=self._key_end)

    # ── keyboard (parity: HexEditorView bindings; scroll_mode = compare) ───

    def _key_left(self, *_args) -> None:
        if not self.input_enabled:
            return
        if self.scroll_mode:
            self.scroll_by(0)  # left/right have no row meaning in compare
        else:
            self.move_to(self._cursor - 1)

    def _key_right(self, *_args) -> None:
        if not self.input_enabled:
            return
        if self.scroll_mode:
            self.scroll_by(0)
        else:
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

    def _ctrl_down(self) -> bool:
        return dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(
            dpg.mvKey_RControl
        )

    def _key_home(self, *_args) -> None:
        if not self.input_enabled:
            return
        if self.scroll_mode or self._ctrl_down():
            self.set_top(0)
        else:
            self.move_to(self._cursor - self._cursor % self._bpl)

    def _key_end(self, *_args) -> None:
        if not self.input_enabled:
            return
        if self.scroll_mode or self._ctrl_down():
            self.set_top(self._max_top())  # bottom of the (shared) range
        elif self._ctrl_down():
            self.move_to(max(self._doc.size - 1, 0))
        else:
            self.move_to(self._cursor - self._cursor % self._bpl + self._bpl - 1)

    # ── pointer / scrollbar ────────────────────────────────────────────────

    def _pointer_over_grid(self) -> bool:
        """True when the mouse is over the grid area.

        ``is_item_hovered`` alone is not reliable over a child window's items,
        so a plain mouse-in-rect test (same coordinate functions the
        hit-tester uses) backs it up.
        """
        if dpg.is_item_hovered(self._grid_tag):
            return True
        mx, my = dpg.get_mouse_pos(local=False)
        rmin = dpg.get_item_rect_min(self._grid_tag)
        rmax = dpg.get_item_rect_max(self._grid_tag)
        return bool(rmin[0] <= mx < rmax[0] and rmin[1] <= my < rmax[1])

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

    def _on_wheel(self, _sender, app_data) -> None:
        if not self.input_enabled:
            return
        try:
            delta = float(app_data)
        except (TypeError, ValueError):
            return
        if not delta or not self._pointer_over_grid():
            self._wheel_remain = 0.0
            return
        # Trackpads emit sub-notch deltas; accumulate and scroll whole rows.
        self._wheel_remain -= delta * WHEEL_ROWS
        rows = int(self._wheel_remain)
        if rows:
            self._wheel_remain -= rows
            self.scroll_by(rows)

    def _on_scroll(self, _sender, app_data) -> None:
        """Slider callback (also the drag callback): user scrolled.

        The slider value is inverted (see :meth:`_push_slider`). DPG stores
        the dragged value *before* queueing this callback, so for real drags
        the trailing :meth:`_push_slider` is a no-op (stored value already
        matches); it only writes if the reported value was out of range,
        re-clamping the thumb to match state.
        """
        try:
            value = int(app_data)
        except (TypeError, ValueError):
            return
        max_top = self._max_top()
        top = max(0, min(max_top - value, max_top))
        if top != self._top:
            self._top = top
            self._dirty_all = True
        self._push_slider()

    # ── public state ────────────────────────────────────────────────────────

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def top_row(self) -> int:
        return self._top

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

    def set_top(self, row: int) -> None:
        """Scroll so *row* is the top visible row (clamped, no cursor move)."""
        new = max(0, min(int(row), self._max_top()))
        if new != self._top:
            self._top = new
            self._dirty_all = True
            self._push_slider()

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
            for row in (old_row, new_row):
                slot = row - self._top
                if 0 <= slot < len(self._pool):
                    self._dirty.add(slot)
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
            for row in range(first, last + 1):
                slot = row - self._top
                if 0 <= slot < len(self._pool):
                    self._dirty.add(slot)

    def scroll_by(self, rows: int) -> None:
        """Scroll the view by *rows* without moving the cursor."""
        new = max(0, min(self._top + rows, self._max_top()))
        if new != self._top:
            self._top = new
            self._dirty_all = True
            self._push_slider()

    def set_scrollbar_visible(self, visible: bool) -> None:
        """Show/hide the vertical scrollbar (compare hides the right pane's)."""
        dpg.configure_item(self._scroll_tag, show=visible)

    def shutdown(self) -> None:
        """Delete the grid's global input handler registry.

        The registry is *not* a child of the grid's widgets, so removing the
        grid's container (e.g. closing a tool window) leaves it behind —
        explicit teardown is required.
        """
        if dpg.does_item_exist(self._input_tag):
            dpg.delete_item(self._input_tag)

    def refresh(self) -> None:
        """Force a full repaint on the next :meth:`tick`."""
        self._dirty_all = True

    def invalidate_range(self, start: int, stop: int) -> None:
        """Mark pool slots covering the byte range ``[start, stop)`` dirty.

        Used after document writes (edits change both the cell texts and the
        edited-byte colors); cheaper than :meth:`refresh` for spot changes.
        """
        if stop <= start:
            stop = start + 1
        for row in range(start // self._bpl, (stop - 1) // self._bpl + 1):
            slot = row - self._top
            if 0 <= slot < len(self._pool):
                self._dirty.add(slot)

    def _push_slider(self) -> None:
        """Write ``_top`` into the scrollbar (inverted mapping).

        DPG's vertical slider puts min at the bottom, so the scrollbar value
        is ``max_top - _top`` ("rows below the view"): thumb at the widget
        top ≙ top of the file. Called on non-slider ``_top`` changes (wheel,
        keys, clicks, clamps) and from ``_on_scroll`` only to re-clamp an
        out-of-range report. Tick never writes the slider value: DPG delivers
        the user's drag value via a queued callback, and an every-frame
        write-back would fight the in-flight drag.
        """
        value = self._max_top() - self._top
        if int(dpg.get_value(self._scroll_tag)) != value:
            dpg.set_value(self._scroll_tag, value)

    # ── per-frame maintenance (main thread) ────────────────────────────────

    def tick(self) -> None:
        """Relayout, measure, repaint dirty slots, and sync the scrollbar."""
        self._relayout()
        self._measure_geometry()
        self._repaint()
        self._sync_slider_max()
        self._reset_internal_scroll()

    def _relayout(self) -> None:
        """Resize the grid to its parent and rebuild the pool on bpl/vis change."""
        size = (
            dpg.get_item_rect_size(self._parent) if self._parent is not None else (0, 0)
        )
        w, h = int(size[0]), int(size[1])
        if w <= 0 or h <= 0 or (w, h) == self._last_parent_size:
            return
        self._last_parent_size = (w, h)
        self._geometry = None  # positions may have shifted; re-measure
        grid_w = max(w - SLIDER_RESERVE, 60)
        grid_h = max(h - 2, 20)
        dpg.configure_item(self._grid_tag, width=grid_w, height=grid_h)
        dpg.configure_item(self._scroll_tag, height=grid_h)
        new_bpl = bpl_for_width(grid_w, self._char_w())
        new_vis = min(visible_rows_for(grid_h, self._char_h()), MAX_POOL_ROWS)
        if new_bpl != self._bpl or new_vis != self._vis:
            self._bpl = new_bpl
            self._vis = new_vis
            self._rebuild_pool()
            self._top = min(self._top, self._max_top())
            self._push_slider()
            self.move_to(self._cursor)

    def _text_size(self):
        try:
            return dpg.get_text_size("0")
        except (SystemError, ValueError):
            return None

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
        except (SystemError, ValueError, TypeError):
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

    def _repaint(self) -> None:
        """Paint dirty pool slots (called once per frame on the main thread)."""
        if self._dirty_all:
            self._dirty_all = False
            self._dirty.clear()
            slots: list[int] | range = range(len(self._pool))
        else:
            slots = sorted(self._dirty)
            self._dirty.clear()
        for s in slots:
            if s < len(self._pool):
                self._paint_slot(s)

    def _emit(self, tag: object, text: str, color: tuple[int, int, int, int]) -> None:
        """set_value/configure_item only when the (text, color) pair changed."""
        if self._cache.get(tag) == (text, color):
            return
        dpg.set_value(tag, text)
        dpg.configure_item(tag, color=color)
        self._cache[tag] = (text, color)

    def _byte_color(self, offset: int) -> tuple[int, int, int, int]:
        if self.color_for is not None:
            # Compare mode: the controller decides per byte (live same/diff
            # comparison of both documents).
            return self.color_for(offset)
        if offset == self._cursor:
            return COL_CURSOR
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

    def _sync_slider_max(self) -> None:
        """Update the scrollbar range; push the value when the range changed.

        DPG does *not* clamp the stored value when ``max_value`` shrinks, so
        after a range change the value must be re-pushed — a stale large value
        would render the thumb pinned at the end. Pushing here (instead of
        every frame) keeps user drags intact; see :meth:`_push_slider`.
        """
        max_top = self._max_top()
        if self._slider_max != max_top:
            dpg.configure_item(self._scroll_tag, max_value=max_top)
            self._slider_max = max_top
            if self._top > max_top:  # e.g. document shrank without a relayout
                self._top = max_top
                self._dirty_all = True
            self._push_slider()

    def _reset_internal_scroll(self) -> None:
        """Keep the built-in child scroll offset at zero.

        The pool is exactly as tall as the painted content, so any internal
        offset is drift (e.g. a wheel event delivered before
        ``no_scroll_with_mouse`` took effect) that would slide rows out of
        view and desync hit-testing from ``_top``.
        """
        if dpg.get_y_scroll(self._grid_tag):
            dpg.set_y_scroll(self._grid_tag, 0)
