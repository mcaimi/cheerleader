"""
Disassembly window controller for the Dear PyGui front-end.

The port of ``tui.tabs.DisasmTab``: a floating tool window with an
executable-section dropdown at the top (TUI rule: segments with
``initprot & 0x4``, sections with ``size > 0`` and ``offset > 0``) and a
virtualized instruction grid filling the rest
(:class:`~cheerleader.gui.disasmgrid.DisasmGrid`).

Flow (TUI parity):

* opening auto-picks a section — ``__TEXT,__text`` first, then any
  ``__text``, then any ``.text`` — and disassembles it immediately;
* disassembly + call-graph construction run on a background worker
  (``EventKind.DISASM_DONE``; the engine is auto-picked once per process:
  capstone if importable, else objdump via
  :func:`cheerleader.libs.objdump._detect_objdump`);
* a newer section pick cancels the in-flight worker; stale completions
  are dropped (``BusyTracker``);
* ``Esc`` cancels a running disassembly;
* ``c`` opens the call-flow tree and ``f`` the control-flow graph for the
  cursor's function (:mod:`cheerleader.gui.callflow`, D4);

The view is read-only (`dirty_edit_count() == 0`); the window holds its own
``BinaryInfo`` snapshot like every inspector view.
"""

from __future__ import annotations

import os
from bisect import bisect_right
from typing import TYPE_CHECKING

from cheerleader.gui.callflow import open_call_flow, open_cfg
from cheerleader.gui.const import DISASM_WIN_H, DISASM_WIN_W, KEY_ESCAPE
from cheerleader.gui.dialogs import PromptDialog
from cheerleader.gui.disasmgrid import DisasmGrid
from cheerleader.gui.tooling import (
    ReadOnlyController,
    add_tool_menu,
    build_tool_window,
    ctrl_down,
    find_tool,
    focus_tool,
)
from cheerleader.gui.views import file_offset_of
from cheerleader.gui.workers import BusyTracker, EventKind, WorkerHandle, start_worker
from cheerleader.libs.cfg import (
    CallGraph,
    build_call_graph,
    build_cfg,
    slice_function_instrs,
)
from cheerleader.libs.disasm import disassemble_section
from cheerleader.libs.types import BinaryInfo, DisasmInstruction

if TYPE_CHECKING:
    from cheerleader.gui.app import GuiApp

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

_ENGINE: str | None = None  # None = not picked yet; "" = no engine available

# Menu-selected engine: "auto" keeps the cached auto-pick
# below; an explicit "capstone"/"objdump" forces that engine — or fails
# soft (None) when the forced engine is unavailable in this process.
_OVERRIDE: str = "auto"

COMBO_RESERVE = 32  # px reserved above the grid pane for the section combo row


def section_label(seg: str, sect: str) -> str:
    """Combo-item text for one executable section."""
    return f"{seg},{sect}"


def engine_available(name: str) -> bool:
    """Whether *name* (``capstone``/``objdump``) works in this process.

    Both probes are cheap and cached downstream: a capstone import (OS
    module cache after the first) and objdump's PATH scan
    (:func:`~cheerleader.libs.objdump._detect_objdump` memoizes).
    """
    if name == "capstone":
        try:
            import capstone  # noqa: F401

            return True
        except ImportError:
            return False
    if name == "objdump":
        from cheerleader.libs.objdump import _detect_objdump

        return _detect_objdump() is not None
    return False


def set_engine_override(name: str) -> None:
    """Set the Disassembler ▸ Engine radio choice (``auto``/``capstone``/
    ``objdump``). Affects *future* ``pick_engine`` calls — open views keep
    their rendered snapshot (close + reopen to re-run)."""
    global _OVERRIDE
    if name not in ("auto", "capstone", "objdump"):
        raise ValueError(f"unknown engine override: {name!r}")
    _OVERRIDE = name


def engine_override() -> str:
    """The current radio choice (``"auto"`` until the menu changes it)."""
    return _OVERRIDE


def pick_engine() -> str | None:
    """Pick the disassembly engine.

    With the menu's **Auto** choice: capstone (in-process, no subprocess)
    is preferred, objdump is the fallback, ``None`` means neither is
    available and views fail soft into their status strip. An explicit
    override forces that engine, or ``None`` when it is unavailable.
    """
    global _ENGINE
    if _OVERRIDE != "auto":
        return _OVERRIDE if engine_available(_OVERRIDE) else None
    if _ENGINE is None:
        try:
            import capstone  # noqa: F401

            _ENGINE = "capstone"
        except ImportError:
            from cheerleader.libs.objdump import _detect_objdump

            _ENGINE = "objdump" if _detect_objdump() is not None else ""
    return _ENGINE or None


def exec_sections(info: BinaryInfo) -> list[tuple[str, str]]:
    """Executable sections in TUI order: ``initprot & 0x4`` segments,
    skipping empty/unmapped sections (``size == 0 or offset == 0``)."""
    exec_segs = {seg.name for seg in info.segments if seg.initprot & 0x4}
    out: list[tuple[str, str]] = []
    for seg in info.segments:
        if seg.name not in exec_segs:
            continue
        for s in seg.sections:
            if s.size == 0 or s.offset == 0:
                continue
            out.append((s.segment, s.name))
    return out


def auto_pick(sections: list[tuple[str, str]]) -> int | None:
    """Default section: ``__TEXT,__text``, then any ``__text``, then any
    ``.text`` (ELF); ``None`` leaves the list without a pick (TUI leaves
    the list idle when no ``__TEXT,__text`` exists)."""
    for i, (seg, sect) in enumerate(sections):
        if seg == "__TEXT" and sect == "__text":
            return i
    for i, (_seg, sect) in enumerate(sections):
        if sect == "__text":
            return i
    for i, (_seg, sect) in enumerate(sections):
        if sect == ".text":
            return i
    return None


# ── combo-driven section selection ────────────────────────────────────────────


class DisasmController(ReadOnlyController):
    """One Disassembly tool window: section dropdown + virtualized grid."""

    keys_prefix = "disasm"

    def __init__(
        self,
        app: GuiApp,
        info: BinaryInfo,
        *,
        window: int | str,
        status_tag: int | str,
        grid: DisasmGrid,
        combo_tag: int | str,
        ns: str,
    ) -> None:
        self.app = app
        self.info = info  # the view's private snapshot
        self.window = window
        self.status_tag = status_tag
        self.grid = grid
        self.combo_tag = combo_tag
        self.ns = ns
        self.view_kind = "disasm"  # focus-guard key (find_tool)
        self.path = info.path
        self.sections: list[tuple[str, str]] = []
        self._labels: list[str] = []  # combo items, parallel to sections
        self.call_graph: CallGraph | None = None
        self._current_index: int | None = None
        self.busy = BusyTracker()  # exclusive in-flight disassembly
        self._dialog: PromptDialog | None = None  # Go to address
        self._ctx_tag = f"{ns}-disasm-ctx"
        self._install_keys()
        self._install_menu()
        self._build_context_menu()
        grid.on_context_click = self._show_context_menu

    # ── setup ─────────────────────────────────────────────────────────────

    def load_sections(self) -> None:
        """Fill the section dropdown and auto-disassemble the default pick."""
        self.sections = exec_sections(self.info)
        self._labels = [section_label(seg, sect) for seg, sect in self.sections]
        if not self.sections:
            dpg.configure_item(self.combo_tag, items=[], enabled=False)
            dpg.set_value(self.combo_tag, "(no executable sections)")
            dpg.set_value(self.status_tag, "Select a section to disassemble")
            return
        dpg.configure_item(self.combo_tag, items=self._labels, enabled=True)
        pick = auto_pick(self.sections)
        if pick is not None:
            # runtime set_value: selects the item without firing the callback
            dpg.set_value(self.combo_tag, self._labels[pick])
            self._start_disasm(pick)
        else:
            dpg.set_value(self.status_tag, "Select a section to disassemble")

    def _on_section_selected(self, _sender, app_data) -> None:
        """Combo callback: *app_data* is the selected ``seg,sect`` label."""
        try:
            index = self._labels.index(app_data)
        except ValueError:
            return  # placeholder text (no sections) — nothing to load
        self._start_disasm(index)

    # ── disassembly worker ────────────────────────────────────────────────

    def _start_disasm(self, index: int) -> None:
        self._current_index = index
        seg, sect = self.sections[index]
        if self.busy.worker is not None:
            self.busy.worker.cancel.cancel()  # superseded run
        dpg.set_value(self.status_tag, f"Disassembling {seg},{sect}…")

        def _job(handle: WorkerHandle) -> None:
            engine = pick_engine()
            try:
                if engine is None:
                    instrs: list[DisasmInstruction] = []
                else:
                    instrs = disassemble_section(self.info, seg, sect, engine=engine)
                graph = build_call_graph(self.info, instrs) if instrs else None
            except Exception as e:  # noqa: BLE001 - never leave the view
                # stuck on "Disassembling…": report the failure instead.
                msg = f"Disassembly of {seg},{sect} failed: {e}"
                self.app.bridge.post(0, EventKind.FAILED, msg, handle)
                return
            self.app.bridge.post(
                0, EventKind.DISASM_DONE, (seg, sect, instrs, graph), handle
            )

        # Tracker ownership makes dispatch route the completion here; a
        # newer pick replaces the tracked handle (stale drops).
        self.busy.start(
            "disassembling", start_worker(_job, name=f"gui-disasm-{self.ns}")
        )

    def on_event(self, event) -> None:
        """Handle the disassembly worker's completion (stale runs dropped)."""
        if event.kind not in (EventKind.DISASM_DONE, EventKind.FAILED):
            return
        if self.busy.is_stale(event.worker) or event.worker.cancel.cancelled:
            return  # superseded or Esc-cancelled run — never lands
        self.busy.clear()
        if event.kind is EventKind.FAILED:
            dpg.set_value(self.status_tag, str(event.payload))
            return
        seg, sect, instrs, graph = event.payload
        self.call_graph = graph
        self.grid.set_instrs(instrs)
        if not instrs:
            engine = pick_engine()
            reason = (
                "no disassembler available (install capstone or objdump)"
                if engine is None
                else f"{engine} unavailable or section unreadable"
            )
            dpg.set_value(self.status_tag, f"No output — {reason}")
            return
        dpg.set_value(
            self.status_tag,
            f"{seg},{sect}  {len(instrs):,} instructions  │  C call flow · F cfg"
            " · right-click for more",
        )

    # ── keys: Esc cancel, c call-flow, f CFG ──────────────────────────────

    def _install_keys(self) -> None:
        self.install_keys(
            [
                (KEY_ESCAPE, self._on_escape),
                (dpg.mvKey_C, self._open_call_flow),
                (dpg.mvKey_F, self._open_cfg),
                (dpg.mvKey_G, self._on_ctrl_g),
            ]
        )

    def _install_menu(self) -> None:
        add_tool_menu(
            self.window,
            [
                (
                    "Function",
                    [
                        ("Call flow", "C", self._open_call_flow),
                        ("Control-flow graph", "F", self._open_cfg),
                        None,
                        ("Show in hex editor", "", self._show_in_hex),
                        ("Copy address", "", self._copy_address),
                    ],
                ),
                ("Go", [("Go to address…", "Ctrl+G", self.action_goto)]),
            ],
        )

    def _build_context_menu(self) -> None:
        """Right-click popup over the grid (row selected first)."""
        with dpg.window(
            tag=self._ctx_tag,
            popup=True,
            show=False,
            no_title_bar=True,
            autosize=True,
            no_saved_settings=True,
        ):
            for label, action in (
                ("Call flow   C", self._open_call_flow),
                ("Control-flow graph   F", self._open_cfg),
                ("Show in hex editor", self._show_in_hex),
                ("Copy address", self._copy_address),
            ):
                dpg.add_menu_item(
                    label=label, callback=lambda *_a, act=action: self._ctx_run(act)
                )

    def _show_context_menu(self) -> None:
        if self._cursor_addr() is None:
            return
        dpg.configure_item(
            self._ctx_tag, pos=dpg.get_mouse_pos(local=False), show=True
        )

    def _ctx_run(self, action) -> None:
        dpg.configure_item(self._ctx_tag, show=False)
        action()

    def _on_ctrl_g(self, *_args) -> None:
        if ctrl_down():
            self.action_goto()

    # ── go to address / show in hex / copy ────────────────────────────────

    def action_goto(self, *_args) -> None:
        """Ctrl+G: jump the cursor to the instruction covering an address."""
        instrs = self.grid.instrs()
        if not instrs:
            dpg.set_value(self.status_tag, "Disassemble a section first")
            return
        lo, hi = instrs[0].addr, instrs[-1].addr

        def validate(text: str) -> int:
            t = text.strip().lower().removeprefix("0x")
            try:
                addr = int(t, 16)
            except ValueError:
                raise ValueError("enter a hex address, e.g. 0x100003f40") from None
            if not lo <= addr <= hi + len(instrs[-1].raw):
                raise ValueError(f"not in this section (0x{lo:x} – 0x{hi:x})")
            return bisect_right([i.addr for i in instrs], addr) - 1

        def accept(index: int) -> None:
            self._close_dialog()
            self.grid.move_to(index)
            dpg.set_value(self.status_tag, f"at 0x{instrs[index].addr:x}")

        self._close_dialog()
        self._dialog = PromptDialog(
            label="Go to address",
            help_text=f"Section range: 0x{lo:x} – 0x{hi:x}",
            hint="0x…",
            validate=validate,
            on_accept=accept,
            on_cancel=self._close_dialog,
            ok_label="Go",
        )
        self._dialog.open()

    def _close_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None

    def _show_in_hex(self, *_args) -> None:
        """Open/focus the hex editor at the cursor instruction's bytes."""
        addr = self._cursor_addr()
        if addr is None:
            dpg.set_value(self.status_tag, "Select an instruction row")
            return
        offset = file_offset_of(self.info, addr)
        if offset is None:
            dpg.set_value(self.status_tag, f"0x{addr:x} is not backed by file bytes")
            return
        self.app.show_in_editor(self.path, offset)

    def _copy_address(self, *_args) -> None:
        addr = self._cursor_addr()
        if addr is not None:
            dpg.set_clipboard_text(f"0x{addr:x}")
            dpg.set_value(self.status_tag, f"copied 0x{addr:x}")

    def _on_escape(self, *_args) -> None:
        if self.busy.worker is not None:
            self.busy.worker.cancel.cancel()
            self.busy.clear()
            dpg.set_value(self.status_tag, "Disassembly cancelled — pick a section")

    # ── c / f: call-flow + CFG windows (TUI DisasmTab parity) ─────────────

    def _cursor_addr(self) -> int | None:
        """The cursor row's instruction address, or None with no rows."""
        instrs = self.grid.instrs()
        cursor = self.grid.cursor
        if not instrs or cursor < 0 or cursor >= len(instrs):
            return None
        return instrs[cursor].addr

    def _cursor_function(self) -> tuple[CallGraph, str, int] | None:
        """``(graph, name, addr)`` of the function under the cursor, or
        ``None`` after explaining in the status strip why there is none."""
        graph = self.call_graph
        if graph is None:
            dpg.set_value(self.status_tag, "Disassemble a section first")
            return None
        addr = self._cursor_addr()
        if addr is None:
            dpg.set_value(self.status_tag, "Select an instruction row")
            return None
        result = graph.func_at(addr)
        if result is None:
            dpg.set_value(
                self.status_tag,
                f"No function symbol found at 0x{addr:x} — binary may be stripped",
            )
            return None
        func_name, func_addr = result
        return graph, func_name, func_addr

    def _open_call_flow(self, *_args) -> None:
        """``c``: open the call-flow tree for the cursor's function."""
        found = self._cursor_function()
        if found is not None:
            graph, func_name, _ = found
            open_call_flow(self.app, self.path, graph, func_name)

    def _open_cfg(self, *_args) -> None:
        """``f``: slice the cursor's function and open its CFG window."""
        found = self._cursor_function()
        if found is None:
            return
        graph, func_name, func_addr = found
        _, next_addr = graph.func_range(func_addr)
        func_instrs = slice_function_instrs(self.grid.instrs(), func_addr, next_addr)
        if not func_instrs:
            dpg.set_value(self.status_tag, "No instructions found for this function")
            return
        cfg = build_cfg(func_instrs, func_name, func_addr)
        open_cfg(self.app, self.path, cfg)

    def shutdown(self) -> None:
        super().shutdown()
        self._close_dialog()
        if dpg.does_item_exist(self._ctx_tag):
            dpg.delete_item(self._ctx_tag)  # a root window, not a child


# ── opener ────────────────────────────────────────────────────────────────────


def open_disassembly(app: GuiApp, path: str) -> None:
    """Open the Disassembly window for *path* (the shell's current binary).

    Silent return when *path* is not the currently parsed binary; an
    already-open disasm view of the same file is focused, not duplicated.
    """
    info = app.current_info
    if info is None or info.path != path:
        return
    existing = find_tool(app, view_kind="disasm", path=path)
    if existing is not None:
        focus_tool(existing)
        return
    ns, window, content, status_tag = build_tool_window(
        app, f"Disassembly — {os.path.basename(path)}", DISASM_WIN_W, DISASM_WIN_H
    )
    combo_tag = f"{ns}-section-combo"
    pane_tag = f"{ns}-gridpane"
    dpg.add_combo(
        tag=combo_tag,
        items=[],
        default_value="(no sections)",
        width=-1,
        parent=content,
        enabled=False,
        callback=None,  # bound in the controller below
    )
    # The grid pane fills the rest of the content area; the negative height
    # is parent-relative, so it keeps tracking window resizes on its own.
    dpg.add_child_window(
        tag=pane_tag,
        parent=content,
        border=False,
        height=-COMBO_RESERVE,
    )

    grid = DisasmGrid(name=ns)
    grid.build(pane_tag)
    controller = DisasmController(
        app,
        info,
        window=window,
        status_tag=status_tag,
        grid=grid,
        combo_tag=combo_tag,
        ns=ns,
    )
    dpg.configure_item(combo_tag, callback=controller._on_section_selected)
    controller.load_sections()
    app.register_tool(window, status_tag, controller, grids=(grid,), tick=grid.tick)
