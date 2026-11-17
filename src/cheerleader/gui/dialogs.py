"""Modal dialogs and file pickers for the Dear PyGui front-end."""

import itertools
import os
from collections.abc import Callable

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

from cheerleader.gui.const import INPUT_W, KEY_ESCAPE, SHORTCUTS
from cheerleader.gui.theme import COL_BLUE, COL_YELLOW, ERR_COLOR
from cheerleader.gui.tooling import focus_guard

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


class ModalDialog:
    """
    Base of the modal dialogs: one modal, fixed-size window with a unique
    tag (several tool windows may each have a dialog up, so fixed tags
    would collide), centered on the viewport when shown.

    The title-bar X calls :meth:`_dismissed` — subclasses map it onto
    their cancel answer so a caller waiting for a reply is never left
    hanging; the default just deletes the window.
    """

    PREFIX = "gui-dlg"
    WIDTH: int | None = None  # None = auto-size to the content

    def __init__(self, *, label: str, tag: str | None = None, show: bool = False):
        self.TAG = tag or _unique_tag(self.PREFIX)
        size = {"width": self.WIDTH} if self.WIDTH else {}
        dpg.add_window(
            tag=self.TAG,
            label=label,
            modal=True,
            no_open_over_existing_popup=True,
            no_collapse=True,
            no_resize=True,
            show=show,
            on_close=self._dismissed,
            **size,
        )
        if show:
            _center(self.TAG)

    def open(self) -> None:
        _center(self.TAG)
        dpg.configure_item(self.TAG, show=True)

    def close(self, *_args) -> None:
        if dpg.does_item_exist(self.TAG):
            dpg.delete_item(self.TAG)

    def _dismissed(self, *_args) -> None:
        self.close()


def _center(tag: str) -> None:
    """Place window *tag* at the viewport center (best effort).

    Auto-sized windows have no measured size before their first frame, so
    an estimate (configured width, nominal height) stands in; headless (no
    viewport) this is a no-op.
    """
    try:
        vw = dpg.get_viewport_client_width()
        vh = dpg.get_viewport_client_height()
        w, h = dpg.get_item_rect_size(tag)
    except Exception:  # noqa: BLE001 - no viewport / not laid out yet
        return
    if not vw or not vh:
        return
    w = w or dpg.get_item_configuration(tag).get("width") or 2 * INPUT_W
    h = h or 140
    dpg.set_item_pos(tag, [max((vw - w) // 2, 0), max((vh - h) // 3, 0)])


class _PromptDialog(ModalDialog):
    """A modal input prompt: text input + error label + OK/Cancel.

    ``validate`` turns the input into a result (raising ``ValueError`` with
    a message shown inline) which goes to ``on_accept``; OK is bound to the
    button *and* to Enter inside the input. Cancel, Esc (routed by the
    controller) and the title-bar X all end in ``on_cancel``.
    """

    def __init__(
        self,
        *,
        label: str,
        tag: str | None,
        validate: Callable,
        on_accept: Callable,
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__(label=label, tag=tag)
        self._validate = validate
        self._on_accept = on_accept
        self._on_cancel = on_cancel
        self._input_tag = f"{self.TAG}-input"
        self._error_tag = f"{self.TAG}-error"
        # Esc cancels while this prompt has the focus.
        self._keys_tag = f"{self.TAG}-keys"
        with dpg.handler_registry(tag=self._keys_tag):
            dpg.add_key_press_handler(
                key=KEY_ESCAPE, callback=focus_guard(self.TAG, self._cancel)
            )

    def _add_input(self, default_value: str, hint: str = "") -> None:
        dpg.add_input_text(
            tag=self._input_tag,
            parent=self.TAG,
            default_value=default_value,
            hint=hint,
            on_enter=True,
            callback=self._try_apply,
            width=INPUT_W,
        )

    def _add_error_and_buttons(self, ok_label: str) -> None:
        dpg.add_text("", tag=self._error_tag, parent=self.TAG, color=ERR_COLOR)
        with dpg.group(horizontal=True, parent=self.TAG):
            dpg.add_button(label=ok_label, callback=self._try_apply)
            dpg.add_button(label="Cancel", callback=self._cancel)

    def _validated(self):
        return self._validate(dpg.get_value(self._input_tag))

    def open(self) -> None:
        super().open()
        dpg.focus_item(self._input_tag)

    def close(self, *_args) -> None:
        super().close()
        if dpg.does_item_exist(self._keys_tag):
            dpg.delete_item(self._keys_tag)

    def _cancel(self, *_args) -> None:
        self._on_cancel()

    def _dismissed(self, *_args) -> None:
        self._cancel()  # title-bar X == Cancel

    def _try_apply(self, *_args) -> None:
        try:
            result = self._validated()
        except ValueError as e:
            dpg.set_value(self._error_tag, str(e))
            return
        dpg.set_value(self._error_tag, "")
        self._on_accept(result)


class PromptDialog(_PromptDialog):
    """A one-line prompt: optional help text, the input, OK/Cancel.

    ``validate(text)`` returns the result handed to ``on_accept`` (or
    raises ``ValueError``); ``on_cancel`` defaults to just closing.
    """

    PREFIX = "gui-dlg-prompt"

    def __init__(
        self,
        *,
        label: str,
        validate: Callable[[str], object],
        on_accept: Callable[[object], None],
        on_cancel: Callable[[], None] | None = None,
        help_text: str = "",
        hint: str = "",
        value: str = "",
        ok_label: str = "OK",
    ) -> None:
        super().__init__(
            label=label,
            tag=None,
            validate=validate,
            on_accept=on_accept,
            on_cancel=on_cancel or self.close,
        )
        if help_text:
            dpg.add_text(help_text, parent=self.TAG)
        self._add_input(value, hint=hint)
        self._add_error_and_buttons(ok_label)


class ByteEditDialog(_PromptDialog):
    """
    Edit-bytes prompt at a fixed offset (the Enter popup).

    Mirrors the TUI ``ByteEditPrompt``: shows the current byte, pre-fills the
    input with it, validates via ``validate(text) -> bytes`` (the controller
    supplies ``parse_byte_sequence`` plus the past-EOF check) and hands the
    parsed bytes to ``on_accept``.
    """

    PREFIX = "gui-dlg-edit"

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
        super().__init__(
            label=f"Edit at 0x{offset:08x}",
            tag=tag,
            validate=validate,
            on_accept=on_accept,
            on_cancel=on_cancel,
        )
        c = current[0] if current else 0
        ch = chr(c) if 0x20 <= c < 0x7F else "."
        dpg.add_text(f"current: 0x{c:02x}  {c:3d}  '{ch}'", parent=self.TAG)
        self._add_input(
            f"{c:02x}" if current else "",
            hint='de ad be ef · 0x7f · #127 · "text',
        )
        self._add_error_and_buttons("Apply")


class SearchDialog(_PromptDialog):
    """
    Go-to-offset / search prompt (the ``/`` popup).

    Mirrors the TUI ``SearchPrompt``: a mode radio (offset / hex / text) plus
    the input; ``validate(mode, text)`` (controller-supplied, using
    ``parse_offset`` / ``parse_byte_sequence``) produces the
    ``(mode, value, raw)`` result handed to ``on_accept``.
    """

    PREFIX = "gui-dlg-search"

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
        super().__init__(
            label="Go to offset / search",
            tag=tag,
            validate=validate,
            on_accept=on_accept,
            on_cancel=on_cancel,
        )
        self._mode_tag = f"{self.TAG}-mode"
        dpg.add_text(
            'offset: 1a2b · 0x1a2b · #6699   hex: de ad be ef   text: "abc',
            parent=self.TAG,
        )
        self._add_input(value)
        dpg.add_radio_button(
            ["offset", "hex", "text"],
            default_value=mode,
            horizontal=True,
            tag=self._mode_tag,
            parent=self.TAG,
        )
        self._add_error_and_buttons("Go")

    def _validated(self):
        mode = dpg.get_value(self._mode_tag)
        return self._validate(mode, dpg.get_value(self._input_tag))


class InfoDialog(ModalDialog):
    """Informational notice (message + OK); OK or X deletes the window."""

    PREFIX = "gui-dlg-info"

    def __init__(self, *, message: str, tag: str | None = None) -> None:
        super().__init__(label="Info", tag=tag)
        self.message = message
        dpg.add_text(message, parent=self.TAG)
        dpg.add_button(label="OK", parent=self.TAG, callback=self.close)


class ShortcutsDialog(ModalDialog):
    """Help ▸ Keyboard shortcuts: :data:`~cheerleader.gui.const.SHORTCUTS`
    as one two-column table per tool."""

    PREFIX = "gui-dlg-keys"
    WIDTH = 600

    def __init__(self) -> None:
        super().__init__(label="Keyboard shortcuts")
        dpg.configure_item(self.TAG, height=520, no_resize=False)
        with dpg.child_window(parent=self.TAG, height=-34, border=False):
            for section, rows in SHORTCUTS:
                dpg.add_text(section, color=COL_BLUE)
                with dpg.table(
                    header_row=False,
                    row_background=True,
                    policy=dpg.mvTable_SizingStretchProp,
                ):
                    dpg.add_table_column(init_width_or_weight=1.3)
                    dpg.add_table_column(init_width_or_weight=1.0)
                    for action, keys in rows:
                        with dpg.table_row():
                            dpg.add_text(action)
                            dpg.add_text(keys, color=COL_YELLOW)
                dpg.add_spacer(height=6)
        dpg.add_button(label="Close", parent=self.TAG, callback=self.close)


class ConfirmDialog(ModalDialog):
    """Yes/No confirmation (overwrite / discard / quit-without-saving)."""

    PREFIX = "gui-dlg-confirm"

    def __init__(
        self,
        *,
        message: str,
        on_answer: Callable[[bool], None],
        tag: str | None = None,
    ) -> None:
        super().__init__(label="Confirm", tag=tag)
        self._on_answer = on_answer
        self.message = message
        dpg.add_text(message, parent=self.TAG)
        with dpg.group(horizontal=True, parent=self.TAG):
            dpg.add_button(label="Yes", callback=self._yes)
            dpg.add_button(label="No", callback=self._no)

    def _yes(self, *_args) -> None:
        self._on_answer(True)

    def _no(self, *_args) -> None:
        self._on_answer(False)

    def _dismissed(self, *_args) -> None:
        self._no()  # title-bar X == No (else the caller waits forever)


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

    def __init__(
        self, *, on_launch: Callable[[str, str], None], source: str = ""
    ) -> None:
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
        dpg.set_value(self._src_tag, source)  # e.g. the shell's loaded file
        dpg.add_text("", tag=self._error_tag, parent=self.TAG, color=ERR_COLOR)
        with dpg.group(horizontal=True, parent=self.TAG):
            dpg.add_button(label="Compare", callback=self._launch)
            dpg.add_button(label="Cancel", callback=self.close)

    def open(self) -> None:
        _center(self.TAG)
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


class ErrorPopup(ModalDialog):
    """
    Non-fatal error notice (message + OK), e.g. a file that vanished
    between the file dialog and the open. Shown on construction; OK or the
    title-bar X deletes the window.
    """

    PREFIX = "gui-error"
    WIDTH = 420

    def __init__(self, message: str) -> None:
        super().__init__(label="Error", show=True)
        dpg.add_text(message, parent=self.TAG, wrap=self.WIDTH - 30)
        with dpg.group(horizontal=True, parent=self.TAG):
            dpg.add_button(label="OK", callback=self.close)
