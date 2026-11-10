"""
Dear PyGui application shell for the cheerleader hex UI.

``cheerleader-gui`` takes no CLI switches: launching shows the shell
window — a **File / View / Disassembler / Tools / Window / Help** menu bar
over a start panel (open, compare, recent files). ``File → Open File…``
(Ctrl+O) or ``File → Open Recent`` loads a file (parsed in the
background) and turns the panel into the file's summary card; a supported
binary (Mach-O/ELF) opens the Disassembly view and enables the
**Disassembler** menu's inspection views, anything else opens
in the hex editor. Tools show in cascaded **floating windows** — several
may be open at once, each with its own menu bar and status strip; the
**Window** menu lists and arranges them. Recent files and the View zoom
persist via :mod:`cheerleader.gui.settings`.

Frame loop::

    while running:
        dpg.run_callbacks(queue)          # widget callbacks, on this thread
        for event in bridge.drain():      # worker results → owning tool
            dispatch(event)               # routed by worker identity
        app.tick()                        # every tool's per-frame maintenance
        dpg.render_dearpygui_frame()

Threading model: tool/controller state is owned by the main frame loop.
DPG's default runs widget callbacks on a separate callback thread, where
they would race the loop's own widget mutations (and DPG's shared ``with``
container stack); the app therefore enables manual callback management and
runs the queued callbacks at the top of each frame, on the main thread.
Worker threads only touch :class:`~cheerleader.gui.workers.UiBridge`.

Quit paths: the viewport is created with ``disable_close=True`` (no close
veto exists) and tool windows route their X through
``Controller.window_close_requested`` (confirm + reshow-veto when dirty);
Ctrl+Q funnels through :meth:`GuiApp.request_quit` — one aggregate confirm
when any tool has unsaved edits.
"""

from __future__ import annotations

import itertools
import os
import platform
import sys
from collections.abc import Callable

from cheerleader.formats import detect_format
from cheerleader.formats import parse as parse_binary
from cheerleader.formats.macho import list_fat_slices
from cheerleader.gui import disasm, strings, views
from cheerleader.gui.compare import CompareController
from cheerleader.gui.const import (
    APP_TITLE,
    APP_VERSION,
    CASCADE_ORIGIN,
    CASCADE_SLOTS,
    CASCADE_STEP,
    COMPARE_WIN_H,
    COMPARE_WIN_W,
    DISASM_ITEMS,
    DISASM_MENU,
    DISASM_MENU_ITEMS,
    DLG_OPEN,
    EDITOR_WIN_H,
    EDITOR_WIN_W,
    ENGINE_ITEMS,
    ENGINE_MENU,
    FONT_CANDIDATES,
    FONT_ENV_VAR,
    FONT_SIZE,
    KEY_EQUAL,
    MENU_TAG,
    MI_EDITOR,
    MI_INFO,
    MI_RECENT,
    MI_SHORTCUTS,
    MIN_VIEWPORT_H,
    MIN_VIEWPORT_W,
    SHELL_FILL,
    SHELL_STATUS,
    SHELL_STATUS_BAR,
    SHELL_STATUS_RIGHT,
    SLICE_MENU,
    SLIDER_RESERVE,
    SRC_URL,
    STATUS_RESERVE,
    VIEWPORT_H,
    VIEWPORT_TITLE,
    VIEWPORT_W,
    WIN_TAG,
    WINDOW_MENU,
    ZOOM_MAX,
    ZOOM_MIN,
    ZOOM_STEP,
)
from cheerleader.gui.dialogs import (
    CompareOpenDialog,
    ConfirmDialog,
    ErrorPopup,
    InfoDialog,
    ShortcutsDialog,
    add_all_files_filter,
    picked_path,
)
from cheerleader.gui.editor import EditorController
from cheerleader.gui.hexgrid import HexGrid
from cheerleader.gui.settings import Settings
from cheerleader.gui.theme import COL_BLUE, COL_DIM, COL_YELLOW, build_theme
from cheerleader.gui.tooling import (
    Controller,
    Tool,
    build_tool_window,
    ctrl_down,
    find_editor,
    focus_tool,
    human_size,
)
from cheerleader.gui.workers import (
    EventKind,
    UiBridge,
    UiEvent,
    WorkerHandle,
    start_worker,
)
from cheerleader.libs.hexdoc import HexDocument
from cheerleader.libs.types import BinaryInfo

try:
    import dearpygui.dearpygui as dpg
except ImportError:  # pragma: no cover - main() prints the install hint first
    dpg = None  # type: ignore[assignment]


class GuiApp:
    """
    Application state shared by all GUI tools.

    Tools self-register in :attr:`tools` when opened; the frame loop
    dispatches drained worker events to the tool whose busy worker posted
    them and runs every tool's per-frame maintenance.
    """

    def __init__(self) -> None:
        self.bridge = UiBridge()
        self.tools: list[Tool] = []
        self._quit_dlg: ConfirmDialog | None = None
        self._ns_counter = itertools.count(1)
        # File loaded via File → Open File… (enables Tools → Hex Editor…);
        # None until the first successful open. Closing an editor window
        # does not unload it.
        self.current_file: str | None = None
        # Parsed model of current_file: set by the background
        # parse worker; gates the Disassembler menu. None for files that
        # are not supported executables (hex tools still work).
        self.current_info: BinaryInfo | None = None
        self._parse_worker: WorkerHandle | None = None
        self.settings = Settings.load()  # recent files + zoom
        self._pos_counter = itertools.count()
        self._font_base_scale = 1.0  # 0.5 when a 2x font is loaded (HiDPI)
        self._shortcuts_dlg: ShortcutsDialog | None = None
        # Architecture slices of current_file: [(index, arch)] — more than
        # one for a fat (universal) Mach-O — and the one being inspected.
        self.current_slices: list[tuple[int, str]] = []
        self.current_slice = 0
        # Shown after the path in the bottom bar (e.g. why the Disassembler
        # is unavailable); cleared whenever a file or slice is (re)loaded.
        self.status_note = ""

    @property
    def is_fat(self) -> bool:
        return len(self.current_slices) > 1

    def select_slice(self, index: int) -> None:
        """Inspect another slice of the current fat binary.

        Windows of the previous slice stay open as snapshots; the new
        slice is parsed in the background and opens its own Disassembly
        view when done (a quick second switch supersedes the first parse).
        """
        if self.current_file is None or not self.is_fat or index == self.current_slice:
            return
        if index not in (i for i, _ in self.current_slices):
            return
        self.current_slice = index
        self.current_info = None
        self.status_note = ""
        _set_disasm_enabled(False)
        self._start_parse(self.current_file)
        _refresh_slice_ui(self)

    def next_window_pos(self) -> list[int]:
        """Cascade position for the next tool window."""
        i = next(self._pos_counter) % CASCADE_SLOTS
        x0, y0 = CASCADE_ORIGIN
        return [x0 + i * CASCADE_STEP, y0 + i * CASCADE_STEP]

    # ── zoom (View menu, Ctrl+= / Ctrl+- / Ctrl+0) ────────────────────────

    @property
    def zoom(self) -> float:
        return self.settings.zoom

    def set_zoom(self, zoom: float) -> None:
        """Scale every font by *zoom* (clamped); grids re-fit next frame."""
        zoom = round(min(max(zoom, ZOOM_MIN), ZOOM_MAX), 2)
        self.settings.zoom = zoom
        self.settings.save()
        self.apply_zoom()

    def apply_zoom(self) -> None:
        dpg.set_global_font_scale(self._font_base_scale * self.settings.zoom)
        for tool in self.tools:
            for grid in tool.grids:
                grid.invalidate_layout()

    def next_ns(self) -> str:
        """Unique per-app namespace for a new tool's tags/registries."""
        return f"t{next(self._ns_counter)}"

    def register_tool(
        self,
        window: int | str,
        status_tag: int | str,
        controller: Controller,
        *,
        grids: tuple = (),
        tick: Callable[[], None] | None = None,
    ) -> Tool:
        """Track a newly opened tool (event dispatch, tick, quit, close)."""
        tool = Tool(
            window=window,
            status_tag=status_tag,
            controller=controller,
            grids=grids,
            tick=tick,
        )
        self.tools.append(tool)
        _refresh_window_menu(self)
        return tool

    def show_in_editor(self, path: str, offset: int) -> bool:
        """Show *offset* of *path* in its hex editor, opening one if needed.

        Returns False when the editor could not be opened (the open path
        already reported why).
        """
        tool = find_editor(self, path)
        if tool is None:
            open_editor(self, path)
            tool = find_editor(self, path)
            if tool is None:
                return False
        else:
            focus_tool(tool)
        tool.controller.grid.move_to(offset)
        return True

    def dispatch(self, event: UiEvent) -> None:
        """Route a worker event to the tool whose busy worker posted it.

        Strict worker identity: with several tools alive, broadcasting would
        leak one tool's generations into another's; events of tools closed
        mid-flight (and handle-less events) are dropped. Shell-level events
        (the Open File… parse worker) are handled first, by the shell.
        """
        if self._parse_worker is not None and event.worker is self._parse_worker:
            self._on_binary_parsed(event)
            return
        for tool in self.tools:
            busy = getattr(tool.controller, "busy", None)
            if (
                busy is not None
                and busy.worker is not None
                and busy.worker is event.worker
            ):
                tool.controller.on_event(event)
                return

    def tick(self) -> None:
        """Run per-frame widget maintenance for every open tool."""
        for tool in self.tools:
            if tool.tick is not None:
                tool.tick()

    def _start_parse(self, path: str) -> None:
        """Parse *path* on a worker thread.

        `formats.parse` reads the whole file, so — like the TUI's load — it
        runs off the render thread. The completion event is owned by this
        shell (dispatch routes it by worker identity); a newer parse
        supersedes an in-flight one purely by handle identity.
        """

        slice_index = self.current_slice  # Mach-O slice (fat files)

        def _job(_handle: WorkerHandle) -> None:
            try:
                if slice_index:
                    info = parse_binary(path, slice_index=slice_index)
                else:
                    info = parse_binary(path)
            except Exception as e:  # noqa: BLE001 - defensive: parsers are
                # non-fatal by design (they return .error); never crash the
                # shell on a malformed file.
                info = BinaryInfo(path=path, error=str(e))
            # Posted with *this* run's handle; a superseded parse is dropped
            # by dispatch's identity check.
            self.bridge.post(0, EventKind.BINARY_PARSED, info, _handle)

        self._parse_worker = start_worker(_job, name="gui-parse")

    def _on_binary_parsed(self, event: UiEvent) -> None:
        """Shell handler for the Open File… parse worker's completion."""
        self._parse_worker = None
        info = event.payload
        if info is None:  # pragma: no cover - job always posts an info
            return
        if info.error is not None:
            # Not a supported executable (or unparseable): the hex flow is
            # already live for this file; the Disassembler menu stays grayed.
            # A status note, not a modal — hex-editing arbitrary files is a
            # normal use, not an error.
            self.current_info = None
            _set_disasm_enabled(False)
            self.status_note = f"Disassembler unavailable: {info.error}"
            _refresh_status(self)
            _refresh_landing(self)
            # Nothing to disassemble: show the bytes instead, so opening a
            # file always puts something on screen.
            if self.current_file is not None:
                open_editor(self, self.current_file)
            return
        self.current_info = info
        _set_disasm_enabled(True)
        _refresh_slice_ui(self)
        
        if self.current_file is not None:
            INSPECTOR_OPENERS["disasm"](self, self.current_file)

    def request_quit(self) -> None:
        """
        Quit the app — the funnel for Ctrl+Q and the File → Quit item.

        With unsaved edits anywhere, show **one** aggregate confirmation
        instead of per-tool dialogs; answering No keeps the app up.
        """
        if self._quit_dlg is not None:
            return  # already confirming
        total = sum(tool.controller.dirty_edit_count() for tool in self.tools)
        if total == 0:
            dpg.stop_dearpygui()
            return
        self._quit_dlg = ConfirmDialog(
            message=(
                f"{total} unsaved edit{'s' if total != 1 else ''}"
                " — quit without saving?"
            ),
            on_answer=self._quit_answer,
        )
        self._quit_dlg.open()

    def _quit_answer(self, ok: bool) -> None:
        dlg, self._quit_dlg = self._quit_dlg, None
        dlg.close()
        if ok:
            dpg.stop_dearpygui()

    def close_tool_for(self, controller: Controller) -> None:
        """Close the tool owned by *controller* (its X / confirm answered)."""
        for tool in self.tools:
            if tool.controller is controller:
                self.close_tool(tool)
                return

    def close_tool(self, tool: Tool) -> None:
        """Full teardown of one tool: worker, modals, registries, window."""
        tool.controller.shutdown()
        for grid in tool.grids:
            grid.shutdown()
        if dpg.does_item_exist(tool.window):
            dpg.delete_item(tool.window)
        self.tools[:] = [t for t in self.tools if t is not tool]
        _refresh_window_menu(self)


def _find_font_file() -> str | None:
    override = os.environ.get(FONT_ENV_VAR, "")
    for path in (override, *FONT_CANDIDATES):
        if path and os.path.isfile(path):
            return path
    return None


def _build_font() -> float:
    """
    Bind the first available monospace font; skip on any failure.

    The built-in DPG font is fixed-width, so the hex grid stays aligned even
    when no candidate font exists on the system. Returns the base global
    font scale: on macOS the font is rasterized at 2x and drawn at 0.5 —
    the same layout size, but sharp on Retina displays.
    """
    path = _find_font_file()
    if path is None:
        return 1.0
    oversample = 2 if sys.platform == "darwin" else 1
    try:
        with dpg.font_registry():
            font = dpg.add_font(path, FONT_SIZE * oversample)
        dpg.bind_font(font)
    except (SystemError, RuntimeError, OSError):
        # Corrupt or unsupported font file — the default font is fine.
        return 1.0
    return 1.0 / oversample


def _on_quit_key(app: GuiApp):
    """Global Ctrl+Q handler factory (Q alone would clash with popup typing)."""

    def _handler(_sender, _app_data) -> None:
        if ctrl_down():
            app.request_quit()

    return _handler


def _on_open_key(app: GuiApp):
    """Global Ctrl+O handler: show the File → Open File… dialog."""

    def _handler(_sender, _app_data) -> None:
        if ctrl_down():
            dpg.configure_item(DLG_OPEN, show=True)

    return _handler


def _on_ctrl(action: Callable[[], None]):
    """Global Ctrl+<key> handler factory."""

    def _handler(*_args) -> None:
        if ctrl_down():
            action()

    return _handler


def show_shortcuts(app: GuiApp) -> None:
    """Help ▸ Keyboard shortcuts (F1) — one instance at a time."""
    dlg = app._shortcuts_dlg
    if dlg is not None and dpg.does_item_exist(dlg.TAG):
        return
    app._shortcuts_dlg = ShortcutsDialog()
    app._shortcuts_dlg.open()


def open_editor(app: GuiApp, path: str) -> None:
    """Open *path* in a floating hex-editor tool window.

    Fails soft: an unreadable path (vanished between the file dialog and
    here) shows an :class:`~cheerleader.gui.dialogs.ErrorPopup`. Opening an
    already-open file focuses the existing window instead.
    """
    try:
        doc = HexDocument(path)
    except OSError as e:
        ErrorPopup(f"Cannot open {path}:\n{e}")
        return
    existing = find_editor(app, path)
    if existing is not None:
        doc.close()
        focus_tool(existing)
        return
    ns, window, content, status_tag = build_tool_window(
        app, f"hex editor — {os.path.basename(path)}", EDITOR_WIN_W, EDITOR_WIN_H
    )
    grid = HexGrid(doc, name=ns)
    grid.build(content)
    grid.enable_cursor_highlight(window, content)
    controller = EditorController(
        app, grid, doc, window=window, status_tag=status_tag, ns=ns
    )
    controller.update_status()
    app.register_tool(window, status_tag, controller, grids=(grid,), tick=grid.tick)


def open_compare(app: GuiApp, left_path: str, right_path: str) -> None:
    """Open two files in a floating side-by-side compare tool window.

    The diff scan starts immediately (TUI on_mount parity). Fails soft with
    an :class:`~cheerleader.gui.dialogs.ErrorPopup` when either file cannot
    be opened.
    """
    try:
        left = HexDocument(left_path)
        try:
            right = HexDocument(right_path)
        except OSError:
            left.close()
            raise
    except OSError as e:
        ErrorPopup(f"Cannot compare:\n{e}")
        return
    ns, window, content, status_tag = build_tool_window(
        app,
        f"hex compare — {os.path.basename(left_path)}"
        f" ⇄ {os.path.basename(right_path)}",
        COMPARE_WIN_W,
        COMPARE_WIN_H,
    )
    # Dual panes: each grid gets its own container so its measured relayout
    # covers one half of the window.
    pane_l, pane_r = f"{ns}-cmp-l-pane", f"{ns}-cmp-r-pane"
    with dpg.group(horizontal=True, parent=content):
        dpg.add_child_window(
            tag=pane_l,
            border=False,
            width=(COMPARE_WIN_W - 2 * SLIDER_RESERVE) // 2,
            height=COMPARE_WIN_H - STATUS_RESERVE - 2,
        )
        dpg.add_child_window(
            tag=pane_r,
            border=False,
            width=(COMPARE_WIN_W - 2 * SLIDER_RESERVE) // 2,
            height=COMPARE_WIN_H - STATUS_RESERVE - 2,
        )
    left_grid = HexGrid(left, name=f"{ns}-cmp-l")
    right_grid = HexGrid(right, name=f"{ns}-cmp-r")
    left_grid.build(pane_l)
    right_grid.build(pane_r)
    # TUI parity: one scrollbar; the left pane's drives both.
    right_grid.set_scrollbar_visible(False)
    controller = CompareController(
        app,
        left_grid,
        right_grid,
        left,
        right,
        window=window,
        status_tag=status_tag,
        ns=ns,
    )
    controller.start()  # initial diff scan
    app.register_tool(
        window,
        status_tag,
        controller,
        grids=(left_grid, right_grid),
        tick=controller.tick,
    )


def _show_dialog(tag: int | str):
    """Menu-item callback factory: show a pre-built file dialog."""

    def _open(*_args) -> None:
        dpg.configure_item(tag, show=True)

    return _open


def _file_picked(app: GuiApp, app_data) -> None:
    """File → Open File… pick (dialog callback)."""
    path = picked_path(app_data)
    if path:  # OK with an empty selection — treat like a cancel
        open_file(app, path)


def open_file(app: GuiApp, path: str) -> None:
    """Load *path* as the shell's current file (Open File…, Open Recent,
    the start panel).

    The file is *loaded* here (a `HexDocument` is constructed to fail fast
    on vanished/unreadable paths, then released) and parsed in the
    background; the parse result opens the Disassembly view for supported
    binaries, else the hex editor.
    """
    try:
        doc = HexDocument(path)
    except OSError as e:
        ErrorPopup(f"Cannot open {path}:\n{e}")
        if path in app.settings.recent:  # a stale recent entry
            app.settings.remove_recent(path)
            _refresh_recent_menu(app)
            _refresh_landing(app)
        return
    doc.close()
    app.settings.add_recent(path)
    _refresh_recent_menu(app)
    app.current_file = path
    app.current_slices = file_slices(path)
    app.current_slice = default_slice(app.current_slices)
    app.status_note = ""
    # The previous file's binary model no longer applies: gray the
    # Disassembler menu until this file's parse completes.
    app.current_info = None
    _set_disasm_enabled(False)
    dpg.configure_item(MI_EDITOR, enabled=True)
    dpg.configure_item(SHELL_STATUS_BAR, show=True)
    app._start_parse(path)
    _refresh_slice_ui(app)


def file_slices(path: str) -> list[tuple[int, str]]:
    """``(index, arch)`` per architecture slice of a Mach-O file (several
    for a fat binary); ``[]`` for anything else."""
    try:
        if detect_format(path) != "macho":
            return []
        return list_fat_slices(path)
    except (OSError, ValueError, IndexError):  # truncated / unreadable header
        return []


def default_slice(slices: list[tuple[int, str]]) -> int:
    """The slice to inspect first: this machine's architecture — an exact
    match (``arm64``), else a variant of it (``arm64e``) — else slice 0."""
    native = platform.machine()
    for i, arch in slices:
        if arch == native:
            return i
    for i, arch in slices:
        if arch.startswith(native):
            return i
    return slices[0][0] if slices else 0


def _slice_label(index: int, arch: str) -> str:
    return f"[{index}] {arch}"


def _current_arch(app: GuiApp) -> str:
    return dict(app.current_slices).get(app.current_slice, "")


def _refresh_slice_ui(app: GuiApp) -> None:
    """Everything showing the current slice: status bar, Slice submenu,
    summary card."""
    _refresh_status(app)
    _refresh_slice_menu(app)
    _refresh_landing(app)


def _refresh_status(app: GuiApp) -> None:
    """Bottom bar: the file path (+ note) on the left; the architecture
    being inspected and the disassembly engine on the right."""
    if not dpg.does_item_exist(SHELL_STATUS) or app.current_file is None:
        return
    left = app.current_file
    if app.status_note:
        left += f"  │  {app.status_note}"
    dpg.set_value(SHELL_STATUS, left)
    dpg.set_value(SHELL_STATUS_RIGHT, status_right_text(app))


def status_right_text(app: GuiApp) -> str:
    """``arm64e  (slice 2 of 3)   │   engine: Auto (capstone)``."""
    info = app.current_info
    arch = info.arch if info is not None else _current_arch(app)
    if not arch:
        arch = "detecting…" if app._parse_worker is not None else "—"
    if app.is_fat:
        position = [i for i, _ in app.current_slices].index(app.current_slice)
        arch += f"  (slice {position + 1} of {len(app.current_slices)})"
    return f"{arch}   │   engine: {disasm.engine_label()}"


def _refresh_slice_menu(app: GuiApp) -> None:
    """Disassembler ▸ Slice: one radio item per slice (``● `` = current)."""
    if not dpg.does_item_exist(SLICE_MENU):
        return
    dpg.delete_item(SLICE_MENU, children_only=True)
    if not app.is_fat:
        dpg.add_menu_item(
            label="(single architecture)", enabled=False, parent=SLICE_MENU
        )
        return
    for index, arch in app.current_slices:
        mark = "● " if index == app.current_slice else ""
        dpg.add_menu_item(
            label=f"{mark}{_slice_label(index, arch)}",
            parent=SLICE_MENU,
            callback=lambda *_a, i=index: app.select_slice(i),
        )


def _set_disasm_enabled(enabled: bool) -> None:
    """Gray in/out every Disassembler menu item (binary loaded or not)."""
    for tag in DISASM_ITEMS:
        dpg.configure_item(tag, enabled=enabled)


# Inspector view openers by kind — the Disassembler menu's targets (see
# const.DISASM_MENU_ITEMS). Each takes (app, path) and fails soft when path
# is not the shell's currently parsed binary.
INSPECTOR_OPENERS: dict[str, Callable[[GuiApp, str], None]] = {
    "disasm": disasm.open_disassembly,
    **views.openers(),
    "strings": strings.open_strings,
}


def _inspector_menu_opener(app: GuiApp, kind: str):
    """Disassembler menu-item callback factory"""

    def _open(*_args) -> None:
        if app.current_file is not None:
            INSPECTOR_OPENERS[kind](app, app.current_file)

    return _open


def _engine_menu_pick(app: GuiApp, value: str):
    """Disassembler ▸ Engine submenu callback factory: set the radio
    choice, refresh the "● " markers and the bottom bar."""

    def _pick(*_args) -> None:
        disasm.set_engine_override(value)
        _mark_engine_items()
        _refresh_status(app)

    return _pick


def _mark_engine_items() -> None:
    """Engine submenu radio state: "● " prefix on the active choice;
    capstone/objdump items grayed while unavailable in this process."""
    active = disasm.engine_override()
    for label, value, tag in ENGINE_ITEMS:
        dpg.configure_item(
            tag,
            label=f"● {label}" if value == active else label,
            enabled=value == "auto" or disasm.engine_available(value),
        )


def _editor_menu_opener(app: GuiApp):
    """Tools → Hex Editor… callback: open the editor for the loaded file.

    The menu item is disabled (grayed) until a file has been loaded via
    File → Open File…; `open_editor`'s duplicate-open guard focuses the
    existing window when the file is already being edited.
    """

    def _open(*_args) -> None:
        if app.current_file is not None:
            open_editor(app, app.current_file)

    return _open


def _compare_dialog_opener(app: GuiApp, source: str = ""):
    """Menu-item callback factory: open the two-file compare picker."""

    def _open(*_args) -> None:
        CompareOpenDialog(
            on_launch=lambda src, tgt: open_compare(app, src, tgt), source=source
        ).open()

    return _open


# ── dynamic shell parts: start panel, Open Recent, Window menu ────────────────


def _refresh_landing(app: GuiApp) -> None:
    """(Re)build the shell's start panel / loaded-file summary card."""
    if not dpg.does_item_exist(SHELL_FILL):
        return  # shell not built (headless controller tests)
    dpg.delete_item(SHELL_FILL, children_only=True)
    with dpg.group(parent=SHELL_FILL, indent=24):
        dpg.add_spacer(height=24)
        if app.current_file is None:
            _landing_welcome(app)
        else:
            _landing_summary(app, app.current_file)


def _landing_welcome(app: GuiApp) -> None:
    dpg.add_text("Cheerleader — your friendly binary inspector", color=COL_BLUE)
    dpg.add_text(
        "Open any file to view and edit its bytes; open a Mach-O or ELF "
        "binary to disassemble and inspect it too.",
        color=COL_DIM,
        wrap=640,
    )
    dpg.add_spacer(height=12)
    with dpg.group(horizontal=True):
        dpg.add_button(label="Open File…  (Ctrl+O)", callback=_show_dialog(DLG_OPEN))
        dpg.add_button(label="Compare two files…", callback=_compare_dialog_opener(app))
    if app.settings.recent:
        dpg.add_spacer(height=16)
        dpg.add_text("Recent files")
        for path in app.settings.recent:
            dpg.add_selectable(
                label=f"{os.path.basename(path)}    {os.path.dirname(path)}",
                callback=lambda *_a, p=path: open_file(app, p),
            )
    dpg.add_spacer(height=16)
    dpg.add_text("Press F1 to see every keyboard shortcut.", color=COL_DIM)


def _landing_summary(app: GuiApp, path: str) -> None:
    info = app.current_info
    dpg.add_text(os.path.basename(path), color=COL_BLUE)
    try:
        size = human_size(os.path.getsize(path))
    except OSError:
        size = "?"
    dpg.add_text(f"{path}   ·   {size}", color=COL_DIM)
    if app.is_fat:
        labels = [_slice_label(i, arch) for i, arch in app.current_slices]
        with dpg.group(horizontal=True):
            dpg.add_text(f"Universal binary — {len(labels)} slices   ")
            dpg.add_combo(
                labels,
                default_value=_slice_label(app.current_slice, _current_arch(app)),
                width=200,
                callback=lambda _s, label: app.select_slice(
                    app.current_slices[labels.index(label)][0]
                ),
            )
    if info is not None:
        dpg.add_text(
            f"{info.file_type} · {info.arch} · {info.bits}-bit   —   "
            f"{len(info.segments)} segments · {len(info.symbols):,} symbols · "
            f"{len(info.libraries)} libraries"
        )
    elif app._parse_worker is not None:
        dpg.add_text("Parsing…", color=COL_YELLOW)
    else:
        dpg.add_text("Not a Mach-O / ELF executable — hex tools only", color=COL_DIM)
    dpg.add_spacer(height=12)
    with dpg.group(horizontal=True):
        dpg.add_button(label="Hex Editor", callback=_editor_menu_opener(app))
        for label, kind in (("Disassembly", "disasm"), ("Strings", "strings")):
            dpg.add_button(
                label=label,
                enabled=info is not None,
                callback=_inspector_menu_opener(app, kind),
            )
        dpg.add_button(
            label="Compare with…", callback=_compare_dialog_opener(app, source=path)
        )
        dpg.add_button(label="Open another file…", callback=_show_dialog(DLG_OPEN))


def _refresh_recent_menu(app: GuiApp) -> None:
    if not dpg.does_item_exist(MI_RECENT):
        return
    dpg.delete_item(MI_RECENT, children_only=True)
    for path in app.settings.recent:
        dpg.add_menu_item(
            label=f"{os.path.basename(path)}    {os.path.dirname(path)}",
            parent=MI_RECENT,
            callback=lambda *_a, p=path: open_file(app, p),
        )
    if not app.settings.recent:
        dpg.add_menu_item(label="(no recent files)", enabled=False, parent=MI_RECENT)
        return
    dpg.add_separator(parent=MI_RECENT)
    dpg.add_menu_item(
        label="Clear recent files",
        parent=MI_RECENT,
        callback=lambda *_a: _clear_recent(app),
    )


def _clear_recent(app: GuiApp) -> None:
    app.settings.clear_recent()
    _refresh_recent_menu(app)
    _refresh_landing(app)


def _refresh_window_menu(app: GuiApp) -> None:
    """Window menu: one entry per open tool (click to focus) + arrange."""
    if not dpg.does_item_exist(WINDOW_MENU):
        return
    dpg.delete_item(WINDOW_MENU, children_only=True)
    for tool in app.tools:
        exists = dpg.does_item_exist(tool.window)
        label = dpg.get_item_label(tool.window) if exists else "?"
        dpg.add_menu_item(
            label=label,
            parent=WINDOW_MENU,
            callback=lambda *_a, t=tool: focus_tool(t),
        )
    if not app.tools:
        dpg.add_menu_item(label="(no open windows)", enabled=False, parent=WINDOW_MENU)
        return
    dpg.add_separator(parent=WINDOW_MENU)
    dpg.add_menu_item(
        label="Cascade windows",
        parent=WINDOW_MENU,
        callback=lambda *_a: cascade_windows(app),
    )
    dpg.add_menu_item(
        label="Close all windows",
        parent=WINDOW_MENU,
        callback=lambda *_a: close_all_windows(app),
    )


def cascade_windows(app: GuiApp) -> None:
    """Re-stack every tool window diagonally, the last one on top."""
    app._pos_counter = itertools.count()
    for tool in app.tools:
        dpg.set_item_pos(tool.window, app.next_window_pos())
        focus_tool(tool)


def close_all_windows(app: GuiApp) -> None:
    """Close every tool without unsaved edits; dirty editors stay open
    (each would need its own confirmation)."""
    for tool in list(app.tools):
        if tool.controller.dirty_edit_count() == 0:
            app.close_tool(tool)
    if app.tools:
        n = len(app.tools)
        app.status_note = (
            f"{n} window{'s' if n != 1 else ''} with unsaved edits left open"
        )
        _refresh_status(app)


def _info_opener(app: GuiApp):
    """Show Info Dialog"""

    def _open(*_args) -> None:
        InfoDialog(
            message=f"{APP_TITLE} - {APP_VERSION}\nSource here: {SRC_URL}"
        ).open()

    return _open


def build_shell(app: GuiApp) -> int | str:
    """Create the persistent chrome: theme, font, shell window + menus.

    The shell itself is empty (menu bar + bottom bar); tools open in
    floating windows. The open-file dialog is a pre-built top-level item,
    shown by the File menu entry.
    """
    build_theme()
    app._font_base_scale = _build_font()
    app.apply_zoom()
    with dpg.window(tag=WIN_TAG), dpg.menu_bar(tag=MENU_TAG):
        with dpg.menu(label="File"):
            dpg.add_menu_item(
                label="Open File…", shortcut="Ctrl+O", callback=_show_dialog(DLG_OPEN)
            )
            dpg.add_menu(label="Open Recent", tag=MI_RECENT)
            dpg.add_separator()
            dpg.add_menu_item(
                label="Quit", shortcut="Ctrl+Q", callback=app.request_quit
            )

        with dpg.menu(label="View"):
            dpg.add_menu_item(
                label="Zoom In",
                shortcut="Ctrl+=",
                callback=lambda *_a: app.set_zoom(app.zoom + ZOOM_STEP),
            )
            dpg.add_menu_item(
                label="Zoom Out",
                shortcut="Ctrl+-",
                callback=lambda *_a: app.set_zoom(app.zoom - ZOOM_STEP),
            )
            dpg.add_menu_item(
                label="Actual Size",
                shortcut="Ctrl+0",
                callback=lambda *_a: app.set_zoom(1.0),
            )

        with dpg.menu(label="Disassembler", tag=DISASM_MENU):
            for label, tag, kind in DISASM_MENU_ITEMS:
                dpg.add_menu_item(
                    label=label,
                    tag=tag,
                    enabled=False,
                    callback=_inspector_menu_opener(app, kind),
                )

            # Slice radio submenu: the architecture slice of a fat binary
            # (rebuilt per open file; a placeholder item for thin files).
            dpg.add_menu(label="Slice", tag=SLICE_MENU)

            # Engine radio submenu: process-global
            # preference, applies to newly opened/re-run views — kept
            # enabled even without a loaded binary.
            with dpg.menu(label="Engine", tag=ENGINE_MENU):
                for label, value, tag in ENGINE_ITEMS:
                    dpg.add_menu_item(
                        label=label,
                        tag=tag,
                        callback=_engine_menu_pick(app, value),
                    )
            _mark_engine_items()

        with dpg.menu(label="Tools"):
            dpg.add_menu_item(
                label="Hex Editor…",
                tag=MI_EDITOR,
                enabled=False,
                callback=_editor_menu_opener(app),
            )
            dpg.add_menu_item(
                label="Hex Compare…", callback=_compare_dialog_opener(app)
            )

        dpg.add_menu(label="Window", tag=WINDOW_MENU)

        with dpg.menu(label="Help"):
            dpg.add_menu_item(
                label="Keyboard Shortcuts",
                tag=MI_SHORTCUTS,
                shortcut="F1",
                callback=lambda *_a: show_shortcuts(app),
            )
            dpg.add_menu_item(
                label="About Cheerleader", tag=MI_INFO, callback=_info_opener(app)
            )

    # Bottom bar: hidden until a file is loaded via File → Open File…; the
    # filler child pins the bar to the bottom of the (resizable) window.
    # (Explicit parents: the context stack is empty outside the with block.)
    dpg.add_child_window(
        tag=SHELL_FILL, parent=WIN_TAG, border=False, height=-STATUS_RESERVE
    )
    # Path on the left; architecture + engine on the right: a fixed-width
    # column fits (and re-fits) its text and hugs the right edge.
    with dpg.group(tag=SHELL_STATUS_BAR, horizontal=False, show=False, parent=WIN_TAG):
        dpg.add_separator()
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit):
            dpg.add_table_column(width_stretch=True, init_width_or_weight=1.0)
            dpg.add_table_column(width_fixed=True)
            with dpg.table_row():
                dpg.add_text("", tag=SHELL_STATUS)
                dpg.add_text("", tag=SHELL_STATUS_RIGHT, color=COL_DIM)

    # File → Open File… dialog (all-files filter + dict payload).
    dpg.add_file_dialog(
        tag=DLG_OPEN,
        directory_selector=False,
        show=False,
        modal=True,
        file_count=1,
        width=560,
        height=400,
        default_path=os.getcwd(),
        callback=lambda _s, a: _file_picked(app, a),
        cancel_callback=lambda *_a: None,
    )
    add_all_files_filter(DLG_OPEN)

    with dpg.handler_registry(tag="gui-global-keys"):
        dpg.add_key_press_handler(key=dpg.mvKey_Q, callback=_on_quit_key(app))
        dpg.add_key_press_handler(key=dpg.mvKey_O, callback=_on_open_key(app))
        dpg.add_key_press_handler(
            key=dpg.mvKey_F1, callback=lambda *_a: show_shortcuts(app)
        )
        zoom_in = _on_ctrl(lambda: app.set_zoom(app.zoom + ZOOM_STEP))
        zoom_out = _on_ctrl(lambda: app.set_zoom(app.zoom - ZOOM_STEP))
        # "=" under both spellings (see const.KEY_EQUAL) + the keypad keys.
        for key in {KEY_EQUAL, dpg.mvKey_Plus, dpg.mvKey_Add}:
            dpg.add_key_press_handler(key=key, callback=zoom_in)
        for key in (dpg.mvKey_Minus, dpg.mvKey_Subtract):
            dpg.add_key_press_handler(key=key, callback=zoom_out)
        dpg.add_key_press_handler(
            key=dpg.mvKey_0, callback=_on_ctrl(lambda: app.set_zoom(1.0))
        )

    _refresh_recent_menu(app)
    _refresh_window_menu(app)
    _refresh_slice_ui(app)
    return WIN_TAG


def run_gui() -> None:
    """Create the context, build the shell, and run the frame loop.

    Each frame: drain worker events into their owning tools, then render.
    """
    if dpg is None:  # pragma: no cover - main() checks first
        raise RuntimeError(
            "dearpygui is not installed — run: uv pip install 'cheerleader[gui]'"
        )
    dpg.create_context()
    try:
        # Callbacks run on the main thread, inside the frame loop below.
        dpg.configure_app(manual_callback_management=True)
        app = GuiApp()
        window = build_shell(app)
        dpg.create_viewport(
            title=VIEWPORT_TITLE,
            width=VIEWPORT_W,
            height=VIEWPORT_H,
            min_width=MIN_VIEWPORT_W,
            min_height=MIN_VIEWPORT_H,
            disable_close=True,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window(window, True)
        while dpg.is_dearpygui_running():
            dpg.run_callbacks(dpg.get_callback_queue())
            for event in app.bridge.drain():
                app.dispatch(event)
            app.tick()
            dpg.render_dearpygui_frame()
    finally:
        dpg.destroy_context()
