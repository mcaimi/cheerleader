"""Per-mode Textual applications and the ``build_app`` factory.

``_CheerApp`` holds the shared plumbing (CSS, quit/reload bindings,
discard confirmation); each launch mode is its own subclass:

- ``DisasmApp``      — binary inspector (default)
- ``HexEditorApp``   — ``--hex``
- ``HexCompareApp``  — ``--hex-compare``

Use ``build_app()`` to construct the right app for a launch mode.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import Footer, Header, TabbedContent

from cheerleader.formats import detect_format, parse
from cheerleader.formats.macho import MachOInfo, list_fat_slices
from cheerleader.libs.types import BinaryInfo
from cheerleader.tui.screens import ConfirmPrompt, SlicePicker
from cheerleader.tui.tabs import (
    ChainedFixupsTab,
    DisasmTab,
    ExportsTab,
    FuncReversingTab,
    HexCompareTab,
    HexEditorTab,
    LibrariesTab,
    SectionsTab,
    SegmentsTab,
    StringsTab,
    SymbolsTab,
)
from cheerleader.tui.widgets import InfoBar

APP_TITLE = "CHEERLEADER"


class _CheerApp(App):
    """Shared application plumbing: CSS, quit/reload, discard confirmation."""

    CSS_PATH = "app.tcss"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reload", "Reload"),
    ]

    _path: reactive[str] = reactive("", recompose=False)

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path
        self._pending_reload_notify = False

    def _load(self) -> None:
        raise NotImplementedError

    def _confirm_discard(self, action: str, then: Callable[[], None]) -> None:
        """Run *then* directly, or after confirmation if the hex editor has unsaved edits."""
        try:
            modified = self.query_one(HexEditorTab).modified
        except NoMatches:
            modified = False
        if not modified:
            then()
            return

        def _answer(ok: bool | None) -> None:
            if ok:
                then()

        self.push_screen(ConfirmPrompt(f"Discard unsaved edits and {action}?"), _answer)

    def action_reload(self) -> None:
        def _reload() -> None:
            self._pending_reload_notify = True
            self._load()

        self._confirm_discard("reload", _reload)

    def action_quit(self) -> None:
        self._confirm_discard("quit", self.exit)


class DisasmApp(_CheerApp):
    """Binary inspector TUI."""

    TITLE = "CHEERLEADER — binary inspector"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("s", "slice", "Switch slice"),
        Binding("1", "tab('tab-segments')", "Segments"),
        Binding("2", "tab('tab-strings')", "Strings"),
        Binding("3", "tab('tab-sections')", "Sections"),
        Binding("4", "tab('tab-libraries')", "Libraries"),
        Binding("5", "tab('tab-symbols')", "Symbols"),
        Binding("6", "tab('tab-exports')", "Exports"),
        Binding("7", "tab('tab-fixups')", "Fixups"),
        Binding("8", "tab('tab-disasm')", "Disasm"),
        Binding("9", "tab('tab-funcrev')", "Func Rev"),
    ]

    _slice: reactive[int] = reactive(0, recompose=False)

    def __init__(
        self,
        path: str,
        env_file: str | None = None,
        disasm_engine: str = "objdump",
    ) -> None:
        super().__init__(path)
        self._slice = 0
        self._info: BinaryInfo | None = None
        self._slices: list[tuple[int, str]] = []
        self._env_file = env_file
        self._disasm_engine = disasm_engine

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
            yield DisasmTab(disasm_engine=self._disasm_engine)
            yield FuncReversingTab(env_file=self._env_file, disasm_engine=self._disasm_engine)
        yield Footer()

    def on_mount(self) -> None:
        fmt = detect_format(self._path)
        if fmt == "macho":
            self._slices = list_fat_slices(self._path)
        else:
            self._slices = [(0, "default")]
        self._load()

    def _load(self) -> None:
        self.query_one(InfoBar).set_analyzing(True)
        self._parse_binary()

    @work(thread=True)
    def _parse_binary(self) -> None:
        info = parse(self._path, slice_index=self._slice)
        self.call_from_thread(self._apply_info, info)

    def _apply_info(self, info: BinaryInfo) -> None:
        self._info = info

        info_bar = self.query_one(InfoBar)
        info_bar.update_info(info)
        info_bar.set_analyzing(False)
        self.query_one(SegmentsTab).load(info)
        self.query_one(StringsTab).load(info)
        self.query_one(SectionsTab).load(info)
        self.query_one(LibrariesTab).load(info)
        self.query_one(SymbolsTab).load(info)
        self.query_one(ExportsTab).load(info)
        self.query_one(ChainedFixupsTab).load(info)
        self.query_one(DisasmTab).load(info)
        self.query_one(FuncReversingTab).load(info)

        arch = info.arch
        self.sub_title = f"{os.path.basename(self._path)}  [{arch}]"

        if info.error:
            self.notify(info.error, severity="error", timeout=8)
        if self._pending_reload_notify:
            self._pending_reload_notify = False
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


class HexEditorApp(_CheerApp):
    """Overwrite-only hex editor TUI (``--hex``)."""

    TITLE = "CHEERLEADER — hex editor"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="main-tabs"):
            yield HexEditorTab()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = os.path.basename(self._path)
        self._load()

    def _load(self) -> None:
        self.query_one(HexEditorTab).load(self._path)

    def on_hex_editor_tab_saved(self, event: HexEditorTab.Saved) -> None:
        # Save-as switches the editor to the new file; reload should follow it.
        self._path = event.path
        self.sub_title = os.path.basename(event.path)


class HexCompareApp(_CheerApp):
    """Side-by-side hex diff TUI (``--hex-compare``)."""

    TITLE = "CHEERLEADER — hex compare"

    def __init__(self, path: str, right_path: str) -> None:
        super().__init__(path)
        self._right_path = right_path

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="main-tabs"):
            yield HexCompareTab()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = (
            f"{os.path.basename(self._path)} ⇄ {os.path.basename(self._right_path)}"
        )
        self._load()

    def _load(self) -> None:
        self.query_one(HexCompareTab).load(self._path, self._right_path)


def build_app(
    path: str,
    env_file: str | None = None,
    hex_mode: bool = False,
    hex_compare: str | None = None,
    disasm_engine: str = "objdump",
) -> App:
    """Construct the App for the requested launch mode.

    This is the stable entry point: the per-mode subclasses may refine
    their ``__init__`` signatures, but ``build_app`` keeps a single
    dispatching signature for callers.
    """
    if hex_compare:
        return HexCompareApp(path, hex_compare)
    if hex_mode:
        return HexEditorApp(path)
    return DisasmApp(path, env_file=env_file, disasm_engine=disasm_engine)
