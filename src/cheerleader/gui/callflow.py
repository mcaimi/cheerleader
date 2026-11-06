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

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from cheerleader.gui.const import KEY_ESCAPE, TABLE_WIN_H, TABLE_WIN_W
from cheerleader.gui.theme import (
    COL_DIM,
    COL_ENTRY,
    COL_GRID_BG,
    COL_GRID_LINE,
    COL_TEXT,
    EDGE_COLORS,
)
from cheerleader.gui.tooling import (
    ReadOnlyController,
    build_tool_window,
    find_tool,
    focus_tool,
)
from cheerleader.libs.cfg import CallGraph, ControlFlowGraph
from cheerleader.libs.types import DisasmInstruction

if TYPE_CHECKING:
    from cheerleader.gui.app import GuiApp

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    dpg = None  # type: ignore[assignment]

def edge_color(etype: str) -> tuple[int, int, int, int]:
    """DPG color for a CFG edge type (TUI ``_EDGE_STYLE`` parity)."""
    return EDGE_COLORS.get(etype, COL_DIM)


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


def _bfs_depths(cfg: ControlFlowGraph) -> dict[int, int]:
    """BFS depth of every block reachable from the entry (entry = 0)."""
    depth: dict[int, int] = {cfg.entry: 0} if cfg.entry in cfg.blocks else {}
    queue = deque(depth)
    while queue:
        addr = queue.popleft()
        for tgt, _ in cfg.blocks[addr].succs:
            if tgt in cfg.blocks and tgt not in depth:
                depth[tgt] = depth[addr] + 1
                queue.append(tgt)
    return depth


def cfg_blocks_ordered(cfg: ControlFlowGraph) -> list[int]:
    """Block addresses in display order: BFS depth from the entry, then
    address; unreachable blocks land in one trailing column (address
    order). Block ``#n`` is the *n*-th entry of this list (1-based)."""
    return _ordered(cfg, _bfs_depths(cfg))


def _ordered(cfg: ControlFlowGraph, depth: dict[int, int]) -> list[int]:
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
    depth = _bfs_depths(cfg)
    order = _ordered(cfg, depth)
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


# ── call-flow window ─────────────────────────────────────────────────────────


class CallFlowController(ReadOnlyController):
    """One call-flow tool window over a ``CallGraph`` snapshot."""

    view_kind = "callflow"
    keys_prefix = "callflow"

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
        self.install_keys(
            [(KEY_ESCAPE, self.window_close_requested), (dpg.mvKey_B, self.back)]
        )


# ── CFG window ───────────────────────────────────────────────────────────────


class CFGController(ReadOnlyController):
    """One CFG tool window over a ``ControlFlowGraph`` snapshot."""

    view_kind = "cfg"
    keys_prefix = "cfg"

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

    def _install_keys(self) -> None:
        self.install_keys([(KEY_ESCAPE, self.window_close_requested)])


# ── openers ──────────────────────────────────────────────────────────────────


def open_call_flow(app: GuiApp, path: str, graph: CallGraph, func_name: str) -> None:
    """Open the call-flow window for *func_name* (focus-guarded per function)."""
    # focus guard per (kind, path, function)
    existing = find_tool(app, view_kind="callflow", path=path, func_name=func_name)
    if existing is not None:
        focus_tool(existing)
        return
    ns, window, content, status_tag = build_tool_window(
        app, f"Call flow — {func_name}", TABLE_WIN_W, TABLE_WIN_H
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
    app.register_tool(window, status_tag, controller)


def open_cfg(app: GuiApp, path: str, cfg: ControlFlowGraph) -> None:
    """Open the CFG window for *cfg* (focus-guarded per function)."""
    existing = find_tool(app, view_kind="cfg", path=path, func_name=cfg.func_name)
    if existing is not None:
        focus_tool(existing)
        return
    n = len(cfg.blocks)
    title = f"CFG — {cfg.func_name}  ·  {n} block{'s' if n != 1 else ''}"
    ns, window, content, status_tag = build_tool_window(
        app, title, TABLE_WIN_W, TABLE_WIN_H
    )
    controller = CFGController(
        app,
        path,
        cfg,
        window=window,
        status_tag=status_tag,
        ns=ns,
    )
    controller.build_editor(content)
    app.register_tool(window, status_tag, controller)
