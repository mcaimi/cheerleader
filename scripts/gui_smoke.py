#!/usr/bin/env python3
"""Smoke-test the cheerleader-gui front-end (`make gui-smoke`).

Two parts:

1. **Headless flow checks** (run anywhere): the empty shell + the
   ``open_editor`` / ``open_compare`` flows are driven in throwaway Dear
   PyGui contexts without a display (``create_context`` / ``build_shell`` /
   ``destroy_context`` are display-free); the compare scan worker is joined.
2. **Live watchdog launch** (desktop only): the no-argument ``cheerleader-gui``
   (empty shell + Tools menu) is started as a real process. Still alive after
   the timeout → booted fine, terminated and counted as a pass (the same
   timeout-as-success convention as the TUI's ``make standalone-tui-test``).
   A non-zero exit (crash, import error) fails the smoke. The menu-driven
   tool flows are interactive — they are covered by the headless part and by
   manual desktop validation. Skipped with a note when no ``DISPLAY`` /
   ``WAYLAND_DISPLAY`` is present.

usage: uv run python scripts/gui_smoke.py [--timeout SECONDS]
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import tempfile

NOP = b"\x1f\x20\x03\xd5"  # arm64 nop — the fixture's __text payload

CHILD_MAIN = "from cheerleader.gui import main; main()"


def _write_inputs(tmp: str) -> tuple[str, str]:
    # a: valid minimal Mach-O (one LC_SEGMENT_64/__text) + payload — the
    # editor flow's pick exercises the background binary parse and the D2
    # table views get a real segment row (plan v3, D1+D2).
    a = os.path.join(tmp, "a.bin")
    b = os.path.join(tmp, "b.bin")
    header = b"\xcf\xfa\xed\xfe" + struct.pack(
        "<IIIIIII",
        0x0100000C,  # cputype: arm64 (0x0100000D would be arm64e — unmapped)
        0,  # cpusubtype
        2,  # filetype: MH_EXECUTE
        1,  # ncmds
        152,  # sizeofcmds: one LC_SEGMENT_64 + one section
        0,  # flags
        0,  # reserved
    )
    segment = (
        struct.pack("<II", 0x19, 152)
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack(
            "<QQQQIIII",
            0x100000000,  # vmaddr
            0x1000,  # vmsize
            0,  # fileoff
            0x1000,  # filesize
            5,  # maxprot r-x
            5,  # initprot r-x
            1,  # nsects
            0,  # flags
        )
    )
    section = (
        b"__text".ljust(16, b"\x00")
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack(
            "<QQIIIIIIII",
            0x100000000,  # addr
            0x100,  # size
            0xB8,  # offset: 32-byte header + 152-byte load commands (184)
            3,  # align (2^3)
            0,  # reloff
            0,  # nreloc
            0,  # flags
            0,  # reserved1
            0,  # reserved2
            0,  # reserved3
        )
    )
    with open(a, "wb") as fh:
        fh.write(header + segment + section + NOP * 64 + b"hello strings\x00")
    with open(b, "wb") as fh:
        fh.write(bytes((i + 1) % 256 for i in range(256)))
    return a, b


def _join_worker(app) -> None:
    """Join every tool's in-flight worker, then dispatch drained events."""
    for tool in app.tools:
        worker = getattr(tool.controller, "busy", None)
        worker = worker.worker if worker else None
        if worker is not None and worker.thread is not None:
            worker.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        app.dispatch(event)


def _settle_parse(app) -> None:
    """Join the shell's Open File… parse worker and dispatch its event."""
    worker = app._parse_worker
    if worker is not None and worker.thread is not None:
        worker.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        app.dispatch(event)


def headless_checks() -> None:
    """Build the shell and drive the open flows in throwaway DPG contexts."""
    import dearpygui.dearpygui as dpg

    from cheerleader.gui.app import DLG_OPEN, GuiApp, build_shell, open_compare

    with tempfile.TemporaryDirectory() as tmp:
        a, b = _write_inputs(tmp)

        dpg.create_context()
        try:
            app = GuiApp()
            build_shell(app)
            app.tick()  # empty shell: per-frame maintenance is display-safe
        finally:
            dpg.destroy_context()
        print("  ok  shell: empty window + Tools menu builds headless")

        dpg.create_context()
        try:
            app = GuiApp()
            build_shell(app)
            # Drive the real menu path: File → Open File… pick loads the file
            # and enables Tools → Hex Editor…; clicking it opens the editor.
            from cheerleader.gui.app import MI_EDITOR
            from cheerleader.gui.const import SHELL_STATUS, SHELL_STATUS_BAR

            dpg.get_item_callback(DLG_OPEN)(DLG_OPEN, {"selections": {"file_1": a}})
            assert app.current_file == a
            assert dpg.get_item_configuration(MI_EDITOR)["enabled"] is True
            assert dpg.get_item_configuration(SHELL_STATUS_BAR)["show"] is True
            assert dpg.get_value(SHELL_STATUS) == a
            # a is a valid Mach-O: the background parse un-grays the
            # Disassembler menu (plan v3 D1) and auto-opens the Disassembly
            # view (plan v3 D3).
            _settle_parse(app)
            from cheerleader.gui.app import DISASM_ITEMS

            assert app.current_info is not None
            assert app.current_info.path == a
            for tag in DISASM_ITEMS:
                assert dpg.get_item_configuration(tag)["enabled"] is True
            assert len(app.tools) == 1
            assert app.tools[0].controller.view_kind == "disasm"
            dpg.get_item_callback(MI_EDITOR)()
            app.tick()  # grid maintenance must be display-safe
            _join_worker(app)
            # the auto-opened disassembly worker landed its __text decode
            assert len(app.tools[0].controller.grid.instrs()) == 64
        finally:
            dpg.destroy_context()
        print("  ok  editor: open-file flow builds headless")

        dpg.create_context()
        try:
            app = GuiApp()
            build_shell(app)
            # Disassembler menu → table views (plan v3 D2): menu callbacks
            # open floating read-only views over the parsed binary.
            from cheerleader.gui.app import MI_SEGMENTS, MI_SYMBOLS

            dpg.get_item_callback(DLG_OPEN)(DLG_OPEN, {"selections": {"file_1": a}})
            _settle_parse(app)
            assert app.current_info is not None
            assert len(app.tools) == 1  # D3 auto-open; close it for a clean D2 run
            app.close_tool(app.tools[0])
            dpg.get_item_callback(MI_SEGMENTS)()
            dpg.get_item_callback(MI_SYMBOLS)()
            assert len(app.tools) == 2
            seg_tool, sym_tool = app.tools
            assert seg_tool.controller.view_kind == "segments"
            seg_rows = dpg.get_item_children(seg_tool.controller.table_tag)[1]
            assert len(seg_rows) == 1  # the fixture's __TEXT segment
            assert "1 segments" in dpg.get_value(seg_tool.status_tag)
            assert sym_tool.controller._filter == "no-stabs"  # TUI parity
            # focus guard: re-firing Segments… must not duplicate
            dpg.get_item_callback(MI_SEGMENTS)()
            assert len(app.tools) == 2
            app.tick()  # views must be tick-safe
            for tool in list(app.tools):
                app.close_tool(tool)
            assert app.tools == []
        finally:
            dpg.destroy_context()
        print("  ok  views: menu-open segments/symbols + focus guard headless")

        dpg.create_context()
        try:
            app = GuiApp()
            build_shell(app)
            open_compare(app, a, b)
            app.tick()
            _join_worker(app)  # initial diff scan
        finally:
            dpg.destroy_context()
        print("  ok  compare: open_compare flow builds headless")

        dpg.create_context()
        try:
            app = GuiApp()
            build_shell(app)
            # Disassembler → Strings… (plan v3 D5): menu callback opens the
            # view, the extraction worker lands, search filters live, and a
            # row click jumps into an already-open hex editor.
            from cheerleader.gui.app import MI_EDITOR, MI_STRINGS

            dpg.get_item_callback(DLG_OPEN)(DLG_OPEN, {"selections": {"file_1": a}})
            _settle_parse(app)
            app.close_tool(app.tools[0])  # close the D3 auto-open
            dpg.get_item_callback(MI_STRINGS)()
            assert len(app.tools) == 1
            ctl = app.tools[0].controller
            assert ctl.view_kind == "strings"
            assert "Extracting" in dpg.get_value(ctl.status_tag)
            _join_worker(app)
            assert "4 strings found" in dpg.get_value(ctl.status_tag)
            rows = dpg.get_item_children(ctl.table_tag)[1]
            labels = [
                dpg.get_item_label(sel)
                for row in rows
                for sel in dpg.get_item_children(row)[1]
            ]
            assert "hello strings" in labels  # the appended payload
            assert "0x000001b8" in labels  # file-offset cell of the payload
            # live search: tick applies the box's current text
            dpg.set_value(ctl.search_tag, "HELLO")
            app.tick()
            rows = dpg.get_item_children(ctl.table_tag)[1]
            assert len(rows) == 1
            assert "1 / 4 strings matching 'HELLO'" in dpg.get_value(ctl.status_tag)
            dpg.set_value(ctl.search_tag, "")
            app.tick()
            assert len(dpg.get_item_children(ctl.table_tag)[1]) == 4
            # row click with no hex editor open → status hint, no new tools
            payload_row = dpg.get_item_children(ctl.table_tag)[1][3]
            sel = dpg.get_item_children(payload_row)[1][0]
            cfg = dpg.get_item_configuration(sel)
            cfg["callback"](sel, None, cfg["user_data"])
            assert "Hex Editor" in dpg.get_value(ctl.status_tag)
            assert len(app.tools) == 1
            # now with the editor open: the click jumps to 0x1B8
            dpg.get_item_callback(MI_EDITOR)()
            app.tick()
            cfg["callback"](sel, None, cfg["user_data"])
            ed = next(t for t in app.tools if getattr(t.controller, "doc", None))
            assert ed.controller.grid.cursor == 0x1B8
            assert "Jumped to 0x000001b8" in dpg.get_value(ctl.status_tag)
            # focus guard: re-firing Strings… must not duplicate
            dpg.get_item_callback(MI_STRINGS)()
            assert len(app.tools) == 2
        finally:
            dpg.destroy_context()
        print("  ok  strings: worker + live search + editor jump headless")

        dpg.create_context()
        try:
            app = GuiApp()
            build_shell(app)
            # Disassembler ▸ Engine radio submenu (plan v3 D5): callbacks
            # move the "● " marker; forced engine flows into pick_engine.
            from cheerleader.gui import disasm as disasm_mod
            from cheerleader.gui.const import (
                MI_ENGINE_AUTO,
                MI_ENGINE_CAPSTONE,
                MI_ENGINE_OBJDUMP,
            )

            assert disasm_mod.engine_override() == "auto"
            assert dpg.get_item_label(MI_ENGINE_AUTO) == "● Auto"
            dpg.get_item_callback(MI_ENGINE_CAPSTONE)()
            assert disasm_mod.engine_override() == "capstone"
            assert dpg.get_item_label(MI_ENGINE_CAPSTONE) == "● Capstone"
            assert dpg.get_item_label(MI_ENGINE_AUTO) == "Auto"
            forced = "objdump" if disasm_mod.engine_available("objdump") else None
            dpg.get_item_callback(MI_ENGINE_OBJDUMP)()
            assert disasm_mod.pick_engine() == forced
            dpg.get_item_callback(MI_ENGINE_AUTO)()
            assert disasm_mod.engine_override() == "auto"
        finally:
            dpg.destroy_context()
        print("  ok  engine: radio submenu + override pick headless")


def _launch(name: str, timeout: float) -> bool:
    """Run one launch under a watchdog; True on boot-and-stay-up / clean exit."""
    cmd = [sys.executable, "-c", CHILD_MAIN]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=10)
        print(f"  ok  {name}: stayed up {timeout:.0f}s (watchdog kill = booted)")
        return True
    if proc.returncode == 0:
        print(f"  ok  {name}: exited cleanly")
        return True
    print(f"  FAIL {name}: exited rc={proc.returncode}")
    if stderr:
        tail = stderr.decode(errors="replace").strip().splitlines()[-5:]
        print("       " + "\n       ".join(tail))
    return False


def live_launches(timeout: float) -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("  --  no display detected — skipping live launch checks")
        return True
    return _launch("shell (no args)", timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        metavar="SECONDS",
        help="watchdog window per live launch (default: 8)",
    )
    args = parser.parse_args()

    print("cheerleader-gui smoke — headless shell + open-flow checks")
    try:
        headless_checks()
    except ImportError as e:
        print(f"FAIL: {e}\nInstall the gui extra with:  uv sync --extra gui")
        return 1

    print("cheerleader-gui smoke — live launch (watchdog)")
    if not live_launches(args.timeout):
        return 1
    print("All GUI smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
