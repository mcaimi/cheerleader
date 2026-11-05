"""Headless tests for the phase-3 modal dialogs.

The dialogs own only widgets and visibility: validation is delegated to a
``validate`` callable, results flow back through the callbacks. Tests drive
the callbacks directly (the same entry points the DPG widgets call).
"""

from __future__ import annotations

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg

from cheerleader.gui.dialogs import (
    ByteEditDialog,
    ConfirmDialog,
    SaveDialog,
    SearchDialog,
)
from cheerleader.tui.hexparse import parse_byte_sequence, parse_offset


@pytest.fixture()
def dpg_context():
    dpg.create_context()
    try:
        yield
    finally:
        dpg.destroy_context()


def test_byte_edit_dialog_accepts_and_reports_errors(dpg_context) -> None:
    accepted: list[bytes] = []
    cancelled: list[None] = []
    dlg = ByteEditDialog(
        offset=0x10,
        current=b"\x7f",
        validate=parse_byte_sequence,
        on_accept=accepted.append,
        on_cancel=lambda: cancelled.append(None),
    )
    assert dpg.get_item_configuration(dlg.TAG)["show"] is False  # built hidden
    assert dpg.get_value(dlg._input_tag) == "7f"  # pre-filled current byte
    dlg.open()
    assert dpg.get_item_configuration(dlg.TAG)["show"] is True

    dpg.set_value(dlg._input_tag, "de ad")
    dlg._try_apply(None, None)
    assert accepted == [b"\xde\xad"]

    dpg.set_value(dlg._input_tag, "xyz")
    dlg._try_apply(None, None)
    assert "not a hex byte" in dpg.get_value(dlg._error_tag)
    assert accepted == [b"\xde\xad"]  # nothing accepted on error

    dpg.set_value(dlg._input_tag, "")
    dlg._try_apply(None, None)
    assert "empty input" in dpg.get_value(dlg._error_tag)

    dlg._cancel()
    assert cancelled == [None]


def test_search_dialog_modes(dpg_context) -> None:
    def validate(mode: str, text: str) -> tuple[str, object, str]:
        if mode == "offset":
            return ("offset", parse_offset(text), text)
        return ("hex", parse_byte_sequence(text), text)

    accepted: list[tuple[str, object, str]] = []
    dlg = SearchDialog(
        mode="offset",
        value="1a",
        validate=validate,
        on_accept=accepted.append,
        on_cancel=lambda: None,
    )
    assert dpg.get_value(dlg._input_tag) == "1a"  # last search pre-filled
    dpg.set_value(dlg._input_tag, "10ff")
    dlg._try_apply(None, None)
    assert accepted == [("offset", 0x10FF, "10ff")]

    dpg.set_value(dlg._mode_tag, "hex")
    dpg.set_value(dlg._input_tag, "de ad be ef")
    dlg._try_apply(None, None)
    assert accepted[-1] == ("hex", b"\xde\xad\xbe\xef", "de ad be ef")

    dpg.set_value(dlg._input_tag, "nope!")
    dlg._try_apply(None, None)
    assert dpg.get_value(dlg._error_tag)  # error shown, nothing accepted
    assert len(accepted) == 2


def test_confirm_dialog_answers(dpg_context) -> None:
    answers: list[bool] = []
    dlg = ConfirmDialog(message="Proceed?", on_answer=answers.append)
    assert dpg.get_item_configuration(dlg.TAG)["show"] is False
    dlg._yes()
    dlg._no()
    assert answers == [True, False]


def test_save_dialog_reports_choice_or_cancel(dpg_context) -> None:
    chosen: list[str | None] = []
    dlg = SaveDialog(default_path="/tmp/dir/in.patched", on_chosen=chosen.append)
    assert dpg.get_item_configuration(dlg.TAG)["show"] is False
    dlg._picked(None, ["/tmp/dir/out.patched"])
    dlg._picked(None, [])
    dlg._cancelled()
    assert chosen == ["/tmp/dir/out.patched", None, None]
