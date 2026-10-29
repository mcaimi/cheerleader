"""Modal screen widgets: slice picker, call flow, CFG, hex editor prompts."""

from __future__ import annotations

import os
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, Label, ListItem, ListView, LoadingIndicator, RichLog, Rule, Select, Static, Tree

from cheerleader.libs.cfg import CallGraph, ControlFlowGraph
from cheerleader.libs.hexdoc import parse_byte_sequence, parse_offset
from cheerleader.tui.highlight import _esc_markup, _render_cfg


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


class CallFlowScreen(Screen):
    """Modal showing the call graph for a single function — callers and callees."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close"),
        Binding("b", "back", "Back"),
    ]

    def __init__(self, graph: CallGraph, func_name: str) -> None:
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


class CFGScreen(Screen):
    """Modal showing a Control Flow Graph for a single function."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def __init__(self, cfg: ControlFlowGraph) -> None:
        super().__init__()
        self._cfg = cfg

    def compose(self) -> ComposeResult:
        with Vertical(id="cfg-box"):
            yield Label("", id="cfg-title")
            yield Rule()
            with VerticalScroll(id="cfg-scroll"):
                yield Static("", id="cfg-content", markup=True)
            yield Rule()
            yield Label(
                "[dim]↑↓ / PgUp / PgDn  scroll    Esc  close[/dim]",
                id="cfg-hint",
            )

    def on_mount(self) -> None:
        cfg = self._cfg
        n = len(cfg.blocks)
        self.query_one("#cfg-title", Label).update(
            f"[bold]CFG:[/bold]  [cyan]{_esc_markup(cfg.func_name)}[/cyan]"
            f"  [dim]0x{cfg.func_addr:x}  ·  {n} block{'s' if n != 1 else ''}[/dim]"
        )
        self.query_one("#cfg-content", Static).update(_render_cfg(cfg))


class AIResponseScreen(Screen):
    """Modal showing a live AI analysis of a disassembled function.

    Pushed as soon as the analysis starts; callers stream progress into it
    via ``add_activity``/``update_answer`` and finish with ``mark_complete``
    or ``mark_error``.
    """

    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def __init__(self, func_name: str) -> None:
        super().__init__()
        self._func_name = func_name

    def compose(self) -> ComposeResult:
        with Vertical(id="ai-box"):
            yield Label("", id="ai-title")
            yield Rule()
            yield LoadingIndicator(id="ai-spinner")
            with Horizontal(id="ai-body"):
                with VerticalScroll(id="ai-scroll"):
                    yield Static("", id="ai-content", markup=False)
                yield RichLog(id="ai-activity", markup=True, wrap=True, auto_scroll=True)
            yield Rule()
            yield Label(
                "[dim]↑↓ / PgUp / PgDn  scroll    Esc  close[/dim]",
                id="ai-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#ai-title", Label).update(
            f"[bold]AI Analysis:[/bold]  [cyan]{_esc_markup(self._func_name)}[/cyan]"
            "  [dim](running…)[/dim]"
        )
        self.add_activity("Starting analysis…")

    def add_activity(self, line: str) -> None:
        try:
            self.query_one("#ai-activity", RichLog).write(line)
        except Exception:
            pass

    def update_answer(self, text: str) -> None:
        try:
            self.query_one("#ai-content", Static).update(text)
            self.query_one("#ai-scroll", VerticalScroll).scroll_end(animate=False)
        except Exception:
            pass

    def mark_complete(self) -> None:
        try:
            self.query_one("#ai-title", Label).update(
                f"[bold]AI Analysis:[/bold]  [cyan]{_esc_markup(self._func_name)}[/cyan]"
            )
        except Exception:
            pass
        self._stop_spinner()

    def mark_error(self, message: str) -> None:
        self.add_activity(f"[red]Error: {_esc_markup(message)}[/red]")
        try:
            self.query_one("#ai-title", Label).update(
                f"[bold]AI Analysis:[/bold]  [cyan]{_esc_markup(self._func_name)}[/cyan]"
                "  [red](failed)[/red]"
            )
        except Exception:
            pass
        self._stop_spinner()

    def _stop_spinner(self) -> None:
        try:
            self.query_one("#ai-spinner", LoadingIndicator).display = False
        except Exception:
            pass


class ConfirmPrompt(ModalScreen[bool]):
    """Yes/no confirmation dialog."""

    BINDINGS = [
        Binding("y", "dismiss(True)", "Yes"),
        Binding("n,escape", "dismiss(False)", "No"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="prompt-box"):
            yield Label(self._message, classes="prompt-title")
            yield Label("[dim]y  yes    n / Esc  no[/dim]", classes="prompt-hint")


class _InputPrompt(ModalScreen[Any]):
    """Base for single-input dialogs with inline validation.

    Subclasses implement ``validate_value`` returning the dismiss result or
    raising ValueError with a message shown under the input.
    """

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    title_text = ""
    hint_text = ""

    def __init__(self, value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self._initial = value
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="prompt-box"):
            yield Label(self.title_text, classes="prompt-title")
            yield from self.compose_extra()
            yield Input(self._initial, placeholder=self._placeholder, id="prompt-input")
            yield Label("", classes="prompt-error")
            yield Label(f"[dim]{self.hint_text}  ·  Enter accept  ·  Esc cancel[/dim]",
                        classes="prompt-hint")

    def compose_extra(self) -> ComposeResult:
        yield from ()

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def show_error(self, message: str) -> None:
        self.query_one(".prompt-error", Label).update(f"[red]{_esc_markup(message)}[/red]")

    @on(Input.Changed, "#prompt-input")
    def _clear_error(self) -> None:
        self.query_one(".prompt-error", Label).update("")

    @on(Input.Submitted, "#prompt-input")
    def _submitted(self, event: Input.Submitted) -> None:
        try:
            result = self.validate_value(event.value)
        except ValueError as e:
            self.show_error(str(e))
            return
        self.accept(result)

    def accept(self, result: Any) -> None:
        self.dismiss(result)

    def validate_value(self, value: str) -> Any:
        raise NotImplementedError


class ByteEditPrompt(_InputPrompt):
    """Ask for replacement bytes at an offset; dismisses with ``bytes``."""

    hint_text = "de ad be ef · 0x7f · #127 · \"text"

    def __init__(self, offset: int, current: bytes, available: int) -> None:
        super().__init__(f"{current[0]:02x}" if current else "")
        self._offset = offset
        self._available = available
        c = current[0] if current else 0
        ch = chr(c) if 0x20 <= c < 0x7F else "."
        self.title_text = (
            f"[bold]Edit at 0x{offset:08x}[/bold]  "
            f"[dim]current: 0x{c:02x}  {c}  '{_esc_markup(ch)}'[/dim]"
        )

    def validate_value(self, value: str) -> bytes:
        data = parse_byte_sequence(value)
        if len(data) > self._available:
            raise ValueError(
                f"{len(data)} bytes would run past end of file "
                f"(only {self._available} left from this offset)"
            )
        return data


class SavePrompt(_InputPrompt):
    """Ask for a destination path; dismisses with an absolute path."""

    title_text = "[bold]Save as[/bold]"
    hint_text = "existing files ask before overwrite"

    def validate_value(self, value: str) -> str:
        path = os.path.abspath(os.path.expanduser(value.strip()))
        if not value.strip():
            raise ValueError("enter a file name")
        if os.path.isdir(path):
            raise ValueError("path is a directory")
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            raise ValueError(f"directory does not exist: {directory}")
        if not os.access(directory, os.W_OK):
            raise ValueError(f"directory is not writable: {directory}")
        return path

    def accept(self, result: str) -> None:
        if not os.path.exists(result):
            self.dismiss(result)
            return

        def _confirmed(ok: bool | None) -> None:
            if ok:
                self.dismiss(result)

        self.app.push_screen(
            ConfirmPrompt(f"[bold]{_esc_markup(result)}[/bold] exists. Overwrite?"), _confirmed
        )


SEARCH_MODES = [("Offset", "offset"), ("Hex bytes", "hex"), ("Text", "text")]


class SearchPrompt(_InputPrompt):
    """Go to offset or search a byte/text pattern.

    Dismisses with ``("offset", int, raw)`` or ``(mode, bytes, raw)`` for hex/text,
    where ``raw`` is the text as typed.
    """

    title_text = "[bold]Go to offset / search[/bold]"
    hint_text = "offset: 1a2b · 0x1a2b · #6699   hex: de ad be ef   Tab → mode"

    def __init__(self, size: int, mode: str = "offset", value: str = "") -> None:
        super().__init__(value)
        self._file_size = size
        self._mode = mode

    def compose_extra(self) -> ComposeResult:
        yield Select(SEARCH_MODES, value=self._mode, allow_blank=False, id="prompt-mode")

    @on(Select.Changed, "#prompt-mode")
    def _mode_changed(self, event: Select.Changed) -> None:
        self._mode = str(event.value)
        self.query_one(".prompt-error", Label).update("")

    @property
    def mode(self) -> str:
        return self._mode

    def validate_value(self, value: str) -> tuple[str, Any]:
        if self._mode == "offset":
            offset = parse_offset(value)
            if not 0 <= offset < self._file_size:
                raise ValueError(f"offset out of range (file is 0x{self._file_size:x} bytes)")
            return ("offset", offset, value)
        if self._mode == "hex":
            return ("hex", parse_byte_sequence(value), value)
        if not value:
            raise ValueError("enter text to search for")
        return ("text", value.encode("utf-8"), value)
