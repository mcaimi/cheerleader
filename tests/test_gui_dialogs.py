"""Headless tests for the modal dialogs.

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
    CompareOpenDialog,
    ConfirmDialog,
    ErrorPopup,
    InfoDialog,
    SaveDialog,
    SearchDialog,
    add_all_files_filter,
    picked_path,
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
    # DPG 2.3.1 payload shape (verified against mvFileDialog.cpp): a dict.
    dlg._picked(None, {"selections": {"file_1": "/tmp/dir/out.patched"}})
    dlg._picked(None, {"selections": {}})  # OK with nothing selected
    dlg._cancelled()
    assert chosen == ["/tmp/dir/out.patched", None, None]


def test_file_dialogs_register_all_files_filter(dpg_context) -> None:
    """A dialog without extension children lists directories only."""
    dlg = SaveDialog(default_path="/tmp/dir/in.patched", on_chosen=lambda _p: None)
    # DPG keeps file-extension items in child slot 0 (slot 1 stays empty).
    ext_types = [
        dpg.get_item_info(child)["type"] for child in dpg.get_item_children(dlg.TAG, 0)
    ]
    assert "mvAppItemType::mvFileExtension" in ext_types
    # the helper used by every file dialog in the package
    dpg.add_file_dialog(tag="fd-x", show=False, width=1, height=1)
    add_all_files_filter("fd-x")
    assert any(
        dpg.get_item_info(child)["type"] == "mvAppItemType::mvFileExtension"
        for child in dpg.get_item_children("fd-x", 0)
    )


# ── picked_path payload extraction (pure) ─────────────────────────────────────


def test_picked_path_from_dict_selections() -> None:
    """The real DPG 2.3.1 payload: {'selections': {key: path}, ...}."""
    payload = {
        "current_path": "/tmp",
        "current_filter": ".*",
        "selections": {"file_1": "/tmp/a.bin", "file_2": "/tmp/b.bin"},
    }
    assert picked_path(payload) == "/tmp/a.bin"
    assert picked_path({"selections": {}}) is None  # OK with nothing picked
    assert picked_path({}) is None


def test_picked_path_other_shapes() -> None:
    # tolerate list/tuple/bare-path shapes (tests, older DPG versions)
    assert picked_path(["/a/b"]) == "/a/b"
    assert picked_path(()) is None
    assert picked_path("/a/b") == "/a/b"
    assert picked_path(None) is None
    assert picked_path("") is None
    # legacy single-selection key seen in the wild
    assert picked_path({"file_path_name": "/a/b"}) == "/a/b"


# ── CompareOpenDialog ────────────────────


def _make_cmp_picker():
    launched: list[tuple[str, str]] = []
    dlg = CompareOpenDialog(on_launch=lambda s, t: launched.append((s, t)))
    return dlg, launched


def test_compare_picker_layout_and_browse(dpg_context) -> None:
    dlg, launched = _make_cmp_picker()
    assert dpg.get_item_configuration(dlg.TAG)["show"] is False  # built hidden
    dlg.open()
    assert dpg.get_item_configuration(dlg.TAG)["show"] is True
    # Two rows, each: label + editable path input + a Browse… button whose
    # callback shows that row's (hidden) file dialog.
    assert dpg.get_value(dlg._src_tag) == ""
    assert dpg.get_value(dlg._tgt_tag) == ""
    kids = dpg.get_item_children(dlg.TAG, 1)
    rows = ((kids[0], kids[1]), (kids[2], kids[3]))
    for (label, row), dlg_tag in zip(rows, (dlg._src_dlg, dlg._tgt_dlg)):
        assert dpg.does_item_exist(dlg_tag)
        assert dpg.get_item_configuration(dlg_tag)["show"] is False
        browse = dpg.get_item_children(row, 1)[1]
        assert dpg.get_item_label(browse) == "Browse…"
        dpg.get_item_callback(browse)()
        assert dpg.get_item_configuration(dlg_tag)["show"] is True
    assert launched == []


def test_compare_picker_browse_fill_via_file_dialogs(dpg_context) -> None:
    """Each Browse button's file dialog fills its own input (dict payload)."""
    dlg, launched = _make_cmp_picker()
    # Drive the file-dialog callbacks exactly as DPG would on OK.
    dpg.get_item_callback(dlg._src_dlg)(
        dlg._src_dlg, {"selections": {"file_1": "/tmp/a.bin"}}
    )
    dpg.get_item_callback(dlg._tgt_dlg)(
        dlg._tgt_dlg, {"selections": {"file_1": "/tmp/b.bin"}}
    )
    assert dpg.get_value(dlg._src_tag) == "/tmp/a.bin"
    assert dpg.get_value(dlg._tgt_tag) == "/tmp/b.bin"
    assert launched == []
    dlg._launch()
    assert launched == [("/tmp/a.bin", "/tmp/b.bin")]
    # A successful launch closes the picker and both file dialogs.
    assert not dpg.does_item_exist(dlg.TAG)
    assert not dpg.does_item_exist(dlg._src_dlg)
    assert not dpg.does_item_exist(dlg._tgt_dlg)


def test_compare_picker_requires_both_paths(dpg_context) -> None:
    dlg, launched = _make_cmp_picker()
    dlg.open()
    dpg.set_value(dlg._src_tag, "/tmp/a.bin")
    dlg._launch()  # target missing → inline error, no launch
    assert "both" in dpg.get_value(dlg._error_tag)
    assert launched == []
    assert dpg.does_item_exist(dlg.TAG)  # stays open for the user to fix
    dpg.set_value(dlg._tgt_tag, "/tmp/b.bin")
    dlg._launch()
    assert launched == [("/tmp/a.bin", "/tmp/b.bin")]


def test_compare_picker_cancel_deletes_everything(dpg_context) -> None:
    dlg, launched = _make_cmp_picker()
    dlg.open()
    dlg.close()  # the Cancel button's callback
    assert not dpg.does_item_exist(dlg.TAG)
    assert not dpg.does_item_exist(dlg._src_dlg)
    assert not dpg.does_item_exist(dlg._tgt_dlg)
    assert launched == []


# ── title-bar X (on_close) ────────────────────────────────────────────────────


def _click_x(tag) -> None:
    """Headless stand-in for the title-bar X: run the wired on_close."""
    dpg.get_item_configuration(tag)["on_close"](tag, None, None)


def test_confirm_dialog_x_answers_no(dpg_context) -> None:
    answers: list[bool] = []
    dlg = ConfirmDialog(message="sure?", on_answer=answers.append)
    dlg.open()
    _click_x(dlg.TAG)
    assert answers == [False]  # the caller is never left waiting


def test_edit_and_search_dialog_x_cancels(dpg_context) -> None:
    cancelled: list[str] = []
    edit = ByteEditDialog(
        offset=0,
        current=b"\x00",
        validate=parse_byte_sequence,
        on_accept=lambda _d: None,
        on_cancel=lambda: cancelled.append("edit"),
    )
    _click_x(edit.TAG)
    search = SearchDialog(
        mode="hex",
        value="",
        validate=lambda m, t: (m, t, t),
        on_accept=lambda _r: None,
        on_cancel=lambda: cancelled.append("search"),
    )
    _click_x(search.TAG)
    assert cancelled == ["edit", "search"]


def test_info_dialog_reopens_and_deletes_itself(dpg_context) -> None:
    first = InfoDialog(message="about")
    first.open()
    second = InfoDialog(message="about")  # fixed tags used to collide here
    second.open()
    assert first.TAG != second.TAG
    first.close()
    _click_x(second.TAG)
    assert not dpg.does_item_exist(first.TAG)
    assert not dpg.does_item_exist(second.TAG)


def test_error_popup_x_deletes_window(dpg_context) -> None:
    popup = ErrorPopup("boom")
    _click_x(popup.TAG)
    assert not dpg.does_item_exist(popup.TAG)
