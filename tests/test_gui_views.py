"""Headless tests for the D2 data-table views (plan v3, D2).

The views render straight from a synthetic :class:`BinaryInfo` — no file
parsing involved (the controller holds the snapshot object). Filter
buttons are driven via ``dpg.get_item_callback`` like every other widget.
"""

from __future__ import annotations

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg

from cheerleader.gui.app import (
    GuiApp,
    Tool,
    build_shell,
)
from cheerleader.gui.const import MI_SEGMENTS
from cheerleader.gui.tooling import tool_window_closed
from cheerleader.gui.views import (
    _SPECS,
    TableController,
    fmt_addr,
    open_inspector,
)
from cheerleader.libs.types import (
    BinaryInfo,
    ChainedFixup,
    Library,
    Section,
    Segment,
    Symbol,
)

PATH = "/x/sample.bin"


@pytest.fixture()
def dpg_context():
    dpg.create_context()
    yield
    dpg.destroy_context()


@pytest.fixture()
def info() -> BinaryInfo:
    """A synthetic binary: 2 segments / 3 sections / 2 imports /
    4 symbols (one stab) / 2 exports / 3 fixups (2 binds, 1 rebase)."""
    info = BinaryInfo(path=PATH, arch="arm64", bits=64, file_type="MH_EXECUTE")
    info.segments.append(
        Segment(
            "__TEXT",
            0x100000000,
            0x4000,
            0,
            0x4000,
            5,
            5,
            sections=[
                Section("__text", "__TEXT", 0x100000000, 0x1000, 0, 4, 0, 0, 0),
                Section("__cstring", "__TEXT", 0x100001000, 0x20, 0x1000, 0, 2, 0, 0),
            ],
        )
    )
    info.segments.append(
        Segment(
            "__DATA",
            0x100004000,
            0x2000,
            0x4000,
            0x1000,
            5,
            3,
            sections=[
                Section("__data", "__DATA", 0x100004000, 0x800, 0x4000, 3, 1, 0, 0)
            ],
        )
    )
    info.libraries.append(
        Library("/usr/lib/libSystem.dylib", "1292.0.0", "1.0.0", "LC_LOAD_DYLIB", 0x200)
    )
    info.libraries.append(
        Library("/usr/lib/libc.dylib", "1.0.0", "1.0.0", "LC_LOAD_WEAK_DYLIB", 0x280)
    )
    info.symbols.append(Symbol("_main", 0x100000000, 0x0F, 1, 0, True, False))
    info.symbols.append(Symbol("_local_fn", 0x100001000, 0x0E, 1, 0, False, False))
    info.symbols.append(Symbol("_extern", 0, 0x01, 0, 0, True, False))
    info.symbols.append(Symbol("<stab>", 0, 0x64, 0, 0, False, True))
    info.exports.append({"addr": 0x100000000, "flags": 0x0, "name": "_main"})
    info.exports.append({"addr": None, "flags": 0x2, "name": "_reexport"})
    info.chained_fixups.append(
        ChainedFixup("__DATA_CONST", 0x4000, "bind", 2, None, 0, False, None)
    )
    info.chained_fixups.append(
        ChainedFixup("__DATA_CONST", 0x4008, "bind", 1, "_foo", 16, False, None)
    )
    info.chained_fixups.append(
        ChainedFixup("__DATA", 0x4010, "rebase", None, None, 0, True, 0x100004000)
    )
    return info


@pytest.fixture()
def app(dpg_context, info) -> GuiApp:
    app = GuiApp()
    app.current_file = PATH
    app.current_info = info
    return app


# ── specs ─────────────────────────────────────────────────────────────────────


def test_fmt_addr() -> None:
    assert fmt_addr(None) == "—"
    assert fmt_addr(0x1234) == "0x0000000000001234"


def test_specs_cover_all_six_kinds() -> None:
    assert set(_SPECS) == {
        "segments",
        "sections",
        "imports",
        "symbols",
        "exports",
        "fixups",
    }
    for spec in _SPECS.values():
        if spec.filters is not None:
            assert spec.default_filter in {f.key for f in spec.filters}


def test_every_spec_renders_full_rows(info) -> None:
    """Row length matches the column count for every item of every view."""
    for spec in _SPECS.values():
        for i, item in enumerate(spec.items(info)):
            assert len(spec.row(item, i)) == len(spec.columns), spec.kind


# ── open_inspector ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind", ["segments", "sections", "imports", "symbols", "exports", "fixups"]
)
def test_open_view_registers_tool(app, kind) -> None:
    open_inspector(app, kind, PATH)
    assert len(app.tools) == 1
    tool = app.tools[0]
    ctl = tool.controller
    assert ctl.view_kind == kind
    assert ctl.path == PATH
    assert tool.grids == ()  # no hex grids in D2
    assert tool.tick == ctl.tick  # live search box maintenance
    assert dpg.get_item_label(tool.window).startswith(_SPECS[kind].title)
    assert dpg.get_value(tool.status_tag)  # status strip is populated


def test_open_view_row_count_matches_data(app, info) -> None:
    open_inspector(app, "segments", PATH)
    table = app.tools[0].controller.table_tag
    rows = dpg.get_item_children(table)[1]
    assert len(rows) == len(info.segments)
    # cells readable back from the widget tree
    cells = [_cell(c) for c in dpg.get_item_children(rows[0])[1]]
    assert cells[0] == "__TEXT"
    assert cells[6] == "2"  # section count


def test_open_view_focus_guard(app) -> None:
    """Re-opening the same (kind, path) focuses instead of duplicating."""
    open_inspector(app, "segments", PATH)
    first = app.tools[0]
    open_inspector(app, "segments", PATH)
    assert app.tools == [first]
    open_inspector(app, "symbols", PATH)  # other kind → separate window
    assert len(app.tools) == 2


def test_open_unknown_kind_is_silent(app) -> None:
    open_inspector(app, "disasm", PATH)  # lands in D3
    assert app.tools == []


def test_open_without_info_is_silent(dpg_context) -> None:
    app = GuiApp()
    open_inspector(app, "segments", PATH)
    assert app.tools == []


def test_open_path_mismatch_is_silent(app, info) -> None:
    other = BinaryInfo(path="/x/other.bin")
    app.current_info = other
    open_inspector(app, "segments", PATH)  # PATH is no longer current
    assert app.tools == []


# ── row formatting (TUI parity) ───────────────────────────────────────────────


def test_segment_row_values(info) -> None:
    spec = _SPECS["segments"]
    assert spec.row(info.segments[0], 0) == (
        "__TEXT",
        "0x0000000100000000",
        "16.0 KiB",
        "0x00000000",
        "16.0 KiB",
        "r-x/r-x",
        "2",
    )
    assert spec.row(info.segments[1], 1)[5] == "rw-/r-x"  # init 3 / max 5


def test_section_row_values(info) -> None:
    spec = _SPECS["sections"]
    assert spec.row(info.segments[0].sections[0], 0) == (
        "__TEXT",
        "__text",
        "0x0000000100000000",
        "4.0 KiB",
        "0x00000000",
        "2^4",
        "regular",
        "0",
    )
    assert spec.row(info.segments[0].sections[1], 1)[6] == "cstring_literals"
    assert spec.row(info.segments[1].sections[0], 2)[6] == "zerofill"


def test_import_row_values(info) -> None:
    spec = _SPECS["imports"]
    assert spec.row(info.libraries[0], 0) == (
        "1",
        "/usr/lib/libSystem.dylib",
        "1292.0.0",
        "1.0.0",
        "LC_LOAD_DYLIB",
        "0x00000200",
    )
    assert spec.row(info.libraries[1], 1)[0] == "2"  # 1-based numbering
    assert spec.row(info.libraries[1], 1)[4] == "LC_LOAD_WEAK_DYLIB"


def test_symbol_row_values(info) -> None:
    spec = _SPECS["symbols"]
    assert spec.row(info.symbols[0], 0) == (
        "0x0000000100000000",
        "SECT",
        "1",
        "global",
        "_main",
    )
    assert spec.row(info.symbols[1], 1)[3] == "local"
    assert spec.row(info.symbols[2], 2) == (
        "0x0000000000000000",
        "UNDEF",
        "0",
        "global",
        "_extern",
    )
    assert spec.row(info.symbols[3], 3)[1] == "STAB"


def test_export_row_values(info) -> None:
    spec = _SPECS["exports"]
    assert spec.row(info.exports[0], 0) == ("0x0000000100000000", "0x0000", "_main")
    assert spec.row(info.exports[1], 1) == ("—", "0x0002", "_reexport")  # addr None


def test_fixup_row_values(info) -> None:
    spec = _SPECS["fixups"]
    bind_ordinal, bind_named, rebase = info.chained_fixups
    assert spec.row(bind_named, 0) == (
        "__DATA_CONST",
        "0x0000000000004008",
        "bind",
        "bind",
        "_foo",
        "_foo",
        "16",
    )
    # unnamed library → resolved ordinal
    assert spec.row(bind_ordinal, 1)[4] == "lib#2"
    assert spec.row(bind_ordinal, 1)[6] == "0"  # zero addend renders "0"
    # rebases: library "—", target address
    assert spec.row(rebase, 2) == (
        "__DATA",
        "0x0000000000004010",
        "rebase",
        "rebase",
        "—",
        "0x0000000100004000",
        "0",
    )


# ── filters ───────────────────────────────────────────────────────────────────


def _cell(tag) -> str:
    """Cell text: a clickable row's selectable label, else the text value."""
    return dpg.get_item_label(tag) or dpg.get_value(tag)


def _table_rows(ctl: TableController) -> list[list[str]]:
    rows = dpg.get_item_children(ctl.table_tag)[1]
    return [[_cell(c) for c in dpg.get_item_children(r)[1]] for r in rows]


def test_symbols_default_filter_is_no_stabs(app) -> None:
    open_inspector(app, "symbols", PATH)
    ctl = app.tools[0].controller
    assert ctl._filter == "no-stabs"
    rows = _table_rows(ctl)
    assert len(rows) == 3  # the stab entry is hidden
    assert all(r[1] != "STAB" for r in rows)
    assert dpg.get_value(ctl.status_tag) == (
        "3 of 4 symbols — filter: No-stabs  ·  click a row to view it in the hex editor"
    )


def test_symbols_filter_buttons(app) -> None:
    open_inspector(app, "symbols", PATH)
    ctl = app.tools[0].controller
    ns = ctl.ns
    for key, want in (
        ("all", 4),
        ("external", 2),
        ("undefined", 1),
        ("no-stabs", 3),
    ):
        dpg.get_item_callback(f"{ns}-flt-{key}")()
        assert len(_table_rows(ctl)) == want, key
        assert ctl._filter == key
        # the active button carries the ● marker
        assert dpg.get_item_label(f"{ns}-flt-{key}").startswith("● ")
    # all markers cleared on non-active buttons
    assert dpg.get_item_label(f"{ns}-flt-all") == "All"


def test_fixups_default_filter_is_all(app) -> None:
    open_inspector(app, "fixups", PATH)
    ctl = app.tools[0].controller
    assert ctl._filter == "all"
    assert len(_table_rows(ctl)) == 3


def test_fixups_binds_rebases(app) -> None:
    open_inspector(app, "fixups", PATH)
    ctl = app.tools[0].controller
    ns = ctl.ns
    dpg.get_item_callback(f"{ns}-flt-binds")()
    rows = _table_rows(ctl)
    assert [r[3] for r in rows] == ["bind", "bind"]
    dpg.get_item_callback(f"{ns}-flt-rebases")()
    rows = _table_rows(ctl)
    assert [r[3] for r in rows] == ["rebase"]
    assert dpg.get_value(ctl.status_tag) == (
        "1 of 3 fixups — filter: Rebases  ·  click a row to view it in the hex editor"
    )


HINT = "  ·  click a row to view it in the hex editor"


def test_unfiltered_views_show_plain_status(app) -> None:
    open_inspector(app, "segments", PATH)
    assert dpg.get_value(app.tools[0].status_tag) == "2 segments" + HINT
    open_inspector(app, "imports", PATH)
    assert dpg.get_value(app.tools[1].status_tag) == "2 imports" + HINT


# ── lifecycle / protocol ──────────────────────────────────────────────────────


def test_view_keeps_snapshot_when_current_info_is_replaced(app, info) -> None:
    """Re-parsing a new file must not touch already-open view windows."""
    open_inspector(app, "segments", PATH)
    ctl = app.tools[0].controller
    replacement = BinaryInfo(path="/x/next.bin")
    app.current_info = replacement
    app.current_file = "/x/next.bin"
    assert ctl.info is info  # private snapshot untouched
    ctl.refresh()  # even an explicit re-render reads the old snapshot
    assert _table_rows(ctl)[0][0] == "__TEXT"


def test_readonly_protocol(app) -> None:
    open_inspector(app, "segments", PATH)
    ctl = app.tools[0].controller
    assert ctl.dirty_edit_count() == 0
    ctl.on_event(None)  # no workers → events are ignored, not crashed on


def test_window_close_requested_closes_tool(app) -> None:
    """Read-only views close immediately (no dirty confirmation)."""
    open_inspector(app, "symbols", PATH)
    tool = app.tools[0]
    ctl = tool.controller
    ctl.window_close_requested()
    assert app.tools == []
    assert not dpg.does_item_exist(tool.window)


def test_close_tool_via_shell(app) -> None:
    open_inspector(app, "exports", PATH)
    tool = app.tools[0]
    app.close_tool(tool)
    assert app.tools == []
    assert not dpg.does_item_exist(tool.window)


def test_on_close_button_runs_controller(app) -> None:
    """The window's X (on_close) routes through window_close_requested."""
    open_inspector(app, "segments", PATH)
    window = app.tools[0].window
    # on_close is not a plain callback; drive the same handler it is
    # wired to (headless stand-in for clicking the X).
    tool_window_closed(app, window)
    assert app.tools == []


def test_quit_with_views_open_is_clean(app, monkeypatch) -> None:
    """Views are read-only → Ctrl+Q quits without any confirmation."""
    open_inspector(app, "segments", PATH)
    open_inspector(app, "symbols", PATH)
    stopped: list[bool] = []
    monkeypatch.setattr(dpg, "stop_dearpygui", lambda: stopped.append(True))
    app.request_quit()
    assert stopped == [True]
    assert app._quit_dlg is None


# ── menu integration ──────────────────────────────────────────────────────────


def test_disasm_menu_item_opens_view(dpg_context, info) -> None:
    """Disassembler → Segments… (menu callback) opens the view window."""
    from cheerleader.gui.app import DLG_OPEN

    app = GuiApp()
    build_shell(app)
    app.current_file = PATH
    app.current_info = info
    dpg.get_item_callback(MI_SEGMENTS)()  # lazy-registers the openers
    assert len(app.tools) == 1
    assert app.tools[0].controller.view_kind == "segments"
    assert dpg.does_item_exist(DLG_OPEN)  # shell intact


def test_opened_view_is_a_tool_instance(app) -> None:
    open_inspector(app, "segments", PATH)
    assert isinstance(app.tools[0], Tool)


# ── Phase 3: sort, search, clipper, row jump ──────────────────────────────────


def test_tables_are_sortable_and_clipped(app) -> None:
    open_inspector(app, "segments", PATH)
    cfg = dpg.get_item_configuration(app.tools[0].controller.table_tag)
    assert cfg["sortable"] is True
    assert cfg["clipper"] is True


def test_sort_by_column_header(app) -> None:
    open_inspector(app, "symbols", PATH)
    ctl = app.tools[0].controller
    ctl._on_filter("all")
    name_col = ctl._columns[4]
    ctl._on_sort(ctl.table_tag, [[name_col, 1]])  # Name ascending
    names = [r[4] for r in _table_rows(ctl)]
    assert names == ["<stab>", "_extern", "_local_fn", "_main"]
    ctl._on_sort(ctl.table_tag, [[name_col, -1]])  # descending
    assert _table_rows(ctl)[0][4] == "_main"
    addr_col = ctl._columns[0]
    ctl._on_sort(ctl.table_tag, [[addr_col, -1]])  # numeric key, not text
    assert _table_rows(ctl)[0][4] == "_local_fn"
    ctl._on_sort(ctl.table_tag, None)  # cleared → snapshot order
    assert _table_rows(ctl)[0][4] == "_main"


def test_import_numbers_survive_sorting(app) -> None:
    """``#`` is the snapshot position, not the on-screen row."""
    open_inspector(app, "imports", PATH)
    ctl = app.tools[0].controller
    ctl._on_sort(ctl.table_tag, [[ctl._columns[1], 1]])  # Name ascending
    rows = _table_rows(ctl)
    assert [(r[0], r[1]) for r in rows] == [
        ("2", "/usr/lib/libc.dylib"),
        ("1", "/usr/lib/libSystem.dylib"),
    ]


def test_search_box_filters_live(app) -> None:
    open_inspector(app, "symbols", PATH)
    ctl = app.tools[0].controller
    assert dpg.get_item_configuration(ctl.search_tag)["hint"] == "Search symbols…"
    dpg.set_value(ctl.search_tag, "FN")
    ctl.tick()
    assert [r[4] for r in _table_rows(ctl)] == ["_local_fn"]
    assert dpg.get_value(ctl.status_tag).startswith(
        "1 of 4 symbols matching 'FN' — filter: No-stabs"
    )


def test_views_without_search_have_no_box(app) -> None:
    open_inspector(app, "segments", PATH)
    assert not dpg.does_item_exist(app.tools[0].controller.search_tag)


def test_row_offsets_map_to_file_bytes(info) -> None:
    seg_off = _SPECS["segments"].offset
    assert seg_off(info, info.segments[1]) == 0x4000
    sym_off = _SPECS["symbols"].offset
    assert sym_off(info, info.symbols[1]) == 0x1000  # _local_fn
    assert sym_off(info, info.symbols[2]) is None  # undefined → no bytes
    assert _SPECS["imports"].offset(info, info.libraries[1]) == 0x280


def test_row_click_without_bytes_says_so(app, info) -> None:
    open_inspector(app, "symbols", PATH)
    ctl = app.tools[0].controller
    ctl._on_row(None, None, info.symbols[2])  # undefined symbol
    assert dpg.get_value(ctl.status_tag) == "This row has no bytes in the file"
    assert len(app.tools) == 1


def test_row_click_opens_editor_at_offset(dpg_context, info, tmp_path) -> None:
    path = tmp_path / "sample.bin"
    path.write_bytes(bytes(0x6000))
    info.path = str(path)
    app = GuiApp()
    app.current_file, app.current_info = str(path), info
    open_inspector(app, "sections", str(path))
    ctl = app.tools[0].controller
    ctl._on_row(None, None, info.segments[0].sections[1])  # __cstring @ 0x1000
    editor = app.tools[1].controller
    assert editor.doc.path == str(path)
    assert editor.grid.cursor == 0x1000
    assert dpg.get_value(ctl.status_tag) == "Jumped to 0x00001000 in the hex editor"


def test_sort_callback_after_close_is_ignored(app) -> None:
    """DPG may deliver a queued sort callback after the window is gone."""
    open_inspector(app, "segments", PATH)
    ctl = app.tools[0].controller
    app.close_tool(app.tools[0])
    ctl._on_sort(None, [[0, 1]])  # must not raise
