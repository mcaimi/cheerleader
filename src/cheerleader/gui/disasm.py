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

The view is read-only (`dirty_edit_count() == 0`); the window holds its own
``BinaryInfo`` snapshot like every inspector view.
"""

import os

from cheerleader.gui.app import (
    STATUS_RESERVE,
    TOOL_MIN_H,
    TOOL_MIN_W,
    GuiApp,
    Tool,
    _tool_window_closed,
    find_inspector_tool,
)
from cheerleader.gui.const import (
    DISASM_WIN_H,
    DISASM_WIN_W,
    KEY_ESCAPE,
)
from cheerleader.gui.disasmgrid import DisasmGrid
from cheerleader.gui.workers import BusyTracker, EventKind, WorkerHandle, start_worker
from cheerleader.libs.cfg import CallGraph, build_call_graph
from cheerleader.libs.disasm import disassemble_section
from cheerleader.libs.types import BinaryInfo, DisasmInstruction

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

_ENGINE: str | None = None  # None = not picked yet; "" = no engine available

COMBO_RESERVE = 32  # px reserved above the grid pane for the section combo row


def section_label(seg: str, sect: str) -> str:
    """Combo-item text for one executable section."""
    return f"{seg},{sect}"


def pick_engine() -> str | None:
    """Auto-pick the disassembly engine once per process (plan v3 §5).

    capstone (in-process, no subprocess) is preferred; objdump is the
    fallback; ``None`` means neither is available and views fail soft into
    their status strip.
    """
    global _ENGINE
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


class DisasmController:
    """One Disassembly tool window: section dropdown + virtualized grid."""

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
        self.view_kind = "disasm"  # focus-guard key (find_inspector_tool)
        self.path = info.path
        self.sections: list[tuple[str, str]] = []
        self._labels: list[str] = []  # combo items, parallel to sections
        self.call_graph: CallGraph | None = None
        self._current_index: int | None = None
        self.busy = BusyTracker()  # exclusive in-flight disassembly
        self._install_keys()

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
            if engine is None:
                instrs: list[DisasmInstruction] = []
            else:
                instrs = disassemble_section(self.info, seg, sect, engine=engine)
            graph = build_call_graph(self.info, instrs) if instrs else None
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
        if event.kind is not EventKind.DISASM_DONE:
            return
        if self.busy.is_stale(event.worker) or event.worker.cancel.cancelled:
            return  # superseded or Esc-cancelled run — never lands
        self.busy.clear()
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
            f"{seg},{sect}  {len(instrs):,} instructions",
        )

    # ── keys: Esc cancel ─────────────────────

    def _install_keys(self) -> None:
        with dpg.handler_registry(tag=f"disasm-keys-{self.ns}"):
            dpg.add_key_press_handler(key=KEY_ESCAPE, callback=self._on_escape)

    def _on_escape(self, *_args) -> None:
        if self.busy.worker is not None:
            self.busy.worker.cancel.cancel()
            dpg.set_value(self.status_tag, "Disassembly cancelled — pick a section")

    # ── the shell's Controller protocol ───────────────────────────────────

    def dirty_edit_count(self) -> int:
        """Read-only view — never dirty."""
        return 0

    def window_close_requested(self) -> None:
        self.app.close_tool_for(self)

    def shutdown(self) -> None:
        """Teardown: keys. The disasm grid is
        shut down by the shell via ``Tool.grids``."""
        if dpg.does_item_exist(f"disasm-keys-{self.ns}"):
            dpg.delete_item(f"disasm-keys-{self.ns}")
        if self.busy.worker is not None:
            self.busy.worker.cancel.cancel()


# ── opener ────────────────────────────────────────────────────────────────────


def open_disassembly(app: GuiApp, path: str) -> None:
    """Open the Disassembly window for *path* (the shell's current binary).

    Silent return when *path* is not the currently parsed binary; an
    already-open disasm view of the same file is focused, not duplicated.
    """
    info = app.current_info
    if info is None or info.path != path:
        return
    existing = find_inspector_tool(app, "disasm", path)
    if existing is not None:
        dpg.configure_item(existing.window, show=True)
        dpg.focus_item(existing.window)
        return
    ns = app.next_ns()
    window = f"gui-tool-{ns}"
    content = f"{window}-content"
    status_tag = f"{window}-status"
    with dpg.window(
        tag=window,
        label=f"Disassembly — {os.path.basename(path)}",
        width=DISASM_WIN_W,
        height=DISASM_WIN_H,
        min_size=[TOOL_MIN_W, TOOL_MIN_H],
        no_collapse=True,
        on_close=lambda *_a: _tool_window_closed(app, window),
    ):
        dpg.add_child_window(tag=content, border=False, height=-STATUS_RESERVE)
        dpg.add_separator()
        dpg.add_text("", tag=status_tag)
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
    app.tools.append(
        Tool(
            window=window,
            status_tag=status_tag,
            controller=controller,
            grids=(grid,),
            tick=grid.tick,
        )
    )


def register_opener() -> None:
    """Publish the D3 opener into the shell's registry (idempotent)."""
    from cheerleader.gui.app import _INSPECTOR_OPENERS

    _INSPECTOR_OPENERS.setdefault(
        "disasm", lambda app, path: open_disassembly(app, path)
    )
