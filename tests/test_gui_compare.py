"""Headless tests for the compare controller.

Two real documents + two real (unrendered) grids are wired through the shell's
``open_compare`` flow inside a floating tool window; the streaming diff worker
is joined explicitly and its UiBridge events drained + dispatched by hand,
mirroring the frame loop.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg

from cheerleader.gui.app import GuiApp, build_shell, open_compare
from cheerleader.gui.compare import COL_DIFF, COL_SAME, CompareController
from cheerleader.libs.hexdoc import DIFF_BLOCK

LEFT = bytearray(bytes(range(256)) * 4)  # 1 KiB
RIGHT = bytearray(LEFT)
RIGHT[0x100 : 0x100 + 4] = b"\xde\xad\xbe\xef"  # first diff at 0x100
RIGHT[0x200] ^= 0xFF  # second diff
RIGHT += b"\x09\x0a"  # right is 2 bytes longer (tail diff)
# Expected: blocks 0x100//8=32, 0x200//8=64, tail block 128; 4+1+2 = 7 bytes.
# With bpl 16 → 2 blocks/row → diff rows [16, 32, 64].


def _status(ns) -> str:
    return dpg.get_value(ns.tool.status_tag)


def _make(left_path, right_path) -> SimpleNamespace:
    app = GuiApp()
    build_shell(app)
    open_compare(app, str(left_path), str(right_path))
    ctl = app.tools[0].controller
    assert isinstance(ctl, CompareController)
    return SimpleNamespace(
        app=app,
        tool=app.tools[0],
        ns=ctl.ns,
        ctl=ctl,
        left=ctl.left,
        right=ctl.right,
        lg=ctl.left_grid,
        rg=ctl.right_grid,
    )


def _finish(ns) -> list:
    """Join the in-flight worker, then drain + dispatch its events."""
    worker = ns.ctl.busy.worker
    assert worker is not None and worker.thread is not None
    worker.thread.join(timeout=10.0)
    events = ns.app.bridge.drain()
    for event in events:
        ns.ctl.on_event(event)
    return events


@pytest.fixture()
def compare(tmp_path):
    dpg.create_context()
    try:
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(bytes(LEFT))
        b.write_bytes(bytes(RIGHT))
        ns = _make(a, b)
        yield ns
    finally:
        dpg.destroy_context()


@pytest.fixture()
def identical(tmp_path):
    dpg.create_context()
    try:
        a = tmp_path / "same1.bin"
        b = tmp_path / "same2.bin"
        a.write_bytes(bytes(LEFT))
        b.write_bytes(bytes(LEFT))
        ns = _make(a, b)
        yield ns
    finally:
        dpg.destroy_context()


# ── status ────────────────────────────────────────────────────────────────────


def test_final_status(compare) -> None:
    _finish(compare)
    text = _status(compare)
    assert "left 1.0 KiB (1,024)" in text
    assert "right 1.0 KiB (1,026)" in text
    assert "7 differing bytes" in text
    assert "first at 0x00000100" in text
    assert "n/p → next/prev diff" in text
    assert "Ctrl+Q quit" in text
    assert compare.ctl.busy.op is None


def test_identical_files_status(identical) -> None:
    _finish(identical)
    text = _status(identical)
    assert "files are identical" in text
    assert "differing bytes" not in text
    assert identical.ctl._diff_rows == []


def test_progress_event(compare) -> None:
    worker = compare.ctl.busy.worker
    compare.ctl.on_event(_evt(1, "progress", 0.37, worker))
    assert "Comparing… 37%" in _status(compare)
    assert "Esc cancels" in _status(compare)
    _finish(compare)


def _evt(gen, kind, payload, worker):
    from cheerleader.gui.workers import EventKind, UiEvent

    return UiEvent(gen, EventKind(kind), payload, worker)


# ── colors (live per-byte comparison) ─────────────────────────────────────────


def test_colors_same_diff_and_tail(compare) -> None:
    lg, rg = compare.lg, compare.rg
    assert lg._byte_color(0x050) == COL_SAME
    assert rg._byte_color(0x050) == COL_SAME
    assert lg._byte_color(0x100) == COL_DIFF  # differs in both panes
    assert rg._byte_color(0x100) == COL_DIFF
    # Tail: offset 0x400 is at/past the left file's EOF — the right pane
    # renders it (red, bytes past the shorter file count as differing) and
    # the left pane's row is blank (its own EOF check in _paint_slot).
    assert rg._byte_color(0x400) == COL_DIFF
    assert lg._total_rows() * 16 == 1024  # left rows stop at its EOF
    assert rg._total_rows() * 16 >= 1026


# ── shared scroll range + lockstep ────────────────────────────────────────────


def test_shared_scroll_range(compare) -> None:
    lg, rg = compare.lg, compare.rg
    assert lg.max_top_rows is not None
    assert lg.max_top_rows() == rg.max_top_rows()
    compare.ctl._set_top(10**9)  # clamps identically on both panes
    assert lg.top_row == rg.top_row == lg._max_top()
    assert lg._max_top() > (1024 // 16) - lg._vis  # range covers the tail


def test_lockstep_sync_from_left(compare) -> None:
    lg, rg = compare.lg, compare.rg
    lg._key_down()  # scroll_mode: arrow keys scroll by rows
    assert lg.top_row == 1
    compare.ctl.tick()
    assert rg.top_row == 1
    lg._key_page_down()
    compare.ctl.tick()
    assert rg.top_row == 1 + lg._vis


def test_lockstep_sync_from_right(compare) -> None:
    lg, rg = compare.lg, compare.rg
    rg.scroll_by(3)  # e.g. wheel over the right pane
    compare.ctl.tick()
    assert lg.top_row == 3
    assert rg.top_row == 3


def test_scroll_mode_keys_do_not_move_cursor(compare) -> None:
    lg = compare.lg
    before = lg.cursor
    lg._key_down()
    lg._key_end()
    assert lg.cursor == before  # no cursor semantics in compare mode
    assert lg.top_row == lg._max_top()
    lg._key_home()
    assert lg.top_row == 0


def test_right_scrollbar_hidden(compare) -> None:
    assert dpg.get_item_configuration(f"{compare.ns}-cmp-l-hexgrid-scroll")["show"]
    assert not dpg.get_item_configuration(f"{compare.ns}-cmp-r-hexgrid-scroll")["show"]
    for tag in (f"{compare.ns}-cmp-l-pane", f"{compare.ns}-cmp-r-pane"):
        assert dpg.does_item_exist(tag)


# ── diff navigation ───────────────────────────────────────────────────────────


def test_next_prev_navigation(compare) -> None:
    ctl = compare.ctl
    _finish(compare)  # ensure rows are known (bpl 16)
    assert ctl._diff_rows == [16, 32, 64]
    assert compare.lg.top_row == 0
    ctl.action_next_diff()
    assert compare.lg.top_row == 16 and compare.rg.top_row == 16
    ctl.action_next_diff()
    ctl.action_next_diff()
    # The tail diff (row 64 = last row of 65) can only be the *bottom* row:
    # set_top clamps to the shared max_top, exactly like the TUI scroll_to.
    assert compare.lg.top_row == compare.lg._max_top()
    assert 64 < compare.lg.top_row + compare.lg._vis  # tail row on screen
    # TUI parity quirk: with the last diff row inside the final screenful,
    # n re-scrolls to the same clamped position instead of exhausting.
    ctl.action_next_diff()
    assert compare.lg.top_row == compare.lg._max_top()
    # Exhaustion fires once no diff row lies below the top (last row ≤ max_top).
    ctl._diff_rows = [16, 32]
    ctl.action_next_diff()
    assert "No further differences" in _status(compare)
    ctl.action_prev_diff()
    assert compare.lg.top_row == 32
    ctl.action_prev_diff()
    assert compare.lg.top_row == 16
    # From a diff row, p goes to strictly earlier rows (bisect_left) → the
    # first diff row itself reports exhaustion, exactly like the TUI.
    ctl.action_prev_diff()
    assert compare.lg.top_row == 16
    assert "No previous differences" in _status(compare)


def test_navigation_before_scan_finds_nothing(compare) -> None:
    compare.ctl.action_next_diff()  # scan still running, rows unknown yet
    assert "No further differences" in _status(compare)
    _finish(compare)


# ── worker lifecycle ──────────────────────────────────────────────────────────


def test_diff_blocks_events_update_navigation(compare) -> None:
    ctl = compare.ctl
    worker = ctl.busy.worker
    ctl.on_event(_evt(1, "diff_blocks", [32], worker))
    ctl.on_event(_evt(1, "diff_blocks", [64, 128], worker))
    assert ctl._diff_blocks == [32, 64, 128]
    assert ctl._diff_rows == [16, 32, 64]
    _finish(compare)


def test_stale_generation_drops_events(compare) -> None:
    ctl = compare.ctl
    old = ctl.busy.worker
    ctl.action_reload()  # bumps the generation
    new = ctl.busy.worker
    assert new is not old
    assert old.cancel.cancelled
    # A completion racing in from the superseded run must be dropped.
    ctl.on_event(_evt(0, "diff_done", (32, 7), old))
    assert "differing bytes" not in _status(compare)
    _finish(compare)
    assert "7 differing bytes" in _status(compare)


def test_esc_cancels_and_drops_late_events(compare) -> None:
    ctl = compare.ctl
    worker = ctl.busy.worker
    ctl._on_escape()
    assert ctl.busy.op is None
    assert "comparison cancelled" in _status(compare)
    worker.thread.join(timeout=10.0)
    for event in compare.app.bridge.drain():  # whatever the run posted late
        ctl.on_event(event)
    assert "Comparing…" not in _status(compare)  # stale events dropped
    assert "differing bytes" not in _status(compare)
    # A synthetic completion of the cancelled generation is dropped too.
    ctl.on_event(_evt(1, "diff_done", (32, 7), worker))
    assert "differing bytes" not in _status(compare)


def test_reload_reopens_and_restarts(compare, tmp_path) -> None:
    ctl = compare.ctl
    _finish(compare)
    # Shrink the right file on disk, then reload: sizes must follow.
    right_path = tmp_path / "b.bin"
    right_path.write_bytes(bytes(RIGHT[:512]))
    ctl.action_reload()
    assert ctl.busy.op == "comparing"
    assert "Comparing… 0%" in _status(compare)
    _finish(compare)
    assert ctl.right.size == 512
    assert "left 1.0 KiB (1,024)" in _status(compare)
    assert "right 512 bytes (512)" in _status(compare)


def test_reload_failure_keeps_docs(compare, tmp_path) -> None:
    ctl = compare.ctl
    _finish(compare)
    # Remove the left file: reopen fails, old docs stay in place.
    (tmp_path / "a.bin").unlink()
    ctl.action_reload()
    assert ctl.busy.op is None
    assert "Reload failed" in _status(compare)
    assert ctl.left.size == 1024


# ── quit / wiring ─────────────────────────────────────────────────────────────


def test_quit_never_dirty(compare, monkeypatch) -> None:
    stopped: list[bool] = []
    monkeypatch.setattr(dpg, "stop_dearpygui", lambda: stopped.append(True))
    compare.app.request_quit()
    assert stopped == [True]  # no confirmation modal in read-only mode
    _finish(compare)


def test_open_failure_shows_error_popup(tmp_path) -> None:
    dpg.create_context()
    try:
        a = tmp_path / "a.bin"
        a.write_bytes(bytes(LEFT))
        app = GuiApp()
        build_shell(app)
        open_compare(app, str(a), str(tmp_path / "missing.bin"))
        assert app.tools == []
        popups = [
            dpg.get_item_alias(w)
            for w in dpg.get_windows()
            if dpg.get_item_alias(w).startswith("gui-error-")
        ]
        assert len(popups) == 1
    finally:
        dpg.destroy_context()


def test_worker_failure_event(compare) -> None:
    ctl = compare.ctl
    worker = ctl.busy.worker
    ctl.on_event(_evt(1, "failed", "Comparison failed — I/O error", worker))
    assert "Comparison failed — I/O error" in _status(compare)
    assert ctl.busy.op is None
    worker.thread.join(timeout=10.0)
    compare.app.bridge.drain()


def test_first_diff_offset_matches_expectation(compare) -> None:
    ctl = compare.ctl
    assert ctl._first_diff_offset(32) == 0x100
    assert ctl._first_diff_offset(64) == 0x200
    # Tail block: bytes past the shorter file's EOF → offset = common end.
    assert ctl._first_diff_offset(128) == 1024 + min(
        len(ctl.left.read(1024, DIFF_BLOCK)), len(ctl.right.read(1024, DIFF_BLOCK))
    )
