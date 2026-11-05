"""Modal dialogs for the Dear PyGui hex editor."""

import os
from collections.abc import Callable

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

from cheerleader.gui.const import ERR_COLOR, INPUT_W


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
    ) -> None:
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
    ) -> None:
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
    ) -> None:
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
    ) -> None:
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

    def open(self) -> None:
        dpg.configure_item(self.TAG, show=True)

    def close(self) -> None:
        dpg.delete_item(self.TAG)

    def _picked(self, _sender, app_data) -> None:
        paths = app_data if isinstance(app_data, (list, tuple)) else [app_data]
        self._on_chosen(str(paths[0]) if paths else None)

    def _cancelled(self, *_args) -> None:
        self._on_chosen(None)
