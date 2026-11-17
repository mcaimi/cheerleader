"""
Virtualized row-grid machinery shared by the hex and disassembly grids.

A fixed pool of row widgets (``_vis + 1`` slots) covers only the visible
window of an arbitrarily long row sequence; scrolling moves ``_top`` and
repaints the pool — never builds widgets — so a multi-GB file or a 10⁶-
instruction section costs the same as a tiny one.

The grid area is a horizontal group: a scroll-locked child window holding
the pool (zero item spacing so cells align on the character grid) and a
vertical slider acting as the scrollbar. DPG's vertical slider puts min at
the bottom, so it stores ``max_top - _top`` (thumb at the widget top ≙ the
first row) and hides its value text (``format=" "``).

Threading + repaint model: callbacks only mutate plain Python state and
mark pool slots dirty; *all* widget mutations happen in :meth:`tick`, which
the shell calls once per frame on the main thread. Pointer input goes
through a global registry plus a :meth:`_pointer_over_grid` guard; key
input is focus-guarded to the grid's tool window.

Subclasses provide the row widgets (:meth:`_build_row`), painting
(:meth:`_paint_slot`), the row count (:meth:`_total_rows`), geometry
measurement + click hit-testing, cursor movement (``move_to``), their key
bindings and the pool-sizing rule (:meth:`_layout_for`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

from cheerleader.gui.const import (
    DEFAULT_VISIBLE_ROWS,
    INIT_GRID_H,
    INIT_GRID_W,
    SLIDER_RESERVE,
    WHEEL_ROWS,
)
from cheerleader.gui.tooling import focus_guard

Color = tuple[int, int, int, int]


class VirtualGrid:
    """Pool, scrollbar, wheel and dirty-slot repaint of a virtualized grid."""

    TAG_KIND = "grid"  # tag infix: {name}-{TAG_KIND}, {name}-{TAG_KIND}-scroll, …

    def __init__(self, *, name: str) -> None:
        self._name = name
        self._cursor = 0
        self._top = 0
        self._vis = DEFAULT_VISIBLE_ROWS
        # While a modal dialog is open the controller turns the handlers off
        # so popup typing never leaks through.
        self.input_enabled = True
        self._pool: list[Any] = []
        self._dirty_all = True
        self._dirty: set[int] = set()
        self._cache: dict[object, tuple[str, Color]] = {}
        self._geometry: Any = None
        self._last_parent_size: tuple[int, int] | None = None
        self._screen_origin: tuple[float, float] | None = None  # grid rect_min
        self._slider_max = -1
        self._wheel_remain = 0.0  # fractional (trackpad) wheel accumulation
        self._parent: int | str | None = None
        # Right-click over the grid: the row under the pointer is selected
        # (as by a left click), then this is called (e.g. a context menu).
        self.on_context_click: Callable[[], None] | None = None
        kind = self.TAG_KIND
        self._area_tag = f"{name}-{kind}-area"
        self._grid_tag = f"{name}-{kind}"
        self._scroll_tag = f"{name}-{kind}-scroll"
        self._input_tag = f"{name}-{kind}-input"

    # ── subclass hooks ────────────────────────────────────────────────────

    def _build_row(self, base: str) -> Any:
        """Create one pool row's widgets under group *base*; return its tags."""
        raise NotImplementedError

    def _paint_slot(self, slot: int) -> None:
        raise NotImplementedError

    def _total_rows(self) -> int:
        raise NotImplementedError

    def _key_bindings(self) -> list[tuple[int, Callable[..., None]]]:
        return []

    def _layout_for(self, grid_w: int, grid_h: int) -> bool:
        """Adopt the pool shape for a *grid_w* × *grid_h* px area; True when
        it changed (the pool is then rebuilt)."""
        raise NotImplementedError

    def _measure_geometry(self) -> None:
        """Measure the live pool once per build (needs a rendered frame)."""

    def move_to(self, index: int) -> None:
        raise NotImplementedError

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
        """(Re)create the pooled row widgets for the current pool shape."""
        if dpg.does_item_exist(self._grid_tag):
            dpg.delete_item(self._grid_tag, children_only=True)
        self._pool.clear()
        self._cache.clear()
        for s in range(self._vis + 1):
            base = f"{self._grid_tag}-r{s}"
            with dpg.group(horizontal=True, parent=self._grid_tag, tag=base):
                row = self._build_row(base)
            self._pool.append(row)
        self._dirty_all = True
        self._geometry = None

    def _install_input(self) -> None:
        """Global pointer + (focus-guarded) keyboard input for this grid."""
        with dpg.handler_registry(tag=self._input_tag):
            dpg.add_mouse_click_handler(
                button=dpg.mvMouseButton_Left, callback=self._on_click
            )
            dpg.add_mouse_click_handler(
                button=dpg.mvMouseButton_Right, callback=self._on_right_click
            )
            dpg.add_mouse_wheel_handler(callback=self._on_wheel)
            # Keys only reach the grid of the focused tool window.
            for key, callback in self._key_bindings():
                dpg.add_key_press_handler(
                    key=key, callback=focus_guard(self._grid_tag, callback)
                )

    # ── public state ──────────────────────────────────────────────────────

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def top_row(self) -> int:
        return self._top

    @property
    def vis(self) -> int:
        return self._vis

    def _max_top(self) -> int:
        return max(0, self._total_rows() - self._vis)

    def set_top(self, row: int) -> None:
        """Scroll so *row* is the top visible row (clamped, no cursor move)."""
        new = max(0, min(int(row), self._max_top()))
        if new != self._top:
            self._top = new
            self._dirty_all = True
            self._push_slider()

    def scroll_by(self, rows: int) -> None:
        """Scroll the view by *rows* without moving the cursor."""
        new = max(0, min(self._top + rows, self._max_top()))
        if new != self._top:
            self._top = new
            self._dirty_all = True
            self._push_slider()

    def refresh(self) -> None:
        """Force a full repaint on the next :meth:`tick`."""
        self._dirty_all = True

    def invalidate_layout(self) -> None:
        """Re-fit and re-measure on the next :meth:`tick` (font size changed)."""
        self._last_parent_size = None
        self._geometry = None

    def _text_size(self) -> tuple[float, float] | None:
        """Rendered size of one character, honoring the global font scale
        (``get_text_size`` reports the unscaled font metrics)."""
        try:
            ts = dpg.get_text_size("0")
            scale = dpg.get_global_font_scale()
        except (SystemError, ValueError):
            return None
        if not ts:
            return None
        return (float(ts[0]) * scale, float(ts[1]) * scale)

    def shutdown(self) -> None:
        """Delete the grid's global input handler registry.

        The registry is *not* a child of the grid's widgets, so removing the
        grid's container (e.g. closing a tool window) leaves it behind —
        explicit teardown is required.
        """
        if dpg.does_item_exist(self._input_tag):
            dpg.delete_item(self._input_tag)

    def _mark_rows_dirty(self, rows) -> None:
        """Mark the pool slots showing document rows *rows* for repaint."""
        for row in rows:
            slot = row - self._top
            if 0 <= slot < len(self._pool):
                self._dirty.add(slot)

    # ── pointer / scrollbar ───────────────────────────────────────────────

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
        raise NotImplementedError

    def _on_right_click(self, *_args) -> None:
        if (
            self.on_context_click is None
            or not self.input_enabled
            or not self._pointer_over_grid()
        ):
            return
        self._on_click()
        self.on_context_click()

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

    def _push_slider(self) -> None:
        """Write ``_top`` into the scrollbar (inverted mapping).

        DPG's vertical slider puts min at the bottom, so the scrollbar value
        is ``max_top - _top`` ("rows below the view"): thumb at the widget
        top ≙ the first row. Called on non-slider ``_top`` changes (wheel,
        keys, clicks, clamps) and from ``_on_scroll`` only to re-clamp an
        out-of-range report. Tick never writes the slider value: DPG delivers
        the user's drag value via a queued callback, and an every-frame
        write-back would fight the in-flight drag.
        """
        if not dpg.does_item_exist(self._scroll_tag):
            return  # not built yet (pure-state use in tests)
        value = self._max_top() - self._top
        if int(dpg.get_value(self._scroll_tag)) != value:
            dpg.set_value(self._scroll_tag, value)

    # ── per-frame maintenance (main thread) ───────────────────────────────

    def tick(self) -> None:
        """Relayout, measure, repaint dirty slots, and sync the scrollbar."""
        self._relayout()
        self._track_origin()
        self._measure_geometry()
        self._repaint()
        self._after_paint()
        self._sync_slider_max()
        self._reset_internal_scroll()

    def _after_paint(self) -> None:
        """Per-frame hook after the repaint (overlays)."""

    def _anchor_item(self) -> int | str | None:
        """A text item of the first pool row (child windows report no
        ``rect_min``; text items do, in viewport coordinates)."""
        return None

    def _track_origin(self) -> None:
        """Re-measure when the grid moved on screen (its window was dragged).

        Geometry is measured in screen coordinates, so a moved window would
        otherwise hit-test clicks against the old position. The first
        observation only records the origin.
        """
        anchor = self._anchor_item()
        if anchor is None:
            return
        try:
            x, y = dpg.get_item_rect_min(anchor)
        except (SystemError, ValueError, TypeError, KeyError):
            return  # not laid out yet
        origin = (float(x), float(y))
        if self._screen_origin is not None and origin != self._screen_origin:
            self._geometry = None
        self._screen_origin = origin

    def _relayout(self) -> None:
        """Resize the grid to its parent; rebuild the pool on a shape change."""
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
        if self._layout_for(grid_w, grid_h):
            self._rebuild_pool()
            self._top = min(self._top, self._max_top())
            self._push_slider()
            self.move_to(self._cursor)

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

    def _emit(self, tag: object, text: str, color: Color) -> None:
        """set_value/configure_item only when the (text, color) pair changed."""
        if self._cache.get(tag) == (text, color):
            return
        dpg.set_value(tag, text)
        dpg.configure_item(tag, color=color)
        self._cache[tag] = (text, color)

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
            if self._top > max_top:  # e.g. the data shrank without a relayout
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
