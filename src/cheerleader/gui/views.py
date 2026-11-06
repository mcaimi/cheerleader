"""
Read-only data-table views for the Dear PyGui front-end.

The Disassembler menu's six table views — Segments / Sections / Imports /
Symbols / Exports / Fixups — port the TUI tabs of the same names onto the
GUI tool pattern. All of them render straight from a
:class:`~cheerleader.libs.types.BinaryInfo` snapshot (already parsed by the
shell's background worker in D1); no view spawns workers, so `on_event` is
a no-op and the tools are strictly read-only (`dirty_edit_count() == 0`).

Structure: a :class:`ViewSpec` per view kind (columns, row formatter,
optional filters) mirrors the TUI tab it ports — including the Symbols
stab/external/undefined/no-stabs filters (default **no-stabs**, TUI
parity) and the Fixups binds/rebases filters (default **all**) — and one
generic :class:`TableController` drives a plain ``dpg.add_table``
(row-count threshold: these tables hold at most thousands of rows; the
instruction view gets virtualization in D3). Filter state renders as a
button row above the table (the TUI's single-letter keys); the active
filter's button is prefixed with ``●``.

Every controller exposes ``view_kind`` + ``path`` so the shell's
:meth:`~cheerleader.gui.app.find_inspector_tool` focus guard can find an
already-open view of the same kind for the same file. A view holds its
*own* ``BinaryInfo`` reference: re-opening a different file replaces
``GuiApp.current_info`` but open view windows keep their snapshot data
(plan v3 decision 3).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, NamedTuple

from cheerleader.gui.app import (
    GuiApp,
    Tool,
    _tool_window_closed,
    find_inspector_tool,
    human_size,
)
from cheerleader.gui.const import (
    STATUS_RESERVE,
    TABLE_WIN_H,
    TABLE_WIN_W,
    TOOL_MIN_H,
    TOOL_MIN_W,
)
from cheerleader.libs.types import N_TYPE, N_UNDF, BinaryInfo

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]


def fmt_addr(v: int | None) -> str:
    """TUI ``_fmt_addr`` parity: em-dash for None, else zero-padded hex."""
    if v is None:
        return "—"
    return f"0x{v:016x}"


class FilterSpec(NamedTuple):
    """One filter choice: key (stable id), button label, item predicate.

    A ``None`` predicate means "no filtering" (the TUI's *all* choice).
    """

    key: str
    label: str
    predicate: Callable[[Any], bool] | None


@dataclass(frozen=True)
class ViewSpec:
    """Column/row/filter layout of one data-table view (TUI tab parity)."""

    kind: str  # view_kind exposed on the controller (focus-guard key)
    title: str  # window title prefix (menu naming)
    noun: str  # status-strip noun ("6,123 symbols")
    columns: tuple[str, ...]
    items: Callable[[BinaryInfo], list[Any]]
    # index = row number (0-based, shown 1-based for imports); returns the
    # rendered cells, one per column.
    row: Callable[[Any, int], tuple[str, ...]]
    filters: tuple[FilterSpec, ...] | None = None
    default_filter: str | None = None
    # Relative column weights under mvTable_SizingStretchProp; missing
    # entries default to 1.0.
    weights: tuple[float, ...] = ()

    def filter(self, key: str | None) -> FilterSpec | None:
        if self.filters is None or key is None:
            return None
        for f in self.filters:
            if f.key == key:
                return f
        return None  # pragma: no cover - keys are internal constants


# ── row formatters (TUI tab parity) ───────────────────────────────────────────


def _segments(info: BinaryInfo) -> list[Any]:
    return info.segments


def _row_segment(seg: Any, _i: int) -> tuple[str, ...]:
    return (
        seg.name,
        f"0x{seg.vmaddr:016x}",
        human_size(seg.vmsize),
        f"0x{seg.fileoff:08x}",
        human_size(seg.filesize),
        seg.prot_str,
        str(len(seg.sections)),
    )


def _sections(info: BinaryInfo) -> list[Any]:
    return [s for seg in info.segments for s in seg.sections]


def _row_section(s: Any, _i: int) -> tuple[str, ...]:
    return (
        s.segment,
        s.name,
        f"0x{s.addr:016x}",
        human_size(s.size),
        f"0x{s.offset:08x}",
        f"2^{s.align}",
        s.type_str,
        str(s.nreloc),
    )


def _libraries(info: BinaryInfo) -> list[Any]:
    return info.libraries


def _row_library(lib: Any, i: int) -> tuple[str, ...]:
    return (
        str(i + 1),
        lib.name,
        lib.current_version,
        lib.compat_version,
        lib.load_type,
        f"0x{lib.offset:08x}",
    )


def _symbols(info: BinaryInfo) -> list[Any]:
    return info.symbols


def _row_symbol(sym: Any, _i: int) -> tuple[str, ...]:
    return (
        fmt_addr(sym.addr),
        sym.type_str,
        str(sym.sect),
        sym.binding,
        sym.name,
    )


def _exports(info: BinaryInfo) -> list[Any]:
    return info.exports


def _row_export(exp: dict, _i: int) -> tuple[str, ...]:
    return (
        fmt_addr(exp.get("addr")),
        f"0x{exp.get('flags', 0):04x}",
        exp.get("name", ""),
    )


def _fixups(info: BinaryInfo) -> list[Any]:
    return info.chained_fixups


def _row_fixup(fx: Any, _i: int) -> tuple[str, ...]:
    kind = "rebase" if fx.is_rebase else "bind"
    return (
        fx.segment,
        fmt_addr(fx.offset),
        fx.kind,
        kind,
        # bind: imported name or resolved ordinal; rebase: no library.
        (fx.name or f"lib#{fx.lib_ordinal}") if not fx.is_rebase else "—",
        # bind: symbol name; rebase: the target address.
        fmt_addr(fx.target) if fx.is_rebase else (fx.name or ""),
        str(fx.addend) if fx.addend else "0",
    )


_SPECS: dict[str, ViewSpec] = {
    "segments": ViewSpec(
        kind="segments",
        title="Segments",
        noun="segments",
        columns=(
            "Segment",
            "VM Addr",
            "VM Size",
            "File Off",
            "File Size",
            "Prot init/max",
            "Sections",
        ),
        items=_segments,
        row=_row_segment,
        weights=(2.0,),
    ),
    "sections": ViewSpec(
        kind="sections",
        title="Sections",
        noun="sections",
        columns=(
            "Segment",
            "Section",
            "Addr",
            "Size",
            "File Off",
            "Align",
            "Type",
            "Relocs",
        ),
        items=_sections,
        row=_row_section,
        weights=(1.0, 2.0),
    ),
    "imports": ViewSpec(
        kind="imports",
        title="Imports",
        noun="imports",
        columns=("#", "Name", "Current Ver", "Compat Ver", "Load Type", "LC Offset"),
        items=_libraries,
        row=_row_library,
        weights=(0.4, 4.0),
    ),
    "symbols": ViewSpec(
        kind="symbols",
        title="Symbols",
        noun="symbols",
        columns=("Address", "Type", "Sect", "Binding", "Name"),
        items=_symbols,
        row=_row_symbol,
        filters=(
            FilterSpec("all", "All", None),
            FilterSpec("external", "External", lambda s: s.external),
            FilterSpec(
                "undefined", "Undefined", lambda s: (s.sym_type & N_TYPE) == N_UNDF
            ),
            FilterSpec("no-stabs", "No-stabs", lambda s: not s.stab),
        ),
        default_filter="no-stabs",  # TUI SymbolsTab parity
        weights=(1.2, 0.7, 0.5, 0.8, 4.0),
    ),
    "exports": ViewSpec(
        kind="exports",
        title="Exports",
        noun="exports",
        columns=("Address", "Flags", "Name"),
        items=_exports,
        row=_row_export,
        weights=(1.2, 0.7, 4.0),
    ),
    "fixups": ViewSpec(
        kind="fixups",
        title="Fixups",
        noun="fixups",
        columns=(
            "Segment",
            "Address",
            "Kind",
            "Type",
            "Library",
            "Symbol / Target",
            "Addend",
        ),
        items=_fixups,
        row=_row_fixup,
        filters=(
            FilterSpec("all", "All", None),
            FilterSpec("binds", "Binds", lambda fx: not fx.is_rebase),
            FilterSpec("rebases", "Rebases", lambda fx: fx.is_rebase),
        ),
        default_filter="all",  # TUI ChainedFixupsTab parity
        weights=(1.2, 1.2, 1.2, 0.7, 2.0, 2.5, 0.6),
    ),
}


# ── the generic controller ────────────────────────────────────────────────────


class TableController:
    """One read-only data-table view over a :class:`BinaryInfo` snapshot.

    Renders *spec* against *info* into a plain DPG table; optional filters
    re-populate the table from the snapshot (buttons above the table; no
    global key handlers, so ``shutdown`` has nothing to unregister).
    """

    def __init__(
        self,
        app: GuiApp,
        spec: ViewSpec,
        info: BinaryInfo,
        *,
        window: int | str,
        status_tag: int | str,
        table_tag: int | str,
        ns: str,
        filter_buttons: dict[str, int | str] | None = None,
    ) -> None:
        self.app = app
        self.spec = spec
        self.info = info  # the view's private snapshot (plan decision 3)
        self.window = window
        self.status_tag = status_tag
        self.table_tag = table_tag
        self.ns = ns
        self.filter_buttons = filter_buttons or {}
        # Focus-guard keys used by find_inspector_tool.
        self.view_kind = spec.kind
        self.path = info.path
        self._filter = spec.default_filter
        self._mark_active_button()

    # ── rendering ────────────────────────────────────────────────────────

    def _visible(self) -> list[Any]:
        """The snapshot items that survive the active filter."""
        f = self.spec.filter(self._filter)
        items = self.spec.items(self.info)
        if f is None or f.predicate is None:
            return items
        return [item for item in items if f.predicate(item)]

    def populate(self) -> None:
        """(Re-)fill the table body from the snapshot + active filter."""
        dpg.delete_item(self.table_tag, children_only=True, slot=1)
        for i, item in enumerate(self._visible()):
            cells = self.spec.row(item, i)
            with dpg.table_row(parent=self.table_tag):
                for cell in cells:
                    dpg.add_text(cell)

    def update_status(self) -> None:
        """Row count + filter state (plan v3 D2 status-strip contract)."""
        total = len(self.spec.items(self.info))
        shown = len(self._visible())
        f = self.spec.filter(self._filter)
        if f is None:
            text = f"{total:,} {self.spec.noun}"
        else:
            text = f"{shown:,} of {total:,} {self.spec.noun} — filter: {f.label}"
        dpg.set_value(self.status_tag, text)

    def refresh(self) -> None:
        self.populate()
        self.update_status()

    def _mark_active_button(self) -> None:
        """``● `` prefix on the active filter's button, plain labels else."""
        for key, tag in self.filter_buttons.items():
            f = self.spec.filter(key)
            if f is None:  # pragma: no cover - buttons are built from specs
                continue
            active = key == self._filter
            dpg.configure_item(tag, label=(f"● {f.label}" if active else f.label))

    def _on_filter(self, key: str, *_args) -> None:
        """Filter-button callback: switch filter, re-render."""
        self._filter = key
        self._mark_active_button()
        self.refresh()

    # ── the shell's Controller protocol ──────────────────────────────────

    def on_event(self, event: Any) -> None:
        """No workers in D2 views — nothing to receive."""

    def dirty_edit_count(self) -> int:
        """Read-only view — never dirty (quit needs no confirmation)."""
        return 0

    def window_close_requested(self) -> None:
        self.app.close_tool_for(self)

    def shutdown(self) -> None:
        """No registries/dialogs/workers — the window delete suffices."""


# ── opener ────────────────────────────────────────────────────────────────────


def open_inspector(app: GuiApp, kind: str, path: str) -> None:
    """Open the *kind* view for *path* in a floating tool window.

    Fails soft (silent return) when the kind is unknown or *path* is not the shell's currently parsed binary —
    the menu gating makes both unreachable in practice. An already-open
    view of the same kind for the same file is focused, not duplicated.
    """
    spec = _SPECS.get(kind)
    info = app.current_info
    if spec is None or info is None or info.path != path:
        return
    existing = find_inspector_tool(app, kind, path)
    if existing is not None:
        dpg.configure_item(existing.window, show=True)
        dpg.focus_item(existing.window)
        return
    ns = app.next_ns()
    window = f"gui-tool-{ns}"
    content = f"{window}-content"
    status_tag = f"{window}-status"
    table_tag = f"{ns}-tbl"
    with dpg.window(
        tag=window,
        label=f"{spec.title} — {os.path.basename(path)}",
        width=TABLE_WIN_W,
        height=TABLE_WIN_H,
        min_size=[TOOL_MIN_W, TOOL_MIN_H],
        no_collapse=True,
        on_close=lambda *_a: _tool_window_closed(app, window),
    ):
        dpg.add_child_window(tag=content, border=False, height=-STATUS_RESERVE)
        dpg.add_separator()
        dpg.add_text("", tag=status_tag)
    controller = TableController(
        app,
        spec,
        info,
        window=window,
        status_tag=status_tag,
        table_tag=table_tag,
        ns=ns,
    )
    filter_buttons: dict[str, int | str] = {}
    if spec.filters:
        with dpg.group(horizontal=True, parent=content):
            dpg.add_text("Filter:")
            for f in spec.filters:
                tag = f"{ns}-flt-{f.key}"
                filter_buttons[f.key] = tag
                dpg.add_button(
                    tag=tag,
                    label=f.label,
                    callback=partial(controller._on_filter, f.key),
                )
    controller.filter_buttons = filter_buttons
    controller._mark_active_button()
    with dpg.table(
        tag=table_tag,
        parent=content,
        header_row=True,
        row_background=True,
        borders_innerH=True,
        borders_outerH=True,
        scrollY=True,
        policy=dpg.mvTable_SizingStretchProp,
    ):
        for i, col in enumerate(spec.columns):
            weight = spec.weights[i] if i < len(spec.weights) else 1.0
            dpg.add_table_column(label=col, init_width_or_weight=weight)
    controller.refresh()
    app.tools.append(
        Tool(
            window=window,
            status_tag=status_tag,
            controller=controller,
            grids=(),
            tick=None,
        )
    )


def _opener_for(kind: str) -> Callable[[GuiApp, str], None]:
    """Registry-shaped opener (app, path) for one view kind."""

    def _open(app: GuiApp, path: str) -> None:
        open_inspector(app, kind, path)

    return _open


def register_inspector_openers() -> None:
    """Publish every inspector opener into the shell's registry."""
    from cheerleader.gui.app import _INSPECTOR_OPENERS

    for kind in _SPECS:
        _INSPECTOR_OPENERS.setdefault(kind, _opener_for(kind))
    
    from cheerleader.gui import disasm as _disasm

    _disasm.register_opener()
