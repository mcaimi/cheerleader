"""
Strings window for the Dear PyGui front-end.

The port of ``tui.tabs.StringsTab``: all printable-ASCII runs (≥ 4 chars,
:func:`~cheerleader.libs.disasm.extract_strings`) across the binary's
data-bearing segments, with the TUI's File Offset / Address / Section /
String columns.

Differences from the TUI (plan decision): the search box is an
**always-visible** input above the table (the TUI's hidden ``s``/``/``
input makes no sense with a mouse), filtering live as you type via the
controller's per-frame :meth:`StringsController.tick` — DPG's
``input_text`` callback only fires on Enter, and a one-string-compare
tick is exactly as deterministic and testable. Clicking a row (single
click, any cell — every cell is a selectable carrying the row's
:class:`~cheerleader.libs.types.BinaryString`) jumps to the string's
file offset **in an already-open hex editor** of the same file
(focus + ``grid.move_to``); with no editor open the status strip says
where the editor would go — the plan's no-auto-open decision.

The extraction runs on a background worker (:class:`BusyTracker` +
``EventKind.STRINGS_DONE``) — large binaries hold megabytes of string
data. A newer run supersedes an in-flight one (stale drops) and ``Esc``
cancels; the window itself closes via its close button like every tool.
"""

from __future__ import annotations

import os

import dearpygui.dearpygui as dpg

from cheerleader.gui.app import (
    GuiApp,
    Tool,
    _tool_window_closed,
    find_inspector_tool,
)
from cheerleader.gui.const import (
    KEY_ESCAPE,
    STATUS_RESERVE,
    TABLE_WIN_H,
    TABLE_WIN_W,
    TOOL_MIN_H,
    TOOL_MIN_W,
)
from cheerleader.gui.workers import BusyTracker, EventKind, WorkerHandle, start_worker
from cheerleader.libs.disasm import extract_strings
from cheerleader.libs.types import BinaryInfo, BinaryString


def offset_cell(s: BinaryString) -> str:
    """File Offset column (TUI parity: ``0x%08x``)."""
    return f"0x{s.file_offset:08x}"


def addr_cell(s: BinaryString) -> str:
    """Address column (TUI parity: ``0x%016x`` or ``—`` when unmapped)."""
    return f"0x{s.addr:016x}" if s.addr else "—"


def row_cells(s: BinaryString) -> tuple[str, str, str, str]:
    """The four rendered cells of one strings row."""
    return (offset_cell(s), addr_cell(s), s.section, s.value)


class StringsController:
    """One Strings tool window: search input + table + worker."""

    def __init__(
        self,
        app: GuiApp,
        info: BinaryInfo,
        *,
        window: int | str,
        status_tag: int | str,
        search_tag: int | str,
        table_tag: int | str,
        ns: str,
    ) -> None:
        self.app = app
        self.info = info  # the view's private snapshot (plan decision 3)
        self.window = window
        self.status_tag = status_tag
        self.search_tag = search_tag
        self.table_tag = table_tag
        self.ns = ns
        self.view_kind = "strings"  # focus-guard key (find_inspector_tool)
        self.path = info.path
        self.busy = BusyTracker()
        self._all: list[BinaryString] = []
        self._visible: list[BinaryString] = []
        self._query = ""  # last query applied by tick (avoids re-filtering)

    # ── extraction worker ─────────────────────────────────────────────────

    def start(self) -> None:
        """Kick off the background extraction (status shows progress)."""
        dpg.set_value(self.status_tag, "Extracting strings…")

        def _job(handle: WorkerHandle) -> None:
            strings = extract_strings(self.info)
            self.app.bridge.post(0, EventKind.STRINGS_DONE, strings, handle)

        # Tracker ownership makes dispatch route the completion here; a
        # superseded run's completion is dropped (is_stale).
        self.busy.start("extracting", start_worker(_job, name=f"gui-strings-{self.ns}"))

    def on_event(self, event) -> None:
        """Handle the extraction worker's completion (stale runs dropped)."""
        if event.kind is not EventKind.STRINGS_DONE:
            return
        if self.busy.is_stale(event.worker) or event.worker.cancel.cancelled:
            return  # superseded or Esc-cancelled run — never lands
        self.busy.clear()
        self._all = event.payload
        self._apply_search()

    # ── search + table ────────────────────────────────────────────────────

    def tick(self) -> None:
        """Per-frame: re-filter when the search box changed (live typing)."""
        query = dpg.get_value(self.search_tag)
        if query != self._query:
            self._query = query
            self._apply_search()

    def _apply_search(self) -> None:
        """Re-run the substring filter and re-render (TUI parity: case-
        insensitive match against the string value)."""
        q = self._query.strip().lower()
        if q:
            self._visible = [s for s in self._all if q in s.value.lower()]
        else:
            self._visible = list(self._all)
        self._populate()
        self._update_status()

    def _populate(self) -> None:
        """(Re-)fill the table body; every cell is a row-jump selectable."""
        dpg.delete_item(self.table_tag, children_only=True, slot=1)
        for s in self._visible:
            with dpg.table_row(parent=self.table_tag):
                for text in row_cells(s):
                    dpg.add_selectable(
                        label=text,
                        user_data=s,
                        callback=self._on_row,
                    )

    def _update_status(self) -> None:
        total = len(self._all)
        shown = len(self._visible)
        if self._query.strip():
            dpg.set_value(
                self.status_tag,
                f"{shown:,} / {total:,} strings matching {self._query.strip()!r}"
                "  ·  click a row to view it in the hex editor",
            )
        else:
            dpg.set_value(
                self.status_tag,
                f"{total:,} strings found  ·  click a row to view it in the hex editor",
            )

    # ── row click → jump into an already-open hex editor ─────────────────

    def _on_row(self, _sender, _app_data, user_data) -> None:
        """Single click on any cell: jump to the string's file offset.

        Only when the owning file is already open in a hex editor (plan
        decision: no auto-open); otherwise the status strip says where the
        editor would land.
        """
        s: BinaryString = user_data
        for tool in self.app.tools:
            doc = getattr(tool.controller, "doc", None)
            if doc is not None and doc.path == self.path:
                dpg.configure_item(tool.window, show=True)
                dpg.focus_item(tool.window)
                tool.controller.grid.move_to(s.file_offset)
                dpg.set_value(
                    self.status_tag,
                    f"Jumped to 0x{s.file_offset:08x} in the hex editor",
                )
                return
        dpg.set_value(
            self.status_tag,
            f"0x{s.file_offset:08x} — open the hex editor "
            "(Tools ▸ Hex Editor…) to inspect this string",
        )

    # ── keys: Esc cancels a running extraction ────────────────────────────

    def _install_keys(self) -> None:
        with dpg.handler_registry(tag=f"str-keys-{self.ns}"):
            dpg.add_key_press_handler(key=KEY_ESCAPE, callback=self._on_escape)

    def _on_escape(self, *_args) -> None:
        if self.busy.worker is not None:
            self.busy.worker.cancel.cancel()
            dpg.set_value(self.status_tag, "String extraction cancelled")

    # ── the shell's Controller protocol ───────────────────────────────────

    def dirty_edit_count(self) -> int:
        """Read-only view — never dirty."""
        return 0

    def window_close_requested(self, *_args) -> None:
        self.app.close_tool_for(self)

    def shutdown(self) -> None:
        if dpg.does_item_exist(f"str-keys-{self.ns}"):
            dpg.delete_item(f"str-keys-{self.ns}")


def open_strings(app: GuiApp, path: str) -> None:
    """Open the Strings view for *path* in a floating tool window.

    Fails soft when *path* is not the shell's currently parsed binary
    (menu gating makes this unreachable in practice); an already-open
    Strings window for the same file is focused, not duplicated.
    """
    info = app.current_info
    if info is None or info.path != path:
        return
    existing = find_inspector_tool(app, "strings", path)
    if existing is not None:
        dpg.configure_item(existing.window, show=True)
        dpg.focus_item(existing.window)
        return
    ns = app.next_ns()
    window = f"gui-tool-{ns}"
    content = f"{window}-content"
    status_tag = f"{window}-status"
    search_tag = f"{ns}-search"
    table_tag = f"{ns}-tbl"
    with dpg.window(
        tag=window,
        label=f"Strings — {os.path.basename(path)}",
        width=TABLE_WIN_W,
        height=TABLE_WIN_H,
        min_size=[TOOL_MIN_W, TOOL_MIN_H],
        no_collapse=True,
        on_close=lambda *_a: _tool_window_closed(app, window),
    ):
        dpg.add_child_window(tag=content, border=False, height=-STATUS_RESERVE)
        dpg.add_separator()
        dpg.add_text("", tag=status_tag)
    # Search box above the table (D2 filter-row layout); the table scrolls
    # within the content child's remaining space.
    dpg.add_input_text(
        tag=search_tag,
        parent=content,
        hint="Search strings…",
        width=-1,
    )
    with dpg.table(
        tag=table_tag,
        parent=content,
        header_row=True,
        row_background=True,
        borders_innerH=True,
        borders_outerH=True,
        scrollY=True,
        policy=dpg.mvTable_SizingStretchProp,
    ):
        dpg.add_table_column(label="File Offset", init_width_or_weight=0.9)
        dpg.add_table_column(label="Address", init_width_or_weight=1.2)
        dpg.add_table_column(label="Section", init_width_or_weight=1.1)
        dpg.add_table_column(label="String", init_width_or_weight=2.2)
    controller = StringsController(
        app,
        info,
        window=window,
        status_tag=status_tag,
        search_tag=search_tag,
        table_tag=table_tag,
        ns=ns,
    )
    controller._install_keys()
    controller.start()
    app.tools.append(
        Tool(
            window=window,
            status_tag=status_tag,
            controller=controller,
            grids=(),
            tick=controller.tick,
        )
    )


def register_opener() -> None:
    """Publish the Strings opener into the shell's registry (idempotent).

    Strings is *not* a ``views._SPECS`` table (its data needs the
    background worker) — it uses the same registry, different module.
    """
    from cheerleader.gui.app import _INSPECTOR_OPENERS

    _INSPECTOR_OPENERS.setdefault("strings", open_strings)
