"""Textual TUI for Mach-O binary inspection."""

from __future__ import annotations

import os
import re
import sys
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Rule,
    Static,
    TabbedContent,
    TabPane,
    Tree,
)
from textual import work

from disasm import macho


# ──────────────────────────────────────────────────────────────────────────────
# Disassembly syntax highlighting
# ──────────────────────────────────────────────────────────────────────────────

_MNEM_RET = frozenset({"ret", "retq", "retn", "eret", "iret", "iretq"})
_MNEM_NOP = frozenset({"nop", "nopw", "nopl", "fnop"})
_MNEM_STACK = frozenset({"push", "pop", "pushf", "popf", "pushfq", "popfq"})
_MNEM_CMP = frozenset({
    "cmp", "test", "tst", "cmn", "fcmp", "ftst",
    "ucomisd", "ucomiss", "comisd", "comiss",
})
_MNEM_BRANCH = frozenset({
    "call", "bl", "blr", "blx", "br", "b",
    "jmp", "je", "jne", "jz", "jnz", "jl", "jg", "jle", "jge",
    "ja", "jb", "jae", "jbe", "jc", "jnc", "js", "jns", "jo", "jno",
    "jp", "jnp", "jcxz", "jecxz", "jrcxz",
    "cbz", "cbnz", "tbz", "tbnz",
})
_MNEM_LOGIC = frozenset({
    "and", "or", "xor", "not", "neg", "inc", "dec", "adc", "sbc",
    "madd", "msub", "sdiv", "udiv", "lsl", "lsr", "asr", "ror",
    "shl", "shr", "sar", "rol", "adcs", "subs", "adds", "ands",
    "orr", "eor", "bic", "mvn",
})

_X86_REG_RE = re.compile(
    r'\b('
    r'r(?:ax|bx|cx|dx|si|di|sp|bp|ip|flags)|'
    r'r(?:1[0-5]|[89])[bwd]?|'
    r'e(?:ax|bx|cx|dx|si|di|sp|bp|ip|flags)|'
    r'[abcd][xhl]|(?:si|di|sp|bp)l?|'
    r'(?:xmm|ymm|zmm)(?:[12]?\d|3[01])|'
    r'mm[0-7]|[cdefgs]s|cr[0-8]|dr[0-7]'
    r')\b',
    re.IGNORECASE,
)
_ARM_REG_RE = re.compile(
    r'\b('
    r'[xw](?:[12]?\d|30)|sp|lr|xzr|wzr|fp|pc|'
    r'[vqdsb](?:[12]?\d|3[01])(?:\.\d[bBhHsS])?'
    r')\b',
)
_HEX_RE = re.compile(r'#?-?0x[0-9a-fA-F]+')
_DEC_IMM_RE = re.compile(r'#-?\d+(?!\w)')


def _colorize_mnemonic(mnemonic: str) -> Text:
    m = mnemonic.lower()
    if m in _MNEM_RET:
        return Text(mnemonic, style="bold red")
    if m in _MNEM_BRANCH or m.startswith("b.") or (m.startswith("j") and len(m) > 1):
        return Text(mnemonic, style="bold yellow")
    if m in _MNEM_NOP:
        return Text(mnemonic, style="dim")
    if m in _MNEM_STACK:
        return Text(mnemonic, style="cyan")
    if m in _MNEM_CMP:
        return Text(mnemonic, style="magenta")
    if m.startswith(("mov", "ldr", "str", "ldp", "stp", "lea", "ld", "st")):
        return Text(mnemonic, style="green")
    if m in _MNEM_LOGIC or m.startswith(("add", "sub", "mul", "imul", "div", "idiv")):
        return Text(mnemonic, style="blue")
    return Text(mnemonic)


def _colorize_operands(op_str: str, arch: str) -> Text:
    if not op_str:
        return Text("")
    reg_re = _ARM_REG_RE if "arm" in arch.lower() else _X86_REG_RE

    spans: list[tuple[int, int, str]] = []
    for m in _HEX_RE.finditer(op_str):
        spans.append((m.start(), m.end(), "yellow"))
    for m in _DEC_IMM_RE.finditer(op_str):
        spans.append((m.start(), m.end(), "yellow"))
    for m in reg_re.finditer(op_str):
        spans.append((m.start(), m.end(), "bright_green"))

    spans.sort(key=lambda x: x[0])
    deduped: list[tuple[int, int, str]] = []
    last = 0
    for s, e, style in spans:
        if s >= last:
            deduped.append((s, e, style))
            last = e

    result = Text()
    pos = 0
    for s, e, style in deduped:
        if pos < s:
            result.append(op_str[pos:s])
        result.append(op_str[s:e], style=style)
        pos = e
    if pos < len(op_str):
        result.append(op_str[pos:])
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Helper formatters
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_addr(v: int | None) -> str:
    if v is None:
        return "—"
    return f"0x{v:016x}"


def _fmt_size(v: int) -> str:
    if v == 0:
        return "0"
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{v} {unit}"
        v //= 1024
    return f"{v} TB"


# ──────────────────────────────────────────────────────────────────────────────
# Slice picker (fat binary)
# ──────────────────────────────────────────────────────────────────────────────

class SlicePicker(Screen):
    """Modal to choose an architecture slice from a fat binary."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, slices: list[tuple[int, str]]) -> None:
        super().__init__()
        self._slices = slices

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("  [bold]Select architecture slice[/bold]", id="sp-title"),
            Rule(),
            ListView(*[ListItem(Label(f"  [{i}]  {arch}")) for i, arch in self._slices],
                     id="sp-list"),
            Label("  [dim]↑↓ navigate · Enter select · Esc cancel[/dim]", id="sp-hint"),
        )

    @on(ListView.Selected, "#sp-list")
    def _picked(self, event: ListView.Selected) -> None:
        self.dismiss(self._slices[event.list_view.index][0])


# ──────────────────────────────────────────────────────────────────────────────
# Info bar
# ──────────────────────────────────────────────────────────────────────────────

class InfoBar(Static):
    """Top banner showing high-level binary metadata."""

    DEFAULT_CSS = """
    InfoBar {
        height: auto;
        padding: 0 1;
        background: $surface;
        color: $text;
    }
    """

    def update_info(self, info: macho.MachOInfo) -> None:
        lines = [
            f"[bold]{os.path.basename(info.path)}[/bold]  "
            f"[cyan]{info.arch}[/cyan]  "
            f"[green]{info.bits}-bit[/green]  "
            f"[yellow]{info.file_type}[/yellow]",
        ]
        meta = []
        if info.uuid:
            meta.append(f"UUID: [dim]{info.uuid}[/dim]")
        if info.min_os:
            meta.append(f"Min OS: [dim]{info.min_os}[/dim]")
        if info.sdk:
            meta.append(f"SDK: [dim]{info.sdk}[/dim]")
        if info.source_version:
            meta.append(f"SrcVer: [dim]{info.source_version}[/dim]")
        if meta:
            lines.append("  ".join(meta))
        if info.dylinker:
            lines.append(f"Linker: [dim]{info.dylinker}[/dim]")
        if info.rpaths:
            lines.append("RPATHs: " + "  ".join(f"[dim]{r}[/dim]" for r in info.rpaths))
        self.update("\n".join(lines))


# ──────────────────────────────────────────────────────────────────────────────
# Tab panels
# ──────────────────────────────────────────────────────────────────────────────

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

    def load(self, info: macho.MachOInfo) -> None:
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

    def load(self, info: macho.MachOInfo) -> None:
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

    def load(self, info: macho.MachOInfo) -> None:
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
        self._all: list[macho.Symbol] = []
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

    def load(self, info: macho.MachOInfo) -> None:
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
            if self._filter == self.FILTER_UNDEF and (sym.sym_type & macho.N_TYPE) != macho.N_UNDF:
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

    def load(self, info: macho.MachOInfo) -> None:
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
        self._all: list[macho.ChainedFixup] = []
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

    def load(self, info: macho.MachOInfo) -> None:
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


class CallFlowScreen(Screen):
    """Modal showing the call graph for a single function — callers and callees."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close"),
        Binding("b", "back", "Back"),
    ]

    def __init__(self, graph: macho.CallGraph, func_name: str) -> None:
        super().__init__()
        self._graph = graph
        self._func_name = func_name
        self._history: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="cf-box"):
            yield Label("", id="cf-title")
            yield Rule()
            yield Tree("Call Graph", id="cf-tree")
            yield Rule()
            yield Label(
                "[dim]↑↓ navigate  Enter drill in  b back  Esc close[/dim]",
                id="cf-hint",
            )

    def on_mount(self) -> None:
        self._populate(self._func_name)

    def _populate(self, func_name: str) -> None:
        graph = self._graph
        addr = graph.name_to_addr.get(func_name, 0)
        addr_s = f"0x{addr:x}" if addr else "external"
        self.query_one("#cf-title", Label).update(
            f"[bold]{func_name}[/bold]  [dim]{addr_s}[/dim]"
        )

        tree: Tree = self.query_one("#cf-tree", Tree)
        tree.root.set_label("Call Graph")
        tree.root.remove_children()

        callees = graph.callees.get(func_name, [])
        callers = graph.callers.get(func_name, [])

        callee_node = tree.root.add(
            f"[green]▶ Calls ({len(callees)})[/green]", expand=True
        )
        for name, c_addr in sorted(callees, key=lambda x: x[0]):
            lbl = name if not c_addr else f"{name}  [dim]0x{c_addr:x}[/dim]"
            callee_node.add_leaf(lbl, data=("nav", name))

        caller_node = tree.root.add(
            f"[yellow]▶ Called by ({len(callers)})[/yellow]", expand=True
        )
        for name, c_addr in sorted(callers, key=lambda x: x[0]):
            lbl = f"{name}  [dim]0x{c_addr:x}[/dim]"
            caller_node.add_leaf(lbl, data=("nav", name))

        tree.root.expand()

    @on(Tree.NodeSelected, "#cf-tree")
    def _node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if not isinstance(data, tuple):
            return
        _, func_name = data
        if func_name == self._func_name:
            return
        self._history.append(self._func_name)
        self._func_name = func_name
        self._populate(func_name)

    def action_back(self) -> None:
        if self._history:
            self._func_name = self._history.pop()
            self._populate(self._func_name)
        else:
            self.dismiss(None)


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

    def load(self, info: macho.MachOInfo) -> None:
        self._load_strings(info)

    @work(thread=True)
    def _load_strings(self, info: macho.MachOInfo) -> None:
        self.app.call_from_thread(self._set_status, "Extracting strings…")
        strings = macho.extract_strings(info)
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
        self._info: macho.MachOInfo | None = None
        self._sections: list[tuple[str, str]] = []
        self._table: DataTable | None = None
        self._list: ListView | None = None
        self._instrs: list[macho.DisasmInstruction] = []
        self._call_graph: macho.CallGraph | None = None

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

    def load(self, info: macho.MachOInfo) -> None:
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
        instrs = macho.disassemble_section(self._info, seg, sect)
        graph = macho.build_call_graph(self._info, instrs) if instrs else None
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
            "  [dim]c → call flow[/dim]"
        )

    def on_key(self, event) -> None:
        if not self.has_focus_within:
            return
        if event.key == "c":
            self._open_call_flow()
            event.stop()

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


# ──────────────────────────────────────────────────────────────────────────────
# Main app
# ──────────────────────────────────────────────────────────────────────────────

CSS = """
Screen {
    background: $background;
}
#info-bar {
    height: auto;
    max-height: 6;
    background: $panel;
    padding: 0 1;
    border-bottom: solid $primary;
}
#main-tabs {
    height: 1fr;
}
TabbedContent {
    height: 1fr;
}
ContentSwitcher {
    height: 1fr;
}
TabPane {
    height: 1fr;
    padding: 0;
}
DataTable {
    height: 1fr;
}
#sym-filter-bar, #fix-filter-bar {
    height: 1;
    background: $panel;
    padding: 0 1;
    dock: top;
}
#sym-filter-bar Label, #fix-filter-bar Label {
    margin-right: 2;
}
CallFlowScreen {
    align: center middle;
    background: $background 60%;
}
#cf-box {
    width: 80;
    height: 30;
    border: round $primary;
    background: $surface;
    padding: 0 1;
}
#cf-title {
    padding: 1;
    text-align: center;
}
#cf-hint {
    padding: 0 1 1 1;
    text-align: center;
}
#cf-tree {
    height: 1fr;
}
#disasm-body {
    height: 1fr;
}
#disasm-sect-list {
    width: 28;
    border-right: solid $primary;
    background: $panel;
}
#disasm-table {
    width: 1fr;
    height: 1fr;
}
#disasm-status, #str-status {
    height: 1;
    padding: 0 1;
    background: $panel;
    dock: bottom;
}
#sp-title {
    padding: 1;
    text-align: center;
}
#sp-hint {
    padding: 1;
    text-align: center;
}
SlicePicker Vertical {
    width: 50;
    height: auto;
    border: round $primary;
    background: $surface;
    margin: 4 20;
    padding: 1 2;
}
"""


class DisasmApp(App):
    """Mach-O inspector TUI."""

    CSS = CSS
    TITLE = "disasm — Mach-O inspector"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reload", "Reload"),
        Binding("s", "slice", "Switch slice"),
        Binding("1", "tab('tab-segments')",  "Segments"),
        Binding("2", "tab('tab-strings')",   "Strings"),
        Binding("3", "tab('tab-sections')",  "Sections"),
        Binding("4", "tab('tab-libraries')", "Libraries"),
        Binding("5", "tab('tab-symbols')",   "Symbols"),
        Binding("6", "tab('tab-exports')",   "Exports"),
        Binding("7", "tab('tab-fixups')",    "Fixups"),
        Binding("8", "tab('tab-disasm')",    "Disasm"),
    ]

    _path: reactive[str] = reactive("", recompose=False)
    _slice: reactive[int] = reactive(0, recompose=False)

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path
        self._slice = 0
        self._info: macho.MachOInfo | None = None
        self._slices: list[tuple[int, str]] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield InfoBar(id="info-bar")
        with TabbedContent(id="main-tabs"):
            yield SegmentsTab()
            yield StringsTab()
            yield SectionsTab()
            yield LibrariesTab()
            yield SymbolsTab()
            yield ExportsTab()
            yield ChainedFixupsTab()
            yield DisasmTab()
        yield Footer()

    def on_mount(self) -> None:
        self._slices = macho.list_fat_slices(self._path)
        self._load()

    def _load(self) -> None:
        info = macho.parse(self._path, self._slice)
        self._info = info

        self.query_one(InfoBar).update_info(info)
        self.query_one(SegmentsTab).load(info)
        self.query_one(StringsTab).load(info)
        self.query_one(SectionsTab).load(info)
        self.query_one(LibrariesTab).load(info)
        self.query_one(SymbolsTab).load(info)
        self.query_one(ExportsTab).load(info)
        self.query_one(ChainedFixupsTab).load(info)
        self.query_one(DisasmTab).load(info)

        arch = info.arch
        self.sub_title = f"{os.path.basename(self._path)}  [{arch}]"

        if info.error:
            self.notify(info.error, severity="error", timeout=8)

    def action_reload(self) -> None:
        self._load()
        self.notify("Reloaded", timeout=2)

    def action_tab(self, tab_id: str) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = tab_id

    def action_slice(self) -> None:
        if len(self._slices) <= 1:
            self.notify("Single-arch binary — no other slices", timeout=3)
            return

        def _on_pick(result: int | None) -> None:
            if result is not None:
                self._slice = result
                self._load()

        self.push_screen(SlicePicker(self._slices), _on_pick)

    def action_quit(self) -> None:
        self.exit()
