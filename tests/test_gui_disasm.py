"""Headless tests for the D3 Disassembly window.

Covers the section-list rule and auto-pick (pure functions), the
virtualized grid's state machine (pool painting, navigation, click
selection), the background disassembly worker (join + drain + stale
drops + cancel), auto-open on file load, and teardown.

The binary fixture is a hand-built Mach-O: one ``__TEXT`` segment holding
an executable ``__text`` section (64 arm64 NOPs) plus a non-executable
``__cstring`` — enough for capstone to produce 64 real instructions.
"""

import struct

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg

from cheerleader.gui.app import (
    DLG_OPEN,
    MI_DISASM,
    GuiApp,
    Tool,
    build_shell,
)
from cheerleader.gui.disasm import (
    COMBO_RESERVE,
    auto_pick,
    exec_sections,
    open_disassembly,
    pick_engine,
    section_label,
)
from cheerleader.gui.disasmgrid import (
    ADDR_W,
    BYTES_W,
    MNEM_W,
    DisasmGeometry,
    DisasmGrid,
    bytes_text,
    row_texts,
)
from cheerleader.gui.workers import EventKind, UiEvent, WorkerHandle
from cheerleader.libs.types import (
    BinaryInfo,
    DisasmInstruction,
    Section,
    Segment,
)

BASE = 0x100000000
NOP = b"\x1f\x20\x03\xd5"


@pytest.fixture()
def dpg_context():
    dpg.create_context()
    yield
    dpg.destroy_context()


def _macho_text(tmp_path) -> str:
    """A minimal Mach-O with an executable __TEXT,__text (64 NOPs).

    Layout: 32-byte header + 72-byte LC_SEGMENT_64 + 80-byte section_64 =
    184 (0xB8) bytes, then the section's bytes at offset 0xB8.
    """
    header = b"\xcf\xfa\xed\xfe" + struct.pack(
        "<IIIIIII", 0x0100000C, 0, 2, 1, 152, 0, 0
    )
    segment = (
        struct.pack("<II", 0x19, 152)
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack("<QQQQIIII", BASE, 0x1000, 0, 0x1000, 5, 5, 1, 0)
    )
    section = (
        b"__text".ljust(16, b"\x00")
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack("<QQIIIIIIII", BASE, 0x100, 0xB8, 3, 0, 0, 0, 0, 0, 0)
    )
    path = tmp_path / "text.bin"
    path.write_bytes(header + segment + section + NOP * 64)
    return str(path)


def _info(path: str = "/x/t.bin", with_data: bool = False) -> BinaryInfo:
    """Synthetic model matching _macho_text (+ an optional non-exec seg)."""
    info = BinaryInfo(path=path, arch="arm64", bits=64, file_type="MH_EXECUTE")
    info.segments.append(
        Segment(
            "__TEXT",
            BASE,
            0x1000,
            0,
            0x1000,
            5,
            5,
            sections=[
                Section("__text", "__TEXT", BASE, 0x100, 0x40, 3, 0, 0, 0),
                Section("__cstring", "__TEXT", BASE + 0x400, 0x20, 0x140, 0, 2, 0, 0),
            ],
        )
    )
    if with_data:
        info.segments.append(
            Segment(
                "__DATA",
                BASE + 0x4000,
                0x1000,
                0x1000,
                0x100,
                3,
                3,
                sections=[
                    Section("__data", "__DATA", BASE + 0x4000, 0x80, 0x1000, 3, 1, 0, 0)
                ],
            )
        )
    return info


def _settle(app: GuiApp, ctl) -> None:
    """Join the controller's disassembly worker, drain + dispatch."""
    worker = ctl.busy.worker
    if worker is not None and worker.thread is not None:
        worker.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        app.dispatch(event)


def _open(app: GuiApp, info: BinaryInfo):
    app.current_file = info.path
    app.current_info = info  # open_disassembly reads the shell's parsed info
    open_disassembly(app, info.path)
    return app.tools[-1].controller


# ── pure helpers ──────────────────────────────────────────────────────────────


def test_exec_sections_rule() -> None:
    """initprot & 0x4 gates segments; empty/unmapped sections are skipped."""
    info = _info(with_data=True)
    info.segments[0].initprot = 5  # r-x
    info.segments[1].initprot = 3  # rw- (not executable)
    assert exec_sections(info) == [("__TEXT", "__text"), ("__TEXT", "__cstring")]
    info.segments[0].sections[0].size = 0
    info.segments[0].sections[1].offset = 0
    assert exec_sections(info) == []
    info.segments[0].initprot = 4  # still executable bit
    assert exec_sections(info) == []


def test_auto_pick_prefers_macho_text() -> None:
    assert auto_pick([("__TEXT", "__cstring"), ("__TEXT", "__text")]) == 1
    assert auto_pick([("__TEXT", "__cstring")]) is None
    assert auto_pick([(".rodata", ".rodata"), (".text", ".text")]) == 1  # ELF .text
    assert auto_pick([("__DATA", "__text"), ("__TEXT", "__text")]) == 1  # any __text


def test_pick_engine_returns_known_kind() -> None:
    assert pick_engine() in ("capstone", "objdump", None)


def test_bytes_text_truncates_and_marks() -> None:
    assert bytes_text(b"\xde\xad\xbe\xef") == "de ad be ef"
    long = bytes(range(12))
    text = bytes_text(long)
    assert text == " ".join(f"{b:02x}" for b in range(8)) + " ~"
    assert len(text) <= BYTES_W


def test_row_texts_fixed_widths() -> None:
    insn = DisasmInstruction(
        addr=0x1000, size=4, mnemonic="mov", op_str="x0, x1", raw=b"\x00\x01"
    )
    addr, byts, mnem, ops = row_texts(insn)
    assert addr == "0x0000000000001000"
    assert len(addr) == ADDR_W and len(byts) == BYTES_W and len(mnem) == MNEM_W
    assert byts.startswith("00 01")
    assert ops == "x0, x1"


# ── grid state machine ────────────────────────────────────────────────────────


def _built_grid(instrs, name: str = "dg") -> DisasmGrid:
    """A grid built into a throwaway window (the real construction path)."""
    with dpg.window(tag=f"win-{name}"):
        pass
    grid = DisasmGrid(name=name)
    grid.build(f"win-{name}")
    grid.set_instrs(instrs)
    return grid


def _insns(n: int, base: int = BASE) -> list[DisasmInstruction]:
    return [
        DisasmInstruction(addr=base + 4 * i, size=4, mnemonic="nop", op_str="", raw=NOP)
        for i in range(n)
    ]


def test_grid_set_instrs_and_navigation(dpg_context) -> None:
    grid = _built_grid(_insns(100))
    assert grid.cursor == 0 and grid.top_row == 0
    grid.move_to(3)
    assert grid.cursor == 3
    grid.move_to(-5)  # clamps
    assert grid.cursor == 0
    grid.move_to(10**9)
    assert grid.cursor == 99
    grid._key_page_up()  # vis rows back up
    assert grid.cursor == 99 - grid.vis
    grid._key_home()
    assert grid.cursor == 0
    grid._key_down()
    assert grid.cursor == 1
    grid._key_up()
    assert grid.cursor == 0
    grid._key_end()
    assert grid.cursor == 99
    assert grid.top_row <= 99  # the view scrolled to keep it visible


def test_grid_scrolling_follows_cursor(dpg_context) -> None:
    grid = _built_grid(_insns(200))
    grid.move_to(150)
    assert grid.top_row <= 150 < grid.top_row + grid.vis
    grid.move_to(0)
    assert grid.top_row == 0


def test_grid_set_instrs_resets(dpg_context) -> None:
    grid = _built_grid(_insns(100))
    grid.move_to(50)
    grid.set_instrs(_insns(10))
    assert grid.cursor == 0 and grid.top_row == 0 and len(grid.instrs()) == 10


def test_grid_paints_visible_pool(dpg_context) -> None:
    """Painted pool rows carry the fixed-width cell texts (headless)."""
    grid = _built_grid(_insns(5))
    grid.tick()  # repaints (relayout/measure no-op headless)
    r0 = f"{grid._grid_tag}-r0"
    assert dpg.get_value(f"{r0}-addr") == f"0x{BASE:016x}"
    assert dpg.get_value(f"{r0}-c0").startswith("1f 20 03 d5")
    assert dpg.get_value(f"{r0}-c1").startswith("nop")
    # blank rows past the data
    r5 = f"{grid._grid_tag}-r5"
    assert dpg.get_value(f"{r5}-addr") == " " * ADDR_W


def test_grid_paint_cursor_row_highlight(dpg_context) -> None:
    from cheerleader.gui.hexgrid import COL_CURSOR

    grid = _built_grid(_insns(5))
    grid.move_to(2)
    grid.tick()
    cells = dpg.get_item_children(f"{grid._grid_tag}-r2")[1]
    cfg = dpg.get_item_configuration(cells[1])["color"]
    assert tuple(round(c * 255) for c in cfg) == COL_CURSOR
    other = dpg.get_item_children(f"{grid._grid_tag}-r0")[1]
    cfg0 = dpg.get_item_configuration(other[1])["color"]
    assert tuple(round(c * 255) for c in cfg0) != COL_CURSOR


def test_grid_click_selects_row(dpg_context, monkeypatch) -> None:
    """Desktop-faithful simulation: measured geometry + mouse position."""
    grid = _built_grid(_insns(50))
    geom = DisasmGeometry(cw=9.0, row_h=18.0, origin_y=6.0)
    grid._geometry = geom
    monkeypatch.setattr(dpg, "is_item_hovered", lambda _item: False)
    monkeypatch.setattr(
        dpg, "get_mouse_pos", lambda local=False: [10.0, 6.0 + 18.0 * 4 + 5.0]
    )
    monkeypatch.setattr(dpg, "get_item_rect_min", lambda _item: [0.0, 0.0])
    monkeypatch.setattr(dpg, "get_item_rect_max", lambda _item: [500.0, 500.0])
    grid._on_click()
    assert grid.cursor == 4  # row 4 (0-based) of the visible window


def test_grid_click_ignores_empty_rows(dpg_context, monkeypatch) -> None:
    grid = _built_grid(_insns(3))
    grid._geometry = DisasmGeometry(cw=9.0, row_h=18.0, origin_y=6.0)
    monkeypatch.setattr(dpg, "is_item_hovered", lambda _item: False)
    monkeypatch.setattr(
        dpg, "get_mouse_pos", lambda local=False: [10.0, 6.0 + 18.0 * 20]
    )
    monkeypatch.setattr(dpg, "get_item_rect_min", lambda _item: [0.0, 0.0])
    monkeypatch.setattr(dpg, "get_item_rect_max", lambda _item: [500.0, 500.0])
    grid._on_click()
    assert grid.cursor == 0  # past-EOF click ignored


def test_grid_click_survives_missing_rect_state(dpg_context, monkeypatch) -> None:
    """Clicking while the grid is hidden/not laid out must not crash.

    Live report: a hidden item's state dict carries no ``rect_min``, so
    ``get_item_rect_min`` raised ``KeyError`` out of the global click
    handler (hex-pane shown → grid area hidden → click anywhere).
    """

    def _no_rect(_item):
        raise KeyError("rect_min")

    grid = _built_grid(_insns(10))
    grid._geometry = DisasmGeometry(cw=9.0, row_h=18.0, origin_y=6.0)
    monkeypatch.setattr(dpg, "is_item_hovered", lambda _item: False)
    monkeypatch.setattr(dpg, "get_item_rect_min", _no_rect)
    monkeypatch.setattr(dpg, "get_item_rect_max", _no_rect)
    monkeypatch.setattr(dpg, "get_mouse_pos", lambda local=False: [10.0, 10.0])
    grid._on_click()
    assert grid.cursor == 0  # quiet no-op, no exception


def test_grid_click_after_delete_is_quiet(dpg_context) -> None:
    """A deleted grid (window-close race) ignores clicks instead of raising."""
    grid = _built_grid(_insns(10))
    grid._geometry = DisasmGeometry(cw=9.0, row_h=18.0, origin_y=6.0)
    dpg.delete_item(grid._grid_tag)
    grid._on_click()
    assert grid.cursor == 0


def test_grid_shutdown_removes_registry(dpg_context) -> None:
    grid = _built_grid(_insns(3))
    assert dpg.does_item_exist(grid._input_tag)
    grid.shutdown()
    assert not dpg.does_item_exist(grid._input_tag)


# ── the window + controller ───────────────────────────────────────────────────


def test_open_auto_disassembles_text(dpg_context, tmp_path) -> None:
    path = _macho_text(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = _info(path)
    open_disassembly(app, path)
    assert len(app.tools) == 1
    tool = app.tools[0]
    ctl = tool.controller
    assert ctl.view_kind == "disasm" and ctl.path == path
    assert ctl.sections == [("__TEXT", "__text"), ("__TEXT", "__cstring")]
    assert dpg.get_value(tool.status_tag) == "Disassembling __TEXT,__text…"
    _settle(app, ctl)
    assert len(ctl.grid.instrs()) == 64  # the 64 NOPs, capstone-disassembled
    assert ctl.grid.instrs()[0].addr == BASE
    assert ctl.call_graph is not None
    assert dpg.get_value(tool.status_tag) == ("__TEXT,__text  64 instructions")
    assert isinstance(tool, Tool) and tool.tick is not None


def test_section_switch(dpg_context, tmp_path) -> None:
    path = _macho_text(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = _info(path)
    ctl = _open(app, _info(path))
    _settle(app, ctl)
    # user picks __cstring in the dropdown (DPG sets the value, then fires)
    dpg.set_value(ctl.combo_tag, section_label("__TEXT", "__cstring"))
    dpg.get_item_callback(ctl.combo_tag)(
        ctl.combo_tag, section_label("__TEXT", "__cstring")
    )
    _settle(app, ctl)
    assert ctl._current_index == 1
    assert dpg.get_value(ctl.combo_tag) == section_label("__TEXT", "__cstring")
    assert len(ctl.grid.instrs()) == 8  # 0x20 bytes of the cstring section
    assert "counters" not in dpg.get_value(ctl.status_tag)


def test_stale_disasm_dropped(dpg_context, tmp_path) -> None:
    """Pick twice without settling: only the newest completion lands."""
    path = _macho_text(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = _info(path)
    ctl = _open(app, _info(path))
    first = ctl.busy.worker
    first.thread.join(timeout=10.0)  # done, but not drained yet
    dpg.get_item_callback(ctl.combo_tag)(
        ctl.combo_tag, section_label("__TEXT", "__cstring")
    )  # switch while pending
    second = ctl.busy.worker
    second.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        app.dispatch(event)  # first's event: busy.worker is second → dropped
    assert len(ctl.grid.instrs()) == 8  # the __cstring result only
    assert app.bridge.drain() == []


def test_cancelled_disasm_event_dropped(dpg_context) -> None:
    """A completion from a cancelled worker never reaches the grid."""
    app = GuiApp()
    info = _info()
    ctl = _open(app, info)
    ctl.sections = [("__TEXT", "__text")]
    handle = WorkerHandle()
    handle.cancel.cancel()  # Esc'd run
    ctl.busy.start("disassembling", handle)
    payload = ("__TEXT", "__text", _insns(4), None)
    ctl.on_event(UiEvent(0, EventKind.DISASM_DONE, payload, handle))
    assert ctl.grid.instrs() == []


def test_engine_missing_fails_soft(dpg_context, tmp_path, monkeypatch) -> None:
    import cheerleader.gui.disasm as disasm_mod

    path = _macho_text(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = _info(path)
    ctl = _open(app, _info(path))
    payload = ("__TEXT", "__text", [], None)
    for engine in (None, "capstone"):
        monkeypatch.setattr(disasm_mod, "pick_engine", lambda e=engine: e)
        handle = WorkerHandle()  # fresh in-flight run for each crafted event
        ctl.busy.start("disassembling", handle)
        ctl.on_event(UiEvent(0, EventKind.DISASM_DONE, payload, handle))
        status = dpg.get_value(ctl.status_tag)
        if engine is None:
            assert "no disassembler available" in status
        else:
            assert "unavailable or section unreadable" in status


def test_no_exec_sections_is_idle(dpg_context) -> None:
    """A binary without executable sections opens idle (no worker)."""
    app = GuiApp()
    info = _info()
    info.segments[0].initprot = 3  # rw-: nothing executable
    ctl = _open(app, info)
    assert ctl.sections == []
    assert ctl.busy.worker is None
    assert dpg.get_item_configuration(ctl.combo_tag)["enabled"] is False
    assert dpg.get_value(ctl.combo_tag) == "(no executable sections)"
    assert dpg.get_value(ctl.status_tag) == "Select a section to disassemble"
    # a placeholder pick on the disabled combo is a no-op
    dpg.get_item_callback(ctl.combo_tag)(ctl.combo_tag, "(no executable sections)")
    assert ctl.busy.worker is None


def test_combo_populated_and_auto_picked(dpg_context, tmp_path) -> None:
    """The dropdown lists the exec sections; the auto-pick is preselected."""
    path = _macho_text(tmp_path)  # real file: the worker reads section bytes
    app = GuiApp()
    info = _info(path, with_data=True)
    info.segments[0].initprot = 5  # __TEXT executable
    info.segments[1].initprot = 3  # __DATA not executable
    ctl = _open(app, info)
    assert dpg.get_item_configuration(ctl.combo_tag)["items"] == [
        section_label("__TEXT", "__text"),
        section_label("__TEXT", "__cstring"),
    ]
    assert dpg.get_value(ctl.combo_tag) == section_label("__TEXT", "__text")
    assert ctl._current_index == 0
    assert ctl.busy.worker is not None  # auto-disassembly started
    _settle(app, ctl)
    assert len(ctl.grid.instrs()) == 64


def test_window_layout_combo_and_pane(dpg_context) -> None:
    """Combo row on top, grid pane below it (parent-relative height)."""
    app = GuiApp()
    info = _info()
    ctl = _open(app, info)
    assert dpg.get_item_configuration(ctl.combo_tag)["enabled"] is True
    # the pane fills the rest of the content area via a parent-relative
    # (negative) height → window resizes track without an explicit re-fit
    pane_cfg = dpg.get_item_configuration(ctl.grid._parent)
    assert pane_cfg["height"] == -COMBO_RESERVE
    assert dpg.does_item_exist(ctl.grid._grid_tag)


def test_escape_key_cancels(dpg_context, tmp_path) -> None:
    path = _macho_text(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = _info(path)
    ctl = _open(app, _info(path))
    assert ctl.busy.worker is not None
    ctl._on_escape()
    assert ctl.busy.worker.cancel.cancelled
    assert "cancelled" in dpg.get_value(ctl.status_tag)


# ── shell integration ─────────────────────────────────────────────────────────


def test_open_focus_guard(dpg_context, tmp_path) -> None:
    path = _macho_text(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = _info(path)
    open_disassembly(app, path)
    first = app.tools[0]
    open_disassembly(app, path)
    assert app.tools == [first]


def test_open_wrong_path_is_silent(dpg_context) -> None:
    app = GuiApp()
    app.current_info = _info("/x/other.bin")
    open_disassembly(app, "/x/t.bin")
    assert app.tools == []


def test_open_without_info_is_silent(dpg_context) -> None:
    app = GuiApp()
    open_disassembly(app, "/x/t.bin")
    assert app.tools == []


def test_protocol_readonly_and_teardown(dpg_context, tmp_path) -> None:
    path = _macho_text(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = _info(path)
    ctl = _open(app, _info(path))
    _settle(app, ctl)
    assert ctl.dirty_edit_count() == 0
    ctl.on_event(UiEvent(0, EventKind.PROGRESS, 0.5, None))  # ignored
    window = app.tools[0].window
    ctl.window_close_requested()  # read-only → immediate close
    assert app.tools == []
    assert not dpg.does_item_exist(window)
    assert not dpg.does_item_exist(f"disasm-keys-{ctl.ns}")


def test_quit_with_disasm_open_is_clean(dpg_context, tmp_path, monkeypatch) -> None:
    path = _macho_text(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = _info(path)
    _open(app, _info(path))
    _settle(app, app.tools[0].controller)
    stopped: list[bool] = []
    monkeypatch.setattr(dpg, "stop_dearpygui", lambda: stopped.append(True))
    app.request_quit()
    assert stopped == [True]
    assert app._quit_dlg is None


def test_file_pick_auto_opens_disassembly(dpg_context, tmp_path) -> None:
    """File → Open File… on a binary auto-opens the Disassembly window."""
    path = _macho_text(tmp_path)
    app = GuiApp()
    build_shell(app)
    dpg.get_item_callback(DLG_OPEN)(DLG_OPEN, {"selections": {"file_1": path}})
    worker = app._parse_worker
    worker.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        app.dispatch(event)
    assert app.current_info is not None
    assert len(app.tools) == 1  # auto-opened
    assert app.tools[0].controller.view_kind == "disasm"
    _settle(app, app.tools[0].controller)
    assert len(app.tools[0].controller.grid.instrs()) == 64


def test_file_pick_auto_open_focus_guard(dpg_context, tmp_path) -> None:
    """Re-picking the same file focuses the open view (no duplicate)."""
    path = _macho_text(tmp_path)
    app = GuiApp()
    build_shell(app)
    for _ in range(2):
        dpg.get_item_callback(DLG_OPEN)(DLG_OPEN, {"selections": {"file_1": path}})
        worker = app._parse_worker
        worker.thread.join(timeout=10.0)
        for event in app.bridge.drain():
            app.dispatch(event)
        _settle(app, app.tools[-1].controller)
    assert len(app.tools) == 1


def test_menu_item_opens_disassembly(dpg_context, tmp_path) -> None:
    path = _macho_text(tmp_path)
    app = GuiApp()
    build_shell(app)
    app.current_file = path
    app.current_info = _info(path)
    dpg.get_item_callback(MI_DISASM)()  # lazy-registers the openers
    assert len(app.tools) == 1
    assert app.tools[0].controller.view_kind == "disasm"
