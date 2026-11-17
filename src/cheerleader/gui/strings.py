"""
Strings window for the Dear PyGui front-end.

The port of ``tui.tabs.StringsTab``: all printable-ASCII runs (≥ 4 chars,
:func:`~cheerleader.libs.disasm.extract_strings`) across the binary's
data-bearing segments, with the TUI's File Offset / Address / Section /
String columns.

The window is a :class:`~cheerleader.gui.views.TableController` (search
box, sortable columns, clickable rows) whose items come from a background
worker instead of the parsed snapshot. Differences from the TUI: the
search box is an **always-visible** input above the table (the TUI's
hidden ``s``/``/`` input makes no sense with a mouse), filtering live as
you type via the per-frame :meth:`~TableController.tick`. Clicking a row
(any cell — every cell is a selectable carrying the row's
:class:`~cheerleader.libs.types.BinaryString`) shows the string's bytes in
the hex editor of the same file, opening one if needed.

The extraction runs on a background worker (:class:`BusyTracker` +
``EventKind.STRINGS_DONE``) — large binaries hold megabytes of string
data. A newer run supersedes an in-flight one (stale drops) and ``Esc``
cancels; the window itself closes via its close button like every tool.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import dearpygui.dearpygui as dpg

from cheerleader.gui.const import KEY_ESCAPE
from cheerleader.gui.tooling import arch_suffix, find_tool, focus_tool
from cheerleader.gui.views import TableController, ViewSpec, open_table_view
from cheerleader.gui.workers import BusyTracker, EventKind, WorkerHandle, start_worker
from cheerleader.libs.disasm import extract_strings
from cheerleader.libs.types import BinaryInfo, BinaryString

if TYPE_CHECKING:
    from cheerleader.gui.app import GuiApp


def offset_cell(s: BinaryString) -> str:
    """File Offset column (TUI parity: ``0x%08x``)."""
    return f"0x{s.file_offset:08x}"


def addr_cell(s: BinaryString) -> str:
    """Address column (TUI parity: ``0x%016x`` or ``—`` when unmapped)."""
    return f"0x{s.addr:016x}" if s.addr else "—"


def row_cells(s: BinaryString) -> tuple[str, str, str, str]:
    """The four rendered cells of one strings row."""
    return (offset_cell(s), addr_cell(s), s.section, s.value)


STRINGS_SPEC = ViewSpec(
    kind="strings",
    title="Strings",
    noun="strings",
    columns=("File Offset", "Address", "Section", "String"),
    items=lambda _info: [],  # loaded by the worker (StringsController._all)
    row=lambda s, _i: row_cells(s),
    weights=(0.9, 1.2, 1.1, 2.2),
    search=lambda s: s.value,  # TUI parity: case-insensitive, value only
    offset=lambda _info, s: s.file_offset,  # already absolute
    sort_keys=(
        lambda s: s.file_offset,
        lambda s: s.addr,
        None,
        lambda s: s.value.lower(),
    ),
)


class StringsController(TableController):
    """One Strings tool window: search input + table + extraction worker."""

    keys_prefix = "str"

    def __init__(
        self,
        app: GuiApp,
        info: BinaryInfo,
        *,
        window: int | str,
        status_tag: int | str,
        ns: str,
    ) -> None:
        super().__init__(
            app, STRINGS_SPEC, info, window=window, status_tag=status_tag, ns=ns
        )
        self.busy = BusyTracker()
        self._all: list[BinaryString] = []

    def _items(self) -> list[BinaryString]:
        return self._all

    # ── extraction worker ─────────────────────────────────────────────────

    def start(self) -> None:
        """Kick off the background extraction (status shows progress)."""
        dpg.set_value(self.status_tag, "Extracting strings…")

        def _job(handle: WorkerHandle) -> None:
            try:
                strings = extract_strings(self.info)
            except Exception as e:  # noqa: BLE001 - never leave the view
                # stuck on "Extracting…": report the failure instead.
                self.app.bridge.post(
                    0, EventKind.FAILED, f"String extraction failed: {e}", handle
                )
                return
            self.app.bridge.post(0, EventKind.STRINGS_DONE, strings, handle)

        # Tracker ownership makes dispatch route the completion here; a
        # superseded run's completion is dropped (is_stale).
        self.busy.start("extracting", start_worker(_job, name=f"gui-strings-{self.ns}"))

    def on_event(self, event) -> None:
        """Handle the extraction worker's completion (stale runs dropped)."""
        if event.kind not in (EventKind.STRINGS_DONE, EventKind.FAILED):
            return
        if self.busy.is_stale(event.worker) or event.worker.cancel.cancelled:
            return  # superseded or Esc-cancelled run — never lands
        self.busy.clear()
        if event.kind is EventKind.FAILED:
            dpg.set_value(self.status_tag, str(event.payload))
            return
        self._all = event.payload
        self._apply_search()

    # ── search + status ───────────────────────────────────────────────────

    def _apply_search(self) -> None:
        """Re-run the filter/sort and re-render."""
        self.refresh()

    def _status_text(self, shown: int, total: int) -> str:
        q = self._query.strip()
        if q:
            text = f"{shown:,} / {total:,} strings matching {q!r}"
        else:
            text = f"{total:,} strings found"
        return text + "  ·  click a row to view it in the hex editor"

    # ── keys: Esc cancels a running extraction ────────────────────────────

    def _install_keys(self) -> None:
        self.install_keys([(KEY_ESCAPE, self._on_escape)])

    def _on_escape(self, *_args) -> None:
        if self.busy.worker is not None:
            self.busy.worker.cancel.cancel()
            self.busy.clear()
            dpg.set_value(self.status_tag, "String extraction cancelled")


def open_strings(app: GuiApp, path: str) -> None:
    """Open the Strings view for *path* in a floating tool window.

    Fails soft when *path* is not the shell's currently parsed binary
    (menu gating makes this unreachable in practice); an already-open
    Strings window for the same file is focused, not duplicated.
    """
    info = app.current_info
    if info is None or info.path != path:
        return
    existing = find_tool(
        app, view_kind="strings", path=path, slice_offset=info.slice_offset
    )
    if existing is not None:
        focus_tool(existing)
        return
    controller = open_table_view(
        app,
        lambda window, status_tag, ns: StringsController(
            app, info, window=window, status_tag=status_tag, ns=ns
        ),
        f"Strings — {os.path.basename(path)}{arch_suffix(info)}",
    )
    controller._install_keys()
    controller.start()
