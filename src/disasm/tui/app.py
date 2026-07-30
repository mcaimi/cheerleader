"""Root DisasmApp Textual application."""

from __future__ import annotations

import os
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Footer, Header, TabbedContent

from disasm.formats import detect_format, parse
from disasm.formats.macho import MachOInfo, list_fat_slices
from disasm.libs.types import BinaryInfo
from disasm.tui.screens import SlicePicker
from disasm.tui.tabs import (
    ChainedFixupsTab, DisasmTab, ExportsTab, LibrariesTab,
    SectionsTab, SegmentsTab, StringsTab, SymbolsTab,
)
from disasm.tui.widgets import InfoBar

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
CFGScreen {
    align: center middle;
    background: $background 60%;
}
#cfg-box {
    width: 92%;
    height: 90%;
    border: round $accent;
    background: $surface;
    padding: 0 1;
}
#cfg-title {
    padding: 1;
    text-align: center;
}
#cfg-hint {
    padding: 0 1 1 1;
    text-align: center;
}
#cfg-scroll {
    height: 1fr;
}
#cfg-content {
    padding: 1 1;
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
    """Binary inspector TUI."""

    CSS = CSS
    TITLE = "disasm — binary inspector"

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
        self._info: BinaryInfo | None = None
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
        fmt = detect_format(self._path)
        if fmt == "macho":
            self._slices = list_fat_slices(self._path)
        else:
            self._slices = [(0, "default")]
        self._load()

    def _load(self) -> None:
        info = parse(self._path, slice_index=self._slice)
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
        if not isinstance(self._info, MachOInfo) or len(self._slices) <= 1:
            self.notify("Single-arch binary — no other slices", timeout=3)
            return

        def _on_pick(result: int | None) -> None:
            if result is not None:
                self._slice = result
                self._load()

        self.push_screen(SlicePicker(self._slices), _on_pick)

    def action_quit(self) -> None:
        self.exit()
