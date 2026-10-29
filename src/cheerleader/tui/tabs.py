"""Tab panel widgets for the binary inspector TUI."""

from __future__ import annotations

from typing import Any

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import DataTable, Input, Label, ListItem, ListView, Static, TabPane
from textual.worker import get_current_worker
from rich.markup import escape
from rich.text import Text

from cheerleader.libs.cfg import CallGraph, build_call_graph, build_cfg
from cheerleader.libs.disasm import disassemble_all_sections, disassemble_section, extract_strings
from cheerleader.libs.hexdoc import HexDocument
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
from cheerleader.tui.screens import (
    AIResponseScreen,
    ByteEditPrompt,
    CallFlowScreen,
    CFGScreen,
    SavePrompt,
    SearchPrompt,
)
from cheerleader.tui.widgets import (
    HexCompareView,
    HexEditorView,
    HexPane,
    compute_diff_blocks,
)


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


def _format_update(node_name: str, data: Any) -> list[str]:
    """Turn one LangGraph "updates"-mode step into human-readable activity lines."""
    if not isinstance(data, dict):
        return [f"→ [{node_name}] updated"]
    messages = data.get("messages", [])
    if not messages:
        return [f"→ [{node_name}] updated"]

    lines: list[str] = []
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            for call in tool_calls:
                name = call.get("name", "?") if isinstance(call, dict) else getattr(call, "name", "?")
                args = call.get("args", {}) if isinstance(call, dict) else getattr(call, "args", {})
                lines.append(f"→ [{node_name}] calling {name}({args})")
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, str) and content.strip():
            snippet = content.strip().replace("\n", " ")[:200]
            lines.append(f"→ [{node_name}] {snippet}")
        else:
            lines.append(f"→ [{node_name}] updated")
    return lines


class FuncReversingTab(TabPane):
    def __init__(self, env_file: str | None = None, disasm_engine: str = "objdump") -> None:
        super().__init__("Function Reversing", id="tab-funcrev")
        self._info: BinaryInfo | None = None
        self._table: DataTable | None = None
        self._list: ListView | None = None
        self._instrs: list[DisasmInstruction] = []
        self._call_graph: CallGraph | None = None
        self._functions: list[tuple[str, int]] = []
        self._sorted_func_addrs: list[int] = []
        self._env_file: str = env_file or ".env"
        self._disasm_engine = disasm_engine
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
        instrs, func_labels = disassemble_all_sections(info, engine=self._disasm_engine)
        if not instrs:
            self.app.call_from_thread(
                self._set_status, "[red]No executable sections found[/red]"
            )
            return
        graph = build_call_graph(info, instrs, extra_func_labels=func_labels)
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
        screen = AIResponseScreen(func_name)
        self.app.push_screen(screen)
        self._invoke_agent(func_name, "\n".join(lines), screen)

    @work(thread=True)
    def _invoke_agent(self, func_name: str, prompt: str, screen: AIResponseScreen) -> None:
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
                self.app.call_from_thread(screen.mark_error, str(exc))
                return

        answer: list[str] = []
        try:
            for mode, chunk in self._agent.stream(prompt):
                if mode == "updates":
                    for node_name, data in chunk.items():
                        for line in _format_update(node_name, data):
                            self.app.call_from_thread(screen.add_activity, line)
                elif mode == "messages":
                    msg_chunk, _meta = chunk
                    delta = getattr(msg_chunk, "content", "")
                    if isinstance(delta, str) and delta:
                        answer.append(delta)
                        self.app.call_from_thread(screen.update_answer, "".join(answer))
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify,
                f"AI query failed: {exc}",
                severity="error",
                timeout=6,
            )
            self.app.call_from_thread(self._set_status, "AI query failed")
            self.app.call_from_thread(screen.mark_error, str(exc))
            return

        self.app.call_from_thread(self._show_ai_response, func_name, screen)

    def _show_ai_response(self, func_name: str, screen: AIResponseScreen) -> None:
        self._set_status(
            f"[dim]{func_name}[/dim]  —  AI analysis complete"
            "  [dim]h → hex view  a → ai analysis[/dim]"
        )
        screen.mark_complete()

    def _toggle_hex(self) -> None:
        self._hex_pane.toggle()

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#funcrev-status", Static).update(msg)
        except Exception:
            pass


class DisasmTab(TabPane):
    def __init__(self, disasm_engine: str = "objdump") -> None:
        super().__init__("Disasm", id="tab-disasm")
        self._info: BinaryInfo | None = None
        self._sections: list[tuple[str, str]] = []
        self._table: DataTable | None = None
        self._list: ListView | None = None
        self._instrs: list[DisasmInstruction] = []
        self._call_graph: CallGraph | None = None
        self._disasm_engine = disasm_engine

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
        instrs = disassemble_section(self._info, seg, sect, engine=self._disasm_engine)
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
                f"[red]No output — {self._disasm_engine} unavailable or section unreadable[/red]"
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


def _human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MiB"
    if size >= 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size} bytes"


class HexEditorTab(TabPane):
    """Overwrite-only hex editor; the file is read on demand, edits kept in memory until saved."""

    BINDINGS = [
        Binding("enter", "edit", "Edit"),
        Binding("u", "undo", "Undo"),
        Binding("s", "save", "Save"),
        Binding("slash", "search", "Go to / Search"),
        Binding("n", "search_next(1)", "Next match"),
        Binding("N", "search_next(0)", "Prev match"),
        Binding("escape", "cancel_search", "Cancel search", show=False),
    ]

    class Saved(Message):
        def __init__(self, path: str) -> None:
            super().__init__()
            self.path = path

    def __init__(self) -> None:
        super().__init__("Hex Editor", id="tab-hexedit")
        self._doc: HexDocument | None = None
        self._busy: str | None = None
        self._search_mode = "offset"
        self._search_raw = ""
        self._pattern: bytes | None = None
        self._search_gen = 0
        # Status note shown while the cursor stays on the given offset.
        self._note: tuple[int, str] | None = None

    def compose(self) -> ComposeResult:
        yield HexEditorView(id="hexedit-view")
        yield Static("", id="hexedit-status")

    @property
    def modified(self) -> bool:
        return self._doc is not None and self._doc.modified

    def load(self, path: str) -> None:
        self.workers.cancel_group(self, "hexsearch")
        self._busy = None
        try:
            doc = HexDocument(path)
        except OSError as e:
            self.app.notify(f"Cannot open {path}: {e}", severity="error", timeout=6)
            return
        if self._doc is not None:
            self._doc.close()
        self._doc = doc
        self._note = None
        view = self.query_one(HexEditorView)
        view.set_document(doc)
        view.focus()

    def _view(self) -> HexEditorView:
        return self.query_one(HexEditorView)

    def _refuse_if_busy(self) -> bool:
        if self._doc is None:
            return True
        if self._busy:
            self.app.notify(f"Busy {self._busy}…", timeout=2)
            return True
        return False

    # ── status ────────────────────────────────────────────────────────────────

    @on(HexEditorView.CursorMoved)
    def _cursor_moved(self) -> None:
        self._update_status()

    def _update_status(self, extra: str = "") -> None:
        doc = self._doc
        if doc is None:
            return
        parts = [f"[dim]{doc.path}[/dim]  {_human_size(doc.size)}"]
        cur = self._view().cursor
        if not extra and self._note and self._note[0] == cur:
            extra = self._note[1]
        if doc.size:
            b = doc.read(cur, 1)[0]
            ch = chr(b) if 0x20 <= b < 0x7F else "."
            parts.append(f"0x{cur:08x} ({cur:,})  = 0x{b:02x} {b:3d} '{escape(ch)}'")
        else:
            parts.append("empty file")
        if doc.modified:
            parts.append(f"[yellow]{doc.edit_count:,} unsaved \\[modified][/yellow]")
        if extra:
            parts.append(extra)
        self._set_status("  │  ".join(parts))

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#hexedit-status", Static).update(msg)
        except Exception:
            pass

    # ── editing ───────────────────────────────────────────────────────────────

    def action_edit(self) -> None:
        if self._refuse_if_busy():
            return
        doc = self._doc
        if doc.size == 0:
            self.app.notify("Empty file — nothing to edit", timeout=2)
            return
        offset = self._view().cursor

        def _apply(data: bytes | None) -> None:
            if not data or self._doc is not doc:
                return
            doc.write(offset, data)
            self._view().move_to(offset + len(data))
            self._view().refresh()
            self._update_status()

        self.app.push_screen(ByteEditPrompt(offset, doc.read(offset, 1), doc.size - offset), _apply)

    def action_undo(self) -> None:
        if self._refuse_if_busy():
            return
        offset = self._doc.undo()
        if offset is None:
            self.app.notify("Nothing to undo", timeout=2)
            return
        self._view().move_to(offset)
        self._view().refresh()
        self._update_status()

    # ── saving ────────────────────────────────────────────────────────────────

    def action_save(self) -> None:
        if self._refuse_if_busy():
            return

        def _chosen(path: str | None) -> None:
            if path:
                self._busy = "saving"
                self._run_save(path)

        self.app.push_screen(SavePrompt(f"{self._doc.path}.patched"), _chosen)

    @work(thread=True, group="hexsave")
    def _run_save(self, path: str) -> None:
        last = [-1]

        def _progress(frac: float) -> None:
            pct = int(frac * 100)
            if pct // 5 != last[0] // 5:
                last[0] = pct
                self.app.call_from_thread(self._update_status, f"Saving… {pct}%")

        try:
            self._doc.save_to(path, _progress)
        except OSError as e:
            self.app.call_from_thread(self._save_done, path, str(e))
            return
        self.app.call_from_thread(self._save_done, path, None)

    def _save_done(self, path: str, error: str | None) -> None:
        self._busy = None
        if error:
            self.app.notify(f"Save failed: {error}", severity="error", timeout=8)
            self._update_status()
            return
        self._doc.reopen(path)
        self._note = None
        self._view().set_document(self._doc)
        self.post_message(self.Saved(path))
        self.app.notify(f"Saved {path}", timeout=3)

    # ── go to / search ────────────────────────────────────────────────────────

    def action_search(self) -> None:
        if self._doc is None:
            return

        def _chosen(result: tuple[str, Any, str] | None) -> None:
            if result is None:
                return
            mode, value, raw = result
            self._search_mode, self._search_raw = mode, raw
            if mode == "offset":
                self._view().set_match(None)
                self._view().move_to(value, top=True)
                return
            self._pattern = value
            self._start_search(forward=True)

        self.app.push_screen(
            SearchPrompt(self._doc.size, self._search_mode, self._search_raw), _chosen
        )

    def action_search_next(self, forward: int) -> None:
        if self._pattern is None:
            self.app.notify("No search pattern — press / first", timeout=2)
            return
        self._start_search(forward=bool(forward))

    def action_cancel_search(self) -> None:
        if self._busy == "searching":
            self.workers.cancel_group(self, "hexsearch")
            self._busy = None
            self._note = (self._view().cursor, "search cancelled")
            self._update_status()

    def _start_search(self, forward: bool) -> None:
        if self._doc is None or self._busy == "saving":
            self._refuse_if_busy()
            return
        self._busy = "searching"
        self._search_gen += 1
        self._run_search(self._pattern, self._view().cursor, forward, self._search_gen)

    @work(thread=True, exclusive=True, group="hexsearch")
    def _run_search(self, pattern: bytes, cur: int, forward: bool, gen: int) -> None:
        worker = get_current_worker()
        doc = self._doc
        last = [-1]

        def _cancelled() -> bool:
            return worker.is_cancelled

        def _progress(frac: float) -> None:
            pct = int(frac * 100)
            if pct // 5 != last[0] // 5:
                last[0] = pct
                self.app.call_from_thread(self._update_status, f"Searching… {pct}%  (Esc cancels)")

        n = len(pattern)
        wrapped = False
        if forward:
            hit = doc.find(pattern, cur + 1, cancelled=_cancelled, progress=_progress)
            if hit is None and not worker.is_cancelled:
                wrapped = True
                hit = doc.find(pattern, 0, cur + n, cancelled=_cancelled, progress=_progress)
        else:
            hit = doc.rfind(pattern, 0, cur + n - 1, cancelled=_cancelled, progress=_progress)
            if hit is None and not worker.is_cancelled:
                wrapped = True
                hit = doc.rfind(pattern, cur, doc.size, cancelled=_cancelled, progress=_progress)
        if not worker.is_cancelled:
            self.app.call_from_thread(self._search_done, gen, hit, n, wrapped)

    def _search_done(self, gen: int, hit: int | None, n: int, wrapped: bool) -> None:
        if gen != self._search_gen:
            return
        self._busy = None
        view = self._view()
        if hit is None:
            view.set_match(None)
            self._note = (view.cursor, "[red]pattern not found[/red]")
            self._update_status()
            return
        self._note = (
            hit,
            f"match at 0x{hit:08x}" + ("  [dim](wrapped)[/dim]" if wrapped else "")
            + "  [dim]n/N → next/prev[/dim]",
        )
        view.set_match((hit, n))
        view.move_to(hit)
        self._update_status()


class HexCompareTab(TabPane):
    def __init__(self) -> None:
        super().__init__("Hex Compare", id="tab-hexcmp")

    def compose(self) -> ComposeResult:
        yield HexCompareView(id="hexcmp-view")
        yield Static("", id="hexcmp-status")

    def load(self, left_path: str, right_path: str) -> None:
        self._load_files(left_path, right_path)

    @work(thread=True, exclusive=True)
    def _load_files(self, left_path: str, right_path: str) -> None:
        self.app.call_from_thread(self._set_status, "Loading…")
        with open(left_path, "rb") as f:
            left = f.read()
        with open(right_path, "rb") as f:
            right = f.read()
        diff_blocks, diff_bytes = compute_diff_blocks(left, right)
        self.app.call_from_thread(
            self._display, left_path, right_path, left, right, diff_blocks, diff_bytes
        )

    def _display(
        self,
        left_path: str,
        right_path: str,
        left: bytes,
        right: bytes,
        diff_blocks: list[int],
        diff_bytes: int,
    ) -> None:
        view = self.query_one(HexCompareView)
        view.set_data(left, right, left_path, right_path, diff_blocks)
        view.focus()
        if diff_blocks:
            a, b = left[diff_blocks[0] * 8 :], right[diff_blocks[0] * 8 :]
            first = diff_blocks[0] * 8 + next(
                (i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b))
            )
            diff_str = (
                f"[red]{diff_bytes:,} differing bytes[/red]  first at 0x{first:08x}"
                "  [dim]n/p → next/prev diff[/dim]"
            )
        else:
            diff_str = "[green]files are identical[/green]"
        self._set_status(
            f"left {_human_size(len(left))} ({len(left):,})  "
            f"right {_human_size(len(right))} ({len(right):,})  {diff_str}"
        )

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#hexcmp-status", Static).update(msg)
        except Exception:
            pass
