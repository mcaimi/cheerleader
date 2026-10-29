"""Tests for HexEditorTab's busy-state machine (_Op enum + _Busy worker marker).

Uses Textual's headless ``run_test()`` harness driven via ``asyncio.run`` —
no extra test dependency needed. Covers the state transitions, the
worker-identity guard for stale search callbacks, cancellation, and the
save flow.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from textual.app import App, ComposeResult

from cheerleader.libs import hexdoc
from cheerleader.libs.hexdoc import HexDocument
from cheerleader.tui.tabs import HexEditorTab, _Busy, _Op
from cheerleader.tui.widgets import HexEditorView

CONTENT = bytes(range(256)) + b"HELLO WORLD, HELLO WORLD" + b"\x00" * 20  # 300 bytes


class _Harness(App):
    def compose(self) -> ComposeResult:
        yield HexEditorTab()


async def _wait_until(predicate, timeout: float = 5.0) -> bool:
    """Pump the UI loop until *predicate* holds or *timeout* elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.01)
        if predicate():
            return True
    return False


def _make_file(tmp_path, data: bytes, name: str = "blob.bin") -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _drive(scenario) -> None:
    """Run an async test scenario to completion on a fresh event loop."""
    asyncio.run(scenario())


async def _boot(tmp_path, pilot_size=(100, 30)):
    """Boot the harness with a loaded document; return (app, pilot, tab)."""
    app = _Harness()
    pilot_ctx = app.run_test(size=pilot_size)
    pilot = await pilot_ctx.__aenter__()
    tab = pilot.app.query_one(HexEditorTab)
    tab.load(_make_file(tmp_path, CONTENT))
    await pilot.pause()
    return app, pilot_ctx, pilot, tab


# ── state machine basics ──────────────────────────────────────────────────────


def test_load_sets_doc_and_idle_state(tmp_path):
    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            assert isinstance(tab._doc, HexDocument)
            assert tab._doc.size == 300
            assert tab._busy is None
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)


def test_actions_refuse_while_saving(tmp_path):
    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            notified: list[str] = []
            app.notify = lambda msg, **kw: notified.append(str(msg))
            tab._busy = _Busy(_Op.SAVE)
            tab.action_edit()
            tab.action_undo()
            tab.action_save()
            tab._start_search(True)
            assert any("Busy saving" in m for m in notified)
            assert tab._busy is not None and tab._busy.op is _Op.SAVE
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)


def test_search_blocked_while_saving(tmp_path):
    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            tab._busy = _Busy(_Op.SAVE)
            tab._pattern = b"HELLO"
            tab._start_search(True)
            assert tab._busy.op is _Op.SAVE  # refused, state unchanged
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)


def test_search_allowed_restart_while_searching(tmp_path):
    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            tab._pattern = b"HELLO"
            tab._start_search(True)
            assert tab._busy is not None and tab._busy.op is _Op.SEARCH
            first_worker = tab._busy.worker
            # a second search during a search must be allowed (restart), not refused
            tab._start_search(False)
            assert tab._busy.op is _Op.SEARCH
            assert tab._busy.worker is not first_worker
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)


# ── search completion paths ───────────────────────────────────────────────────


def test_search_found_completes(tmp_path):
    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            tab._pattern = b"HELLO"
            tab._start_search(True)
            assert tab._busy is not None and tab._busy.op is _Op.SEARCH
            done = await _wait_until(lambda: tab._busy is None)
            assert done, "search never completed"
            view = tab.query_one(HexEditorView)
            assert view._match == (256, 5)
            # status note anchored at the hit
            assert tab._note is not None and tab._note[0] == 256
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)


def test_search_not_found_clears_busy(tmp_path):
    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            tab._pattern = b"NOT PRESENT"
            tab._start_search(True)
            done = await _wait_until(lambda: tab._busy is None)
            assert done, "search never completed"
            assert tab.query_one(HexEditorView)._match is None
            assert tab._note is not None and "not found" in tab._note[1]
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)


def test_stale_search_done_ignored(tmp_path):
    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            tab._pattern = b"HELLO"
            tab._start_search(True)
            current = tab._busy.worker
            done = await _wait_until(lambda: tab._busy is None)
            assert done
            tab._note = None
            # a late callback from the completed worker (busy already None) is dropped
            tab._search_done(current, 256, 5, False)
            assert tab._busy is None
            assert tab._note is None  # state untouched

            # a callback from a superseded worker while a new search runs is dropped
            tab._busy = _Busy(_Op.SEARCH, worker=object())  # newer run owns the state
            tab._search_done(current, 256, 5, False)
            assert tab._busy is not None and tab._busy.op is _Op.SEARCH
            assert tab._note is None
            tab._busy = None
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)


def test_cancel_search_during_slow_search(tmp_path, monkeypatch):
    monkeypatch.setattr(hexdoc, "SEARCH_CHUNK", 64)

    def slow_find(self, pattern, start, end=None, cancelled=None, progress=None):
        # blocks until cancelled, simulating a long scan of a big file
        while not (cancelled and cancelled()):
            time.sleep(0.005)
        return None

    monkeypatch.setattr(HexDocument, "find", slow_find)

    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            tab._pattern = b"NEEDLE"
            tab._start_search(True)
            started = await _wait_until(lambda: tab._busy is not None)
            assert started and tab._busy.op is _Op.SEARCH
            worker = tab._busy.worker

            tab.action_cancel_search()
            assert tab._busy is None
            assert tab._note is not None and "cancelled" in tab._note[1]

            # let the cancelled worker exit; it must not touch the state
            for _ in range(100):
                await asyncio.sleep(0.005)
                await pilot.pause()
                if tab._busy is not None:
                    break
            assert tab._busy is None, "cancelled worker resurrected the busy state"
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)


# ── save flow ─────────────────────────────────────────────────────────────────


def test_save_flow_reopens_and_clears_busy(tmp_path):
    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            doc = tab._doc
            doc.write(256, b"JELLO")
            dst = tmp_path / "saved.bin"
            tab._run_save(str(dst))
            done = await _wait_until(lambda: tab._busy is None)
            assert done, "save never completed"
            # _save_done re-opened the document at the saved path
            assert tab._doc.path == str(dst)
            assert tab._doc.read(256, 5) == b"JELLO"
            assert not tab._doc.modified
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)


def test_save_failure_notifies_and_clears_busy(tmp_path, monkeypatch):
    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            def boom(self, path, progress=None):
                raise OSError("disk full")

            monkeypatch.setattr(HexDocument, "save_to", boom)
            tab._run_save(str(tmp_path / "never.bin"))
            done = await _wait_until(lambda: tab._busy is None)
            assert done, "failed save never cleared busy"
            assert tab._doc.path != str(tmp_path / "never.bin")
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)


def test_load_cancels_search_and_clears_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(hexdoc, "SEARCH_CHUNK", 64)

    def slow_find(self, pattern, start, end=None, cancelled=None, progress=None):
        while not (cancelled and cancelled()):
            time.sleep(0.005)
        return None

    monkeypatch.setattr(HexDocument, "find", slow_find)

    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            tab._pattern = b"NEEDLE"
            tab._start_search(True)
            started = await _wait_until(lambda: tab._busy is not None)
            assert started

            # loading another file cancels the in-flight search and resets state
            other = _make_file(tmp_path, b"OTHER CONTENT", name="other.bin")
            tab.load(other)
            assert tab._busy is None
            assert tab._doc.path == other
            assert tab._doc.size == len(b"OTHER CONTENT")
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)


def test_status_mixin_updates_status_widget(tmp_path):
    async def scenario():
        app, pilot_ctx, pilot, tab = await _boot(tmp_path)
        try:
            # StatusMixin._set_status routes through the tab's STATUS_ID
            assert tab.STATUS_ID == "hexedit-status"
            tab._set_status("hello status")
            await pilot.pause()
            text = pilot.app.query_one("#hexedit-status").content
            assert text == "hello status"
        finally:
            await pilot_ctx.__aexit__(None, None, None)

    _drive(scenario)
