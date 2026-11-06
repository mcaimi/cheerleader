"""GUI: choosing the architecture slice of a fat (universal) Mach-O binary."""

from __future__ import annotations

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg
from machofixtures import thin_macho, write_fat
from test_gui_callflow import _graph, _loop_cfg

from cheerleader.formats.macho import parse
from cheerleader.gui import app as app_mod
from cheerleader.gui import disasm as disasm_mod
from cheerleader.gui.app import GuiApp, build_shell, default_slice, open_file
from cheerleader.gui.callflow import open_call_flow, open_cfg
from cheerleader.gui.const import (
    SHELL_FILL,
    SHELL_STATUS,
    SHELL_STATUS_RIGHT,
    SLICE_MENU,
)
from cheerleader.gui.tooling import find_tool


@pytest.fixture()
def dpg_context():
    dpg.create_context()
    try:
        yield
    finally:
        dpg.destroy_context()


@pytest.fixture()
def native_arm64(monkeypatch):
    monkeypatch.setattr(app_mod.platform, "machine", lambda: "arm64")


def _settle(app: GuiApp) -> None:
    """Join the shell's parse worker (and tool workers), dispatch events."""
    for _ in range(3):
        workers = [app._parse_worker] + [
            getattr(getattr(t.controller, "busy", None), "worker", None)
            for t in app.tools
        ]
        for w in workers:
            if w is not None and w.thread is not None:
                w.thread.join(timeout=10.0)
        for event in app.bridge.drain():
            app.dispatch(event)


def _open_fat(tmp_path) -> tuple[GuiApp, str]:
    path = write_fat(tmp_path / "fat.bin")
    app = GuiApp()
    build_shell(app)
    open_file(app, path)
    _settle(app)
    return app, path


def _slice_menu_labels() -> list[str]:
    return [dpg.get_item_label(c) for c in dpg.get_item_children(SLICE_MENU)[1]]


def _titles(app: GuiApp) -> list[str]:
    return [dpg.get_item_label(t.window) for t in app.tools]


def _shell_texts(tag=SHELL_FILL) -> list[str]:
    out: list[str] = []
    for child in dpg.get_item_children(tag)[1]:
        kind = dpg.get_item_info(child)["type"]
        if kind.endswith("mvText"):
            out.append(dpg.get_value(child))
        elif kind.endswith("mvCombo"):
            out.append(f"combo:{dpg.get_value(child)}")
        out.extend(_shell_texts(child))
    return out


# ── default slice ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "machine, slices, expected",
    [
        ("arm64", [(0, "x86_64"), (1, "arm64")], 1),
        ("arm64", [(0, "x86_64"), (1, "arm64e"), (2, "arm64e.x1")], 1),  # variant
        ("arm64", [(0, "arm64e"), (1, "arm64")], 1),  # exact beats variant
        ("x86_64", [(0, "arm64"), (1, "x86_64h"), (2, "x86_64")], 2),
        ("riscv64", [(0, "x86_64"), (1, "arm64")], 0),  # no match: slice 0
        ("arm64", [], 0),
    ],
)
def test_default_slice_prefers_native(monkeypatch, machine, slices, expected) -> None:
    monkeypatch.setattr(app_mod.platform, "machine", lambda: machine)
    assert default_slice(slices) == expected


# ── opening a fat binary ──────────────────────────────────────────────────────


def test_open_fat_loads_native_slice(dpg_context, tmp_path, native_arm64) -> None:
    app, path = _open_fat(tmp_path)
    assert app.current_slices == [(0, "x86_64"), (1, "arm64")]
    assert app.current_slice == 1
    assert app.current_info.arch == "arm64"
    assert app.current_info.slice_offset == 0x2000
    assert dpg.get_value(SHELL_STATUS) == path  # left: just the path
    assert dpg.get_value(SHELL_STATUS_RIGHT) == (
        f"arm64  (slice 2 of 2)   │   engine: {disasm_mod.engine_label()}"
    )
    assert _slice_menu_labels() == ["[0] x86_64", "● [1] arm64"]
    assert _titles(app) == ["Disassembly — fat.bin [arm64]"]
    texts = _shell_texts()
    assert "Universal binary — 2 slices   " in texts
    assert "combo:[1] arm64" in texts


def test_thin_file_has_no_slice_choice(dpg_context, tmp_path) -> None:
    path = tmp_path / "thin.bin"
    path.write_bytes(thin_macho())
    app = GuiApp()
    build_shell(app)
    open_file(app, str(path))
    _settle(app)
    assert not app.is_fat
    assert _slice_menu_labels() == ["(single architecture)"]
    assert dpg.get_value(SHELL_STATUS) == str(path)
    assert dpg.get_value(SHELL_STATUS_RIGHT).startswith("arm64   │   engine: ")
    assert _titles(app) == ["Disassembly — thin.bin"]  # no arch suffix
    assert not any(t.startswith("combo:") for t in _shell_texts())


# ── switching ─────────────────────────────────────────────────────────────────


def test_switch_slice_keeps_old_windows(dpg_context, tmp_path, native_arm64) -> None:
    app, _path = _open_fat(tmp_path)
    app.select_slice(0)
    assert app.current_info is None  # re-parsing; menu grayed meanwhile
    _settle(app)
    assert app.current_info.arch == "x86_64"
    assert app.current_info.slice_offset == 0x1000
    assert _titles(app) == [
        "Disassembly — fat.bin [arm64]",  # the previous slice's snapshot
        "Disassembly — fat.bin [x86_64]",
    ]
    assert _slice_menu_labels() == ["● [0] x86_64", "[1] arm64"]
    assert dpg.get_value(SHELL_STATUS_RIGHT).startswith("x86_64  (slice 1 of 2)")
    app.select_slice(0)  # current slice: no-op
    assert app.current_info is not None


def test_menu_item_switches_slice(dpg_context, tmp_path, native_arm64) -> None:
    app, _ = _open_fat(tmp_path)
    first = dpg.get_item_children(SLICE_MENU)[1][0]
    dpg.get_item_configuration(first)["callback"]()
    _settle(app)
    assert app.current_slice == 0 and app.current_info.arch == "x86_64"


def test_quick_double_switch_lands_last(dpg_context, tmp_path, native_arm64) -> None:
    app, _ = _open_fat(tmp_path)
    app.select_slice(0)
    app.select_slice(1)  # supersedes the slice-0 parse
    _settle(app)
    assert app.current_slice == 1
    assert app.current_info.arch == "arm64"
    assert len(app.tools) == 1  # the slice-0 parse never opened a window


def test_same_view_per_slice(dpg_context, tmp_path, native_arm64) -> None:
    app, path = _open_fat(tmp_path)
    app_mod.INSPECTOR_OPENERS["segments"](app, path)
    app.select_slice(0)
    _settle(app)
    app_mod.INSPECTOR_OPENERS["segments"](app, path)
    app_mod.INSPECTOR_OPENERS["segments"](app, path)  # same slice: focus
    segs = [t for t in app.tools if t.controller.view_kind == "segments"]
    assert [dpg.get_item_label(t.window) for t in segs] == [
        "Segments — fat.bin [arm64]",
        "Segments — fat.bin [x86_64]",
    ]
    assert {t.controller.slice_offset for t in segs} == {0x1000, 0x2000}


def test_strings_of_selected_slice(dpg_context, tmp_path, native_arm64) -> None:
    app, path = _open_fat(tmp_path)
    app_mod.INSPECTOR_OPENERS["strings"](app, path)
    _settle(app)
    ctl = find_tool(app, view_kind="strings", path=path).controller
    values = [s.value for s in ctl._all]
    assert "arm payload" in values and "x86 payload" not in values
    hit = next(s for s in ctl._all if s.value == "arm payload")
    ctl._on_row(None, None, hit)  # jump: absolute offset inside slice 1
    editor = next(t for t in app.tools if getattr(t.controller, "doc", None))
    assert editor.controller.grid.cursor == 0x2000 + 0x208


def test_graph_windows_are_per_slice(dpg_context, tmp_path) -> None:
    path = write_fat(tmp_path / "fat.bin")
    x86, arm = parse(path, slice_index=0), parse(path, slice_index=1)
    app = GuiApp()
    for info in (x86, arm, arm):
        open_cfg(app, path, _loop_cfg(), info=info)
        open_call_flow(app, path, _graph(), "_main", info=info)
    assert sorted(_titles(app)) == sorted(
        [
            "Call flow — _main [arm64]",
            "Call flow — _main [x86_64]",
            "CFG — _loop [arm64]  ·  4 blocks",
            "CFG — _loop [x86_64]  ·  4 blocks",
        ]
    )


# ── bottom bar: path left, architecture + engine right ────────────────────────


def test_engine_label(monkeypatch) -> None:
    monkeypatch.setattr(disasm_mod, "_OVERRIDE", "auto")
    monkeypatch.setattr(disasm_mod, "pick_engine", lambda: "capstone")
    assert disasm_mod.engine_label() == "Auto (capstone)"
    monkeypatch.setattr(disasm_mod, "pick_engine", lambda: None)
    assert disasm_mod.engine_label() == "Auto (none available)"
    monkeypatch.setattr(disasm_mod, "_OVERRIDE", "objdump")
    assert disasm_mod.engine_label() == "objdump (unavailable)"
    monkeypatch.setattr(disasm_mod, "pick_engine", lambda: "objdump")
    assert disasm_mod.engine_label() == "objdump"


def test_engine_change_refreshes_bar(dpg_context, tmp_path, monkeypatch) -> None:
    from cheerleader.gui.const import MI_ENGINE_AUTO, MI_ENGINE_CAPSTONE

    monkeypatch.setattr(disasm_mod, "_OVERRIDE", "auto")  # restored afterwards
    monkeypatch.setattr(disasm_mod, "engine_available", lambda name: True)
    path = tmp_path / "thin.bin"
    path.write_bytes(thin_macho())
    app = GuiApp()
    build_shell(app)
    open_file(app, str(path))
    _settle(app)
    dpg.get_item_callback(MI_ENGINE_CAPSTONE)()
    assert dpg.get_value(SHELL_STATUS_RIGHT).endswith("engine: capstone")
    dpg.get_item_callback(MI_ENGINE_AUTO)()
    assert "engine: Auto (" in dpg.get_value(SHELL_STATUS_RIGHT)


def test_non_executable_bar(dpg_context, tmp_path) -> None:
    path = tmp_path / "raw.bin"
    path.write_bytes(b"\x00" * 512)
    app = GuiApp()
    build_shell(app)
    open_file(app, str(path))
    _settle(app)
    left = dpg.get_value(SHELL_STATUS)
    assert left.startswith(f"{path}  │  Disassembler unavailable")
    assert dpg.get_value(SHELL_STATUS_RIGHT).startswith("—   │   engine: ")


def test_note_stays_left_of_the_bar(dpg_context, tmp_path) -> None:
    from cheerleader.gui.app import close_all_windows

    path = tmp_path / "thin.bin"
    path.write_bytes(thin_macho())
    app = GuiApp()
    build_shell(app)
    open_file(app, str(path))
    _settle(app)
    app_mod.open_editor(app, str(path))
    editor = next(t for t in app.tools if getattr(t.controller, "doc", None))
    editor.controller.type_hex_digit(0xA)  # dirty: survives "Close all"
    close_all_windows(app)
    assert dpg.get_value(SHELL_STATUS) == (
        f"{path}  │  1 window with unsaved edits left open"
    )
    assert dpg.get_value(SHELL_STATUS_RIGHT).startswith("arm64")
    open_file(app, str(path))  # re-loading clears the note
    assert dpg.get_value(SHELL_STATUS) == str(path)
