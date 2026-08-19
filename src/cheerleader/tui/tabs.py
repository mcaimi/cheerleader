"""Tab panel widgets for the binary inspector TUI."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Input, Label, ListItem, ListView, Static, TabPane
from rich.text import Text

from cheerleader.libs.cfg import CallGraph, build_call_graph, build_cfg
from cheerleader.libs.disasm import disassemble_section, extract_strings
from cheerleader.libs.types import (
    BinaryInfo,
    ChainedFixup,
    DisasmInstruction,
    N_TYPE,
    N_UNDF,
    Symbol,
)
from cheerleader.tui.highlight import (
    _colorize_mnemonic,
    _colorize_operands,
    _fmt_addr,
    _fmt_size,
)
from cheerleader.tui.screens import AIResponseScreen, CallFlowScreen, CFGScreen
from cheerleader.tui.widgets import HexPane, build_hex_dump


class SegmentsTab(TabPane):
    def __init__(self) -> None:
        super().__init__("Segments", id="tab-segments")
        self._table: DataTable | None = None

    def compose(self) -> ComposeResult:
        yield DataTable(id="seg-table", cursor_type="row")

    def on_mount(self) -> None:
        t = self.query_one("#seg-table", DataTable)
        t.add_columns(
            "Segment",
            "VM Addr",
            "VM Size",
            "File Off",
            "File Size",
            "Prot init/max",
            "Sections",
        )
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
        t.add_columns(
            "Segment", "Section", "Addr", "Size", "File Off", "Align", "Type", "Relocs"
        )
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
        t.add_columns(
            "#", "Name", "Current Ver", "Compat Ver", "Load Type", "LC Offset"
        )
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
    FILTER_ALL = "all"
    FILTER_EXT = "external"
    FILTER_UNDEF = "undefined"
    FILTER_NOSYM = "no-stabs"

    def __init__(self) -> None:
        super().__init__("Symbols", id="tab-symbols")
        self._all: list[Symbol] = []
        self._table: DataTable | None = None
        self._filter = self.FILTER_NOSYM
        self._search_query: str = ""

    def compose(self) -> ComposeResult:
        with Horizontal(id="sym-filter-bar"):
            yield Label("[bold]Filter:[/bold]")
            yield Label("[underline]a[/underline]ll", id="sf-all")
            yield Label("[underline]e[/underline]xternal", id="sf-ext")
            yield Label("[underline]u[/underline]ndefined", id="sf-undef")
            yield Label("[underline]n[/underline]o-stabs", id="sf-nosym")
        yield Input(placeholder="Search symbols… (Esc to close)", id="sym-search")
        yield DataTable(id="sym-table", cursor_type="row")
        yield Static("", id="sym-status")

    def on_mount(self) -> None:
        t = self.query_one("#sym-table", DataTable)
        t.add_columns("Address", "Type", "Sect", "Binding", "Name")
        self._table = t
        self.query_one("#sym-search", Input).display = False

    def load(self, info: BinaryInfo) -> None:
        self._all = info.symbols
        self._search_query = ""
        search = self.query_one("#sym-search", Input)
        search.value = ""
        search.display = False
        self._apply_filter()

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#sym-status", Static).update(msg)
        except Exception:
            pass

    def _apply_filter(self) -> None:
        t = self._table
        if t is None:
            return
        t.clear()
        query = self._search_query.lower()
        for sym in self._all:
            if self._filter == self.FILTER_EXT and not sym.external:
                continue
            if self._filter == self.FILTER_UNDEF and (sym.sym_type & N_TYPE) != N_UNDF:
                continue
            if self._filter == self.FILTER_NOSYM and sym.stab:
                continue
            if query and query not in sym.name.lower():
                continue
            t.add_row(
                _fmt_addr(sym.addr),
                sym.type_str,
                str(sym.sect),
                sym.binding,
                sym.name,
            )

        self._set_status(
            f"{len(self._all):,} symbols found [dim] s,/ -> search symbol [/dim]"
        )

    @on(Input.Changed, "#sym-search")
    def _on_search_changed(self, event: Input.Changed) -> None:
        self._search_query = event.value
        self._apply_filter()

    def on_key(self, event) -> None:
        if not self.has_focus_within:
            return

        search_input = self.query_one("#sym-search", Input)

        if event.key == "escape" and search_input.display:
            search_input.value = ""
            search_input.display = False
            self._search_query = ""
            self._apply_filter()
            self.query_one("#sym-table", DataTable).focus()
            event.stop()
            return

        if search_input.has_focus:
            return

        if event.character in ("S", "/"):
            search_input.display = True
            search_input.focus()
            event.stop()
            return

        mapping = {
            "a": self.FILTER_ALL,
            "e": self.FILTER_EXT,
            "u": self.FILTER_UNDEF,
            "n": self.FILTER_NOSYM,
        }
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
    FILTER_ALL = "all"
    FILTER_BIND = "binds"
    FILTER_REBASE = "rebases"

    def __init__(self) -> None:
        super().__init__("Fixups", id="tab-fixups")
        self._all: list[ChainedFixup] = []
        self._table: DataTable | None = None
        self._filter = self.FILTER_ALL

    def compose(self) -> ComposeResult:
        with Horizontal(id="fix-filter-bar"):
            yield Label("[bold]Filter:[/bold]")
            yield Label("[underline]a[/underline]ll")
            yield Label("[underline]b[/underline]inds")
            yield Label("[underline]r[/underline]ebases")
        yield DataTable(id="fix-table", cursor_type="row")

    def on_mount(self) -> None:
        t = self.query_one("#fix-table", DataTable)
        t.add_columns(
            "Segment", "Address", "Kind", "Type", "Library", "Symbol / Target", "Addend"
        )
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
            kind = "rebase" if fx.is_rebase else "bind"
            lib = fx.name if fx.is_rebase else (fx.name or f"lib#{fx.lib_ordinal}")
            target = _fmt_addr(fx.target) if fx.is_rebase else (fx.name or "")
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
        self._all: list = []
        self._search_query: str = ""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search strings… (Esc to close)", id="str-search")
        yield DataTable(id="str-table", cursor_type="row")
        yield Static("", id="str-status")

    def on_mount(self) -> None:
        t = self.query_one("#str-table", DataTable)
        t.add_columns("File Offset", "Address", "Section", "String")
        self._table = t
        self.query_one("#str-search", Input).display = False

    def load(self, info: BinaryInfo) -> None:
        self._search_query = ""
        search = self.query_one("#str-search", Input)
        search.value = ""
        search.display = False
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
        self._all = strings
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        t = self._table
        if t is None:
            return
        t.clear()
        query = self._search_query.lower()
        count = 0
        for s in self._all:
            if query and query not in s.value.lower():
                continue
            t.add_row(
                f"0x{s.file_offset:08x}",
                f"0x{s.addr:016x}" if s.addr else "—",
                s.section,
                s.value,
            )
            count += 1
        if query:
            self._set_status(
                f"{count:,} / {len(self._all):,} strings matching '{self._search_query}'"
                "[dim] escape -> close search [/dim]"
            )
        else:
            self._set_status(
                f"{len(self._all):,} strings found [dim] s,/ -> search string [/dim]"
            )

    @on(Input.Changed, "#str-search")
    def _on_search_changed(self, event: Input.Changed) -> None:
        self._search_query = event.value
        self._rebuild_table()

    def on_key(self, event) -> None:
        if not self.has_focus_within:
            return

        search_input = self.query_one("#str-search", Input)

        if event.key == "escape" and search_input.display:
            search_input.value = ""
            search_input.display = False
            self._search_query = ""
            self._rebuild_table()
            self.query_one("#str-table", DataTable).focus()
            event.stop()
            return

        if search_input.has_focus:
            return

        if event.character in ("S", "/"):
            search_input.display = True
            search_input.focus()
            event.stop()


class FuncReversingTab(TabPane):
    def __init__(self, env_file: str | None = None) -> None:
        super().__init__("Function Reversing", id="tab-funcrev")
        self._info: BinaryInfo | None = None
        self._table: DataTable | None = None
        self._list: ListView | None = None
        self._instrs: list[DisasmInstruction] = []
        self._call_graph: CallGraph | None = None
        self._functions: list[tuple[str, int]] = []
        self._sorted_func_addrs: list[int] = []
        self._env_file: str = env_file or ".env"
        self._agent = None
        self._current_func_name: str | None = None
        self._current_func_instrs: list[DisasmInstruction] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="funcrev-body"):
            yield ListView(id="funcrev-func-list")
            with Vertical(id="funcrev-right"):
                yield DataTable(id="funcrev-table", cursor_type="row")
                yield HexPane(no_data_message="Select a function first")
        yield Static("Select a function to disassemble", id="funcrev-status")

    def on_mount(self) -> None:
        t = self.query_one("#funcrev-table", DataTable)
        t.add_columns("Address", "Bytes", "Mnemonic", "Operands")
        self._table = t
        self._list = self.query_one("#funcrev-func-list", ListView)
        self._hex_pane = self.query_one(HexPane)

    def load(self, info: BinaryInfo) -> None:
        self._info = info
        self._functions = []
        self._sorted_func_addrs = []
        lv = self._list
        if lv is None:
            return
        lv.clear()
        t = self._table
        if t is not None:
            t.clear()
        self._build_function_list(info)

    @work(thread=True)
    def _build_function_list(self, info: BinaryInfo) -> None:
        self.app.call_from_thread(self._set_status, "Disassembling…")
        instrs: list[DisasmInstruction] = []
        exec_segs = {seg.name for seg in info.segments if seg.initprot & 0x4}
        for seg in info.segments:
            if seg.name not in exec_segs:
                continue
            for s in seg.sections:
                if s.size == 0 or s.offset == 0:
                    continue
                instrs.extend(disassemble_section(info, s.segment, s.name))
        if not instrs:
            self.app.call_from_thread(
                self._set_status, "[red]No executable sections found[/red]"
            )
            return
        instrs.sort(key=lambda i: i.addr)
        graph = build_call_graph(info, instrs)
        self.app.call_from_thread(self._populate_functions, instrs, graph)

    def _populate_functions(
        self,
        instrs: list[DisasmInstruction],
        graph: CallGraph,
    ) -> None:
        self._instrs = instrs
        self._call_graph = graph

        func_map: dict[int, str] = dict(graph.addr_to_name)
        min_addr = instrs[0].addr
        max_addr = instrs[-1].addr
        for callee_list in graph.callees.values():
            for callee_name, callee_addr in callee_list:
                if callee_name.startswith("sub_") and callee_addr not in func_map:
                    if min_addr <= callee_addr <= max_addr:
                        func_map[callee_addr] = callee_name
        for caller_list in graph.callers.values():
            for caller_name, caller_addr in caller_list:
                if caller_name.startswith("sub_") and caller_addr not in func_map:
                    if min_addr <= caller_addr <= max_addr:
                        func_map[caller_addr] = caller_name

        sorted_funcs = sorted(func_map.items(), key=lambda x: x[0])
        self._functions = [(name, addr) for addr, name in sorted_funcs]
        self._sorted_func_addrs = [addr for addr, _ in sorted_funcs]

        lv = self._list
        if lv is None:
            return
        lv.clear()
        for addr, name in sorted_funcs:
            lv.append(ListItem(Label(f" {name}")))

        self._set_status(f"{len(self._functions)} functions identified")

    @on(ListView.Selected, "#funcrev-func-list")
    def _func_picked(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if 0 <= idx < len(self._functions):
            name, addr = self._functions[idx]
            self._show_function(name, addr)

    def _show_function(self, name: str, addr: int) -> None:
        try:
            pos = self._sorted_func_addrs.index(addr)
        except ValueError:
            return
        next_addr = (
            self._sorted_func_addrs[pos + 1]
            if pos + 1 < len(self._sorted_func_addrs)
            else None
        )
        func_instrs = [
            i
            for i in self._instrs
            if i.addr >= addr
            and (next_addr is None or i.addr < next_addr)
        ]
        self._current_func_name = name
        self._current_func_instrs = func_instrs
        t = self._table
        if t is None:
            return
        t.clear()
        arch = self._info.arch if self._info else ""
        for insn in func_instrs:
            raw_hex = " ".join(f"{b:02x}" for b in insn.raw)
            t.add_row(
                Text(f"0x{insn.addr:016x}", style="cyan"),
                Text(raw_hex, style="dim"),
                _colorize_mnemonic(insn.mnemonic),
                _colorize_operands(insn.op_str, arch),
            )
        self._hex_pane.set_data(
            b"".join(insn.raw for insn in func_instrs),
            func_instrs[0].addr if func_instrs else 0,
        )
        self._set_status(
            f"[dim]{name}[/dim] @ 0x{addr:x}  —  {len(func_instrs):,} instructions"
            "  [dim]h → hex view  a → ai analysis[/dim]"
        )

    def on_key(self, event) -> None:
        if not self.has_focus_within:
            return
        if event.key == "h":
            self._toggle_hex()
            event.stop()
        elif event.key == "a":
            self._ask_ai()
            event.stop()

    def _ask_ai(self) -> None:
        if not self._current_func_name or not self._current_func_instrs:
            self.app.notify("Select a function first", timeout=3)
            return
        arch = self._info.arch if self._info else "unknown"
        func_name = self._current_func_name
        func_addr = self._current_func_instrs[0].addr
        lines = [f"Function: {func_name} @ 0x{func_addr:x}", f"Architecture: {arch}", ""]
        for insn in self._current_func_instrs:
            lines.append(f"0x{insn.addr:x}  {insn.mnemonic} {insn.op_str}")
        self._invoke_agent(func_name, "\n".join(lines))

    @work(thread=True)
    def _invoke_agent(self, func_name: str, prompt: str) -> None:
        self.app.call_from_thread(self._set_status, "Querying AI model…")
        if self._agent is None:
            try:
                from cheerleader.agent import CheerleaderAIAgent, load_agent_settings

                config = load_agent_settings(self._env_file)
                self._agent = CheerleaderAIAgent(config)
            except Exception as exc:
                self.app.call_from_thread(
                    self.app.notify,
                    f"AI agent init failed: {exc}",
                    severity="error",
                    timeout=6,
                )
                self.app.call_from_thread(self._set_status, "AI agent unavailable")
                return

        try:
            result = self._agent.invoke(prompt)
            messages = result.get("messages", [])
            response = messages[-1].content if messages else "No response from model"
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify,
                f"AI query failed: {exc}",
                severity="error",
                timeout=6,
            )
            self.app.call_from_thread(self._set_status, "AI query failed")
            return

        self.app.call_from_thread(self._show_ai_response, func_name, response)

    def _show_ai_response(self, func_name: str, response: str) -> None:
        self._set_status(
            f"[dim]{func_name}[/dim]  —  AI analysis complete"
            "  [dim]h → hex view  a → ai analysis[/dim]"
        )
        self.app.push_screen(AIResponseScreen(func_name, response))

    def _toggle_hex(self) -> None:
        self._hex_pane.toggle()

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#funcrev-status", Static).update(msg)
        except Exception:
            pass


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
            with Vertical(id="disasm-bottom"):
                yield DataTable(id="disasm-table", cursor_type="row")
                yield HexPane(no_data_message="Disassemble a section first")
        yield Static("Select a section to disassemble", id="disasm-status")

    def on_mount(self) -> None:
        t = self.query_one("#disasm-table", DataTable)
        t.add_columns("Address", "Bytes", "Mnemonic", "Operands")
        self._table = t
        self._list = self.query_one("#disasm-sect-list", ListView)
        self._hex_pane = self.query_one(HexPane)

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
            self._set_status(
                "[red]No output — capstone not installed or section unreadable[/red]"
            )
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
        self._hex_pane.set_data(
            b"".join(insn.raw for insn in instrs),
            instrs[0].addr if instrs else 0,
        )
        self._set_status(
            f"[dim]{seg},{sect}[/dim]  {len(instrs):,} instructions"
            "  [dim]c → call flow   f → cfg   h → hex view[/dim]"
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
        elif event.key == "h":
            self._toggle_hex()
            event.stop()

    def _toggle_hex(self) -> None:
        self._hex_pane.toggle()

    def _open_cfg(self) -> None:
        if self._call_graph is None or self._table is None:
            self.app.notify("Disassemble a section first", timeout=3)
            return
        row = self._table.cursor_row
        if row < 0 or row >= len(self._instrs):
            self.app.notify("Select an instruction row", timeout=2)
            return
        addr = self._instrs[row].addr
        result = self._call_graph.func_at(addr)
        if result is None:
            self.app.notify(
                f"No function symbol at 0x{addr:x} — binary may be stripped",
                timeout=4,
            )
            return
        func_name, func_addr = result

        sa = self._call_graph._sorted_addrs
        pos = sa.index(func_addr)
        next_func_addr = sa[pos + 1] if pos + 1 < len(sa) else None

        func_instrs = [
            i
            for i in self._instrs
            if i.addr >= func_addr
            and (next_func_addr is None or i.addr < next_func_addr)
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
        result = self._call_graph.func_at(addr)
        if result is None:
            self.app.notify(
                f"No function symbol found at 0x{addr:x} — binary may be stripped",
                timeout=4,
            )
            return
        func_name, _ = result
        self.app.push_screen(CallFlowScreen(self._call_graph, func_name))


class HexEditorTab(TabPane):
    def __init__(self) -> None:
        super().__init__("Hex Editor", id="tab-hexedit")
        self._path: str | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="hexedit-scroll"):
            yield Static("", id="hexedit-content", markup=False)
        yield Static("", id="hexedit-status")

    def load(self, path: str) -> None:
        self._path = path
        self._load_file(path)

    @work(thread=True)
    def _load_file(self, path: str) -> None:
        self.app.call_from_thread(self._set_status, "Loading…")
        with open(path, "rb") as f:
            data = f.read()
        dump = build_hex_dump(data, 0)
        self.app.call_from_thread(self._display, data, dump)

    def _display(self, data: bytes, dump: str) -> None:
        self.query_one("#hexedit-content", Static).update(dump)
        size = len(data)
        if size >= 1024 * 1024:
            size_str = f"{size / (1024 * 1024):.1f} MiB"
        elif size >= 1024:
            size_str = f"{size / 1024:.1f} KiB"
        else:
            size_str = f"{size} bytes"
        self._set_status(
            f"[dim]{self._path}[/dim]  {size_str}  ({size:,} bytes)"
        )

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#hexedit-status", Static).update(msg)
        except Exception:
            pass
