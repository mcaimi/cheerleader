"""Headless tests for the menu-driven shell.

DPG context creation, item construction, themes and fonts do not need a
display — only the viewport does. These tests build the empty shell inside a
throwaway context, drive the ``open_editor`` / ``open_compare`` flows and
tear everything down again. Background workers are joined explicitly; their
UiBridge events are drained + dispatched by hand, mirroring the frame loop.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg

from cheerleader.gui.app import (
    DISASM_ITEMS,
    DLG_OPEN,
    MENU_TAG,
    MI_DISASM,
    MI_EDITOR,
    MI_SEGMENTS,
    SHELL_STATUS,
    SHELL_STATUS_BAR,
    WIN_TAG,
    GuiApp,
    build_shell,
    find_inspector_tool,
    open_compare,
    open_editor,
)
from cheerleader.gui.workers import EventKind, UiEvent

DATA = bytes(range(256)) * 16  # 4 KiB


@pytest.fixture()
def dpg_context():
    dpg.create_context()
    try:
        yield
    finally:
        dpg.destroy_context()


def _error_popups() -> list[str]:
    """Tags of the ErrorPopup windows currently alive (unique per instance)."""
    return [
        alias
        for w in dpg.get_windows()
        for alias in (dpg.get_item_alias(w),)
        if alias.startswith("gui-error-")
    ]


def _popup_message(tag: str) -> str:
    """An ErrorPopup's message (its first child is the wrapped text)."""
    return dpg.get_value(dpg.get_item_children(tag)[1][0])


def _menu_labels() -> list[str]:
    items = dpg.get_item_children(MENU_TAG)[1]  # slot 1: the "Tools" menu
    labels: list[str] = []
    for menu in items:
        labels.append(dpg.get_item_label(menu))
        for item in dpg.get_item_children(menu)[1]:
            labels.append(dpg.get_item_label(item))
    return labels


# ── shell ─────────────────────────────────────────────────────────────────────


def _menu_item(label: str):
    """Find a menu item by label across all menus (None when absent)."""
    for menu in dpg.get_item_children(MENU_TAG)[1]:
        for item in dpg.get_item_children(menu)[1]:
            if dpg.get_item_label(item) == label:
                return item
    return None


def _macho_minimal() -> bytes:
    """A structurally valid, empty 64-bit Mach-O (arm64, no load commands)."""
    import struct

    return b"\xcf\xfa\xed\xfe" + struct.pack(
        "<IIIIIII",
        0x0100000D,  # cputype: arm64
        0,  # cpusubtype
        2,  # filetype: MH_EXECUTE
        0,  # ncmds
        0,  # sizeofcmds
        0,  # flags
        0,  # reserved
    )


def _pick_file(app: GuiApp, path) -> None:
    """Drive File → Open File… and settle its background parse."""
    dpg.get_item_callback(DLG_OPEN)(DLG_OPEN, {"selections": {"file_1": str(path)}})
    worker = app._parse_worker
    if worker is not None and worker.thread is not None:
        worker.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        app.dispatch(event)


def test_build_shell_minimal(dpg_context) -> None:
    app = GuiApp()
    build_shell(app)
    assert dpg.does_item_exist(WIN_TAG)
    assert dpg.does_item_exist(MENU_TAG)
    assert _menu_labels() == [
        "File",
        "Open File…",
        "Quit",
        "Disassembler",
        "Disassembly…",
        "Segments…",
        "Sections…",
        "Imports…",
        "Symbols…",
        "Exports…",
        "Fixups…",
        "Tools",
        "Hex Editor…",
        "Hex Compare…",
        "About",
        "Info"
    ]
    # File → Open File… dialog exists, hidden until the menu shows it.
    assert dpg.does_item_exist(DLG_OPEN)
    assert dpg.get_item_configuration(DLG_OPEN)["show"] is False
    # The compare picker owns its two file dialogs privately.
    # Hex Editor… starts grayed out — no file loaded yet.
    assert dpg.get_item_configuration(MI_EDITOR)["enabled"] is False
    assert app.current_file is None
    # Every Disassembler item starts grayed out — no binary parsed yet.
    for tag in DISASM_ITEMS:
        assert dpg.get_item_configuration(tag)["enabled"] is False
    assert app.current_info is None
    # Bottom bar exists but stays hidden until a file is loaded.
    assert dpg.does_item_exist(SHELL_STATUS_BAR)
    assert dpg.get_item_configuration(SHELL_STATUS_BAR)["show"] is False
    # Global key registry: Ctrl+Q quit + Ctrl+O open.
    assert dpg.does_item_exist("gui-global-keys")
    assert len(dpg.get_item_children("gui-global-keys")[1]) == 2
    assert app.tools == []


def test_next_ns_counter(dpg_context) -> None:
    app = GuiApp()
    assert [app.next_ns() for _ in range(3)] == ["t1", "t2", "t3"]


def test_compare_menu_item_opens_picker(dpg_context) -> None:
    """Tools → Hex Compare… opens the two-file picker, not a file dialog."""
    app = GuiApp()
    build_shell(app)
    item = _menu_item("Hex Compare…")
    assert item is not None
    dpg.get_item_callback(item)()
    pickers = [
        alias
        for w in dpg.get_windows()
        if re.fullmatch(r"gui-dlg-cmp-\d+", alias := dpg.get_item_alias(w))
    ]
    assert len(pickers) == 1
    # The picker owns its two private file dialogs and is shown open.
    assert dpg.does_item_exist(f"{pickers[0]}-src-dlg")
    assert dpg.does_item_exist(f"{pickers[0]}-tgt-dlg")
    assert dpg.get_item_configuration(pickers[0])["show"] is True


# ── open flows ────────────────────────────────────────────────────────────────


def test_open_file_flow_enables_then_opens_editor(dpg_context, tmp_path) -> None:
    """File → Open File… loads; Tools → Hex Editor… (now enabled) displays."""
    path = tmp_path / "sample.bin"
    path.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    # Grayed-out item is inert even if something fired it.
    dpg.get_item_callback(MI_EDITOR)()
    assert app.tools == []
    # Pick a file exactly as the file dialog's OK would.
    dpg.get_item_callback(DLG_OPEN)(DLG_OPEN, {"selections": {"file_1": str(path)}})
    assert app.current_file == str(path)
    assert dpg.get_item_configuration(MI_EDITOR)["enabled"] is True
    # Bottom bar appears with the loaded file's full path.
    assert dpg.get_item_configuration(SHELL_STATUS_BAR)["show"] is True
    assert dpg.get_value(SHELL_STATUS) == str(path)
    assert app.tools == []  # loaded, not displayed yet
    dpg.get_item_callback(MI_EDITOR)()
    assert len(app.tools) == 1
    assert app.tools[0].controller.doc.path == str(path)
    # Clicking again focuses the existing editor (no second window).
    dpg.get_item_callback(MI_EDITOR)()
    assert len(app.tools) == 1


def test_open_file_missing_shows_error_and_stays_disabled(
    dpg_context, tmp_path
) -> None:
    app = GuiApp()
    build_shell(app)
    dpg.get_item_callback(DLG_OPEN)(
        DLG_OPEN, {"selections": {"file_1": str(tmp_path / "vanished.bin")}}
    )
    assert app.current_file is None
    assert dpg.get_item_configuration(MI_EDITOR)["enabled"] is False
    assert dpg.get_item_configuration(SHELL_STATUS_BAR)["show"] is False
    assert len(_error_popups()) == 1


def test_open_file_updates_bottom_bar(dpg_context, tmp_path) -> None:
    """A second Open File… pick replaces the bar's path (and the editor
    item's target); an editor opened for the first file stays open."""
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    for p in (a, b):
        p.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    pick = lambda p: dpg.get_item_callback(DLG_OPEN)(
        DLG_OPEN, {"selections": {"file_1": str(p)}}
    )
    pick(a)
    dpg.get_item_callback(MI_EDITOR)()
    assert len(app.tools) == 1
    pick(b)
    assert dpg.get_value(SHELL_STATUS) == str(b)
    assert app.current_file == str(b)
    assert len(app.tools) == 1  # the first editor window is untouched
    dpg.get_item_callback(MI_EDITOR)()  # now opens b in a second window
    assert len(app.tools) == 2
    assert app.tools[0].controller.doc.path == str(a)  # old editor intact
    assert app.tools[1].controller.doc.path == str(b)


def test_open_editor_flow(dpg_context, tmp_path) -> None:
    path = tmp_path / "sample.bin"
    path.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(path))
    assert len(app.tools) == 1
    tool = app.tools[0]
    assert tool.window == "gui-tool-t1"
    assert tool.status_tag == "gui-tool-t1-status"
    for tag in (tool.window, "gui-tool-t1-content", tool.status_tag):
        assert dpg.does_item_exist(tag)
    # Grid named after the tool namespace + its global input registry.
    assert dpg.does_item_exist("t1-hexgrid")
    assert dpg.does_item_exist("t1-hexgrid-input")
    assert dpg.does_item_exist("editor-keys-t1")
    status = dpg.get_value(tool.status_tag)
    assert "sample.bin" in status
    assert "4.0 KiB" in status
    assert "0x00000000" in status
    assert "Ctrl+Q quit" in status
    app.tick()  # grid maintenance runs without a display
    assert dpg.get_value("t1-hexgrid-r0-off") == "00000000  "


def test_open_editor_missing_file_shows_error_popup(dpg_context, tmp_path) -> None:
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(tmp_path / "vanished.bin"))
    assert app.tools == []
    assert len(_error_popups()) == 1


def test_open_editor_duplicate_focuses_existing(dpg_context, tmp_path) -> None:
    path = tmp_path / "sample.bin"
    path.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(path))
    tool = app.tools[0]
    open_editor(app, str(path))  # same file again → no second window
    assert len(app.tools) == 1
    assert app.tools[0] is tool
    assert dpg.get_item_configuration(tool.window)["show"] is True


def test_open_compare_flow(dpg_context, tmp_path) -> None:
    left = tmp_path / "left.bin"
    right = tmp_path / "right.bin"
    left.write_bytes(b"a" * 4096)
    right.write_bytes(b"a" * 4096)  # identical → clean scan result
    app = GuiApp()
    build_shell(app)
    open_compare(app, str(left), str(right))
    assert len(app.tools) == 1
    tool = app.tools[0]
    assert tool.window == "gui-tool-t1"
    # Dual panes + one scrollbar (the right pane's is hidden).
    for tag in ("t1-cmp-l-pane", "t1-cmp-r-pane"):
        assert dpg.does_item_exist(tag)
    assert dpg.get_item_configuration("t1-cmp-l-hexgrid-scroll")["show"] is True
    assert dpg.get_item_configuration("t1-cmp-r-hexgrid-scroll")["show"] is False
    assert tool.controller.busy.op == "comparing"
    tool.controller.busy.worker.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        tool.controller.on_event(event)
    assert "files are identical" in dpg.get_value(tool.status_tag)


def test_open_compare_missing_right_shows_error_popup(dpg_context, tmp_path) -> None:
    left = tmp_path / "left.bin"
    left.write_bytes(b"a")
    app = GuiApp()
    build_shell(app)
    open_compare(app, str(left), str(tmp_path / "missing.bin"))
    assert app.tools == []
    assert len(_error_popups()) == 1


def test_two_tools_get_distinct_namespaces(dpg_context, tmp_path) -> None:
    """Editors and compare windows coexist; every ns is unique."""
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    c = tmp_path / "c.bin"
    for p in (a, b, c):
        p.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(a))
    open_editor(app, str(b))
    open_compare(app, str(b), str(c))
    assert [t.window for t in app.tools] == [
        "gui-tool-t1",
        "gui-tool-t2",
        "gui-tool-t3",
    ]
    # Each editor has its own grid + key registry namespace.
    assert dpg.does_item_exist("t1-hexgrid")
    assert dpg.does_item_exist("t2-hexgrid")
    assert dpg.does_item_exist("t3-cmp-l-hexgrid")
    assert dpg.does_item_exist("editor-keys-t1")
    assert dpg.does_item_exist("editor-keys-t2")
    assert dpg.does_item_exist("compare-keys-t3")


# ── event routing + tick ──────────────────────────────────────────────────────


def _finish(app: GuiApp, tool) -> None:
    worker = tool.controller.busy.worker
    if worker is not None and worker.thread is not None:
        worker.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        app.dispatch(event)


def test_dispatch_routes_by_worker_identity(dpg_context, tmp_path) -> None:
    """A busy tool receives its worker's events; the idle sibling does not."""
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(DATA)
    b.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(a))
    open_editor(app, str(b))
    tool_a, tool_b = app.tools
    ctl_a = tool_a.controller
    ctl_a._pattern = b"\x0a\x0b"
    ctl_a._start_search(forward=True)
    worker_a = ctl_a.busy.worker
    app.dispatch(UiEvent(0, EventKind.PROGRESS, 0.5, worker_a))
    assert "Searching… 50%" in dpg.get_value(tool_a.status_tag)
    assert "Searching" not in dpg.get_value(tool_b.status_tag)
    ctl_a.busy.worker.cancel.cancel()
    worker_a.thread.join(timeout=10.0)
    app.bridge.drain()


def test_dispatch_drops_unowned_events(dpg_context, tmp_path) -> None:
    """Handle-less / foreign events are dropped, not broadcast."""
    path = tmp_path / "a.bin"
    path.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(path))
    tool = app.tools[0]
    before = dpg.get_value(tool.status_tag)
    app.dispatch(UiEvent(0, EventKind.PROGRESS, 0.5, None))
    assert dpg.get_value(tool.status_tag) == before
    app.dispatch(UiEvent(0, EventKind.PROGRESS, 0.5, object()))  # foreign
    assert dpg.get_value(tool.status_tag) == before


def test_dispatch_drops_events_of_closed_tool(dpg_context, tmp_path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(path))
    tool = app.tools[0]
    ctl = tool.controller
    ctl._pattern = b"\x0a\x0b"
    ctl._start_search(forward=True)
    worker = ctl.busy.worker
    app.close_tool(tool)  # teardown cancels + unregisters the worker
    assert worker.cancel.cancelled
    worker.thread.join(timeout=10.0)
    app.dispatch(UiEvent(0, EventKind.PROGRESS, 0.9, worker))  # no owner left
    assert app.tools == []


def test_tick_runs_every_tool(dpg_context, tmp_path) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(DATA)
    b.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(a))
    open_editor(app, str(b))
    app.tick()  # both grids maintain their pools without error
    assert dpg.get_value("t1-hexgrid-r0-off") == "00000000  "
    assert dpg.get_value("t2-hexgrid-r0-off") == "00000000  "


# ── quit (aggregate over tools) ───────────────────────────────────────────────


def test_quit_clean_stops_immediately(dpg_context, tmp_path, monkeypatch) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(path))
    stopped: list[bool] = []
    monkeypatch.setattr(dpg, "stop_dearpygui", lambda: stopped.append(True))
    app.request_quit()
    assert stopped == [True]
    assert app._quit_dlg is None


def test_quit_dirty_aggregates_all_tools(dpg_context, tmp_path, monkeypatch) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(DATA)
    b.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(a))
    open_editor(app, str(b))
    app.tools[0].controller.type_hex_digit(0xF)  # 1 edit
    app.tools[1].controller.grid.move_to(100)
    app.tools[1].controller.type_hex_digit(0xA)  # 1 edit at another offset
    app.tools[1].controller.type_hex_digit(0xB)
    stopped: list[bool] = []
    monkeypatch.setattr(dpg, "stop_dearpygui", lambda: stopped.append(True))
    app.request_quit()
    dlg = app._quit_dlg
    assert dlg is not None
    assert "2 unsaved edits" in dlg.message
    assert "quit without saving" in dlg.message
    app.request_quit()  # already confirming → no second dialog
    assert app._quit_dlg is dlg
    dlg._on_answer(False)
    assert stopped == []
    assert app._quit_dlg is None
    app.request_quit()
    app._quit_dlg._on_answer(True)
    assert stopped == [True]


def test_quit_compare_counts_zero(dpg_context, tmp_path, monkeypatch) -> None:
    """Compare is read-only — a lone compare window never blocks quitting."""
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"a" * 16)
    b.write_bytes(b"b" * 16)
    app = GuiApp()
    build_shell(app)
    open_compare(app, str(a), str(b))
    stopped: list[bool] = []
    monkeypatch.setattr(dpg, "stop_dearpygui", lambda: stopped.append(True))
    app.request_quit()
    assert stopped == [True]
    _finish(app, app.tools[0])


# ── teardown ──────────────────────────────────────────────────────────────────


def test_close_tool_removes_registries_and_window(dpg_context, tmp_path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(path))
    tool = app.tools[0]
    assert dpg.does_item_exist("editor-keys-t1")
    assert dpg.does_item_exist("t1-hexgrid-input")
    app.close_tool(tool)
    assert app.tools == []
    assert not dpg.does_item_exist(tool.window)
    assert not dpg.does_item_exist("editor-keys-t1")
    assert not dpg.does_item_exist("t1-hexgrid-input")


def test_close_tool_for_unknown_controller_is_noop(dpg_context, tmp_path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    open_editor(app, str(path))
    app.close_tool_for(SimpleNamespace())  # matches nothing → no-op
    assert len(app.tools) == 1


def test_error_popup_dismisses(dpg_context) -> None:
    from cheerleader.gui.dialogs import ErrorPopup

    popup = ErrorPopup("boom")
    assert dpg.does_item_exist(popup.TAG)
    popup.close()
    assert not dpg.does_item_exist(popup.TAG)


# ── disassembler shell plumbing (plan v3, D1) ─────────────────────────────────


def test_open_file_parses_binary_and_enables_disasm(dpg_context, tmp_path) -> None:
    """A supported binary parses in the background; the menu un-grays."""
    path = tmp_path / "macho.bin"
    path.write_bytes(_macho_minimal())
    app = GuiApp()
    build_shell(app)
    _pick_file(app, path)
    assert app.current_info is not None
    assert app.current_info.path == str(path)
    assert app.current_info.error is None
    for tag in DISASM_ITEMS:
        assert dpg.get_item_configuration(tag)["enabled"] is True
    assert app._parse_worker is None  # settled
    # D3: a valid binary auto-opens the Disassembly view (focus-guarded).
    assert len(app.tools) == 1
    assert app.tools[0].controller.view_kind == "disasm"
    assert len(_error_popups()) == 0


def test_open_file_garbage_pops_error_disasm_stays_grayed(
    dpg_context, tmp_path
) -> None:
    """An unrecognized file keeps the hex flow live but grays the menu."""
    path = tmp_path / "raw.bin"
    path.write_bytes(DATA)
    app = GuiApp()
    build_shell(app)
    _pick_file(app, path)
    # Hex flow unaffected (current_file + bottom bar + editor item).
    assert app.current_file == str(path)
    assert dpg.get_item_configuration(MI_EDITOR)["enabled"] is True
    assert dpg.get_item_configuration(SHELL_STATUS_BAR)["show"] is True
    assert dpg.get_value(SHELL_STATUS) == str(path)
    # …but no binary model and the Disassembler menu stays grayed.
    assert app.current_info is None
    for tag in DISASM_ITEMS:
        assert dpg.get_item_configuration(tag)["enabled"] is False
    popups = _error_popups()
    assert len(popups) == 1
    message = _popup_message(popups[0])
    assert "Cannot disassemble" in message
    assert str(path) in message


def test_reopen_file_replaces_and_clears_current_info(dpg_context, tmp_path) -> None:
    """good → garbage → good: current_info tracks the latest pick."""
    a = tmp_path / "a.bin"
    g = tmp_path / "g.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(_macho_minimal())
    g.write_bytes(DATA)
    b.write_bytes(_macho_minimal())
    app = GuiApp()
    build_shell(app)
    _pick_file(app, a)
    assert app.current_info is not None and app.current_info.path == str(a)
    _pick_file(app, g)
    assert app.current_info is None
    for tag in DISASM_ITEMS:
        assert dpg.get_item_configuration(tag)["enabled"] is False
    _pick_file(app, b)
    assert app.current_info is not None and app.current_info.path == str(b)
    for tag in DISASM_ITEMS:
        assert dpg.get_item_configuration(tag)["enabled"] is True


def test_superseded_parse_dropped_by_worker_identity(dpg_context, tmp_path) -> None:
    """Two rapid picks: only the latest parse's event reaches the shell."""
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(_macho_minimal())
    b.write_bytes(_macho_minimal())
    app = GuiApp()
    build_shell(app)
    # Pick twice without settling: two parse workers now exist.
    dpg.get_item_callback(DLG_OPEN)(DLG_OPEN, {"selections": {"file_1": str(a)}})
    worker_a = app._parse_worker
    dpg.get_item_callback(DLG_OPEN)(DLG_OPEN, {"selections": {"file_1": str(b)}})
    worker_b = app._parse_worker
    assert worker_a is not None and worker_b is not None
    assert worker_a is not worker_b
    worker_a.thread.join(timeout=10.0)
    worker_b.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        app.dispatch(event)  # identity check drops worker_a's stale event
    assert app._parse_worker is None
    assert app.current_info is not None
    assert app.current_info.path == str(b)


def test_inspector_menu_items_inert_without_file(dpg_context) -> None:
    """D1: with no file loaded, even a fired inspector callback returns
    early (D2 view-opening is covered in tests/test_gui_views.py)."""
    app = GuiApp()
    build_shell(app)
    dpg.get_item_callback(MI_SEGMENTS)()
    dpg.get_item_callback(MI_DISASM)()
    assert app.tools == []
    assert len(_error_popups()) == 0


def test_find_inspector_tool_matches_kind_and_path(dpg_context) -> None:
    """The focus guard matches (view_kind, path) across open tools."""
    app = GuiApp()
    app.tools.append(
        SimpleNamespace(controller=SimpleNamespace(view_kind="segments", path="/x/a"))
    )
    app.tools.append(
        SimpleNamespace(controller=SimpleNamespace(view_kind="symbols", path="/x/a"))
    )
    app.tools.append(
        SimpleNamespace(controller=SimpleNamespace(view_kind="segments", path="/x/b"))
    )
    assert find_inspector_tool(app, "segments", "/x/a") is app.tools[0]
    assert find_inspector_tool(app, "symbols", "/x/a") is app.tools[1]
    assert find_inspector_tool(app, "segments", "/x/b") is app.tools[2]
    assert find_inspector_tool(app, "exports", "/x/a") is None
    assert find_inspector_tool(app, "segments", "/x/zzz") is None
    # Tools without the inspector attributes (e.g. hex editors) never match.
    app.tools.append(SimpleNamespace(controller=object()))
    assert find_inspector_tool(app, "", "") is None
