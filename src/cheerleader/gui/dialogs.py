"""Modal dialogs for the Dear PyGui hex editor."""

import itertools
import os
from collections.abc import Callable

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

from cheerleader.gui.const import ERR_COLOR, INPUT_W

# Unique tags for transient popups (several tool windows may be open at
# once so fixed class-level tags would collide).
_instance_counter = itertools.count(1)


def _unique_tag(prefix: str) -> str:
    return f"{prefix}-{next(_instance_counter)}"


# DPG 2.3.1 file-dialog facts (verified against the wheel + mvFileDialog.cpp):
# a file dialog lists *no files* unless at least one ``add_file_extension``
# child is registered (with none, it "defaults to directories" per the docs)
# — ``".*"`` is the all-files filter; and the OK callback receives a
# *dictionary* (``current_path``, ``current_filter``, ``selections``), not a
# list of paths.


def add_all_files_filter(dialog_tag: int | str) -> None:
    """Register the all-files (``".*"``) filter on a file dialog.

    Without an extension child the dialog browses directories only.
    """
    dpg.add_file_extension(".*", parent=dialog_tag)


def picked_path(app_data) -> str | None:
    """Extract the first selected path from a file-dialog callback payload.

    DPG hands the OK callback a dict whose ``selections`` maps selection keys
    to paths; tolerate the other shapes a caller may hand us (list, tuple,
    bare path string). Returns ``None`` when nothing was selected (OK with an
    empty selection) — callers treat that like a cancel.
    """
    if isinstance(app_data, dict):
        selections = app_data.get("selections")
        if isinstance(selections, dict):
            for value in selections.values():
                return str(value)
        fallback = app_data.get("file_path_name") or app_data.get("current_file")
        return str(fallback) if fallback else None
    if isinstance(app_data, (list, tuple)):
        return str(app_data[0]) if app_data else None
    return str(app_data) if app_data else None


class ByteEditDialog:
    """
    Edit-bytes prompt at a fixed offset (the Enter popup).

    Mirrors the TUI ``ByteEditPrompt``: shows the current byte, pre-fills the
    input with it, validates via ``validate(text) -> bytes`` (the controller
    supplies ``parse_byte_sequence`` plus the past-EOF check) and hands the
    parsed bytes to ``on_accept``. Apply is bound to the button *and* to
    Enter inside the input.
    """

    TAG = "gui-dlg-edit"

    def __init__(
        self,
        *,
        offset: int,
        current: bytes,
        validate: Callable[[str], bytes],
        on_accept: Callable[[bytes], None],
        on_cancel: Callable[[], None],
        tag: str | None = None,
    ) -> None:
        self.TAG = tag or type(self).TAG
        self._validate = validate
        self._on_accept = on_accept
        self._on_cancel = on_cancel
        self._input_tag = f"{self.TAG}-input"
        self._error_tag = f"{self.TAG}-error"
        dpg.add_window(
            tag=self.TAG,
            label=f"Edit at 0x{offset:08x}",
            modal=True,
            no_open_over_existing_popup=True,
            no_collapse=True,
            no_resize=True,
            show=False,
        )
        c = current[0] if current else 0
        ch = chr(c) if 0x20 <= c < 0x7F else "."
        dpg.add_text(f"current: 0x{c:02x}  {c:3d}  '{ch}'", parent=self.TAG)
        dpg.add_input_text(
            tag=self._input_tag,
            parent=self.TAG,
            default_value=f"{c:02x}" if current else "",
            hint='de ad be ef · 0x7f · #127 · "text',
            on_enter=True,
            callback=self._try_apply,
            width=INPUT_W,
        )
        dpg.add_text("", tag=self._error_tag, parent=self.TAG, color=ERR_COLOR)
        with dpg.group(horizontal=True, parent=self.TAG):
            dpg.add_button(label="Apply", callback=self._try_apply)
            dpg.add_button(label="Cancel", callback=self._cancel)

    def open(self) -> None:
        dpg.configure_item(self.TAG, show=True)
        dpg.focus_item(self._input_tag)

    def close(self) -> None:
        dpg.delete_item(self.TAG)

    def _cancel(self, *_args) -> None:
        self._on_cancel()

    def _try_apply(self, *_args) -> None:
        text = dpg.get_value(self._input_tag)
        try:
            data = self._validate(text)
        except ValueError as e:
            dpg.set_value(self._error_tag, str(e))
            return
        dpg.set_value(self._error_tag, "")
        self._on_accept(data)


class SearchDialog:
    """
    Go-to-offset / search prompt (the ``/`` popup).

    Mirrors the TUI ``SearchPrompt``: a mode radio (offset / hex / text) plus
    the input; ``validate(mode, text)`` (controller-supplied, using
    ``parse_offset`` / ``parse_byte_sequence``) produces the
    ``(mode, value, raw)`` result handed to ``on_accept``.
    """

    TAG = "gui-dlg-search"

    def __init__(
        self,
        *,
        mode: str,
        value: str,
        validate: Callable[[str, str], tuple[str, object, str]],
        on_accept: Callable[[tuple[str, object, str]], None],
        on_cancel: Callable[[], None],
        tag: str | None = None,
    ) -> None:
        self.TAG = tag or type(self).TAG
        self._validate = validate
        self._on_accept = on_accept
        self._on_cancel = on_cancel
        self._input_tag = f"{self.TAG}-input"
        self._mode_tag = f"{self.TAG}-mode"
        self._error_tag = f"{self.TAG}-error"
        dpg.add_window(
            tag=self.TAG,
            label="Go to offset / search",
            modal=True,
            no_open_over_existing_popup=True,
            no_collapse=True,
            no_resize=True,
            show=False,
        )
        dpg.add_text(
            'offset: 1a2b · 0x1a2b · #6699   hex: de ad be ef   text: "abc',
            parent=self.TAG,
        )
        dpg.add_input_text(
            tag=self._input_tag,
            parent=self.TAG,
            default_value=value,
            on_enter=True,
            callback=self._try_apply,
            width=INPUT_W,
        )
        dpg.add_radio_button(
            ["offset", "hex", "text"],
            default_value=mode,
            horizontal=True,
            tag=self._mode_tag,
            parent=self.TAG,
        )
        dpg.add_text("", tag=self._error_tag, parent=self.TAG, color=ERR_COLOR)
        with dpg.group(horizontal=True, parent=self.TAG):
            dpg.add_button(label="Go", callback=self._try_apply)
            dpg.add_button(label="Cancel", callback=self._cancel)

    def open(self) -> None:
        dpg.configure_item(self.TAG, show=True)
        dpg.focus_item(self._input_tag)

    def close(self) -> None:
        dpg.delete_item(self.TAG)

    def _cancel(self, *_args) -> None:
        self._on_cancel()

    def _try_apply(self, *_args) -> None:
        text = dpg.get_value(self._input_tag)
        mode = dpg.get_value(self._mode_tag)
        try:
            result = self._validate(mode, text)
        except ValueError as e:
            dpg.set_value(self._error_tag, str(e))
            return
        dpg.set_value(self._error_tag, "")
        self._on_accept(result)


class ConfirmDialog:
    """Yes/No confirmation (overwrite / discard / quit-without-saving)."""

    TAG = "gui-dlg-confirm"

    def __init__(
        self,
        *,
        message: str,
        on_answer: Callable[[bool], None],
        tag: str | None = None,
    ) -> None:
        self.TAG = tag or type(self).TAG
        self._on_answer = on_answer
        self.message = message
        dpg.add_window(
            tag=self.TAG,
            label="Confirm",
            modal=True,
            no_open_over_existing_popup=True,
            no_collapse=True,
            no_resize=True,
            show=False,
        )
        dpg.add_text(message, parent=self.TAG)
        with dpg.group(horizontal=True, parent=self.TAG):
            dpg.add_button(label="Yes", callback=self._yes)
            dpg.add_button(label="No", callback=self._no)

    def open(self) -> None:
        dpg.configure_item(self.TAG, show=True)

    def close(self) -> None:
        dpg.delete_item(self.TAG)

    def _yes(self, *_args) -> None:
        self._on_answer(True)

    def _no(self, *_args) -> None:
        self._on_answer(False)


class SaveDialog:
    """
    Save-as via DPG's file dialog.

    Pre-fills the destination name with ``{path}.patched`` (the TUI
    ``SavePrompt`` default) split into ``default_path`` + ``default_filename``.
    ``on_chosen`` receives the picked path or ``None`` on cancel — DPG fires
    ``cancel_callback`` on its own Esc/Cancel handling, so the controller
    never needs to reach into this dialog.
    """

    TAG = "gui-dlg-save"

    def __init__(
        self,
        *,
        default_path: str,
        on_chosen: Callable[[str | None], None],
        tag: str | None = None,
    ) -> None:
        self.TAG = tag or type(self).TAG
        self._on_chosen = on_chosen
        dpg.add_file_dialog(
            tag=self.TAG,
            directory_selector=False,
            show=False,
            modal=True,
            file_count=1,
            width=560,
            height=400,
            default_path=os.path.dirname(default_path) or os.getcwd(),
            default_filename=os.path.basename(default_path),
            callback=self._picked,
            cancel_callback=self._cancelled,
        )
        # No extension child → the dialog lists directories only (DPG 2.3.1).
        add_all_files_filter(self.TAG)

    def open(self) -> None:
        dpg.configure_item(self.TAG, show=True)

    def close(self) -> None:
        dpg.delete_item(self.TAG)

    def _picked(self, _sender, app_data) -> None:
        self._on_chosen(picked_path(app_data))

    def _cancelled(self, *_args) -> None:
        self._on_chosen(None)


class CompareOpenDialog:
    """
    Two-file picker for the compare tool (Tools → Hex Compare…).

    One row per file — **Source file** / **Target file** — each an editable
    path input with its own **Browse…** button. The dialog owns two private
    file dialogs (instance-unique tags), so no chaining state survives
    between picks. **Compare** hands both paths to ``on_launch``; any exit
    path (Compare / Cancel / X) deletes the window *and* its file dialogs.
    Paths are not validated here: ``open_compare`` fails soft with an
    :class:`ErrorPopup` when a file cannot be opened.
    """

    def __init__(self, *, on_launch: Callable[[str, str], None]) -> None:
        self.TAG = _unique_tag("gui-dlg-cmp")
        self._on_launch = on_launch
        self._src_tag = f"{self.TAG}-src"
        self._tgt_tag = f"{self.TAG}-tgt"
        self._error_tag = f"{self.TAG}-error"
        self._src_dlg = f"{self.TAG}-src-dlg"
        self._tgt_dlg = f"{self.TAG}-tgt-dlg"
        dpg.add_window(
            tag=self.TAG,
            label="Hex compare",
            no_collapse=True,
            no_resize=True,
            show=False,
            on_close=self.close,
        )
        for label, input_tag, dlg_tag in (
            ("Source file", self._src_tag, self._src_dlg),
            ("Target file", self._tgt_tag, self._tgt_dlg),
        ):
            dpg.add_text(label, parent=self.TAG)
            with dpg.group(horizontal=True, parent=self.TAG):
                dpg.add_input_text(
                    tag=input_tag,
                    hint="path…",
                    width=2 * INPUT_W,
                )
                dpg.add_button(label="Browse…", callback=self._browse(dlg_tag))
            dpg.add_file_dialog(
                tag=dlg_tag,
                directory_selector=False,
                show=False,
                modal=True,
                file_count=1,
                width=560,
                height=400,
                default_path=os.getcwd(),
                callback=self._picked(input_tag),
                cancel_callback=lambda *_a: None,
            )
            add_all_files_filter(dlg_tag)
        dpg.add_text("", tag=self._error_tag, parent=self.TAG, color=ERR_COLOR)
        with dpg.group(horizontal=True, parent=self.TAG):
            dpg.add_button(label="Compare", callback=self._launch)
            dpg.add_button(label="Cancel", callback=self.close)

    def open(self) -> None:
        dpg.configure_item(self.TAG, show=True)

    def close(self, *_args) -> None:
        for tag in (self._src_dlg, self._tgt_dlg, self.TAG):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)

    def _browse(self, dlg_tag: str) -> Callable[[], None]:
        def _show(*_args) -> None:
            dpg.configure_item(dlg_tag, show=True)

        return _show

    def _picked(self, input_tag: str) -> Callable[[object, object], None]:
        def _fill(_sender, app_data) -> None:
            path = picked_path(app_data)
            if path:
                dpg.set_value(input_tag, path)

        return _fill

    def _launch(self, *_args) -> None:
        src = dpg.get_value(self._src_tag).strip()
        tgt = dpg.get_value(self._tgt_tag).strip()
        if not src or not tgt:
            dpg.set_value(
                self._error_tag, "Choose both the source and the target file."
            )
            return
        dpg.set_value(self._error_tag, "")
        self.close()
        self._on_launch(src, tgt)


class ErrorPopup:
    """
    Non-fatal error notice (message + OK), e.g. a file that vanished
    between the file dialog and the open. Transient: the window deletes
    itself on OK; the tag is unique per instance so several popups can
    coexist.
    """

    def __init__(self, message: str) -> None:
        self.TAG = _unique_tag("gui-error")
        dpg.add_window(
            tag=self.TAG,
            label="Error",
            modal=True,
            no_open_over_existing_popup=True,
            no_collapse=True,
            no_resize=True,
            width=420,
        )
        dpg.add_text(message, parent=self.TAG, wrap=390)
        with dpg.group(horizontal=True, parent=self.TAG):
            dpg.add_button(label="OK", callback=self.close)

    def close(self, *_args) -> None:
        dpg.delete_item(self.TAG)
