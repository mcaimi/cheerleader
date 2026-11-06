"""
Editor controller for the Dear PyGui hex front-end.

Owns every hex-editor flow: inline hex typing, the Enter byte-edit popup,
undo, save (file dialog → background worker → reopen), goto/search with
wrap + ``n``/``N`` + Esc-cancel, reload, and the dirty-quit confirmation.
This is the port of ``tui.tabs.HexEditorTab`` onto the GUI worker pattern
(``workers.start_worker`` + :class:`~cheerleader.gui.workers.UiBridge`).

Concurrency (mirrors the TUI tab):

* one :class:`~cheerleader.gui.workers.BusyTracker` marks the exclusive
in-flight operation (``"saving"`` / ``"searching"``); busy paths refuse
conflicting actions with a status note, like ``_refuse_if_busy``.
* starting a search while one runs cancels the old worker first (the TUI's
exclusive worker group); completion events carry the producing
:class:`~cheerleader.gui.workers.WorkerHandle` and stale completions are
dropped via ``BusyTracker.is_stale``.
* long operations run on daemon threads and report through the bridge; the
frame loop drains it and calls :meth:`on_event`.

Key handling: the controller registers its own global handler registry. All
editor keys are no-ops while a dialog is open (the dialog's input owns the
keyboard), and the grid's key/click/wheel handlers are switched off via
``grid.input_enabled`` for the same reason.

Inline typing model: the first hex digit replaces the high nibble of the
byte at the cursor (written immediately — visible feedback, and standard
hex-editor behavior), the second digit replaces the low nibble and advances
the cursor. A cursor move clears the pending nibble; ``undo`` reverts a
half-written byte like any other write.
"""

import os
from functools import partial
from typing import Any

from cheerleader.gui.app import GuiApp, human_size
from cheerleader.gui.dialogs import (
    ByteEditDialog,
    ConfirmDialog,
    SaveDialog,
    SearchDialog,
)
from cheerleader.gui.hexgrid import HexGrid, ascii_cell_text
from cheerleader.gui.workers import (
    BusyTracker,
    EventKind,
    UiEvent,
    WorkerHandle,
    start_worker,
)
from cheerleader.libs.hexdoc import HexDocument

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

from cheerleader.gui.const import KEY_ENTER

# mvKey_0..9 (536..545) / mvKey_A..F (546..551) → nibble value. Populated
# only for constants the installed wheel actually defines.
_HEX_KEY_CODES: dict[int, int] = {}
if dpg is not None:  # pragma: no branch - always set outside the stub test
    for _i, _ch in enumerate("0123456789ABCDEF"):
        _code = getattr(dpg, f"mvKey_{_ch}", None)
        if _code is not None:
            _HEX_KEY_CODES[_code] = _i


class EditorController:
    """Hex-editor flows over one :class:`HexDocument` + :class:`HexGrid`.

    several editor windows may coexist, so *everything* the
    controller registers globally is namespaced with *ns* — the key
    registry, the modal dialog tags and the grid's own registries (the grid
    gets ``name=ns`` from the opener). *window* and *status_tag* point at
    this tool's floating window and its private status strip.
    """

    def __init__(
        self,
        app: GuiApp,
        grid: HexGrid,
        doc: HexDocument,
        *,
        window: int | str,
        status_tag: int | str,
        ns: str,
    ) -> None:
        self.app = app
        self.grid = grid
        self.doc = doc
        self.window = window
        self.status_tag = status_tag
        self.ns = ns
        self.busy = BusyTracker()
        self._pending: int | None = None  # first typed hex nibble (high)
        self._mode = "offset"
        self._raw = ""
        self._pattern: bytes | None = None
        # Status note shown only while the cursor stays on the given offset.
        self._note: tuple[int, str] | None = None
        self._modal: str | None = None  # "edit" | "search" | "confirm" | "save"
        self._dialog: Any = None
        grid.on_cursor = self._cursor_moved
        self._install_keys()

    # ── global keys ────────────────────────────────────────────────────────

    def _install_keys(self) -> None:
        """Register the editor's global key handlers (per-instance tag)."""
        with dpg.handler_registry(tag=f"editor-keys-{self.ns}"):
            dpg.add_key_press_handler(key=KEY_ENTER, callback=self._on_enter_key)
            dpg.add_key_press_handler(key=dpg.mvKey_U, callback=self._on_undo_key)
            dpg.add_key_press_handler(key=dpg.mvKey_S, callback=self._on_save_key)
            dpg.add_key_press_handler(key=dpg.mvKey_Slash, callback=self._on_search_key)
            # n and N differ only by the shift state (mvKey_N fires for both).
            dpg.add_key_press_handler(key=dpg.mvKey_N, callback=self._on_next_key)
            dpg.add_key_press_handler(key=dpg.mvKey_R, callback=self._on_reload_key)
            dpg.add_key_press_handler(
                key=dpg.mvKey_Escape, callback=self._on_escape_key
            )
            for code in _HEX_KEY_CODES:
                dpg.add_key_press_handler(
                    key=code, callback=partial(self._type_key, code)
                )

    def _shift_down(self) -> bool:
        return dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift)

    def _on_enter_key(self, *_args) -> None:
        if self._modal is None:
            self.action_edit()

    def _on_undo_key(self, *_args) -> None:
        if self._modal is None:
            self.action_undo()

    def _on_save_key(self, *_args) -> None:
        if self._modal is None:
            self.action_save()

    def _on_search_key(self, *_args) -> None:
        if self._modal is None:
            self.action_search()

    def _on_next_key(self, *_args) -> None:
        if self._modal is None:
            self.action_search_next(forward=not self._shift_down())

    def _on_reload_key(self, *_args) -> None:
        if self._modal is None:
            self.action_reload()

    def _type_key(self, code: int, *_args) -> None:
        self.type_hex_digit(_HEX_KEY_CODES[code])

    def _on_escape_key(self, *_args) -> None:
        if self._modal is not None:
            # The save file dialog handles its own Esc (cancel_callback).
            if self._modal != "save":
                self._close_dialog()
            return
        if self.busy.op == "searching":
            self._cancel_search()

    # ── dialog bookkeeping ────────────────────────────────────────────────

    def _open_dialog(self, kind: str, dialog: Any) -> None:
        if self._dialog is not None:
            self._dialog.close()
        self._dialog = dialog
        self._modal = kind
        self.grid.input_enabled = False
        dialog.open()

    def _close_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None
        self._modal = None
        self.grid.input_enabled = True

    # ── status (TUI HexEditorTab._update_status parity) ───────────────────

    def _cursor_moved(self, _offset: int) -> None:
        self._pending = None
        self.update_status()

    def _set_note(self, offset: int, text: str) -> None:
        self._note = (offset, text)

    def update_status(self, extra: str = "") -> None:
        """Recompose the status strip; *extra* overrides the cursor note."""
        doc = self.doc
        parts = [f"{doc.path}  {human_size(doc.size)}"]
        cur = self.grid.cursor
        if not extra and self._note and self._note[0] == cur:
            extra = self._note[1]
        if doc.size:
            b = doc.read(cur, 1)[0]
            parts.append(
                f"0x{cur:08x} ({cur:,})  = 0x{b:02x} {b:3d} '{ascii_cell_text(b)}'"
            )
        else:
            parts.append("empty file")
        if doc.modified:
            parts.append(f"{doc.edit_count:,} unsaved [modified]")
        if extra:
            parts.append(extra)
        parts.append("Ctrl+Q quit")
        dpg.set_value(self.status_tag, "  │  ".join(parts))

    def _refuse_if_busy(self) -> bool:
        if self.busy.op is None:
            return False
        self._set_note(self.grid.cursor, f"busy {self.busy.op}…")
        self.update_status()
        return True

    # ── inline hex typing ──────────────────────────────────────────────────

    def type_hex_digit(self, nibble: int) -> None:
        """Type one hex nibble at the cursor (2 digits auto-advance)."""
        if self._modal is not None or self.busy.op is not None:
            return
        if self.doc.size == 0:
            return
        cur = self.grid.cursor
        if self._pending is None:
            low = self.doc.read(cur, 1)[0] & 0x0F
            self.doc.write(cur, bytes([(nibble << 4) | low]))
            self._pending = nibble
            self.grid.invalidate_range(cur, cur + 1)
            self.update_status()
        else:
            self.doc.write(cur, bytes([(self._pending << 4) | nibble]))
            self._pending = None
            self.grid.invalidate_range(cur, cur + 1)
            self.grid.move_to(cur + 1)  # auto-advance; clears pending + status

    # ── editing ────────────────────────────────────────────────────────────

    def action_edit(self) -> None:
        """Open the byte-edit popup at the cursor (Enter)."""
        if self._modal is not None or self._refuse_if_busy():
            return
        if self.doc.size == 0:
            self._set_note(self.grid.cursor, "empty file — nothing to edit")
            self.update_status()
            return
        offset = self.grid.cursor
        available = self.doc.size - offset

        def validate(text: str) -> bytes:
            # Lazy import: cheerleader.tui.__init__ drags the whole Textual
            # app in, so the GUI must not import it at module load. Only the
            # popups need the parsers, and they reuse the TUI's module
            # verbatim to guarantee input-syntax parity.
            from cheerleader.tui.hexparse import parse_byte_sequence

            data = parse_byte_sequence(text)
            if len(data) > available:
                raise ValueError(
                    f"{len(data)} bytes would run past end of file "
                    f"(only {available} left from this offset)"
                )
            return data

        def apply(data: bytes) -> None:
            self._close_dialog()
            self.doc.write(offset, data)
            self.grid.invalidate_range(offset, offset + len(data))
            self.grid.move_to(offset + len(data))
            self.update_status()

        self._open_dialog(
            "edit",
            ByteEditDialog(
                offset=offset,
                current=self.doc.read(offset, 1),
                validate=validate,
                on_accept=apply,
                on_cancel=self._close_dialog,
                tag=f"gui-dlg-edit-{self.ns}",
            ),
        )

    def action_undo(self) -> None:
        if self._modal is not None or self._refuse_if_busy():
            return
        offset = self.doc.undo()
        if offset is None:
            self._set_note(self.grid.cursor, "nothing to undo")
        else:
            self.grid.refresh()  # edited colors may span several rows
            self.grid.move_to(offset)
        self.update_status()

    # ── saving ─────────────────────────────────────────────────────────────

    def action_save(self) -> None:
        if self._modal is not None or self._refuse_if_busy():
            return
        self._open_dialog(
            "save",
            SaveDialog(
                default_path=f"{self.doc.path}.patched",
                on_chosen=self._on_save_chosen,
                tag=f"gui-dlg-save-{self.ns}",
            ),
        )

    def _on_save_chosen(self, path: str | None) -> None:
        if not path:
            self._close_dialog()
            return
        if os.path.exists(path):
            self._open_dialog(
                "confirm",
                ConfirmDialog(
                    message=f"{path} exists. Overwrite?",
                    on_answer=lambda ok: self._overwrite_answer(ok, path),
                    tag=f"gui-dlg-confirm-{self.ns}",
                ),
            )
            return
        self._close_dialog()
        self._start_save(path)

    def _overwrite_answer(self, ok: bool, path: str) -> None:
        self._close_dialog()
        if ok:
            self._start_save(path)

    def _start_save(self, path: str) -> None:
        handle = start_worker(lambda h: self._save_worker(path, h), name="gui-save")
        self.busy.start("saving", handle)
        self.update_status("Saving… 0%")

    def _save_worker(self, path: str, handle: WorkerHandle) -> None:
        last = -1

        def progress(frac: float) -> None:
            nonlocal last
            pct = int(frac * 100)
            if pct // 5 != last // 5:  # 5%-step throttling (TUI parity)
                last = pct
                self.app.bridge.post(0, EventKind.PROGRESS, pct / 100, handle)

        try:
            self.doc.save_to(path, progress)
        except OSError as e:
            self.app.bridge.post(0, EventKind.FAILED, f"Save failed: {e}", handle)
            return
        self.app.bridge.post(0, EventKind.SAVE_DONE, path, handle)

    # ── go to / search ─────────────────────────────────────────────────────

    def action_search(self) -> None:
        if self._modal is not None:
            return
        if self.busy.op == "saving" and self._refuse_if_busy():
            return

        def validate(mode: str, text: str) -> tuple[str, Any, str]:
            # Lazy import — see action_edit.validate.
            from cheerleader.tui.hexparse import parse_byte_sequence, parse_offset

            if mode == "offset":
                offset = parse_offset(text)
                if not 0 <= offset < self.doc.size:
                    raise ValueError(
                        f"offset out of range (file is 0x{self.doc.size:x} bytes)"
                    )
                return ("offset", offset, text)
            if mode == "hex":
                return ("hex", parse_byte_sequence(text), text)
            if not text:
                raise ValueError("enter text to search for")
            return ("text", text.encode("utf-8"), text)

        def accept(result: tuple[str, Any, str]) -> None:
            self._close_dialog()
            mode, value, raw = result
            self._mode, self._raw = mode, raw
            if mode == "offset":
                self.grid.set_match(None)
                self.grid.move_to(value, top=True)
                self._note = None
                self.update_status()
                return
            self._pattern = value
            self._start_search(forward=True)

        self._open_dialog(
            "search",
            SearchDialog(
                mode=self._mode,
                value=self._raw,
                validate=validate,
                on_accept=accept,
                on_cancel=self._close_dialog,
                tag=f"gui-dlg-search-{self.ns}",
            ),
        )

    def action_search_next(self, forward: bool) -> None:
        if self._pattern is None:
            self._set_note(self.grid.cursor, "no search pattern — press / first")
            self.update_status()
            return
        if self._modal is not None:
            return
        self._start_search(forward)

    def _start_search(self, forward: bool) -> None:
        if self.busy.op == "saving" and self._refuse_if_busy():
            return
        if self.busy.op == "searching":
            # Restarting while a search runs cancels the old run (TUI
            # exclusive-group parity); its late completion drops as stale.
            self.busy.worker.cancel.cancel()
        pattern = self._pattern
        cur = self.grid.cursor
        handle = start_worker(
            lambda h: self._search_worker(pattern, cur, forward, h),
            name="gui-search",
        )
        self.busy.start("searching", handle)
        self.update_status("Searching… 0%  (Esc cancels)")

    def _search_worker(
        self, pattern: bytes, cur: int, forward: bool, handle: WorkerHandle
    ) -> None:
        doc = self.doc
        cancelled = handle.cancel.cancelled
        last = -1

        def progress(frac: float) -> None:
            nonlocal last
            pct = int(frac * 100)
            if pct // 5 != last // 5:
                last = pct
                self.app.bridge.post(0, EventKind.PROGRESS, pct / 100, handle)

        n = len(pattern)
        wrapped = False
        try:
            if forward:
                hit = doc.find(pattern, cur + 1, cancelled=cancelled, progress=progress)
                if hit is None and not handle.cancel.cancelled:
                    wrapped = True
                    hit = doc.find(
                        pattern, 0, cur + n, cancelled=cancelled, progress=progress
                    )
            else:
                hit = doc.rfind(
                    pattern, 0, cur + n - 1, cancelled=cancelled, progress=progress
                )
                if hit is None and not handle.cancel.cancelled:
                    wrapped = True
                    hit = doc.rfind(
                        pattern, cur, doc.size, cancelled=cancelled, progress=progress
                    )
        except OSError as e:
            if not handle.cancel.cancelled:
                self.app.bridge.post(0, EventKind.FAILED, f"Search failed: {e}", handle)
            return
        if not handle.cancel.cancelled:
            self.app.bridge.post(0, EventKind.FOUND, (hit, n, wrapped), handle)

    def _cancel_search(self) -> None:
        self.busy.worker.cancel.cancel()
        self.busy.clear()
        self._set_note(self.grid.cursor, "search cancelled")
        self.update_status()

    def _search_done(self, payload: Any, worker: WorkerHandle) -> None:
        # Drop stale callbacks from a superseded/cancelled search run.
        if self.busy.is_stale(worker):
            return
        self.busy.clear()
        hit, n, wrapped = payload
        if hit is None:
            self.grid.set_match(None)
            self._set_note(self.grid.cursor, "pattern not found")
            self.update_status()
            return
        self._set_note(
            hit,
            f"match at 0x{hit:08x}"
            + ("  (wrapped)" if wrapped else "")
            + "  n/N → next/prev",
        )
        self.grid.set_match((hit, n))
        self.grid.move_to(hit)
        self.update_status()

    # ── reload / quit ──────────────────────────────────────────────────────

    def action_reload(self) -> None:
        if self._modal is not None or self._refuse_if_busy():
            return
        if not self.doc.modified:
            self._reload()
            return
        self._open_dialog(
            "confirm",
            ConfirmDialog(
                message="Discard unsaved edits and reload?",
                on_answer=self._reload_answer,
                tag=f"gui-dlg-confirm-{self.ns}",
            ),
        )

    def _reload_answer(self, ok: bool) -> None:
        self._close_dialog()
        if ok:
            self._reload()

    def _reload(self) -> None:
        try:
            self.doc.reopen(self.doc.path)
        except OSError as e:
            self._set_note(self.grid.cursor, f"Reload failed: {e}")
            self.update_status()
            return
        self._pending = None
        self._note = None
        self.grid.set_match(None)
        self.grid.refresh()
        self.grid.move_to(self.grid.cursor)  # clamps to the (new) size
        self.update_status()

    # ── window lifecycle ────────

    def dirty_edit_count(self) -> int:
        """Unsaved edit count for the shell's aggregate quit confirm."""
        return self.doc.edit_count

    def window_close_requested(self) -> None:
        """The tool window's X was clicked: confirm when dirty, else close.

        DPG hides the window before this runs; answering *No* re-shows it
        (the veto-by-reshow pattern — DPG has no close veto).
        """
        if not self.doc.modified:
            self.app.close_tool_for(self)
            return
        n = self.doc.edit_count
        self._open_dialog(
            "confirm",
            ConfirmDialog(
                message=(
                    f"{n} unsaved edit{'s' if n != 1 else ''} — close without saving?"
                ),
                on_answer=self._close_answer,
                tag=f"gui-dlg-confirm-{self.ns}",
            ),
        )

    def _close_answer(self, ok: bool) -> None:
        self._close_dialog()
        if ok:
            self.app.close_tool_for(self)
        else:
            dpg.configure_item(self.window, show=True)  # veto: bring it back

    def shutdown(self) -> None:
        """Teardown before the tool window is deleted: worker + modals + keys."""
        if self.busy.op is not None and self.busy.worker is not None:
            self.busy.worker.cancel.cancel()
        if self._modal is not None:
            self._close_dialog()
        if dpg.does_item_exist(f"editor-keys-{self.ns}"):
            dpg.delete_item(f"editor-keys-{self.ns}")

    # ── bridge events (frame loop dispatch) ────────────────────────────────

    def on_event(self, event: UiEvent) -> None:
        """Handle one drained UiBridge event."""
        kind, payload, worker = event.kind, event.payload, event.worker
        if kind is EventKind.PROGRESS:
            if self.busy.is_stale(worker):
                return
            if self.busy.op == "saving":
                self.update_status(f"Saving… {int(payload * 100)}%")
            elif self.busy.op == "searching":
                self.update_status(f"Searching… {int(payload * 100)}%  (Esc cancels)")
        elif kind is EventKind.SAVE_DONE:
            if self.busy.is_stale(worker):
                return
            self.busy.clear()
            self.doc.reopen(payload)
            self._note = None
            self.grid.set_match(None)
            self.grid.refresh()
            self.grid.move_to(self.grid.cursor)
            self._set_note(self.grid.cursor, f"saved {payload}")
            self.update_status()
        elif kind is EventKind.FAILED:
            if self.busy.is_stale(worker):
                return
            self.busy.clear()
            self._set_note(self.grid.cursor, str(payload))
            self.update_status()
        elif kind is EventKind.FOUND:
            self._search_done(payload, worker)
