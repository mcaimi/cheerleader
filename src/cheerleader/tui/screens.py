"""Modal screen widgets: slice picker, call flow, CFG."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, ListItem, ListView, LoadingIndicator, RichLog, Rule, Static, Tree

from cheerleader.libs.cfg import CallGraph, ControlFlowGraph
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
