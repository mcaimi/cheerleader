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

Every controller exposes ``view_kind`` + ``path`` so the
:func:`~cheerleader.gui.tooling.find_tool` focus guard can find an
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
from typing import TYPE_CHECKING, Any, NamedTuple

from cheerleader.gui.const import TABLE_WIN_H, TABLE_WIN_W
from cheerleader.gui.tooling import (
    ReadOnlyController,
    arch_suffix,
    build_tool_window,
    find_tool,
    focus_tool,
    human_size,
)
from cheerleader.libs.types import N_TYPE, N_UNDF, BinaryInfo

if TYPE_CHECKING:
    from cheerleader.gui.app import GuiApp

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]


def file_offset_of(info: BinaryInfo, addr: int | None) -> int | None:
    """Absolute file offset of virtual address *addr*, via the segment
    that maps it from the file (``None`` when unmapped / zero-fill)."""
    if addr is None:
        return None
    for seg in info.segments:
        if seg.filesize and seg.vmaddr <= addr < seg.vmaddr + seg.filesize:
            return info.slice_offset + seg.fileoff + (addr - seg.vmaddr)
    return None


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
    # Live search box above the table: the text an item is matched against
    # (case-insensitive substring). None = no search box.
    search: Callable[[Any], str] | None = None
    # Absolute file offset of an item's bytes; makes rows clickable (jump
    # into the hex editor). None = plain, non-clickable rows.
    offset: Callable[[BinaryInfo, Any], int | None] | None = None
    # Per-column sort key (item → comparable); missing / None entries sort
    # by the rendered cell text (numerically when it is all digits).
    sort_keys: tuple[Callable[[Any], Any] | None, ...] = ()

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
        offset=lambda info, seg: info.slice_offset + seg.fileoff
        if seg.filesize
        else None,
        sort_keys=(
            None,
            lambda seg: seg.vmaddr,
            lambda seg: seg.vmsize,
            lambda seg: seg.fileoff,
            lambda seg: seg.filesize,
        ),
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
        offset=lambda info, s: info.slice_offset + s.offset if s.offset else None,
        sort_keys=(
            None,
            None,
            lambda s: s.addr,
            lambda s: s.size,
            lambda s: s.offset,
            lambda s: s.align,
        ),
    ),
    "imports": ViewSpec(
        kind="imports",
        title="Imports",
        noun="imports",
        columns=("#", "Name", "Current Ver", "Compat Ver", "Load Type", "LC Offset"),
        items=_libraries,
        row=_row_library,
        weights=(0.4, 4.0),
        search=lambda lib: lib.name,
        # the library's load command inside the header
        offset=lambda info, lib: info.slice_offset + lib.offset,
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
        search=lambda sym: sym.name,
        offset=lambda info, sym: file_offset_of(info, sym.addr),
        sort_keys=(lambda sym: sym.addr or 0, None, lambda sym: sym.sect),
    ),
    "exports": ViewSpec(
        kind="exports",
        title="Exports",
        noun="exports",
        columns=("Address", "Flags", "Name"),
        items=_exports,
        row=_row_export,
        weights=(1.2, 0.7, 4.0),
        search=lambda exp: exp.get("name", ""),
        offset=lambda info, exp: file_offset_of(info, exp.get("addr")),
        sort_keys=(lambda exp: exp.get("addr") or 0, lambda exp: exp.get("flags", 0)),
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
        search=lambda fx: fx.name or "",
        offset=lambda info, fx: file_offset_of(info, fx.offset),
        sort_keys=(None, lambda fx: fx.offset),
    ),
}


# ── the generic controller ────────────────────────────────────────────────────


def _text_key(text: str) -> tuple[int, int | str]:
    """Default sort key: numbers numerically, everything else as text."""
    return (0, int(text)) if text.isdigit() else (1, text.lower())


class TableController(ReadOnlyController):
    """One read-only data-table view over a :class:`BinaryInfo` snapshot.

    Renders *spec* against *info* into a DPG table — sortable by any column
    header, row-clipped (only visible rows are laid out) — with optional
    filter buttons, an optional live search box (re-filtered per frame by
    :meth:`tick`) and optionally clickable rows that show the item's bytes
    in the hex editor. Rows keep their snapshot position as the row index
    (the Imports ``#``), whatever the filter/search/sort.
    """

    def __init__(
        self,
        app: GuiApp,
        spec: ViewSpec,
        info: BinaryInfo,
        *,
        window: int | str,
        status_tag: int | str,
        ns: str,
    ) -> None:
        self.app = app
        self.spec = spec
        self.info = info  # the view's private snapshot (plan decision 3)
        self.window = window
        self.status_tag = status_tag
        self.ns = ns
        self.table_tag = f"{ns}-tbl"
        self.search_tag = f"{ns}-search"
        self.filter_buttons: dict[str, int | str] = {}
        self._columns: list[int | str] = []  # column tags, in column order
        # Focus-guard keys used by find_tool (the slice of a fat binary too).
        self.view_kind = spec.kind
        self.path = info.path
        self.slice_offset = info.slice_offset
        self._filter = spec.default_filter
        self._query = ""  # search text currently applied
        self._sort: tuple[int, bool] | None = None  # (column, descending)

    # ── widgets ──────────────────────────────────────────────────────────

    def build(self, content: int | str) -> None:
        """Filter buttons, search box and table inside *content*."""
        spec = self.spec
        if spec.filters:
            with dpg.group(horizontal=True, parent=content):
                dpg.add_text("Filter:")
                for f in spec.filters:
                    tag = f"{self.ns}-flt-{f.key}"
                    self.filter_buttons[f.key] = tag
                    dpg.add_button(
                        tag=tag, label=f.label, callback=partial(self._on_filter, f.key)
                    )
            self._mark_active_button()
        if spec.search is not None:
            dpg.add_input_text(
                tag=self.search_tag,
                parent=content,
                hint=f"Search {spec.noun}…",
                width=-1,
            )
        with dpg.table(
            tag=self.table_tag,
            parent=content,
            header_row=True,
            row_background=True,
            borders_innerH=True,
            borders_outerH=True,
            scrollY=True,
            clipper=True,  # lay out only the visible rows (large tables)
            sortable=True,
            callback=self._on_sort,
            policy=dpg.mvTable_SizingStretchProp,
        ):
            for i, col in enumerate(spec.columns):
                weight = spec.weights[i] if i < len(spec.weights) else 1.0
                self._columns.append(
                    dpg.add_table_column(label=col, init_width_or_weight=weight)
                )

    # ── data ─────────────────────────────────────────────────────────────

    def _items(self) -> list[Any]:
        """Every item of the view (subclasses may load them elsewhere)."""
        return self.spec.items(self.info)

    def _visible(self) -> list[tuple[int, Any]]:
        """``(snapshot index, item)`` surviving filter + search, sorted."""
        rows = list(enumerate(self._items()))
        f = self.spec.filter(self._filter)
        if f is not None and f.predicate is not None:
            rows = [(i, item) for i, item in rows if f.predicate(item)]
        q = self._query.strip().lower()
        if q and self.spec.search is not None:
            text = self.spec.search
            rows = [(i, item) for i, item in rows if q in text(item).lower()]
        if self._sort is not None:
            col, descending = self._sort
            rows.sort(key=self._sort_key(col), reverse=descending)
        return rows

    def _sort_key(self, col: int) -> Callable[[tuple[int, Any]], Any]:
        keys = self.spec.sort_keys
        custom = keys[col] if col < len(keys) else None
        if custom is not None:
            return lambda row: custom(row[1])
        return lambda row: _text_key(self.spec.row(row[1], row[0])[col])

    # ── rendering ────────────────────────────────────────────────────────

    def populate(self, visible: list[tuple[int, Any]] | None = None) -> None:
        """(Re-)fill the table body from the snapshot + filter/search/sort."""
        if visible is None:
            visible = self._visible()
        clickable = self.spec.offset is not None
        dpg.delete_item(self.table_tag, children_only=True, slot=1)
        for i, item in visible:
            with dpg.table_row(parent=self.table_tag):
                for cell in self.spec.row(item, i):
                    if clickable:
                        dpg.add_selectable(
                            label=cell, user_data=item, callback=self._on_row
                        )
                    else:
                        dpg.add_text(cell)

    def _status_text(self, shown: int, total: int) -> str:
        """Row count + filter/search state (plan v3 D2 status-strip contract)."""
        noun = self.spec.noun
        f = self.spec.filter(self._filter)
        q = self._query.strip()
        if f is None and not q:
            text = f"{total:,} {noun}"
        else:
            text = f"{shown:,} of {total:,} {noun}"
            if q:
                text += f" matching {q!r}"
            if f is not None:
                text += f" — filter: {f.label}"
        if self.spec.offset is not None:
            text += "  ·  click a row to view it in the hex editor"
        return text

    def update_status(self, shown: int | None = None) -> None:
        if shown is None:
            shown = len(self._visible())
        dpg.set_value(self.status_tag, self._status_text(shown, len(self._items())))

    def refresh(self) -> None:
        visible = self._visible()  # filter once for body + status
        self.populate(visible)
        self.update_status(len(visible))

    def tick(self) -> None:
        """Per-frame: re-filter when the search box changed (live typing)."""
        if self.spec.search is None or not dpg.does_item_exist(self.search_tag):
            return
        query = dpg.get_value(self.search_tag)
        if query != self._query:
            self._query = query
            self.refresh()

    # ── callbacks ────────────────────────────────────────────────────────

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

    def _on_sort(self, _sender, app_data) -> None:
        """Header click: *app_data* is ``[[column, direction]]`` (direction
        1 ascending, -1 descending) or ``None`` when sorting is cleared."""
        if not dpg.does_item_exist(self.table_tag):
            return  # queued callback delivered after the window closed
        self._sort = None
        if app_data:
            column, direction = app_data[0]
            if column in self._columns:
                self._sort = (self._columns.index(column), direction < 0)
        self.refresh()

    def _on_row(self, sender, _app_data, user_data) -> None:
        """Row click: show the item's bytes in the hex editor (opened on
        demand — a click never leaves the user without the bytes)."""
        if sender is not None and dpg.does_item_exist(sender):
            dpg.set_value(sender, False)  # selectables are buttons here
        offset = self.spec.offset(self.info, user_data) if self.spec.offset else None
        if offset is None:
            dpg.set_value(self.status_tag, "This row has no bytes in the file")
            return
        if self.app.show_in_editor(self.path, offset):
            dpg.set_value(
                self.status_tag, f"Jumped to 0x{offset:08x} in the hex editor"
            )


# ── opener ────────────────────────────────────────────────────────────────────


def open_table_view(app: GuiApp, controller_factory, title: str) -> TableController:
    """Shared tool-window construction of the table views.

    *controller_factory* is ``(window, status_tag, ns) -> TableController``;
    the controller builds its widgets into the window's content area.
    """
    ns, window, content, status_tag = build_tool_window(
        app, title, TABLE_WIN_W, TABLE_WIN_H
    )
    controller = controller_factory(window, status_tag, ns)
    controller.build(content)
    app.register_tool(window, status_tag, controller, tick=controller.tick)
    return controller


def open_inspector(app: GuiApp, kind: str, path: str) -> None:
    """Open the *kind* view for *path* in a floating tool window.

    Fails soft (silent return) when the kind is unknown or *path* is not the
    shell's currently parsed binary — the menu gating makes both unreachable
    in practice. An already-open view of the same kind for the same file is
    focused, not duplicated.
    """
    spec = _SPECS.get(kind)
    info = app.current_info
    if spec is None or info is None or info.path != path:
        return
    existing = find_tool(
        app, view_kind=kind, path=path, slice_offset=info.slice_offset
    )
    if existing is not None:
        focus_tool(existing)
        return
    controller = open_table_view(
        app,
        lambda window, status_tag, ns: TableController(
            app, spec, info, window=window, status_tag=status_tag, ns=ns
        ),
        f"{spec.title} — {os.path.basename(path)}{arch_suffix(info)}",
    )
    controller.refresh()


def openers() -> dict[str, Callable[[GuiApp, str], None]]:
    """Shell-registry-shaped openers ``(app, path)``, one per table kind."""
    return {
        kind: (lambda app, path, kind=kind: open_inspector(app, kind, path))
        for kind in _SPECS
    }
