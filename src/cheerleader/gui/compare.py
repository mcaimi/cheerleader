"""Side-by-side hex compare controller for the Dear PyGui front-end.

Port of ``tui.tabs.HexCompareTab`` + ``tui.widgets.HexCompareView`` onto the
GUI worker pattern: two :class:`~cheerleader.gui.hexgrid.HexGrid` panes over
two :class:`~cheerleader.libs.hexdoc.HexDocument` instances, scrolled as one,
with the diff streaming in a background worker so multi-GB files display
instantly.

Lockstep scrolling: each pane is a full HexGrid (own pool, wheel, keys);
the controller equalizes their ``top_row`` every frame ("last mover wins" —
whichever pane the user wheeled/dragged/scrolled, the other follows). The
right pane's scrollbar is hidden (the TUI shows one), the left one drives.
Both panes share the *longer* document's scroll range via the grids'
``max_top_rows`` hook; the shorter pane renders blank tail rows (its own
EOF check in :meth:`HexGrid._paint_slot`), matching the TUI.

Colors are decided per byte by comparing the two documents live (the TUI
styles against the other pane's bytes), so a byte is green (same) or red
(different) even while the scan is still streaming — the diff blocks from
the worker are only used for ``n``/``p`` navigation.

Worker contract (mirrors the TUI's exclusive ``hexcmp`` worker group):

* every ``iter_diff`` yield posts its ``DIFF_BLOCKS`` (navigation data must
  not be dropped) plus a 5%-throttled ``PROGRESS``;
* ``DIFF_DONE`` carries ``(first_block | None, diff_bytes)``;
* restart (``r``) cancels the previous run; stale events are dropped by
  generation (the port of the TUI ``_gen`` counter) *and* worker identity;
* ``Esc`` cancels; the comparison is read-only, so ``r`` never confirms.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right

from cheerleader.gui.app import GuiApp, human_size
from cheerleader.gui.const import SLIDER_RESERVE
from cheerleader.gui.hexgrid import HexGrid
from cheerleader.gui.workers import (
    BusyTracker,
    EventKind,
    Generation,
    UiEvent,
    WorkerHandle,
    start_worker,
)
from cheerleader.libs.hexdoc import DIFF_BLOCK, HexDocument, iter_diff

try:
    import dearpygui.dearpygui as dpg
except ImportError:  # pragma: no cover - gui/__init__ prints the hint first
    dpg = None  # type: ignore[assignment]

# TUI HexCompareView parity: matching bytes green, differing red.
COL_SAME = (110, 200, 110, 255)
COL_DIFF = (255, 100, 100, 255)


class CompareController:
    """Dual-pane hex diff flows over two documents + two grids.

    compare lives in a floating tool window; *window* / *status_tag*
    / *ns* follow the :class:`~cheerleader.gui.editor.EditorController`
    conventions (per-instance registry + dialog tags, private status strip).
    The pane containers are created by the opener with tags
    ``{ns}-cmp-l-pane`` / ``{ns}-cmp-r-pane``.
    """

    def __init__(
        self,
        app: GuiApp,
        left_grid: HexGrid,
        right_grid: HexGrid,
        left: HexDocument,
        right: HexDocument,
        *,
        window: int | str,
        status_tag: int | str,
        ns: str,
    ) -> None:
        self.app = app
        self.left_grid = left_grid
        self.right_grid = right_grid
        self.left = left
        self.right = right
        self.window = window
        self.status_tag = status_tag
        self.ns = ns
        self._pane_tags = (f"{ns}-cmp-l-pane", f"{ns}-cmp-r-pane")
        self.busy = BusyTracker()
        self._gen = Generation()
        self._diff_blocks: list[int] = []
        self._diff_rows: list[int] = []
        self._nav_bpl = left_grid.bpl
        self._note: str | None = None
        self._synced = 0  # last top_row propagated to both panes
        self._pane_w = -1  # last pane width pushed to the containers
        # Shared scroll range + per-pane coloring + row-scrolling keys. The
        # range reads the sizes live (reload may change them via reopen).
        for grid in (left_grid, right_grid):
            grid.max_top_rows = self._shared_rows
            grid.scroll_mode = True
            grid.on_cursor = None
        left_grid.color_for = self._left_color
        right_grid.color_for = self._right_color
        self._install_keys()

    def _shared_rows(self) -> int:
        """Total rows of the longer document (shared scroll range)."""
        longest = max(self.left.size, self.right.size)
        return (longest + self.left_grid.bpl - 1) // self.left_grid.bpl

    # ── wiring helpers ─────────────────────────────────────────────────────

    def _install_keys(self) -> None:
        """Register the compare mode's global key handlers (per-instance)."""
        with dpg.handler_registry(tag=f"compare-keys-{self.ns}"):
            dpg.add_key_press_handler(key=dpg.mvKey_N, callback=self._on_next_key)
            dpg.add_key_press_handler(key=dpg.mvKey_P, callback=self._on_prev_key)
            dpg.add_key_press_handler(key=dpg.mvKey_R, callback=self._on_reload_key)
            dpg.add_key_press_handler(key=dpg.mvKey_Escape, callback=self._on_escape)

    def start(self) -> None:
        """Start the initial (or restarted) background comparison."""
        self._start_scan()

    # ── colors (live per-byte comparison; TUI _pane_row parity) ────────────

    def _left_color(self, offset: int) -> tuple[int, int, int, int]:
        return self._color(offset, self.left, self.right)

    def _right_color(self, offset: int) -> tuple[int, int, int, int]:
        return self._color(offset, self.right, self.left)

    def _color(
        self, offset: int, mine: HexDocument, other: HexDocument
    ) -> tuple[int, int, int, int]:
        m = mine.read(offset, 1)
        t = other.read(offset, 1)
        # Bytes past the end of the shorter document count as differing.
        if not m or not t or m[0] != t[0]:
            return COL_DIFF
        return COL_SAME

    # ── lockstep scrolling ──────────────────────────────────────────────────

    def _set_top(self, row: int) -> None:
        """Controller-initiated scroll: set both panes immediately."""
        self.left_grid.set_top(row)
        self.right_grid.set_top(row)
        self._synced = self.left_grid.top_row

    def tick(self) -> None:
        """Per-frame maintenance: pane sizes, sync, repaint both grids."""
        self._size_panes()
        lt, rt = self.left_grid.top_row, self.right_grid.top_row
        if lt != rt:
            if rt != self._synced:
                # The right pane moved last (wheel over it, its keys).
                self.left_grid.set_top(rt)
                self._synced = rt
            else:
                self.right_grid.set_top(lt)
                self._synced = lt
        if self.left_grid.bpl != self._nav_bpl:
            # Window resize changed the bytes-per-row: rebuild the diff-row
            # navigation index (TUI _relayout parity).
            self._nav_bpl = self.left_grid.bpl
            self._recompute_rows()
        self.left_grid.tick()
        self.right_grid.tick()

    def _size_panes(self) -> None:
        """Keep the two pane containers at half the tool content width each."""
        try:
            w, h = dpg.get_item_rect_size(f"{self.window}-content")
        except (SystemError, ValueError):
            return
        width = max((int(w) - 2 * SLIDER_RESERVE) // 2, 100)
        height = max(int(h), 100)
        if (width, height) == self._pane_w:
            return
        self._pane_w = (width, height)
        for tag in self._pane_tags:
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, width=width, height=height)

    # ── keys ────────────────────────────────────────────────────────────────

    def _on_next_key(self, *_args) -> None:
        self.action_next_diff()

    def _on_prev_key(self, *_args) -> None:
        self.action_prev_diff()

    def _on_reload_key(self, *_args) -> None:
        self.action_reload()

    def _on_escape(self, *_args) -> None:
        if self.busy.op == "comparing":
            self.busy.worker.cancel.cancel()
            self.busy.clear()
            # Bump the generation so events the worker posted just before
            # observing the cancel cannot overwrite the cancelled note.
            self._gen.next()
            self._note = "comparison cancelled"
            self.update_status()

    # ── diff navigation (TUI action_next_diff / action_prev_diff parity) ───

    def _add_diff_blocks(self, blocks: list[int]) -> None:
        """Append newly-found differing blocks and update the nav index."""
        if not blocks:
            return
        self._diff_blocks.extend(blocks)
        per_row = max(self._nav_bpl // DIFF_BLOCK, 1)
        for b in blocks:
            row = b // per_row
            if not self._diff_rows or self._diff_rows[-1] != row:
                self._diff_rows.append(row)

    def _recompute_rows(self) -> None:
        per_row = max(self._nav_bpl // DIFF_BLOCK, 1)
        self._diff_rows = sorted({b // per_row for b in self._diff_blocks})

    def action_next_diff(self) -> None:
        idx = bisect_right(self._diff_rows, self.left_grid.top_row)
        if idx >= len(self._diff_rows):
            self._note = "No further differences"
            self.update_status()
            return
        self._set_top(self._diff_rows[idx])

    def action_prev_diff(self) -> None:
        idx = bisect_left(self._diff_rows, self.left_grid.top_row) - 1
        if idx < 0:
            self._note = "No previous differences"
            self.update_status()
            return
        self._set_top(self._diff_rows[idx])

    # ── scan control ────────────────────────────────────────────────────────

    def action_reload(self) -> None:
        """``r``: re-read both files and restart the comparison.

        Compare mode is read-only, so there is nothing to discard (the TUI
        app-level reload confirms only the editor's edits).
        """
        if self.busy.op == "comparing":
            self.busy.worker.cancel.cancel()
            self.busy.clear()
        # TUI load() parity: bump the generation before touching anything,
        # so every event of the superseded run drops as stale.
        self._gen.next()
        try:
            self.left.reopen(self.left.path)
            self.right.reopen(self.right.path)
        except OSError as e:
            self._note = f"Reload failed: {e}"
            self.update_status()
            return
        self._diff_blocks.clear()
        self._diff_rows.clear()
        self._note = None
        self.left_grid.refresh()
        self.right_grid.refresh()
        self._start_scan()

    def _start_scan(self) -> None:
        gen = self._gen.next()
        handle = start_worker(lambda h: self._worker(gen, h), name="gui-compare")
        self.busy.start("comparing", handle)
        self.update_status("Comparing… 0%  (Esc cancels)")

    def _worker(self, gen: int, handle: WorkerHandle) -> None:
        cancelled = handle.cancel.cancelled
        first_block: int | None = None
        diff_bytes = 0
        last_pct = -1
        try:
            for new_blocks, n, frac in iter_diff(
                self.left, self.right, cancelled=cancelled
            ):
                if new_blocks and first_block is None:
                    first_block = new_blocks[0]
                diff_bytes += n
                self.app.bridge.post(gen, EventKind.DIFF_BLOCKS, new_blocks, handle)
                pct = int(frac * 100)
                if pct // 5 != last_pct // 5:
                    last_pct = pct
                    self.app.bridge.post(gen, EventKind.PROGRESS, frac, handle)
        except (OSError, ValueError):
            # Documents were closed/reopened underneath the scan — obsolete.
            if not cancelled and self._gen.is_current(gen):
                self.app.bridge.post(
                    gen,
                    EventKind.FAILED,
                    "Comparison failed — I/O error",
                    handle,
                )
            return
        if not handle.cancel.cancelled:
            self.app.bridge.post(
                gen, EventKind.DIFF_DONE, (first_block, diff_bytes), handle
            )

    # ── status (TUI _finish parity) ─────────────────────────────────────────

    def update_status(self, extra: str = "") -> None:
        """Recompose the status strip; *extra* overrides notes/summary."""
        parts = [
            f"left {human_size(self.left.size)} ({self.left.size:,})",
            f"right {human_size(self.right.size)} ({self.right.size:,})",
        ]
        if not extra and self._note:
            extra = self._note
        if extra:
            parts.append(extra)
        parts.append("Ctrl+Q quit")
        dpg.set_value(self.status_tag, "  │  ".join(parts))

    def _first_diff_offset(self, block: int) -> int:
        off = block * DIFF_BLOCK
        a = self.left.read(off, DIFF_BLOCK)
        b = self.right.read(off, DIFF_BLOCK)
        return off + next(
            (i for i, (x, y) in enumerate(zip(a, b)) if x != y),
            min(len(a), len(b)),
        )

    # ── bridge events (frame loop dispatch) ─────────────────────────────────

    def on_event(self, event: UiEvent) -> None:
        """Handle one drained UiBridge event."""
        if not self._gen.is_current(event.generation):
            return  # stale run (superseded/cancelled) — TUI _gen parity
        kind, payload = event.kind, event.payload
        if kind is EventKind.DIFF_BLOCKS:
            self._add_diff_blocks(payload)
            # Colors are computed live per byte — blocks only affect
            # navigation, so no repaint is needed.
        elif kind is EventKind.PROGRESS:
            self._note = None
            self.update_status(f"Comparing… {int(payload * 100)}%  (Esc cancels)")
        elif kind is EventKind.DIFF_DONE:
            self.busy.clear()
            first_block, diff_bytes = payload
            if first_block is None:
                summary = "files are identical"
            else:
                first = self._first_diff_offset(first_block)
                summary = (
                    f"{diff_bytes:,} differing bytes  first at 0x{first:08x}"
                    "  n/p → next/prev diff"
                )
            self._note = None
            self.update_status(summary)
        elif kind is EventKind.FAILED:
            self.busy.clear()
            self._note = str(payload)
            self.update_status()

    def dirty_edit_count(self) -> int:
        """Compare is read-only — never dirty."""
        return 0

    def window_close_requested(self) -> None:
        self.app.close_tool_for(self)

    def shutdown(self) -> None:
        """Teardown before the tool window is deleted: worker + keys."""
        if self.busy.op is not None and self.busy.worker is not None:
            self.busy.worker.cancel.cancel()
        if dpg.does_item_exist(f"compare-keys-{self.ns}"):
            dpg.delete_item(f"compare-keys-{self.ns}")
