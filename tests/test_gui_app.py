"""Headless smoke tests for the Dear PyGui shell.

DPG context creation, item construction, themes and fonts do not need a
display — only the viewport does. These tests build the shell inside a
throwaway context and tear it down again.
"""

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg

from cheerleader.gui.app import (
    CONTENT_TAG,
    STATUS_TAG,
    WIN_TAG,
    GuiApp,
    build_shell,
)
from cheerleader.gui.workers import EventKind, UiEvent


@pytest.fixture()
def dpg_context():
    dpg.create_context()
    try:
        yield
    finally:
        dpg.destroy_context()


def test_build_shell_minimal(dpg_context) -> None:
    app = GuiApp()
    shell = build_shell(app, binary=None, hex_compare=None)
    assert shell.window == WIN_TAG
    assert shell.content == CONTENT_TAG
    assert shell.status == STATUS_TAG
    for tag in (shell.window, shell.content, shell.status):
        assert dpg.does_item_exist(tag)
    assert "Ctrl+Q" in dpg.get_value(STATUS_TAG)


def test_build_shell_with_binary(dpg_context, tmp_path) -> None:
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"\x7fELF" + b"\0" * 2048)
    app = GuiApp()
    build_shell(app, binary=str(sample), hex_compare=None)
    status = dpg.get_value(STATUS_TAG)
    assert "sample.bin" in status
    assert "2.0 KiB" in status
    assert "0x00000000" in status  # cursor offset visible from the start
    # hex editor mode builds a live HexGrid and wires its per-frame tick
    assert app.grid is not None
    assert dpg.does_item_exist("hex-hexgrid")
    assert dpg.does_item_exist("hex-hexgrid-scroll")
    app.tick()  # grid maintenance runs without a display
    assert dpg.get_value("hex-hexgrid-r0-off") == "00000000  "


def test_build_shell_compare_mode(dpg_context, tmp_path) -> None:
    left = tmp_path / "left.bin"
    right = tmp_path / "right.bin"
    left.write_bytes(b"a" * 4096)
    right.write_bytes(b"b" * 4096)
    app = GuiApp()
    build_shell(app, binary=str(left), hex_compare=str(right))
    status = dpg.get_value(STATUS_TAG)
    assert "left.bin" in status
    assert "right.bin" in status


def test_build_shell_twice_in_new_contexts(tmp_path) -> None:
    """Shell construction is repeatable across fresh contexts."""
    sample = tmp_path / "x.bin"
    sample.write_bytes(b"abc")
    for _ in range(2):
        dpg.create_context()
        try:
            build_shell(GuiApp(), binary=str(sample), hex_compare=None)
            assert dpg.does_item_exist(WIN_TAG)
        finally:
            dpg.destroy_context()


def test_dispatch_routes_to_controller(dpg_context) -> None:
    class _Stub:
        def __init__(self) -> None:
            self.events: list[UiEvent] = []

        def on_event(self, event: UiEvent) -> None:
            self.events.append(event)

        def request_quit(self) -> None: ...

    app = GuiApp()
    stub = _Stub()
    app.set_controller(stub)
    event = UiEvent(1, EventKind.PROGRESS, 0.5)
    app.dispatch(event)
    assert stub.events == [event]
    app.set_controller(None)
    app.dispatch(event)
    assert len(stub.events) == 1  # detached controller no longer receives events
