"""Headless tests for the D5 Strings window.

Covers the pure cell formatters, the background extraction worker (join +
drain + stale drops + cancel), the live search tick, the click-to-jump
interaction (status hint without an editor, grid cursor with one), the
focus guard, the menu-registry wiring, and the controller protocol.

The binary fixture is a minimal Mach-O whose ``__text`` holds the D3
fixture's 64 arm64 NOPs plus a ``__cstring`` section carrying
``hello strings\x00`` — the payload row is section-annotated
(``__TEXT,__cstring``) while the macho name fields (``__TEXT``/
``__text``) are un-annotated ("—") rows, covering both shapes.
"""

import struct

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg

from cheerleader.formats import parse
from cheerleader.gui.app import DLG_OPEN, GuiApp, build_shell, open_editor
from cheerleader.gui.strings import (
    addr_cell,
    offset_cell,
    open_strings,
    row_cells,
)
from cheerleader.gui.workers import EventKind, UiEvent, WorkerHandle
from cheerleader.libs.disasm import extract_strings
from cheerleader.libs.types import BinaryInfo, BinaryString

BASE = 0x100000000
NOP = b"\x1f\x20\x03\xd5"


@pytest.fixture()
def dpg_context():
    dpg.create_context()
    yield
    dpg.destroy_context()


def _macho_strings(tmp_path) -> str:
    """Minimal Mach-O: ``__text`` (64 NOPs) + a ``__cstring`` payload.

    Layout: 32-byte header + 72-byte LC_SEGMENT_64 (two sections) + two
    80-byte section headers = 264 (0x108); the NOPs are the ``__text``
    bytes at 0x108, the printable payload sits at 0x208 *inside*
    ``__cstring`` (size 14) — so the fixture exercises both the
    section-annotated row and un-annotated ("—") name-field rows
    (``__TEXT`` at 0x28, ``__text`` at 0x68, ``__TEXT`` at 0x78 — all
    ≥ 4 printable chars).
    """
    header = b"\xcf\xfa\xed\xfe" + struct.pack(
        "<IIIIIII", 0x0100000C, 0, 2, 1, 232, 0, 0
    )
    segment = (
        struct.pack("<II", 0x19, 232)
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack("<QQQQIIII", BASE, 0x2000, 0, 0x2000, 5, 5, 2, 0)
    )
    text_sect = (
        b"__text".ljust(16, b"\x00")
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack("<QQIIIIIIII", BASE, 0x100, 0x108, 3, 0, 0, 0, 0, 0, 0)
    )
    cstring_sect = (
        b"__cstring".ljust(16, b"\x00")
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack("<QQIIIIIIII", BASE + 0x100, 14, 0x208, 3, 0, 0, 0, 0, 0, 0)
    )
    path = str(tmp_path / "strings.bin")
    with open(path, "wb") as fh:
        fh.write(
            header
            + segment
            + text_sect
            + cstring_sect
            + NOP * 64
            + b"hello strings\x00"
        )
    return path


def _row_texts(ctl) -> list[tuple[str, ...]]:
    rows = dpg.get_item_children(ctl.table_tag)[1]
    return [
        tuple(dpg.get_item_label(sel) for sel in dpg.get_item_children(row)[1])
        for row in rows
    ]


def _open_strings(app: GuiApp, path: str):
    """Parse *path* like the shell does, then open the Strings window."""
    app.current_file = path
    app.current_info = parse(path)
    open_strings(app, path)
    return app.tools[-1].controller


# ── pure formatters ───────────────────────────────────────────────────────────


def test_offset_cell_format() -> None:
    s = BinaryString(file_offset=0x1B8, addr=0, section="—", value="x")
    assert offset_cell(s) == "0x000001b8"


def test_addr_cell_unmapped_is_dash() -> None:
    mapped = BinaryString(
        file_offset=0, addr=0x1000, section="__TEXT,__text", value="x"
    )
    unmapped = BinaryString(file_offset=0, addr=0, section="—", value="x")
    assert addr_cell(mapped) == "0x0000000000001000"
    assert addr_cell(unmapped) == "—"


def test_row_cells_tuple() -> None:
    s = BinaryString(
        file_offset=0x40, addr=BASE + 0x40, section="__TEXT,__text", value="abc"
    )
    assert row_cells(s) == (
        "0x00000040",
        f"0x{BASE + 0x40:016x}",
        "__TEXT,__text",
        "abc",
    )


# ── window + worker ───────────────────────────────────────────────────────────


def test_open_strings_window_built(dpg_context, tmp_path) -> None:
    path = _macho_strings(tmp_path)
    app = GuiApp()
    _open_strings(app, path)
    assert len(app.tools) == 1
    tool = app.tools[0]
    ctl = tool.controller
    assert ctl.view_kind == "strings" and ctl.path == path
    assert dpg.get_item_configuration(tool.window)["label"] == "Strings — strings.bin"
    assert "Extracting" in dpg.get_value(tool.status_tag)
    # search box + 4 columns exist
    assert dpg.get_item_configuration(ctl.search_tag)["hint"] == "Search strings…"
    cols = dpg.get_item_children(ctl.table_tag)[0]
    assert [dpg.get_item_label(c) for c in cols] == [
        "File Offset",
        "Address",
        "Section",
        "String",
    ]


def test_worker_populates_table(dpg_context, tmp_path) -> None:
    path = _macho_strings(tmp_path)
    app = GuiApp()
    ctl = _open_strings(app, path)
    worker = ctl.busy.worker
    worker.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        app.dispatch(event)
    assert ctl._all == extract_strings(app.current_info)
    rows = _row_texts(ctl)
    assert (
        len(rows) == 6
    )  # 4 name fields (__TEXT/__text/__TEXT/__cstring) + segname + payload
    assert "6 strings found" in dpg.get_value(ctl.status_tag)
    # the payload row: offset 0x208, mapped address, inside __cstring
    assert (
        "0x00000208",
        f"0x{BASE + 0x208:016x}",
        "__TEXT,__cstring",
        "hello strings",
    ) in rows
    # a load-command name field: mapped, but no section covers it
    assert ("0x00000028", f"0x{BASE + 0x28:016x}", "—", "__TEXT") in rows


def test_stale_completion_dropped(dpg_context, tmp_path) -> None:
    path = _macho_strings(tmp_path)
    app = GuiApp()
    ctl = _open_strings(app, path)
    old = WorkerHandle()
    strings = [BinaryString(file_offset=0, addr=0, section="—", value="stale")]
    ctl.on_event(UiEvent(0, EventKind.STRINGS_DONE, strings, old))
    assert ctl._all == []  # never tracked → stale → dropped
    # a superseded (no longer tracked) run drops too
    ctl.busy.start("extracting", old)
    newer = WorkerHandle()
    ctl.busy.start("extracting", newer)
    ctl.on_event(UiEvent(0, EventKind.STRINGS_DONE, strings, old))
    assert ctl._all == []
    # the tracked, un-cancelled run lands
    ctl.on_event(UiEvent(0, EventKind.STRINGS_DONE, strings, newer))
    assert ctl._all == strings


def test_cancelled_completion_dropped(dpg_context, tmp_path) -> None:
    path = _macho_strings(tmp_path)
    app = GuiApp()
    ctl = _open_strings(app, path)
    handle = WorkerHandle()
    ctl.busy.start("extracting", handle)
    handle.cancel.cancel()  # Esc-cancelled while in flight
    strings = [BinaryString(file_offset=0, addr=0, section="—", value="x")]
    ctl.on_event(UiEvent(0, EventKind.STRINGS_DONE, strings, handle))
    assert ctl._all == []


def test_escape_cancels_extraction(dpg_context, tmp_path) -> None:
    path = _macho_strings(tmp_path)
    app = GuiApp()
    ctl = _open_strings(app, path)
    assert dpg.does_item_exist(f"str-keys-{ctl.ns}")  # Esc handler installed
    ctl._on_escape()
    assert ctl.busy.worker.cancel.cancelled
    assert dpg.get_value(ctl.status_tag) == "String extraction cancelled"


# ── search ────────────────────────────────────────────────────────────────────


def _populated(ctl) -> None:
    ctl._all = [
        BinaryString(file_offset=0x10, addr=0, section="—", value="Alpha one"),
        BinaryString(
            file_offset=0x20, addr=0x2000, section="__TEXT,__cstring", value="beta two"
        ),
        BinaryString(file_offset=0x30, addr=0, section="—", value="alphabet soup"),
    ]
    ctl._query = ""
    dpg.set_value(ctl.search_tag, "")
    ctl._apply_search()


def test_search_filters_live(dpg_context, tmp_path) -> None:
    path = _macho_strings(tmp_path)
    app = GuiApp()
    ctl = _open_strings(app, path)
    _populated(ctl)
    dpg.set_value(ctl.search_tag, "ALPHA")  # case-insensitive (TUI parity)
    ctl.tick()
    rows = _row_texts(ctl)
    assert len(rows) == 2
    assert "3 / 3 strings matching 'ALPHA'" not in dpg.get_value(ctl.status_tag)
    assert "2 / 3 strings matching 'ALPHA'" in dpg.get_value(ctl.status_tag)
    # clearing the box restores every row
    dpg.set_value(ctl.search_tag, "")
    ctl.tick()
    assert len(_row_texts(ctl)) == 3
    assert "3 strings found" in dpg.get_value(ctl.status_tag)


def test_search_no_match(dpg_context, tmp_path) -> None:
    path = _macho_strings(tmp_path)
    app = GuiApp()
    ctl = _open_strings(app, path)
    _populated(ctl)
    dpg.set_value(ctl.search_tag, "zzz")
    ctl.tick()
    assert _row_texts(ctl) == []
    assert "0 / 3 strings matching 'zzz'" in dpg.get_value(ctl.status_tag)


# ── click → jump ──────────────────────────────────────────────────────────────


def test_row_click_without_editor_hints(dpg_context, tmp_path) -> None:
    path = _macho_strings(tmp_path)
    app = GuiApp()
    ctl = _open_strings(app, path)
    s = BinaryString(
        file_offset=0x1B8, addr=BASE + 0x1B8, section="—", value="hello strings"
    )
    ctl._on_row(None, None, s)
    assert "0x000001b8" in dpg.get_value(ctl.status_tag)
    assert "Hex Editor" in dpg.get_value(ctl.status_tag)
    assert len(app.tools) == 1  # no auto-open


def test_row_click_jumps_into_open_editor(dpg_context, tmp_path) -> None:
    path = _macho_strings(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = parse(path)
    open_editor(app, path)
    open_strings(app, path)
    ctl = app.tools[-1].controller
    s = BinaryString(
        file_offset=0x1B8, addr=BASE + 0x1B8, section="—", value="hello strings"
    )
    ctl._on_row(None, None, s)
    ed = next(t for t in app.tools if getattr(t.controller, "doc", None))
    assert ed.controller.grid.cursor == 0x1B8  # jumped to the offset
    assert dpg.get_value(ctl.status_tag) == "Jumped to 0x000001b8 in the hex editor"


def test_row_click_other_file_editor_no_jump(dpg_context, tmp_path) -> None:
    """An editor open on a *different* file must not receive the jump."""
    path = _macho_strings(tmp_path)
    other = str(tmp_path / "other.bin")
    with open(other, "wb") as fh:
        fh.write(b"\x00" * 64)
    app = GuiApp()
    app.current_file = path
    app.current_info = parse(path)
    open_editor(app, other)
    open_strings(app, path)
    ctl = app.tools[-1].controller
    s = BinaryString(file_offset=0x1B8, addr=0, section="—", value="hello strings")
    ctl._on_row(None, None, s)
    ed = next(t for t in app.tools if getattr(t.controller, "doc", None))
    assert ed.controller.grid.cursor != 0x1B8
    assert "Hex Editor" in dpg.get_value(ctl.status_tag)  # hint, not a jump


# ── focus guard + menu wiring + protocol ──────────────────────────────────────


def test_focus_guard(dpg_context, tmp_path) -> None:
    path = _macho_strings(tmp_path)
    app = GuiApp()
    _open_strings(app, path)
    open_strings(app, path)
    assert len(app.tools) == 1


def test_menu_registry_opens_strings(dpg_context, tmp_path) -> None:
    """Disassembler ▸ Strings… drives the real registry → open_strings."""
    from cheerleader.gui.app import MI_STRINGS

    path = _macho_strings(tmp_path)
    app = GuiApp()
    build_shell(app)
    dpg.get_item_callback(DLG_OPEN)(DLG_OPEN, {"selections": {"file_1": path}})
    worker = app._parse_worker
    worker.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        app.dispatch(event)
    app.close_tool(app.tools[0])  # close the D3 auto-open
    dpg.get_item_callback(MI_STRINGS)()
    assert len(app.tools) == 1
    assert app.tools[0].controller.view_kind == "strings"


def test_protocol_readonly_and_shutdown(dpg_context, tmp_path) -> None:
    path = _macho_strings(tmp_path)
    app = GuiApp()
    ctl = _open_strings(app, path)
    assert ctl.dirty_edit_count() == 0
    ctl.shutdown()
    assert not dpg.does_item_exist(f"str-keys-{ctl.ns}")


def test_open_strings_ignores_unparsed_path(dpg_context, tmp_path) -> None:
    """Fails soft when *path* is not the shell's current binary."""
    path = _macho_strings(tmp_path)
    app = GuiApp()
    app.current_info = BinaryInfo(path=path)
    open_strings(app, str(tmp_path / "nope.bin"))
    assert app.tools == []
