"""
Virtualized disassembly grid for the Dear PyGui front-end (plan v3, D3).

One :class:`~cheerleader.libs.types.DisasmInstruction` per row — four fixed-
width text cells (address, bytes, mnemonic, operands) in a horizontal group.
Rendering is *virtualized* exactly like :class:`~cheerleader.gui.hexgrid.HexGrid`:
a fixed pool of row widgets covers only the visible window; sections with
10⁵–10⁶ instructions cost the same as a 20-instruction one.

Row layout (monospace font, zero item spacing — the HexGrid theme trick):

    0x0000000100000000  1f 20 03 d5 …           pacibsp      x29, sp
    └ addr (18 ch)      └ bytes (24 ch)         └ mnemonic (10 ch)

The bytes cell shows at most 8 bytes (a ``~`` marks longer instructions) so
the mnemonic column stays aligned; addresses are cyan-dim (``COL_ADDR``),
bytes dim (``COL_SEP``), cursor row bright (``COL_CURSOR``).

Threading + repaint model are the HexGrid's: callbacks only mutate plain
Python state and mark pool slots dirty; *all* widget mutations happen in
:meth:`tick` on the main thread. Click/wheel are pointer-over-guarded;
navigation keys (↑ ↓ PgUp PgDn Home End) follow the HexGrid bindings. Row
hit-testing is y-only (whole-row selection). Geometry (row height, origin)
is measured from the live pool like HexGrid's; headless tests drive the
grid through its state methods and the same geometry-simulation
monkeypatching used by ``tests/test_gui_hexgrid.py``.
"""

from collections.abc import Callable

from cheerleader.libs.types import DisasmInstruction

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

from cheerleader.gui.const import (
    DEFAULT_VISIBLE_ROWS,
    FALLBACK_CHAR_H,
    INIT_GRID_H,
    INIT_GRID_W,
    KEY_PAGE_DOWN,
    KEY_PAGE_UP,
    MAX_POOL_ROWS,
    SLIDER_RESERVE,
    WHEEL_ROWS,
)
from cheerleader.gui.hexgrid import (
    COL_ADDR,
    COL_ADDR_CURSOR,
    COL_CURSOR,
    COL_SEP,
    COL_TEXT,
)

# Fixed char widths of the addr/bytes/mnemonic cells (operands take the rest).
ADDR_W = 18  # "0x" + 16 hex digits
BYTES_W = 26  # 8 bytes "de ad be ef 00 11 22 33" + truncation marker
MNEM_W = 10
MAX_BYTES_SHOWN = 8


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


class DisasmGeometry:
    """Measured row geometry (HexGrid's :class:`GridGeometry`, row-level)."""

    def __init__(self, cw: float, row_h: float, origin_y: float) -> None:
        self.cw = cw
        self.row_h = row_h
        self.origin_y = origin_y

    def is_sane(self) -> bool:
        return self.cw > 2.0 and self.row_h > 4.0 and self.origin_y >= 0.0


def _blank_row() -> tuple[str, str, str, str]:
    return (" " * ADDR_W, " " * BYTES_W, " " * MNEM_W, "")


class DisasmGrid:
    """Virtualized instruction grid; data arrives via :meth:`set_instrs`."""

    def __init__(
        self,
        *,
        name: str = "disasm",
        on_select: Callable[[int], None] | None = None,
    ) -> None:
        self._name = name
        self._instrs: list[DisasmInstruction] = []
        self._cursor = 0  # selected instruction index
        self._top = 0
        self._vis = DEFAULT_VISIBLE_ROWS
        self.on_select = on_select
        # While a modal dialog is open the controller turns the handlers off.
        self.input_enabled = True
        self._pool: list[tuple[int | str, tuple[int | str, ...]]] = []
        self._dirty_all = True
        self._dirty: set[int] = set()
        self._cache: dict[object, tuple[str, tuple[int, int, int, int]]] = {}
        self._geometry: DisasmGeometry | None = None
        self._last_parent_size: tuple[int, int] | None = None
        self._slider_max = -1
        self._wheel_remain = 0.0
        self._parent: int | str | None = None
        self._area_tag = f"{name}-disasm-area"
        self._grid_tag = f"{name}-disasm"
        self._scroll_tag = f"{name}-disasm-scroll"
        self._input_tag = f"{name}-disasm-input"

    # ── widget construction ───────────────────────────────────────────────

    def build(self, parent: int | str) -> None:
        """Create the grid inside *parent* (a window or child window tag)."""
        if dpg.does_item_exist(self._area_tag):
            return
        self._parent = parent
        with dpg.group(horizontal=True, parent=parent, tag=self._area_tag):
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
                format=" ",
                callback=self._on_scroll,
                drag_callback=self._on_scroll,
            )
        self._bind_grid_theme()
        self._rebuild_pool()
        self._install_input()

    def _bind_grid_theme(self) -> None:
        with dpg.theme() as theme, dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0)
        dpg.bind_item_theme(self._grid_tag, theme)

    def _rebuild_pool(self) -> None:
        if dpg.does_item_exist(self._grid_tag):
            dpg.delete_item(self._grid_tag, children_only=True)
        self._pool.clear()
        self._cache.clear()
        prefix = self._grid_tag
        for s in range(self._vis + 1):
            base = f"{prefix}-r{s}"
            with dpg.group(horizontal=True, parent=self._grid_tag, tag=base):
                addr = dpg.add_text(" " * ADDR_W, tag=f"{base}-addr", color=COL_ADDR)
                cells = tuple(
                    dpg.add_text(" ", tag=f"{base}-c{i}", color=COL_TEXT)
                    for i in range(3)
                )
            self._pool.append((base, (addr, *cells)))
        self._dirty_all = True
        self._geometry = None

    def _install_input(self) -> None:
        with dpg.handler_registry(tag=self._input_tag):
            dpg.add_mouse_click_handler(
                button=dpg.mvMouseButton_Left, callback=self._on_click
            )
            dpg.add_mouse_wheel_handler(callback=self._on_wheel)
            dpg.add_key_press_handler(key=dpg.mvKey_Up, callback=self._key_up)
            dpg.add_key_press_handler(key=dpg.mvKey_Down, callback=self._key_down)
            for key in {KEY_PAGE_UP, dpg.mvKey_Prior}:
                dpg.add_key_press_handler(key=key, callback=self._key_page_up)
            for key in {KEY_PAGE_DOWN, dpg.mvKey_Next}:
                dpg.add_key_press_handler(key=key, callback=self._key_page_down)
            dpg.add_key_press_handler(key=dpg.mvKey_Home, callback=self._key_home)
            dpg.add_key_press_handler(key=dpg.mvKey_End, callback=self._key_end)

    # ── data ───────────────────────────────────────────────────────────────

    def set_instrs(self, instrs: list[DisasmInstruction]) -> None:
        """Replace the instruction list (resets cursor + scroll)."""
        self._instrs = instrs
        self._cursor = 0
        self._top = 0
        self._dirty_all = True
        self._push_slider()

    def instrs(self) -> list[DisasmInstruction]:
        return self._instrs

    # ── public state ───────────────────────────────────────────────────────

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def top_row(self) -> int:
        return self._top

    @property
    def vis(self) -> int:
        return self._vis

    def _total_rows(self) -> int:
        return len(self._instrs)

    def _max_top(self) -> int:
        return max(0, self._total_rows() - self._vis)

    def set_top(self, row: int) -> None:
        new = max(0, min(int(row), self._max_top()))
        if new != self._top:
            self._top = new
            self._dirty_all = True
            self._push_slider()

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
            for row in (old, new):
                slot = row - self._top
                if 0 <= slot < len(self._pool):
                    self._dirty.add(slot)
        if self.on_select is not None:
            self.on_select(new)

    def scroll_by(self, rows: int) -> None:
        new = max(0, min(self._top + rows, self._max_top()))
        if new != self._top:
            self._top = new
            self._dirty_all = True
            self._push_slider()

    def refresh(self) -> None:
        self._dirty_all = True

    def set_visible(self, visible: bool) -> None:
        """Show/hide the whole grid area (the controller's pane toggle)."""
        dpg.configure_item(self._area_tag, show=visible)

    def shutdown(self) -> None:
        """Delete the grid's global input registry (not a widget child)."""
        if dpg.does_item_exist(self._input_tag):
            dpg.delete_item(self._input_tag)

    # ── keyboard (HexGrid navigation parity, one row per instruction) ─────

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

    # ── pointer / scrollbar ────────────────────────────────────────────────

    def _pointer_over_grid(self) -> bool:
        """True when the mouse is over the grid area.

        ``is_item_hovered`` alone is not reliable over a child window's
        items, so a plain mouse-in-rect test (same coordinate functions the
        hit-tester uses) backs it up. Items that are hidden or not yet laid
        out carry no ``rect_min``/``rect_max`` in their state (and
        ``is_item_visible`` raises the same way) — any state-fetch failure
        means "not over the grid".
        """
        if not dpg.does_item_exist(self._grid_tag):
            return False  # deleted mid-frame (window close race)
        if dpg.is_item_hovered(self._grid_tag):
            return True
        try:
            mx, my = dpg.get_mouse_pos(local=False)
            rmin = dpg.get_item_rect_min(self._grid_tag)
            rmax = dpg.get_item_rect_max(self._grid_tag)
        except (SystemError, ValueError, TypeError, KeyError):
            return False
        return bool(rmin[0] <= mx < rmax[0] and rmin[1] <= my < rmax[1])

    def _on_click(self, *_args) -> None:
        geom = self._geometry
        if geom is None or not self.input_enabled or not self._pointer_over_grid():
            return
        _, my = dpg.get_mouse_pos(local=False)
        row = int((my - geom.origin_y) / geom.row_h) + self._top
        if 0 <= row < self._total_rows():
            self.move_to(row)

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
        self._wheel_remain -= delta * WHEEL_ROWS
        rows = int(self._wheel_remain)
        if rows:
            self._wheel_remain -= rows
            self.scroll_by(rows)

    def _on_scroll(self, _sender, app_data) -> None:
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

    def _push_slider(self) -> None:
        if not dpg.does_item_exist(self._scroll_tag):
            return  # not built yet (pure-state use in tests)
        value = self._max_top() - self._top
        if int(dpg.get_value(self._scroll_tag)) != value:
            dpg.set_value(self._scroll_tag, value)

    # ── per-frame maintenance (main thread) ────────────────────────────────

    def tick(self) -> None:
        self._relayout()
        self._measure_geometry()
        self._repaint()
        self._sync_slider_max()
        self._reset_internal_scroll()

    def _relayout(self) -> None:
        size = (
            dpg.get_item_rect_size(self._parent) if self._parent is not None else (0, 0)
        )
        w, h = int(size[0]), int(size[1])
        if w <= 0 or h <= 0 or (w, h) == self._last_parent_size:
            return
        self._last_parent_size = (w, h)
        self._geometry = None
        grid_w = max(w - SLIDER_RESERVE, 60)
        grid_h = max(h - 2, 20)
        dpg.configure_item(self._grid_tag, width=grid_w, height=grid_h)
        dpg.configure_item(self._scroll_tag, height=grid_h)
        new_vis = min(max(int(grid_h // self._row_h_hint()), 1), MAX_POOL_ROWS)
        if new_vis != self._vis:
            self._vis = new_vis
            self._rebuild_pool()
            self._top = min(self._top, self._max_top())
            self._push_slider()
            self.move_to(self._cursor)

    def _row_h_hint(self) -> float:
        if self._geometry is not None and self._geometry.row_h > 0:
            return self._geometry.row_h
        return FALLBACK_CHAR_H

    def _measure_geometry(self) -> None:
        """Measure row height once per pool build (needs a rendered frame)."""
        if self._geometry is not None or len(self._pool) < 2:
            return
        try:
            y0 = float(dpg.get_item_rect_min(self._pool[0][0])[1])
            y1 = float(dpg.get_item_rect_min(self._pool[1][0])[1])
            cw = float(dpg.get_text_size("0")[0]) or 9.0
        except (SystemError, ValueError, TypeError, KeyError):
            return
        geom = DisasmGeometry(cw=cw, row_h=y1 - y0, origin_y=y0)
        if geom.is_sane():
            self._geometry = geom

    def _repaint(self) -> None:
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
        if self._cache.get(tag) == (text, color):
            return
        dpg.set_value(tag, text)
        dpg.configure_item(tag, color=color)
        self._cache[tag] = (text, color)

    def _paint_slot(self, slot: int) -> None:
        base, tags = self._pool[slot]
        del base  # naming clarity: tags[0]=addr, tags[1]=bytes, [2]=mnem, [3]=ops
        index = self._top + slot
        if index >= self._total_rows():
            texts = _blank_row()
            colors = (COL_ADDR, COL_SEP, COL_TEXT, COL_TEXT)
        else:
            texts = row_texts(self._instrs[index])
            cursor_row = index == self._cursor
            colors = (
                COL_ADDR_CURSOR if cursor_row else COL_ADDR,
                COL_CURSOR if cursor_row else COL_SEP,
                COL_CURSOR if cursor_row else COL_TEXT,
                COL_CURSOR if cursor_row else COL_TEXT,
            )
        for tag, text, color in zip(tags, texts, colors):
            self._emit(tag, text, color)

    def _sync_slider_max(self) -> None:
        max_top = self._max_top()
        if self._slider_max != max_top:
            dpg.configure_item(self._scroll_tag, max_value=max_top)
            self._slider_max = max_top
            if self._top > max_top:
                self._top = max_top
                self._dirty_all = True
            self._push_slider()

    def _reset_internal_scroll(self) -> None:
        if dpg.get_y_scroll(self._grid_tag):
            dpg.set_y_scroll(self._grid_tag, 0)
