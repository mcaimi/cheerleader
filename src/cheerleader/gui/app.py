"""
Dear PyGui application shell for the cheerleader hex UI.

``cheerleader-gui`` takes no CLI switches: launching shows an empty shell
window with a **File** / **Tools** menu bar. ``File → Open File…`` (Ctrl+O)
loads a file; ``Tools → Hex Editor…`` — grayed out until a file is loaded —
displays it in a floating editor window, and ``Tools → Hex Compare…`` opens
a two-file picker (Source/Target). Tools show in **floating windows** —
several may be open at once, each with its own status strip.

Frame loop::

    while running:
        for event in bridge.drain():      # worker results → owning tool
            dispatch(event)               # routed by worker identity
        app.tick()                        # every tool's per-frame maintenance
        dpg.render_dearpygui_frame()

Threading model: tool/controller state is owned by the main frame loop. DPG
widget callbacks run inside ``render_dearpygui_frame()`` on the main thread;
worker threads only touch :class:`~cheerleader.gui.workers.UiBridge`.

Quit paths: the viewport is created with ``disable_close=True`` (no close
veto exists) and tool windows route their X through
``Controller.window_close_requested`` (confirm + reshow-veto when dirty);
Ctrl+Q funnels through :meth:`GuiApp.request_quit` — one aggregate confirm
when any tool has unsaved edits.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from cheerleader.gui.const import (
    COMPARE_WIN_H,
    COMPARE_WIN_W,
    DLG_OPEN,
    EDITOR_WIN_H,
    EDITOR_WIN_W,
    FONT_CANDIDATES,
    FONT_ENV_VAR,
    FONT_SIZE,
    MENU_TAG,
    MI_EDITOR,
    MIN_VIEWPORT_H,
    MIN_VIEWPORT_W,
    SHELL_FILL,
    SHELL_STATUS,
    SHELL_STATUS_BAR,
    SLIDER_RESERVE,
    STATUS_RESERVE,
    TOOL_MIN_H,
    TOOL_MIN_W,
    VIEWPORT_H,
    VIEWPORT_TITLE,
    VIEWPORT_W,
    WIN_TAG,
)
from cheerleader.gui.dialogs import (
    CompareOpenDialog,
    ConfirmDialog,
    ErrorPopup,
    add_all_files_filter,
    picked_path,
)
from cheerleader.gui.hexgrid import HexGrid
from cheerleader.gui.workers import UiBridge, UiEvent
from cheerleader.libs.hexdoc import HexDocument

try:
    import dearpygui.dearpygui as dpg
except ImportError:  # pragma: no cover - main() prints the install hint first
    dpg = None  # type: ignore[assignment]


def human_size(size: int) -> str:
    """Human-readable byte size: '300 bytes', '1.0 KiB', '336.5 MiB'."""
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.1f} GiB"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MiB"
    if size >= 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size} bytes"


class Controller(Protocol):
    """What the shell needs from a tool's controller."""

    def on_event(self, event: UiEvent) -> None: ...

    def dirty_edit_count(self) -> int: ...

    def window_close_requested(self) -> None: ...

    def shutdown(self) -> None: ...


@dataclass(eq=False)
class Tool:
    """One open floating tool window (editor or compare) and its parts."""

    window: int | str
    status_tag: int | str
    controller: Controller
    grids: tuple[HexGrid, ...]
    tick: Callable[[], None] | None


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

    def next_ns(self) -> str:
        """Unique per-app namespace for a new tool's tags/registries."""
        return f"t{next(self._ns_counter)}"

    def dispatch(self, event: UiEvent) -> None:
        """Route a worker event to the tool whose busy worker posted it.

        Strict worker identity: with several tools alive, broadcasting would
        leak one tool's generations into another's; events of tools closed
        mid-flight (and handle-less events) are dropped.
        """
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


def _find_font_file() -> str | None:
    override = os.environ.get(FONT_ENV_VAR, "")
    for path in (override, *FONT_CANDIDATES):
        if path and os.path.isfile(path):
            return path
    return None


def _build_theme() -> None:
    """Compact dark theme in the spirit of the TUI."""
    with dpg.theme() as theme, dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (18, 18, 22, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (28, 28, 34, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (38, 38, 46, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Text, (216, 216, 222, 255))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (30, 30, 37, 255))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (38, 38, 46, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Button, (44, 44, 54, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (58, 58, 70, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (72, 72, 86, 255))
        dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (68, 120, 190, 255))
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 6, 4)
    dpg.bind_theme(theme)


def _build_font() -> None:
    """
    Bind the first available monospace font; skip on any failure.

    The built-in DPG font is fixed-width, so the hex grid stays aligned even
    when no candidate font exists on the system.
    """
    path = _find_font_file()
    if path is None:
        return
    try:
        with dpg.font_registry():
            font = dpg.add_font(path, FONT_SIZE)
        dpg.bind_font(font)
    except (SystemError, RuntimeError, OSError):
        # Corrupt or unsupported font file — the default font is fine.
        pass


def _on_quit_key(app: GuiApp):
    """Global Ctrl+Q handler factory (Q alone would clash with popup typing)."""

    def _handler(_sender, _app_data) -> None:
        if dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl):
            app.request_quit()

    return _handler


def _on_open_key(app: GuiApp):
    """Global Ctrl+O handler: show the File → Open File… dialog."""

    def _handler(_sender, _app_data) -> None:
        if dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl):
            dpg.configure_item(DLG_OPEN, show=True)

    return _handler


def _tool_window_closed(app: GuiApp, window: int | str) -> None:
    """``on_close`` of a tool window (X clicked): the controller decides."""
    for tool in app.tools:
        if tool.window == window:
            tool.controller.window_close_requested()
            return


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
    for tool in app.tools:
        existing = getattr(tool.controller, "doc", None)
        if existing is not None and existing.path == path:
            doc.close()
            dpg.configure_item(tool.window, show=True)
            dpg.focus_item(tool.window)
            return
    ns = app.next_ns()
    window = f"gui-tool-{ns}"
    content = f"{window}-content"
    status_tag = f"{window}-status"
    with dpg.window(
        tag=window,
        label=f"hex editor — {os.path.basename(path)}",
        width=EDITOR_WIN_W,
        height=EDITOR_WIN_H,
        min_size=[TOOL_MIN_W, TOOL_MIN_H],
        no_collapse=True,
        on_close=lambda *_a: _tool_window_closed(app, window),
    ):
        dpg.add_child_window(tag=content, border=False, height=-STATUS_RESERVE)
        dpg.add_separator()
        dpg.add_text("", tag=status_tag)
    grid = HexGrid(doc, name=ns)
    grid.build(content)
    # Lazy import: this module must stay importable on its own (the editor
    # imports this module's helpers).
    from cheerleader.gui.editor import EditorController

    controller = EditorController(
        app, grid, doc, window=window, status_tag=status_tag, ns=ns
    )
    controller.update_status()
    app.tools.append(
        Tool(
            window=window,
            status_tag=status_tag,
            controller=controller,
            grids=(grid,),
            tick=grid.tick,
        )
    )


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
    ns = app.next_ns()
    window = f"gui-tool-{ns}"
    content = f"{window}-content"
    status_tag = f"{window}-status"
    with dpg.window(
        tag=window,
        label=(
            f"hex compare — {os.path.basename(left_path)}"
            f" ⇄ {os.path.basename(right_path)}"
        ),
        width=COMPARE_WIN_W,
        height=COMPARE_WIN_H,
        min_size=[TOOL_MIN_W, TOOL_MIN_H],
        no_collapse=True,
        on_close=lambda *_a: _tool_window_closed(app, window),
    ):
        dpg.add_child_window(tag=content, border=False, height=-STATUS_RESERVE)
        dpg.add_separator()
        dpg.add_text("", tag=status_tag)
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
    # Lazy import (compare imports this module's helpers).
    from cheerleader.gui.compare import CompareController

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
    app.tools.append(
        Tool(
            window=window,
            status_tag=status_tag,
            controller=controller,
            grids=(left_grid, right_grid),
            tick=controller.tick,
        )
    )


def _show_dialog(tag: int | str):
    """Menu-item callback factory: show a pre-built file dialog."""

    def _open(*_args) -> None:
        dpg.configure_item(tag, show=True)

    return _open


def _file_picked(app: GuiApp, app_data) -> None:
    """File → Open File… pick: validate and hold as the shell's current file.

    The file is *loaded* here (a `HexDocument` is constructed to fail fast
    on vanished/unreadable paths, then released) — the editor itself is
    displayed only when Tools → Hex Editor… is clicked, which stays grayed
    out until a file has been picked successfully.
    """
    path = picked_path(app_data)
    if not path:
        return  # OK with an empty selection — treat like a cancel
    try:
        doc = HexDocument(path)
    except OSError as e:
        ErrorPopup(f"Cannot open {path}:\n{e}")
        return
    doc.close()
    app.current_file = path
    dpg.configure_item(MI_EDITOR, enabled=True)
    dpg.configure_item(SHELL_STATUS_BAR, show=True)
    dpg.set_value(SHELL_STATUS, path)


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


def _compare_dialog_opener(app: GuiApp):
    """Menu-item callback factory: open the two-file compare picker."""

    def _open(*_args) -> None:
        CompareOpenDialog(on_launch=lambda src, tgt: open_compare(app, src, tgt)).open()

    return _open


def build_shell(app: GuiApp) -> int | str:
    """Create the persistent chrome: theme, font, shell window + Tools menu.

    The shell itself is empty (menu bar only); tools open in floating
    windows. The open-file dialogs are pre-built top-level items, shown by
    the menu entries.
    """
    _build_theme()
    _build_font()
    with dpg.window(tag=WIN_TAG), dpg.menu_bar(tag=MENU_TAG):
        with dpg.menu(label="File"):
            dpg.add_menu_item(label="Open File…", callback=_show_dialog(DLG_OPEN))
            dpg.add_menu_item(label="Quit", callback=app.request_quit)
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

    # Bottom bar: hidden until a file is loaded via File → Open File…; the
    # filler child pins the bar to the bottom of the (resizable) window.
    # (Explicit parents: the context stack is empty outside the with block.)
    dpg.add_child_window(
        tag=SHELL_FILL, parent=WIN_TAG, border=False, height=-STATUS_RESERVE
    )
    with dpg.group(tag=SHELL_STATUS_BAR, horizontal=False, show=False, parent=WIN_TAG):
        dpg.add_separator()
        dpg.add_text("", tag=SHELL_STATUS)

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
            for event in app.bridge.drain():
                app.dispatch(event)
            app.tick()
            dpg.render_dearpygui_frame()
    finally:
        dpg.destroy_context()
