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
import subprocess
import sys
import tempfile

CHILD_MAIN = "from cheerleader.gui import main; main()"


def _write_inputs(tmp: str) -> tuple[str, str]:
    a = os.path.join(tmp, "a.bin")
    b = os.path.join(tmp, "b.bin")
    with open(a, "wb") as fh:
        fh.write(bytes(range(256)))
    with open(b, "wb") as fh:
        fh.write(bytes((i + 1) % 256 for i in range(256)))
    return a, b


def _join_worker(app) -> None:
    """Join every tool's in-flight worker (compare scan) and drain events."""
    for tool in app.tools:
        worker = getattr(tool.controller, "busy", None)
        worker = worker.worker if worker else None
        if worker is not None and worker.thread is not None:
            worker.thread.join(timeout=10.0)
    app.bridge.drain()


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
            dpg.get_item_callback(MI_EDITOR)()
            app.tick()  # grid maintenance must be display-safe
            _join_worker(app)
        finally:
            dpg.destroy_context()
        print("  ok  editor: open-file flow builds headless")

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
