"""Tab panel widgets for the binary inspector TUI."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Label, ListItem, ListView, Static, TabPane
from rich.text import Text

from disasm.libs.cfg import CallGraph, build_call_graph, build_cfg
from disasm.libs.disasm import disassemble_section, extract_strings
from disasm.libs.types import BinaryInfo, ChainedFixup, DisasmInstruction, N_TYPE, N_UNDF, Symbol
from disasm.tui.highlight import (
    _colorize_mnemonic, _colorize_operands, _fmt_addr, _fmt_size,
)
from disasm.tui.screens import CallFlowScreen, CFGScreen


class SegmentsTab(TabPane):
    def __init__(self) -> None:
        super().__init__("Segments", id="tab-segments")
        self._table: DataTable | None = None

    def compose(self) -> ComposeResult:
        yield DataTable(id="seg-table", cursor_type="row")

    def on_mount(self) -> None:
        t = self.query_one("#seg-table", DataTable)
        t.add_columns("Segment", "VM Addr", "VM Size", "File Off", "File Size", "Prot init/max", "Sections")
        self._table = t

    def load(self, info: BinaryInfo) -> None:
        t = self._table
        if t is None:
            return
        t.clear()
        for seg in info.segments:
            t.add_row(
                seg.name,
                f"0x{seg.vmaddr:016x}",
                _fmt_size(seg.vmsize),
                f"0x{seg.fileoff:08x}",
                _fmt_size(seg.filesize),
                seg.prot_str,
                str(len(seg.sections)),
            )


class SectionsTab(TabPane):
    def __init__(self) -> None:
        super().__init__("Sections", id="tab-sections")
        self._table: DataTable | None = None

    def compose(self) -> ComposeResult:
        yield DataTable(id="sect-table", cursor_type="row")

    def on_mount(self) -> None:
        t = self.query_one("#sect-table", DataTable)
        t.add_columns("Segment", "Section", "Addr", "Size", "File Off", "Align", "Type", "Relocs")
        self._table = t

    def load(self, info: BinaryInfo) -> None:
        t = self._table
        if t is None:
            return
        t.clear()
        for seg in info.segments:
            for s in seg.sections:
                t.add_row(
                    s.segment,
                    s.name,
                    f"0x{s.addr:016x}",
                    _fmt_size(s.size),
                    f"0x{s.offset:08x}",
                    f"2^{s.align}",
                    s.type_str,
                    str(s.nreloc),
                )


class LibrariesTab(TabPane):
    def __init__(self) -> None:
        super().__init__("Libraries", id="tab-libraries")
        self._table: DataTable | None = None

    def compose(self) -> ComposeResult:
        yield DataTable(id="lib-table", cursor_type="row")

    def on_mount(self) -> None:
        t = self.query_one("#lib-table", DataTable)
        t.add_columns("#", "Name", "Current Ver", "Compat Ver", "Load Type", "LC Offset")
        self._table = t

    def load(self, info: BinaryInfo) -> None:
        t = self._table
        if t is None:
            return
        t.clear()
        for i, lib in enumerate(info.libraries, 1):
            t.add_row(
                str(i),
                lib.name,
                lib.current_version,
                lib.compat_version,
                lib.load_type,
                f"0x{lib.offset:08x}",
            )


class SymbolsTab(TabPane):
    FILTER_ALL    = "all"
    FILTER_EXT    = "external"
    FILTER_UNDEF  = "undefined"
    FILTER_NOSYM  = "no-stabs"

    DEFAULT_CSS = """
    SymbolsTab #sym-filter-bar {
        height: 1;
        padding: 0 1;
        background: $panel;
    }
    SymbolsTab #sym-filter-bar Label {
        margin-right: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("Symbols", id="tab-symbols")
        self._all: list[Symbol] = []
        self._table: DataTable | None = None
        self._filter = self.FILTER_NOSYM

    def compose(self) -> ComposeResult:
        with Horizontal(id="sym-filter-bar"):
            yield Label("[bold]Filter:[/bold]")
            yield Label(f"[underline]a[/underline]ll", id="sf-all")
            yield Label(f"[underline]e[/underline]xternal", id="sf-ext")
            yield Label(f"[underline]u[/underline]ndefined", id="sf-undef")
            yield Label(f"[underline]n[/underline]o-stabs", id="sf-nosym")
        yield DataTable(id="sym-table", cursor_type="row")

    def on_mount(self) -> None:
        t = self.query_one("#sym-table", DataTable)
        t.add_columns("Address", "Type", "Sect", "Binding", "Name")
        self._table = t

    def load(self, info: BinaryInfo) -> None:
        self._all = info.symbols
        self._apply_filter()

    def _apply_filter(self) -> None:
        t = self._table
        if t is None:
            return
        t.clear()
        for sym in self._all:
            if self._filter == self.FILTER_EXT and not sym.external:
                continue
            if self._filter == self.FILTER_UNDEF and (sym.sym_type & N_TYPE) != N_UNDF:
                continue
            if self._filter == self.FILTER_NOSYM and sym.stab:
                continue
            t.add_row(
                _fmt_addr(sym.addr),
                sym.type_str,
                str(sym.sect),
                sym.binding,
                sym.name,
            )

    def on_key(self, event) -> None:
        if not self.has_focus_within:
            return
        mapping = {"a": self.FILTER_ALL, "e": self.FILTER_EXT,
                   "u": self.FILTER_UNDEF, "n": self.FILTER_NOSYM}
        if event.character in mapping:
            self._filter = mapping[event.character]
            self._apply_filter()
            event.stop()


class ExportsTab(TabPane):
    def __init__(self) -> None:
        super().__init__("Exports", id="tab-exports")
        self._table: DataTable | None = None

    def compose(self) -> ComposeResult:
        yield DataTable(id="exp-table", cursor_type="row")

    def on_mount(self) -> None:
        t = self.query_one("#exp-table", DataTable)
        t.add_columns("Address", "Flags", "Name")
        self._table = t

    def load(self, info: BinaryInfo) -> None:
        t = self._table
        if t is None:
            return
        t.clear()
        for exp in info.exports:
            t.add_row(
                _fmt_addr(exp.get("addr")),
                f"0x{exp.get('flags', 0):04x}",
                exp.get("name", ""),
            )


class ChainedFixupsTab(TabPane):
    FILTER_ALL    = "all"
    FILTER_BIND   = "binds"
    FILTER_REBASE = "rebases"

    def __init__(self) -> None:
        super().__init__("Fixups", id="tab-fixups")
        self._all: list[ChainedFixup] = []
        self._table: DataTable | None = None
        self._filter = self.FILTER_ALL

    def compose(self) -> ComposeResult:
        with Horizontal(id="fix-filter-bar"):
            yield Label("[bold]Filter:[/bold]")
            yield Label(f"[underline]a[/underline]ll")
            yield Label(f"[underline]b[/underline]inds")
            yield Label(f"[underline]r[/underline]ebases")
        yield DataTable(id="fix-table", cursor_type="row")

    def on_mount(self) -> None:
        t = self.query_one("#fix-table", DataTable)
        t.add_columns("Segment", "Address", "Kind", "Type", "Library", "Symbol / Target", "Addend")
        self._table = t

    def load(self, info: BinaryInfo) -> None:
        self._all = info.chained_fixups
        self._apply_filter()

    def _apply_filter(self) -> None:
        t = self._table
        if t is None:
            return
        t.clear()
        for fx in self._all:
            if self._filter == self.FILTER_BIND and fx.is_rebase:
                continue
            if self._filter == self.FILTER_REBASE and not fx.is_rebase:
                continue
            kind    = "rebase" if fx.is_rebase else "bind"
            lib     = fx.name if fx.is_rebase else (fx.name or f"lib#{fx.lib_ordinal}")
            target  = _fmt_addr(fx.target) if fx.is_rebase else (fx.name or "")
            t.add_row(
                fx.segment,
                _fmt_addr(fx.offset),
                fx.kind,
                kind,
                lib if not fx.is_rebase else "—",
                target,
                str(fx.addend) if fx.addend else "0",
            )

    def on_key(self, event) -> None:
        if not self.has_focus_within:
            return
        mapping = {"a": self.FILTER_ALL, "b": self.FILTER_BIND, "r": self.FILTER_REBASE}
        if event.character in mapping:
            self._filter = mapping[event.character]
            self._apply_filter()
            event.stop()


class StringsTab(TabPane):
    def __init__(self) -> None:
        super().__init__("Strings", id="tab-strings")
        self._table: DataTable | None = None

    def compose(self) -> ComposeResult:
        yield DataTable(id="str-table", cursor_type="row")
        yield Static("", id="str-status")

    def on_mount(self) -> None:
        t = self.query_one("#str-table", DataTable)
        t.add_columns("File Offset", "Address", "Section", "String")
        self._table = t

    def load(self, info: BinaryInfo) -> None:
        self._load_strings(info)

    @work(thread=True)
    def _load_strings(self, info: BinaryInfo) -> None:
        self.app.call_from_thread(self._set_status, "Extracting strings…")
        strings = extract_strings(info)
        self.app.call_from_thread(self._populate, strings)

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#str-status", Static).update(msg)
        except Exception:
            pass

    def _populate(self, strings: list) -> None:
        t = self._table
        if t is None:
            return
        t.clear()
        for s in strings:
            t.add_row(
                f"0x{s.file_offset:08x}",
                f"0x{s.addr:016x}" if s.addr else "—",
                s.section,
                s.value,
            )
        self._set_status(f"{len(strings):,} strings found")


class DisasmTab(TabPane):
    def __init__(self) -> None:
        super().__init__("Disasm", id="tab-disasm")
        self._info: BinaryInfo | None = None
        self._sections: list[tuple[str, str]] = []
        self._table: DataTable | None = None
        self._list: ListView | None = None
        self._instrs: list[DisasmInstruction] = []
        self._call_graph: CallGraph | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="disasm-body"):
            yield ListView(id="disasm-sect-list")
            yield DataTable(id="disasm-table", cursor_type="row")
        yield Static("Select a section to disassemble", id="disasm-status")

    def on_mount(self) -> None:
        t = self.query_one("#disasm-table", DataTable)
        t.add_columns("Address", "Bytes", "Mnemonic", "Operands")
        self._table = t
        self._list = self.query_one("#disasm-sect-list", ListView)

    def load(self, info: BinaryInfo) -> None:
        self._info = info
        self._sections = []
        lv = self._list
        if lv is None:
            return
        lv.clear()
        exec_segs = {seg.name for seg in info.segments if seg.initprot & 0x4}
        for seg in info.segments:
            if seg.name not in exec_segs:
                continue
            for s in seg.sections:
                if s.size == 0 or s.offset == 0:
                    continue
                self._sections.append((s.segment, s.name))
                lv.append(ListItem(Label(f" {s.segment},{s.name}")))
        for i, (seg, sect) in enumerate(self._sections):
            if seg == "__TEXT" and sect == "__text":
                lv.index = i
                self._disassemble(seg, sect)
                break

    @on(ListView.Selected, "#disasm-sect-list")
    def _sect_picked(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if 0 <= idx < len(self._sections):
            seg, sect = self._sections[idx]
            self._disassemble(seg, sect)

    @work(thread=True)
    def _disassemble(self, seg: str, sect: str) -> None:
        if self._info is None:
            return
        self.app.call_from_thread(self._set_status, f"Disassembling {seg},{sect}…")
        instrs = disassemble_section(self._info, seg, sect)
        graph = build_call_graph(self._info, instrs) if instrs else None
        self.app.call_from_thread(self._populate, seg, sect, instrs, graph)

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#disasm-status", Static).update(msg)
        except Exception:
            pass

    def _populate(self, seg: str, sect: str, instrs: list, graph) -> None:
        self._instrs = instrs
        self._call_graph = graph
        t = self._table
        if t is None:
            return
        t.clear()
        if not instrs:
            self._set_status("[red]No output — capstone not installed or section unreadable[/red]")
            return
        arch = self._info.arch if self._info else ""
        for insn in instrs:
            raw_hex = " ".join(f"{b:02x}" for b in insn.raw)
            t.add_row(
                Text(f"0x{insn.addr:016x}", style="cyan"),
                Text(raw_hex, style="dim"),
                _colorize_mnemonic(insn.mnemonic),
                _colorize_operands(insn.op_str, arch),
            )
        self._set_status(
            f"[dim]{seg},{sect}[/dim]  {len(instrs):,} instructions"
            "  [dim]c → call flow   f → cfg[/dim]"
        )

    def on_key(self, event) -> None:
        if not self.has_focus_within:
            return
        if event.key == "c":
            self._open_call_flow()
            event.stop()
        elif event.key == "f":
            self._open_cfg()
            event.stop()

    def _open_cfg(self) -> None:
        if self._call_graph is None or self._table is None:
            self.app.notify("Disassemble a section first", timeout=3)
            return
        row = self._table.cursor_row
        if row < 0 or row >= len(self._instrs):
            self.app.notify("Select an instruction row", timeout=2)
            return
        addr = self._instrs[row].addr
        func_name = self._call_graph.func_at(addr)
        if func_name is None:
            self.app.notify(
                f"No function symbol at 0x{addr:x} — binary may be stripped",
                timeout=4,
            )
            return
        func_addr = self._call_graph.name_to_addr.get(func_name, addr)

        sa = self._call_graph._sorted_addrs
        lo, hi, pos = 0, len(sa) - 1, -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if sa[mid] == func_addr:
                pos = mid
                break
            elif sa[mid] < func_addr:
                lo = mid + 1
            else:
                hi = mid - 1
        next_func_addr = sa[pos + 1] if pos >= 0 and pos + 1 < len(sa) else None

        func_instrs = [
            i for i in self._instrs
            if i.addr >= func_addr and (next_func_addr is None or i.addr < next_func_addr)
        ]
        if not func_instrs:
            self.app.notify("No instructions found for this function", timeout=3)
            return

        cfg = build_cfg(func_instrs, func_name, func_addr)
        self.app.push_screen(CFGScreen(cfg))

    def _open_call_flow(self) -> None:
        if self._call_graph is None or self._table is None:
            self.app.notify("Disassemble a section first", timeout=3)
            return
        row = self._table.cursor_row
        if row < 0 or row >= len(self._instrs):
            self.app.notify("Select an instruction row", timeout=2)
            return
        addr = self._instrs[row].addr
        func_name = self._call_graph.func_at(addr)
        if func_name is None:
            self.app.notify(
                f"No function symbol found at 0x{addr:x} — binary may be stripped",
                timeout=4,
            )
            return
        self.app.push_screen(CallFlowScreen(self._call_graph, func_name))
