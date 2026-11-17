"""
Shared plumbing for the Dear PyGui tool windows.

Every tool (hex editor, compare, the inspector views, call flow, CFG) is a
floating window with a content child and a status strip, owned by a
controller implementing :class:`Controller` and registered with the shell
as a :class:`Tool`. :func:`build_tool_window` creates that skeleton,
:func:`find_tool` / :func:`focus_tool` implement the "focus the existing
window instead of opening a duplicate" guard, and :class:`ReadOnlyController`
carries the protocol boilerplate shared by the read-only views.

This module sits *below* :mod:`cheerleader.gui.app`: it never imports the
shell at runtime, so the shell can import every tool module at load time.

Key scoping: DPG key handlers live in *global* handler registries, so a
key press reaches every registered tool. :func:`focus_guard` wraps a
handler so it only runs while its tool's window is the active one — DPG
records the focused window (or focused child window) as the context's
``activeWindow``, and two items belong to the same tool when they share a
top-level window.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

from cheerleader.gui.const import STATUS_RESERVE, TOOL_MIN_H, TOOL_MIN_W

if TYPE_CHECKING:
    from cheerleader.gui.app import GuiApp
    from cheerleader.gui.workers import UiEvent

try:
    import dearpygui.dearpygui as dpg
except ImportError:  # pragma: no cover - gui/__init__ prints the hint first
    dpg = None  # type: ignore[assignment]

# DPG reports missing items with a bare ``Exception`` (error 1005).
_STATE_ERRORS = (Exception,)


def _uuid(item: int | str | None) -> int:
    """Numeric UUID for an alias or UUID (0 for ``None`` / unknown alias)."""
    if item is None:
        return 0
    return dpg.get_alias_id(item) if isinstance(item, str) else int(item)


def _top_window(item: int | str) -> int | None:
    """UUID of the root (top-level window) *item* lives in, or ``None``."""
    try:
        uuid = _uuid(item)
        while uuid:
            # get_item_parent answers with the parent's alias when it has one.
            parent = _uuid(dpg.get_item_parent(uuid))
            if not parent:
                return uuid
            uuid = parent
        return None  # unknown alias
    except _STATE_ERRORS:
        return None  # deleted / never existed


def window_has_focus(item: int | str) -> bool:
    """True when the active (focused) window is *item*'s top-level window."""
    try:
        active = dpg.get_active_window()
    except _STATE_ERRORS:
        return False
    if not active or not dpg.does_item_exist(item):
        return False
    top = _top_window(item)
    return top is not None and _top_window(active) == top


def focus_guard(
    item: int | str,
    callback: Callable[..., None],
    *,
    also: Callable[[], bool] | None = None,
) -> Callable[..., None]:
    """Wrap a key-handler *callback* to run only while *item*'s window has
    focus (or while *also* returns True — e.g. "my own modal is open")."""

    def _guarded(*args) -> None:
        if window_has_focus(item) or (also is not None and also()):
            callback(*args)

    return _guarded


def ctrl_down() -> bool:
    """Either Control key held."""
    return dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)


def install_keys(
    tag: str,
    window: int | str,
    bindings: Iterable[tuple[int, Callable[..., None]]],
    *,
    also: Callable[[], bool] | None = None,
) -> None:
    """Register *bindings* (key code → callback) in a handler registry
    tagged *tag*, each focus-guarded to *window* (see :func:`focus_guard`)."""
    with dpg.handler_registry(tag=tag):
        for key, callback in bindings:
            dpg.add_key_press_handler(
                key=key, callback=focus_guard(window, callback, also=also)
            )


# A menu entry: (label, shortcut text, callback); ``None`` = separator.
MenuEntry = tuple[str, str, Callable[[], None]] | None


def add_tool_menu(
    window: int | str,
    menus: Iterable[tuple[str, Iterable[MenuEntry]] | tuple[str, str, Callable]],
) -> None:
    """Give tool *window* a menu bar.

    Each element is either ``(menu label, entries)`` — a drop-down menu —
    or a bare entry ``(label, shortcut, callback)`` placed directly in the
    bar, where it acts as a button. Shortcut texts are display-only (the
    tool's key registry does the binding).
    """
    bar = dpg.add_menu_bar(parent=window)
    for menu in menus:
        if len(menu) == 3:  # bare bar item
            label, shortcut, callback = menu
            _add_entry(bar, label, shortcut, callback)
            continue
        label, entries = menu
        with dpg.menu(label=label, parent=bar) as m:
            for entry in entries:
                if entry is None:
                    dpg.add_separator(parent=m)
                else:
                    _add_entry(m, *entry)


def _add_entry(parent, label: str, shortcut: str, callback: Callable[[], None]):
    dpg.add_menu_item(
        label=label,
        shortcut=shortcut,
        parent=parent,
        callback=lambda *_a: callback(),
    )


def delete_if_exists(tag: int | str) -> None:
    if dpg.does_item_exist(tag):
        dpg.delete_item(tag)


def arch_suffix(info) -> str:
    """Window-title suffix naming the slice of a fat binary (``" [arm64e]"``).

    Only fat-file slices start past offset 0, so thin files get no suffix.
    """
    return f" [{info.arch}]" if info is not None and info.slice_offset else ""


def human_size(size: int) -> str:
    """Human-readable byte size: '300 bytes', '1.0 KiB', '336.5 MiB'."""
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.1f} GiB"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MiB"
    if size >= 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size} bytes"


# ── tools ─────────────────────────────────────────────────────────────────────


class Controller(Protocol):
    """What the shell needs from a tool's controller."""

    def on_event(self, event: UiEvent) -> None: ...

    def dirty_edit_count(self) -> int: ...

    def window_close_requested(self) -> None: ...

    def shutdown(self) -> None: ...


@dataclass(eq=False)
class Tool:
    """One open floating tool window and its parts."""

    window: int | str
    status_tag: int | str
    controller: Controller
    grids: tuple[Any, ...]  # HexGrid / DisasmGrid: shut down with the tool
    tick: Callable[[], None] | None


class ToolWindow(NamedTuple):
    """Tags of a freshly built tool-window skeleton."""

    ns: str  # per-tool namespace for every tag/registry the tool creates
    window: str
    content: str  # child window above the status strip
    status_tag: str


def build_tool_window(app: GuiApp, title: str, width: int, height: int) -> ToolWindow:
    """Create the shared tool-window skeleton: a floating window holding a
    content child (filling all but the status reserve), a separator and the
    status strip. The window's X routes to the owning controller."""
    ns = app.next_ns()
    window = f"gui-tool-{ns}"
    content = f"{window}-content"
    status_tag = f"{window}-status"
    with dpg.window(
        tag=window,
        label=title,
        width=width,
        height=height,
        pos=app.next_window_pos(),  # cascade: new windows never stack exactly
        min_size=[TOOL_MIN_W, TOOL_MIN_H],
        no_collapse=True,
        on_close=lambda *_a: tool_window_closed(app, window),
    ):
        dpg.add_child_window(tag=content, border=False, height=-STATUS_RESERVE)
        dpg.add_separator()
        dpg.add_text("", tag=status_tag)
    return ToolWindow(ns, window, content, status_tag)


def tool_window_closed(app: GuiApp, window: int | str) -> None:
    """``on_close`` of a tool window (X clicked): the controller decides."""
    for tool in app.tools:
        if tool.window == window:
            tool.controller.window_close_requested()
            return


def find_tool(app: GuiApp, **attrs: Any) -> Tool | None:
    """The first open tool whose controller matches every ``attr=value``.

    The focus-guard lookup, e.g. ``find_tool(app, view_kind="segments",
    path=p)``; controllers lacking an attribute never match.
    """
    missing = object()
    for tool in app.tools:
        ctl = tool.controller
        if all(getattr(ctl, k, missing) == v for k, v in attrs.items()):
            return tool
    return None


def find_editor(app: GuiApp, path: str) -> Tool | None:
    """The open hex editor of *path*, if any (editors expose ``doc``)."""
    for tool in app.tools:
        doc = getattr(tool.controller, "doc", None)
        if doc is not None and doc.path == path:
            return tool
    return None


def focus_tool(tool: Tool) -> None:
    """Re-show (a vetoed close may have hidden it) and focus *tool*."""
    dpg.configure_item(tool.window, show=True)
    dpg.focus_item(tool.window)


class ReadOnlyController:
    """Shell-protocol boilerplate for the read-only views.

    Never dirty (quit needs no confirmation), the window's X closes the
    tool, worker events are ignored unless a subclass overrides
    :meth:`on_event`, and :meth:`shutdown` cancels the in-flight worker (if
    the subclass tracks one in ``busy``) and deletes the key registry
    installed via :meth:`install_keys` (tag ``{keys_prefix}-keys-{ns}``).
    """

    keys_prefix = "view"
    app: GuiApp
    window: int | str
    ns: str

    @property
    def keys_tag(self) -> str:
        return f"{self.keys_prefix}-keys-{self.ns}"

    def install_keys(self, bindings: Iterable[tuple[int, Callable[..., None]]]) -> None:
        install_keys(self.keys_tag, self.window, bindings)

    def on_event(self, event: UiEvent) -> None:
        """No workers by default — nothing to receive."""

    def dirty_edit_count(self) -> int:
        """Read-only view — never dirty."""
        return 0

    def window_close_requested(self, *_args) -> None:
        self.app.close_tool_for(self)

    def shutdown(self) -> None:
        busy = getattr(self, "busy", None)
        if busy is not None and busy.worker is not None:
            busy.worker.cancel.cancel()
        delete_if_exists(self.keys_tag)
