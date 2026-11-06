"""Headless tests for the editor controller.

The controller flows run against a real :class:`HexDocument` and a real
(unrendered) :class:`HexGrid`, opened through the shell's ``open_editor``
flow inside a floating tool window; background workers are joined explicitly
and their UiBridge events drained + dispatched by hand, mirroring the frame
loop.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg

from cheerleader.gui.app import GuiApp, build_shell, open_editor
from cheerleader.gui.editor import EditorController
from cheerleader.gui.hexgrid import HexGrid
from cheerleader.gui.workers import EventKind, UiEvent, WorkerHandle
from cheerleader.libs.hexdoc import HexDocument

DATA = bytes(range(256)) * 16  # 4 KiB; contains every byte value


@pytest.fixture()
def editor(tmp_path):
    dpg.create_context()
    try:
        path = tmp_path / "sample.bin"
        path.write_bytes(DATA)
        app = GuiApp()
        build_shell(app)
        open_editor(app, str(path))
        tool = app.tools[-1]
        ctl = tool.controller
        yield SimpleNamespace(
            app=app, tool=tool, grid=ctl.grid, doc=ctl.doc, ctl=ctl, path=str(path)
        )
    finally:
        dpg.destroy_context()


def _status(ns) -> str:
    return dpg.get_value(ns.tool.status_tag)


def _finish(ns) -> list[UiEvent]:
    """Join the in-flight worker, then drain + dispatch its events."""
    worker = ns.ctl.busy.worker
    assert worker is not None and worker.thread is not None
    worker.thread.join(timeout=10.0)
    events = ns.app.bridge.drain()
    for event in events:
        ns.ctl.on_event(event)
    return events


# ── status ────────────────────────────────────────────────────────────────────


def test_initial_status(editor) -> None:
    text = _status(editor)
    assert "sample.bin" in text
    assert "4.0 KiB" in text
    assert "0x00000000" in text
    assert "= 0x00   0 '.'" in text
    assert "Ctrl+Q quit" in text
    assert "unsaved" not in text


def test_status_tracks_cursor_and_edits(editor) -> None:
    editor.grid.move_to(0x101)
    assert "0x00000101 (257)" in _status(editor)
    assert "= 0x01   1 '.'" in _status(editor)
    editor.ctl.type_hex_digit(0xF)
    editor.ctl.type_hex_digit(0xF)
    assert "1 unsaved [modified]" in _status(editor)


def test_note_only_shows_while_cursor_stays(editor) -> None:
    editor.ctl.type_hex_digit(0xF)
    editor.ctl.action_undo()
    editor.ctl.action_undo()  # nothing to undo → note at cursor 0
    assert "nothing to undo" in _status(editor)
    editor.grid.move_to(300)
    assert "nothing to undo" not in _status(editor)


# ── inline hex typing ─────────────────────────────────────────────────────────


def test_typing_two_digits_advance(editor) -> None:
    ctl = editor.ctl
    assert DATA[0] == 0x00
    ctl.type_hex_digit(0xD)  # high nibble written immediately
    assert editor.doc.read(0, 1) == b"\xd0"
    assert editor.doc.is_edited(0)
    assert editor.grid.cursor == 0
    ctl.type_hex_digit(0x3)  # low nibble + auto-advance
    assert editor.doc.read(0, 1) == b"\xd3"
    assert editor.grid.cursor == 1


def test_typing_preserves_low_nibble_and_clears_on_move(editor) -> None:
    ctl = editor.ctl
    assert DATA[0] == 0x00
    ctl.type_hex_digit(0xA)
    assert editor.doc.read(0, 1)[0] == 0xA0  # high nibble replaced, low kept
    editor.grid.move_to(5)  # a cursor move discards the pending nibble
    assert DATA[5] == 0x05
    ctl.type_hex_digit(0x7)
    assert editor.doc.read(5, 1)[0] == 0x75  # 0x70 | (0x05 & 0x0F)
    assert editor.grid.cursor == 5


def test_typing_ignored_while_busy(editor, tmp_path) -> None:
    ctl = editor.ctl
    target = tmp_path / "out.bin"
    ctl.action_save()
    ctl._on_save_chosen(str(target))  # starts the save worker
    assert ctl.busy.op == "saving"
    ctl.type_hex_digit(0xF)
    assert editor.doc.read(0, 1) == DATA[:1]
    assert not editor.doc.modified
    _finish(editor)


def test_typing_ignored_on_empty_file(tmp_path) -> None:
    dpg.create_context()
    try:
        path = tmp_path / "empty.bin"
        path.write_bytes(b"")
        doc = HexDocument(str(path))
        with dpg.window(tag="empty-win"):
            dpg.add_text("", tag="empty-status")
        grid = HexGrid(doc)
        grid.build("empty-win")
        ctl = EditorController(
            GuiApp(),
            grid,
            doc,
            window="empty-win",
            status_tag="empty-status",
            ns="tx",
        )
        ctl.type_hex_digit(0xF)
        assert doc.size == 0
    finally:
        dpg.destroy_context()


# ── Enter popup ───────────────────────────────────────────────────────────────


def test_enter_popup_writes_and_advances(editor) -> None:
    ctl = editor.ctl
    editor.grid.move_to(16)
    ctl.action_edit()
    assert ctl._modal == "edit"
    assert not editor.grid.input_enabled  # grid input off while a dialog is up
    dlg = ctl._dialog
    assert dpg.get_value(dlg._input_tag) == "10"  # current byte (0x10) pre-filled
    dpg.set_value(dlg._input_tag, "de ad be ef")
    dlg._try_apply(None, None)
    assert editor.doc.read(16, 4) == b"\xde\xad\xbe\xef"
    assert editor.grid.cursor == 20  # auto-advance past the sequence
    assert ctl._modal is None
    assert editor.grid.input_enabled


def test_enter_popup_past_eof_error(editor) -> None:
    ctl = editor.ctl
    editor.grid.move_to(editor.doc.size - 2)
    ctl.action_edit()
    dlg = ctl._dialog
    dpg.set_value(dlg._input_tag, "11 22 33 44")
    dlg._try_apply(None, None)
    assert "past end of file" in dpg.get_value(dlg._error_tag)
    assert editor.doc.read(editor.doc.size - 2, 2) == DATA[-2:]  # untouched
    assert ctl._modal == "edit"  # stays open for correction
    dpg.set_value(dlg._input_tag, "11 22")
    dlg._try_apply(None, None)
    assert editor.doc.read(editor.doc.size - 2, 2) == b"\x11\x22"
    assert ctl._modal is None


def test_edit_refused_when_busy(editor) -> None:
    ctl = editor.ctl
    editor.doc.write(0, b"\xaa")
    editor.grid.move_to(50)
    ctl.busy.start("searching", WorkerHandle())  # simulate an in-flight search
    ctl.action_edit()
    assert ctl._modal is None
    assert "busy searching…" in _status(editor)
    ctl.busy.clear()


def test_undo_restores_and_reports_exhaustion(editor) -> None:
    ctl = editor.ctl
    # Inline typing commits per nibble → per-nibble undo steps (each write
    # call is one undo unit, TUI parity): two u presses revert a full byte.
    ctl.type_hex_digit(0xB)
    ctl.type_hex_digit(0xC)
    assert editor.doc.read(0, 1) == b"\xbc"
    ctl.action_undo()  # reverts the second (low) nibble write
    assert editor.doc.read(0, 1) == b"\xb0"
    assert editor.grid.cursor == 0  # cursor parked at the write start
    ctl.action_undo()  # reverts the first (high) nibble write
    assert editor.doc.read(0, 1) == b"\x00"  # overlay back to the original
    assert not editor.doc.is_edited(0)
    ctl.action_undo()
    assert "nothing to undo" in _status(editor)


# ── saving ────────────────────────────────────────────────────────────────────


def test_save_flow(editor, tmp_path) -> None:
    ctl = editor.ctl
    ctl.type_hex_digit(0xF)
    ctl.type_hex_digit(0xE)
    target = tmp_path / "out.patched"
    ctl.action_save()
    assert ctl._modal == "save"
    ctl._on_save_chosen(str(target))
    assert ctl._modal is None
    assert ctl.busy.op == "saving"
    assert "Saving…" in _status(editor)
    _finish(editor)
    assert target.read_bytes() == b"\xfe" + DATA[1:]
    assert editor.doc.path == str(target)
    assert not editor.doc.modified  # reopen dropped the edits
    assert ctl.busy.op is None
    assert f"saved {target}" in _status(editor)


def test_save_overwrite_confirm(editor, tmp_path) -> None:
    ctl = editor.ctl
    target = tmp_path / "exists.bin"
    target.write_bytes(b"old data")
    ctl.action_save()
    ctl._on_save_chosen(str(target))
    assert ctl._modal == "confirm"  # overwrite asked first
    assert ctl.busy.op is None
    ctl._dialog._on_answer(True)
    assert ctl._modal is None
    assert ctl.busy.op == "saving"
    _finish(editor)
    assert target.read_bytes() == DATA


def test_save_overwrite_declined(editor, tmp_path) -> None:
    ctl = editor.ctl
    target = tmp_path / "exists.bin"
    target.write_bytes(b"keep me")
    ctl.action_save()
    ctl._on_save_chosen(str(target))
    ctl._dialog._on_answer(False)
    assert ctl._modal is None
    assert ctl.busy.op is None
    assert target.read_bytes() == b"keep me"


def test_save_failure_shows_note(editor, tmp_path) -> None:
    ctl = editor.ctl
    bad = tmp_path / "missing-dir" / "x.bin"
    ctl.action_save()
    ctl._on_save_chosen(str(bad))
    _finish(editor)
    assert "Save failed" in _status(editor)
    assert ctl.busy.op is None


def test_progress_events_update_status(editor, tmp_path) -> None:
    ctl = editor.ctl
    target = tmp_path / "out.bin"
    ctl.action_save()
    ctl._on_save_chosen(str(target))
    worker = ctl.busy.worker
    ctl.on_event(UiEvent(0, EventKind.PROGRESS, 0.47, worker))
    assert "Saving… 47%" in _status(editor)
    _finish(editor)


# ── search ────────────────────────────────────────────────────────────────────


def test_search_offset_mode(editor) -> None:
    ctl = editor.ctl
    ctl.action_search()
    assert ctl._modal == "search"
    assert not editor.grid.input_enabled
    dlg = ctl._dialog
    dpg.set_value(dlg._input_tag, "0x200")
    dlg._try_apply(None, None)
    assert editor.grid.cursor == 0x200
    assert editor.grid.top_row == 0x200 // 16  # placed at the top
    assert ctl._modal is None
    assert editor.grid.input_enabled
    assert "match at" not in _status(editor)


def test_search_hex_found_then_n_and_upper_n(editor) -> None:
    ctl = editor.ctl
    # b"\x0a\x0b" occurs at 10, 266, 522, ...
    ctl._pattern = b"\x0a\x0b"
    ctl._start_search(forward=True)
    _finish(editor)
    assert editor.grid.cursor == 10
    assert editor.grid._match == (10, 2)
    assert "match at 0x0000000a" in _status(editor)
    assert "n/N" in _status(editor)

    ctl.action_search_next(forward=True)
    _finish(editor)
    assert editor.grid.cursor == 266

    ctl.action_search_next(forward=False)  # N: shift is irrelevant to the API
    _finish(editor)
    assert editor.grid.cursor == 10


def test_search_next_without_pattern(editor) -> None:
    editor.ctl.action_search_next(forward=True)
    assert "no search pattern" in _status(editor)


def test_search_wraps_backward_and_forward(editor) -> None:
    ctl = editor.ctl
    ctl._pattern = b"\x0a\x0b"
    editor.grid.move_to(4000)
    ctl._start_search(forward=True)  # nothing after 4000 → wraps to the top
    _finish(editor)
    assert editor.grid.cursor == 10  # first occurrence (TUI wrap semantics)
    assert "(wrapped)" in _status(editor)

    editor.grid.move_to(5)
    ctl._start_search(forward=False)  # nothing before 5 → wraps to the bottom
    _finish(editor)
    assert editor.grid.cursor == 3850  # last occurrence in [5, size)
    assert "(wrapped)" in _status(editor)


def test_search_not_found(editor) -> None:
    ctl = editor.ctl
    ctl._pattern = b"\xff\xff\xff"
    editor.grid.move_to(100)
    ctl._start_search(forward=True)
    _finish(editor)
    assert "pattern not found" in _status(editor)
    assert editor.grid._match is None


def test_search_text_mode_via_dialog(editor) -> None:
    ctl = editor.ctl
    ctl.action_search()
    dlg = ctl._dialog
    dpg.set_value(dlg._mode_tag, "text")
    dpg.set_value(dlg._input_tag, "abc")  # bytes 0x61 0x62 0x63
    dlg._try_apply(None, None)
    assert ctl._modal is None
    _finish(editor)
    assert editor.grid.cursor == 0x61
    assert ctl._pattern == b"abc"


def test_search_offset_out_of_range_error(editor) -> None:
    ctl = editor.ctl
    ctl.action_search()
    dlg = ctl._dialog
    dpg.set_value(dlg._input_tag, "deadbeef")
    dlg._try_apply(None, None)
    assert "offset out of range" in dpg.get_value(dlg._error_tag)
    assert ctl._modal == "search"


def test_search_progress_events(editor) -> None:
    ctl = editor.ctl
    ctl._pattern = b"\x0a\x0b"
    ctl._start_search(forward=True)
    worker = ctl.busy.worker
    ctl.on_event(UiEvent(0, EventKind.PROGRESS, 0.3, worker))
    assert "Searching… 30%" in _status(editor)
    assert "Esc cancels" in _status(editor)
    _finish(editor)


def test_esc_cancels_search_and_drops_late_completion(editor) -> None:
    ctl = editor.ctl
    ctl._pattern = b"\x0a\x0b"
    ctl._start_search(forward=True)
    worker = ctl.busy.worker
    ctl._on_escape_key()
    assert ctl.busy.op is None
    assert "search cancelled" in _status(editor)
    worker.thread.join(timeout=10.0)
    for event in editor.app.bridge.drain():  # the worker's own (late) events
        ctl.on_event(event)
    assert editor.grid.cursor == 0  # not moved by the cancelled run
    # a completion racing the cancel is dropped as stale
    editor.app.bridge.post(0, EventKind.FOUND, (10, 2, False), worker)
    for event in editor.app.bridge.drain():
        ctl.on_event(event)
    assert editor.grid.cursor == 0
    assert editor.grid._match is None


def test_restarting_search_cancels_previous(editor) -> None:
    ctl = editor.ctl
    ctl._pattern = b"\x0a\x0b"
    ctl._start_search(forward=True)
    first = ctl.busy.worker
    ctl._start_search(forward=True)
    second = ctl.busy.worker
    assert first is not second
    assert first.cancel.cancelled  # old run cancelled
    _finish(editor)
    assert editor.grid.cursor == 10


def test_save_refused_while_searching(editor) -> None:
    ctl = editor.ctl
    ctl._pattern = b"\x0a\x0b"
    ctl._start_search(forward=True)
    worker = ctl.busy.worker
    ctl.action_save()
    assert ctl._modal is None  # no save dialog opened
    assert "busy searching…" in _status(editor)
    ctl._on_escape_key()
    assert ctl.busy.op is None
    worker.thread.join(timeout=10.0)
    for event in editor.app.bridge.drain():  # cancelled run → dropped stale
        ctl.on_event(event)
    assert editor.grid.cursor == 0


def test_search_refused_while_saving(editor, tmp_path) -> None:
    ctl = editor.ctl
    target = tmp_path / "out.bin"
    ctl.action_save()
    ctl._on_save_chosen(str(target))
    assert ctl.busy.op == "saving"
    ctl.action_search()
    assert ctl._modal is None
    assert "busy saving…" in _status(editor)
    _finish(editor)


# ── reload ────────────────────────────────────────────────────────────────────


def test_reload_clean_is_immediate(editor) -> None:
    ctl = editor.ctl
    ctl.action_reload()
    assert ctl._modal is None
    assert editor.doc.read(0, 1) == DATA[:1]


def test_reload_discards_edits_after_confirm(editor) -> None:
    ctl = editor.ctl
    ctl.type_hex_digit(0xF)
    editor.grid.move_to(30)
    ctl.action_reload()
    assert ctl._modal == "confirm"
    ctl._dialog._on_answer(True)
    assert editor.doc.read(0, 1) == b"\x00"  # edit gone
    assert not editor.doc.modified
    assert ctl._modal is None
    assert editor.grid.cursor == 30  # clamped, position kept


# ── window close (X button) ───────────────────────────────────────────────────


def test_window_close_clean_closes_tool(editor) -> None:
    ctl = editor.ctl
    assert dpg.does_item_exist(ctl.window)
    ctl.window_close_requested()  # clean → closes without a dialog
    assert ctl._modal is None
    assert editor.app.tools == []
    assert not dpg.does_item_exist(ctl.window)
    assert not dpg.does_item_exist(f"editor-keys-{ctl.ns}")
    assert not dpg.does_item_exist(f"{ctl.ns}-hexgrid-input")


def test_window_close_dirty_confirm_veto_then_close(editor) -> None:
    ctl = editor.ctl
    ctl.type_hex_digit(0xF)
    ctl.window_close_requested()
    assert ctl._modal == "confirm"
    assert "1 unsaved edit" in ctl._dialog.message
    assert "close without saving" in ctl._dialog.message
    ctl._dialog._on_answer(False)  # veto: the window comes back
    assert ctl._modal is None
    assert editor.app.tools  # tool kept alive
    assert dpg.does_item_exist(ctl.window)
    assert dpg.get_item_configuration(ctl.window)["show"] is True
    ctl.window_close_requested()  # second attempt
    assert ctl._modal == "confirm"
    ctl._dialog._on_answer(True)  # discard → tool closes
    assert editor.app.tools == []
    assert not dpg.does_item_exist(ctl.window)


def test_dirty_edit_count_for_aggregate_quit(editor) -> None:
    assert editor.ctl.dirty_edit_count() == 0
    editor.ctl.type_hex_digit(0xF)  # 1 edited byte (per-offset count)
    assert editor.ctl.dirty_edit_count() == 1
    editor.grid.move_to(50)
    editor.ctl.type_hex_digit(0xA)
    editor.ctl.type_hex_digit(0xB)  # same byte → still 1 more edited offset
    assert editor.ctl.dirty_edit_count() == 2


# ── grid keys vs. dialogs ─────────────────────────────────────────────────────


def test_grid_keys_disabled_while_dialog_open(editor) -> None:
    ctl = editor.ctl
    editor.grid.tick()
    ctl.action_edit()
    assert not editor.grid.input_enabled
    before = editor.grid.cursor
    editor.grid._key_left()
    editor.grid._key_right()
    editor.grid._key_down()
    editor.grid._on_wheel(None, -1)
    assert editor.grid.cursor == before  # all ignored
    ctl._on_escape_key()  # closes the dialog
    assert editor.grid.input_enabled
    editor.grid._key_right()
    assert editor.grid.cursor == before + 1
