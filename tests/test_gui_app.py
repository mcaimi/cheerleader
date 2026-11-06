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
    DLG_EDITOR,
    MENU_TAG,
    WIN_TAG,
    GuiApp,
    build_shell,
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


def _menu_labels() -> list[str]:
    items = dpg.get_item_children(MENU_TAG)[1]  # slot 1: the "Tools" menu
    labels: list[str] = []
    for menu in items:
        labels.append(dpg.get_item_label(menu))
        for item in dpg.get_item_children(menu)[1]:
            labels.append(dpg.get_item_label(item))
    return labels


# ── shell ─────────────────────────────────────────────────────────────────────


def test_build_shell_minimal(dpg_context) -> None:
    app = GuiApp()
    build_shell(app)
    assert dpg.does_item_exist(WIN_TAG)
    assert dpg.does_item_exist(MENU_TAG)
    assert _menu_labels() == ["Tools", "Hex Editor…", "Hex Compare…", "Quit"]
    # Pre-built open dialog exists, hidden until the menu shows it. The
    # compare picker owns its two file dialogs privately (CompareOpenDialog).
    assert dpg.does_item_exist(DLG_EDITOR)
    assert dpg.get_item_configuration(DLG_EDITOR)["show"] is False
    # Global Ctrl+Q registry is registered.
    assert dpg.does_item_exist("gui-global-keys")
    assert app.tools == []


def test_next_ns_counter(dpg_context) -> None:
    app = GuiApp()
    assert [app.next_ns() for _ in range(3)] == ["t1", "t2", "t3"]


def test_compare_menu_item_opens_picker(dpg_context) -> None:
    """Tools → Hex Compare… opens the two-file picker, not a file dialog."""
    app = GuiApp()
    build_shell(app)
    items = dpg.get_item_children(MENU_TAG)[1]
    for item in dpg.get_item_children(items[0])[1]:
        if dpg.get_item_label(item) == "Hex Compare…":
            dpg.get_item_callback(item)()
            break
    else:  # pragma: no cover - menu wiring asserted elsewhere
        pytest.fail("Hex Compare… menu item not found")
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
