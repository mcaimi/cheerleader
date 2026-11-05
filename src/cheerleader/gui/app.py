"""
    Dear PyGui application shell for the cheerleader hex UI.

    Owns the DPG context, viewport, theme, monospace font, and the frame
    loop::

        while running:
            for event in bridge.drain():      # worker results → controller
                dispatch(event)
            app.tick()                        # per-frame widget maintenance
            dpg.render_dearpygui_frame()

    Threading model: controller state is owned by the main frame loop. DPG
    widget callbacks run inside ``render_dearpygui_frame()`` on the main thread,
    so callbacks may mutate plain controller state directly; worker threads only touch 
    :class:`~cheerleader.gui.workers.UiBridge`.

    The viewport is created with ``disable_close=True``: DPG offers no way to veto
    the native close button, so it is removed and *every* quit path goes through
    :meth:`GuiApp.request_quit` (Ctrl+Q); with unsaved edits the editor
    controller shows a confirmation modal first.
"""

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple, Protocol

from cheerleader.gui.hexgrid import HexGrid
from cheerleader.gui.workers import UiBridge, UiEvent
from cheerleader.libs.hexdoc import HexDocument

if TYPE_CHECKING:  # pragma: no cover - import cycle guard only
    from cheerleader.gui.editor import EditorController

# import settings
from cheerleader.gui.const import *

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


def set_status(text: str) -> None:
    """Update the status strip text."""
    dpg.set_value(STATUS_TAG, text)


class Shell(NamedTuple):
    """Handles of the persistent UI chrome."""

    window: int | str
    content: int | str
    status: int | str


class Controller(Protocol):
    """What the app shell needs from the active phase controller."""

    def on_event(self, event: UiEvent) -> None: ...

    def request_quit(self) -> None: ...


class GuiApp:
    """
        Application state shared by all GUI controllers.

        The active controller is installed via :meth:`set_controller`; the frame 
        loop forwards drained worker events to its ``on_event``. Per-frame widget
        maintenance (the hex grid's repaint/relayout) goes through :meth:`set_tick` / :meth:`tick`.
    """

    def __init__(self) -> None:
        self.bridge = UiBridge()
        self.dirty = False
        self.grid: HexGrid | None = None
        self.controller: Controller | None = None
        self._tick: Callable[[], None] | None = None

    def set_controller(self, controller: Controller | None) -> None:
        """Install the active controller (``None`` detaches)."""
        self.controller = controller

    def dispatch(self, event: UiEvent) -> None:
        if self.controller is not None:
            self.controller.on_event(event)

    def set_tick(self, on_tick: Callable[[], None] | None) -> None:
        """Install the per-frame maintenance callable (``None`` detaches)."""
        self._tick = on_tick

    def tick(self) -> None:
        """Run per-frame widget maintenance (no-op without a handler)."""
        if self._tick is not None:
            self._tick()

    def request_quit(self) -> None:
        """
            Quit the app via the active controller.

            The viewport is created with ``disable_close=True`` so every quit
            path (Ctrl+Q, dialogs) funnels through here; with unsaved edits the
            editor controller shows the confirmation modal first.
        """
        if self.controller is not None:
            self.controller.request_quit()
            return
        dpg.stop_dearpygui()


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

def _placeholder_content(binary: str | None, hex_compare: str | None) -> None:
    dpg.add_spacer(height=12)
    if binary is None:
        dpg.add_text("cheerleader-gui")
        dpg.add_text("usage:  cheerleader-gui FILE  [--hex-compare TARGET]")
        return
    dpg.add_text(os.path.basename(binary))
    dpg.add_text(binary, color=(140, 140, 148))
    dpg.add_text(human_size(os.path.getsize(binary)))
    if hex_compare is not None:
        dpg.add_spacer(height=6)
        dpg.add_text(f"⇄ {os.path.basename(hex_compare)}")
        dpg.add_text(hex_compare, color=(140, 140, 148))
    dpg.add_spacer(height=6)

def _on_quit_key(app: GuiApp):
    """Global Ctrl+Q handler factory (Q alone would clash with popup typing)."""

    def _handler(_sender, _app_data) -> None:
        if dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl):
            app.request_quit()

    return _handler

def build_shell(app: GuiApp, *, binary: str | None, hex_compare: str | None) -> Shell:
    """Create the persistent chrome: theme, font, primary window, status bar."""
    _build_theme()
    _build_font()

    grid: HexGrid | None = None
    controller: EditorController | None = None
    with dpg.window(tag=WIN_TAG):
        with dpg.child_window(tag=CONTENT_TAG, border=False, height=-STATUS_RESERVE):
            if binary is not None and hex_compare is None:
                doc = HexDocument(binary)
                grid = HexGrid(doc)
                grid.build(parent=CONTENT_TAG)
                app.grid = grid
                app.set_tick(grid.tick)
                # Lazy import: this module must stay importable on its own
                # (the editor imports this module's helpers).
                from cheerleader.gui.editor import EditorController

                controller = EditorController(app, grid, doc)
                app.set_controller(controller)
            else:
                _placeholder_content(binary, hex_compare)
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_text(_status_line(binary, hex_compare), tag=STATUS_TAG)

    if controller is not None:
        # The controller owns the status line from here on (cursor offset,
        # byte value, unsaved count, notes); show the full line from the start.
        controller.update_status()

    with dpg.handler_registry(tag="gui-global-keys"):
        dpg.add_key_press_handler(key=dpg.mvKey_Q, callback=_on_quit_key(app))

    return Shell(window=WIN_TAG, content=CONTENT_TAG, status=STATUS_TAG)


def _file_entry(binary: str, hex_compare: str | None) -> str:
    entry = f"{os.path.basename(binary)} — {human_size(os.path.getsize(binary))}"
    if hex_compare is not None:
        entry += (
            f"  ⇄  {os.path.basename(hex_compare)}"
            f" ({human_size(os.path.getsize(hex_compare))})"
        )
    return entry


def _status_line(binary: str | None, hex_compare: str | None) -> str:
    parts: list[str] = []
    if binary is not None:
        parts.append(_file_entry(binary, hex_compare))
    parts.append("Ctrl+Q quit")
    return "  │  ".join(parts)


def run_gui(*, binary: str | None = None, hex_compare: str | None = None) -> None:
    """Create the context, build the shell, and run the frame loop.

    Each frame: drain worker events into the active controller, then render.
    """
    if dpg is None:  # pragma: no cover - main() checks first
        raise RuntimeError(
            "dearpygui is not installed — run: uv pip install 'cheerleader[gui]'"
        )
    dpg.create_context()
    try:
        app = GuiApp()
        shell = build_shell(app, binary=binary, hex_compare=hex_compare)
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
        dpg.set_primary_window(shell.window, True)
        while dpg.is_dearpygui_running():
            for event in app.bridge.drain():
                app.dispatch(event)
            app.tick()
            dpg.render_dearpygui_frame()
    finally:
        dpg.destroy_context()
