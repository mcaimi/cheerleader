"""Headless tests for the shared tool-window plumbing (key focus scoping).

Without a viewport DPG never focuses a window, so ``get_active_window`` is
monkeypatched to simulate which window the user last clicked.
"""

from __future__ import annotations

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg

from cheerleader.gui import tooling
from cheerleader.gui.tooling import focus_guard, window_has_focus


@pytest.fixture()
def two_windows():
    """Two tool-like windows, the first with a nested child window."""
    dpg.create_context()
    try:
        with dpg.window(tag="win-a"), dpg.child_window(tag="win-a-child"):
            dpg.add_text("", tag="win-a-text")
        with dpg.window(tag="win-b"):
            dpg.add_text("", tag="win-b-text")
        yield
    finally:
        dpg.destroy_context()


def _activate(monkeypatch, tag) -> None:
    uuid = dpg.get_alias_id(tag) if tag else 0
    monkeypatch.setattr(tooling.dpg, "get_active_window", lambda: uuid)


def test_no_active_window_means_no_focus(two_windows, monkeypatch) -> None:
    _activate(monkeypatch, None)
    assert window_has_focus("win-a") is False


def test_focus_follows_the_top_level_window(two_windows, monkeypatch) -> None:
    _activate(monkeypatch, "win-a")
    assert window_has_focus("win-a")
    assert window_has_focus("win-a-text")  # nested item, same window
    assert not window_has_focus("win-b")


def test_focused_child_window_counts_as_its_parent(two_windows, monkeypatch) -> None:
    # DPG records a focused child window (e.g. a grid pane) as activeWindow.
    _activate(monkeypatch, "win-a-child")
    assert window_has_focus("win-a")
    assert not window_has_focus("win-b-text")


def test_deleted_item_never_has_focus(two_windows, monkeypatch) -> None:
    _activate(monkeypatch, "win-a")
    assert not window_has_focus("does-not-exist")


def test_focus_guard_drops_keys_for_unfocused_tools(two_windows, monkeypatch) -> None:
    calls: list[str] = []
    a = focus_guard("win-a", lambda *_a: calls.append("a"))
    b = focus_guard("win-b", lambda *_a: calls.append("b"))
    _activate(monkeypatch, "win-b")
    a(None, None)
    b(None, None)
    assert calls == ["b"]


def test_focus_guard_also_hook(two_windows, monkeypatch) -> None:
    """``also`` lets a tool keep handling keys while its own modal is up."""
    calls: list[int] = []
    modal_open = [False]
    guarded = focus_guard(
        "win-a", lambda *_a: calls.append(1), also=lambda: modal_open[0]
    )
    _activate(monkeypatch, "win-b")
    guarded()
    modal_open[0] = True
    guarded()
    assert calls == [1]
