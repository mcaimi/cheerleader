"""
Call-flow and CFG windows for the Dear PyGui front-end.

The port of ``tui.screens.CallFlowScreen`` / ``CFGScreen``, opened from the
Disassembly window (plan decision: independent floating ``Tool`` windows
holding immutable snapshots — closing the parent does not force-close them).

* **Call flow** — a DPG tree: ``Calls (N)`` / ``Called by (N)`` nodes under
  a root, one selectable leaf per function (``user_data`` carries the name).
  Clicking a leaf drills in (history stack); ``b`` pops back; ``Esc`` or
  ``b`` at the root closes.
* **CFG** — a read-only dpg **Node Editor**: one node per basic
  block (title bar ``Block #i  0x…``, instruction rows as static
  attributes, one output pin per successor edge labeled/colored by edge
  type), one input pin per node, and a link per in-function edge colored
  via per-link themes. Blocks are placed by the pure :func:`cfg_layout`
  (BFS depth → column, stacked by estimated height). Read-only = nodes
  ``draggable=False``; a mouse-detached link self-heals in the editor's
  ``delink_callback`` (the snapshot stays immutable). Blocks are numbered
  by ``(BFS depth, address)``; the entry block's title bar is highlighted.

Both windows are built once from their snapshot and never re-rendered;
there is no worker, no per-frame tick, and ``dirty_edit_count() == 0``.
"""

from cheerleader.gui.app import (
    STATUS_RESERVE,
    TOOL_MIN_H,
    TOOL_MIN_W,
    GuiApp,
    Tool,
    _tool_window_closed,
)
from cheerleader.gui.const import KEY_ESCAPE, TABLE_WIN_H, TABLE_WIN_W
from cheerleader.libs.cfg import CallGraph, ControlFlowGraph
from cheerleader.libs.types import DisasmInstruction

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

# ── palette (TUI highlight._EDGE_STYLE parity) ───────────────────────────────

COL_TEXT = (216, 216, 222, 255)
COL_DIM = (140, 140, 148, 255)
COL_BLOCK = (110, 210, 230, 255)  # cyan block headers
COL_ENTRY = (255, 230, 120, 255)  # entry-block title bar
COL_CALLS = (120, 200, 120, 255)  # green "Calls" node
COL_CALLED_BY = (250, 210, 80, 255)  # yellow "Called by" node
COL_GRID_BG = (24, 24, 30, 255)
COL_GRID_LINE = (36, 36, 44, 255)

_EDGE_COLORS = {
    "fall": (120, 200, 120, 255),  # green
    "cond": (250, 210, 80, 255),  # yellow
    "jump": (110, 170, 255, 255),  # blue
    "indirect": (140, 140, 148, 255),  # dim
    "ret": (240, 120, 120, 255),  # red
}


def edge_color(etype: str) -> tuple[int, int, int, int]:
    """DPG color for a CFG edge type (TUI ``_EDGE_STYLE`` parity)."""
    return _EDGE_COLORS.get(etype, COL_DIM)


# ── CFG node-graph layout (pure; unit-tested without a display) ──────────────

# Estimated node geometry (px). DPG nodes auto-size to their content; the
# layout only needs plausible boxes so columns/stacks don't overlap.
NODE_W = 480  # estimated content width of a full-instruction node + margin
LINE_H = 19  # one text row inside a node
NODE_PAD_H = 46  # title bar + attribute padding
X_GAP = 70  # horizontal gap between depth columns
Y_GAP = 40  # vertical gap between stacked nodes


def _raw_hex(raw: bytes, limit: int | None = 6) -> str:
    """Hex byte string (``de ad be ef``), optionally truncated (TUI parity)."""
    if limit is not None:
        raw = raw[:limit]
    return " ".join(f"{b:02x}" for b in raw)


def _insn_text(insn: DisasmInstruction) -> str:
    """One instruction row inside a block node (TUI text parity)."""
    hex_str = _raw_hex(insn.raw) + ("…" if len(insn.raw) > 6 else "")
    return f"  0x{insn.addr:x}  {hex_str:<20}  {insn.mnemonic:<8}  {insn.op_str}"


def cfg_blocks_ordered(cfg: ControlFlowGraph) -> list[int]:
    """Block addresses in display order: BFS depth from the entry, then
    address; unreachable blocks land in one trailing column (address
    order). Block ``#n`` is the *n*-th entry of this list (1-based)."""
    depth: dict[int, int] = {cfg.entry: 0} if cfg.entry in cfg.blocks else {}
    queue = [cfg.entry] if depth else []
    while queue:
        addr = queue.pop(0)
        for tgt, _ in cfg.blocks[addr].succs:
            if tgt in cfg.blocks and tgt not in depth:
                depth[tgt] = depth[addr] + 1
                queue.append(tgt)
    # unreachable blocks share the sort key "after every reached depth",
    # tie-broken by address (one trailing column, address order)
    return sorted(cfg.blocks, key=lambda a: (depth.get(a, len(depth)), a))


def _est_node_height(cfg: ControlFlowGraph, addr: int) -> int:
    """Estimated pixel height of the block's node (title + rows + pins)."""
    block = cfg.blocks[addr]
    rows = 1 + len(block.instrs) + max(len(block.succs), 1)  # in + instrs + outs
    return rows * LINE_H + NODE_PAD_H


def cfg_layout(cfg: ControlFlowGraph) -> dict[int, tuple[int, int]]:
    """Node positions ``(x, y)`` per block address: BFS depth from the
    entry becomes the column (``x``), blocks within a column stack
    top-down by estimated height (``y``). Ties sorted by address — the
    same order as :func:`cfg_blocks_ordered`. Deterministic."""
    if not cfg.blocks:
        return {}
    order = cfg_blocks_ordered(cfg)
    depth = {cfg.entry: 0} if cfg.entry in cfg.blocks else {}
    queue = [cfg.entry] if depth else []
    while queue:
        addr = queue.pop(0)
        for tgt, _ in cfg.blocks[addr].succs:
            if tgt in cfg.blocks and tgt not in depth:
                depth[tgt] = depth[addr] + 1
                queue.append(tgt)
    max_d = max(depth.values(), default=-1)
    positions: dict[int, tuple[int, int]] = {}
    col_y: dict[int, int] = {}
    for addr in order:
        d = depth.get(addr, max_d + 1)  # unreachable → one trailing column
        x = d * (NODE_W + X_GAP)
        y = col_y.get(d, 0)
        positions[addr] = (x, y)
        col_y[d] = y + _est_node_height(cfg, addr) + Y_GAP
    return positions


# ── per-function focus guard ─────────────────────────────────────────────────


def _find_graph_tool(
    app: GuiApp, view_kind: str, path: str, func_name: str
) -> Tool | None:
    """The open window for (kind, path, function), or ``None``."""
    for tool in app.tools:
        ctl = tool.controller
        if (
            getattr(ctl, "view_kind", None) == view_kind
            and getattr(ctl, "path", None) == path
            and getattr(ctl, "func_name", None) == func_name
        ):
            return tool
    return None


# ── call-flow window ─────────────────────────────────────────────────────────


class CallFlowController:
    """One call-flow tool window over a ``CallGraph`` snapshot."""

    view_kind = "callflow"

    def __init__(
        self,
        app: GuiApp,
        path: str,
        graph: CallGraph,
        func_name: str,
        *,
        window: int | str,
        status_tag: int | str,
        tree_tag: int | str,
        ns: str,
    ) -> None:
        self.app = app
        self.path = path
        self.graph = graph
        self.func_name = func_name
        self.window = window
        self.status_tag = status_tag
        self.tree_tag = tree_tag
        self.ns = ns
        self._history: list[str] = []
        self._install_keys()

    # ── tree population ───────────────────────────────────────────────────

    def populate(self, func_name: str) -> None:
        """(Re)build the tree for *func_name* (the drill-in step)."""
        self.func_name = func_name
        graph = self.graph
        addr = graph.name_to_addr.get(func_name, 0)
        title = f"{func_name}  0x{addr:x}" if addr else f"{func_name}  external"

        dpg.delete_item(self.tree_tag, children_only=True)
        root = dpg.add_tree_node(label=title, parent=self.tree_tag, default_open=True)

        callees = graph.callees.get(func_name, [])
        callers = graph.callers.get(func_name, [])

        calls_node = dpg.add_tree_node(
            label=f"Calls ({len(callees)})",
            parent=root,
            default_open=True,
        )
        for name, c_addr in sorted(callees, key=lambda x: x[0]):
            self._add_leaf(calls_node, name, c_addr)

        called_node = dpg.add_tree_node(
            label=f"Called by ({len(callers)})",
            parent=root,
            default_open=True,
        )
        for name, c_addr in sorted(callers, key=lambda x: x[0]):
            self._add_leaf(called_node, name, c_addr)

        dpg.set_value(
            self.status_tag,
            "click a name to drill in  ·  b back  ·  Esc close",
        )

    def _add_leaf(self, parent, name: str, addr: int) -> None:
        label = f"{name}  0x{addr:x}" if addr else name
        dpg.add_selectable(
            label=label,
            parent=parent,
            user_data=name,
            callback=self._on_leaf,
        )

    def _on_leaf(self, sender, _app_data) -> None:
        name = dpg.get_item_user_data(sender)
        if name == self.func_name:
            return  # clicking the current function is a no-op
        self._history.append(self.func_name)
        self.populate(name)

    def back(self, *_args) -> None:
        """``b``: pop the drill-in history; at the root, close."""
        if self._history:
            self.populate(self._history.pop())
        else:
            self.window_close_requested()

    # ── keys ───────────────────────────────────────────────────────────────

    def _install_keys(self) -> None:
        with dpg.handler_registry(tag=f"callflow-keys-{self.ns}"):
            dpg.add_key_press_handler(
                key=KEY_ESCAPE, callback=self.window_close_requested
            )
            dpg.add_key_press_handler(key=dpg.mvKey_B, callback=self.back)

    # ── the shell's Controller protocol ───────────────────────────────────

    def dirty_edit_count(self) -> int:
        """Read-only view — never dirty."""
        return 0

    def on_event(self, event) -> None:
        """No workers — nothing to receive."""

    def window_close_requested(self, *_args) -> None:
        self.app.close_tool_for(self)

    def shutdown(self) -> None:
        if dpg.does_item_exist(f"callflow-keys-{self.ns}"):
            dpg.delete_item(f"callflow-keys-{self.ns}")


# ── CFG window ───────────────────────────────────────────────────────────────


class CFGController:
    """One CFG tool window over a ``ControlFlowGraph`` snapshot."""

    view_kind = "cfg"

    def __init__(
        self,
        app: GuiApp,
        path: str,
        cfg: ControlFlowGraph,
        *,
        window: int | str,
        status_tag: int | str,
        ns: str,
    ) -> None:
        self.app = app
        self.path = path
        self.cfg = cfg
        self.func_name = cfg.func_name
        self.window = window
        self.status_tag = status_tag
        self.ns = ns
        self.editor_tag: int | str | None = None
        self.node_tags: dict[int, int | str] = {}  # block addr → node tag
        self._links: dict[int | str, tuple[int | str, int | str, str]] = {}
        self._link_themes: dict[str, int | str] = {}  # edge type → theme tag
        self._install_keys()

    # ── node-editor construction (read-only) ──────────────────────────────

    def build_editor(self, content: int | str) -> None:
        """Build the CFG node editor: one node per block, one link per
        in-function edge. Nodes are fixed (``draggable=False``); a link
        detached by mouse self-heals (see ``_on_delink``)."""
        cfg = self.cfg
        order = cfg_blocks_ordered(cfg)
        positions = cfg_layout(cfg)
        block_num = {addr: i + 1 for i, addr in enumerate(order)}

        editor = dpg.add_node_editor(
            tag=f"{self.ns}-editor",
            parent=content,
            minimap=True,
            minimap_location=dpg.mvNodeMiniMap_Location_BottomRight,
            delink_callback=self._on_delink,
        )
        self.editor_tag = editor
        dpg.bind_item_theme(editor, self._editor_theme())

        # one input pin per node (the link endpoint), created with its node
        out_pins: list[tuple[int | str, int, str, int | str]] = []
        # ^ (pin tag, target addr, edge type, source block addr)

        for addr in order:
            block = cfg.blocks[addr]
            n = block_num[addr]
            entry_s = " ⬤ entry" if addr == cfg.entry else ""
            node = dpg.add_node(
                tag=f"{self.ns}-node-{addr}",
                label=f"Block #{n}  0x{addr:x}{entry_s}",
                parent=editor,
                pos=list(positions[addr]),
                draggable=False,  # read-only: blocks can't be moved
            )
            self.node_tags[addr] = node
            if addr == cfg.entry:
                dpg.bind_item_theme(node, self._entry_theme())

            with dpg.node_attribute(
                tag=f"{self.ns}-in-{addr}",
                parent=node,
                attribute_type=dpg.mvNode_Attr_Input,
                shape=dpg.mvNode_PinShape_CircleFilled,
            ):
                dpg.add_text("in", color=COL_DIM)

            with dpg.node_attribute(parent=node, attribute_type=dpg.mvNode_Attr_Static):
                for insn in block.instrs:
                    dpg.add_text(_insn_text(insn), color=COL_TEXT)

            for i, (tgt, etype) in enumerate(block.succs):
                with dpg.node_attribute(
                    tag=f"{self.ns}-out-{addr}-{i}",
                    parent=node,
                    attribute_type=dpg.mvNode_Attr_Output,
                    shape=dpg.mvNode_PinShape_CircleFilled,
                ):
                    dpg.add_text(
                        self._edge_label(addr, tgt, etype, block_num),
                        color=edge_color(etype),
                    )
                if tgt in cfg.blocks and etype not in ("ret", "indirect"):
                    # NB: ret/indirect edges carry the sentinel target 0,
                    # which would alias a block at address 0 — never linked.
                    out_pins.append((f"{self.ns}-out-{addr}-{i}", tgt, etype, addr))

        # links + per-edge-type colors
        for pin, tgt, etype, _src in out_pins:
            in_pin = f"{self.ns}-in-{tgt}"
            link = dpg.add_node_link(pin, in_pin, parent=editor)
            theme = self._link_theme(etype)
            dpg.bind_item_theme(link, theme)
            self._links[link] = (pin, in_pin, etype)

        dpg.set_value(
            self.status_tag,
            f"{len(cfg.blocks)} blocks  ·  read-only (nodes fixed)  ·  "
            "drag canvas to pan  ·  wheel zooms  ·  Esc close",
        )

    @staticmethod
    def _edge_label(src: int, tgt: int, etype: str, block_num: dict[int, int]) -> str:
        """Output-pin label: plain for target-less edges, annotated else."""
        if etype in ("ret", "indirect"):
            return etype
        tgt_num = block_num.get(tgt)
        tgt_s = f"Block #{tgt_num}" if tgt_num else f"0x{tgt:x} (external)"
        back = " (back-edge)" if tgt <= src and tgt in block_num else ""
        return f"{etype} → {tgt_s}{back}"

    def _editor_theme(self):
        with dpg.theme() as theme, dpg.theme_component(dpg.mvNodeEditor):
            dpg.add_theme_color(
                dpg.mvNodeCol_GridBackground,
                COL_GRID_BG,
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_GridLine,
                COL_GRID_LINE,
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_style(
                dpg.mvNodeStyleVar_LinkThickness,
                2.0,
                category=dpg.mvThemeCat_Nodes,
            )
        return theme

    def _entry_theme(self):
        with dpg.theme() as theme, dpg.theme_component(dpg.mvNode):
            dpg.add_theme_color(
                dpg.mvNodeCol_TitleBar,
                COL_ENTRY,
                category=dpg.mvThemeCat_Nodes,
            )
        return theme

    def _link_theme(self, etype: str):
        theme = self._link_themes.get(etype)
        if theme is None:
            with dpg.theme() as theme, dpg.theme_component(dpg.mvNodeLink):
                dpg.add_theme_color(
                    dpg.mvNodeCol_Link,
                    edge_color(etype),
                    category=dpg.mvThemeCat_Nodes,
                )
            self._link_themes[etype] = theme
        return theme

    def _on_delink(self, sender, app_data) -> None:
        """A link was detached by mouse (DPG deletes it) — re-add it from
        the endpoint map so the snapshot's topology can't be broken."""
        spec = self._links.pop(app_data, None)
        if spec is None or self.editor_tag is None:
            return
        out, inp, etype = spec
        link = dpg.add_node_link(out, inp, parent=self.editor_tag)
        dpg.bind_item_theme(link, self._link_theme(etype))
        self._links[link] = spec

    # ── the shell's Controller protocol ───────────────────────────────────

    def dirty_edit_count(self) -> int:
        """Read-only view — never dirty."""
        return 0

    def on_event(self, event) -> None:
        """No workers — nothing to receive."""

    def window_close_requested(self, *_args) -> None:
        self.app.close_tool_for(self)

    def shutdown(self) -> None:
        if dpg.does_item_exist(f"cfg-keys-{self.ns}"):
            dpg.delete_item(f"cfg-keys-{self.ns}")

    def _install_keys(self) -> None:
        with dpg.handler_registry(tag=f"cfg-keys-{self.ns}"):
            dpg.add_key_press_handler(
                key=KEY_ESCAPE, callback=self.window_close_requested
            )


# ── openers ──────────────────────────────────────────────────────────────────


def _build_window(app: GuiApp, path: str, title: str):
    """The shared D4 tool-window skeleton: content child + status strip."""
    ns = app.next_ns()
    window = f"gui-tool-{ns}"
    content = f"{window}-content"
    status_tag = f"{window}-status"
    with dpg.window(
        tag=window,
        label=title,
        width=TABLE_WIN_W,
        height=TABLE_WIN_H,
        min_size=[TOOL_MIN_W, TOOL_MIN_H],
        no_collapse=True,
        on_close=lambda *_a: _tool_window_closed(app, window),
    ):
        dpg.add_child_window(tag=content, border=False, height=-STATUS_RESERVE)
        dpg.add_separator()
        dpg.add_text("", tag=status_tag)
    return ns, window, content, status_tag


def open_call_flow(app: GuiApp, path: str, graph: CallGraph, func_name: str) -> None:
    """Open the call-flow window for *func_name* (focus-guarded per function)."""
    existing = _find_graph_tool(app, "callflow", path, func_name)
    if existing is not None:
        dpg.configure_item(existing.window, show=True)
        dpg.focus_item(existing.window)
        return
    ns, window, content, status_tag = _build_window(
        app, path, f"Call flow — {func_name}"
    )
    tree_tag = f"{ns}-tree"
    dpg.add_group(tag=tree_tag, parent=content)
    controller = CallFlowController(
        app,
        path,
        graph,
        func_name,
        window=window,
        status_tag=status_tag,
        tree_tag=tree_tag,
        ns=ns,
    )
    controller.populate(func_name)
    app.tools.append(
        Tool(
            window=window,
            status_tag=status_tag,
            controller=controller,
            grids=(),
            tick=None,
        )
    )


def open_cfg(app: GuiApp, path: str, cfg: ControlFlowGraph) -> None:
    """Open the CFG window for *cfg* (focus-guarded per function)."""
    existing = _find_graph_tool(app, "cfg", path, cfg.func_name)
    if existing is not None:
        dpg.configure_item(existing.window, show=True)
        dpg.focus_item(existing.window)
        return
    n = len(cfg.blocks)
    title = f"CFG — {cfg.func_name}  ·  {n} block{'s' if n != 1 else ''}"
    ns, window, content, status_tag = _build_window(app, path, title)
    controller = CFGController(
        app,
        path,
        cfg,
        window=window,
        status_tag=status_tag,
        ns=ns,
    )
    controller.build_editor(content)
    app.tools.append(
        Tool(
            window=window,
            status_tag=status_tag,
            controller=controller,
            grids=(),
            tick=None,
        )
    )
