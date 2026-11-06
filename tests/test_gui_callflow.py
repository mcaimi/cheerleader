"""Headless tests for the D4 call-flow + CFG windows.

Covers the pure ``cfg_lines`` renderer (TUI ``_render_cfg`` parity: BFS
block order from the entry, unreachable blocks appended by address,
boxed instruction rows, colored successor edges with back-edge /
external annotations, predecessor lists, truncation), the call-flow
tree controller (populate, drill-in, back, close-at-root), the CFG
window, the per-function focus guards, independence from the parent
Disassembly window, teardown — and the Disassembly window's ``c``/``f``
wiring with its TUI-parity guards.

The CFG fixture is built with the real ``build_cfg`` over hand-made
arm64-ish instructions (a cbz/b/ret loop → 4 blocks, the ``b`` forming
a back-edge); the call-graph fixture is a hand-built ``CallGraph``.
"""

import struct

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg

from cheerleader.gui.app import GuiApp
from cheerleader.gui.callflow import (
    COL_DIM,
    NODE_W,
    X_GAP,
    _insn_text,
    cfg_blocks_ordered,
    cfg_layout,
    edge_color,
    open_call_flow,
    open_cfg,
)
from cheerleader.gui.disasm import open_disassembly
from cheerleader.libs.cfg import (
    CallGraph,
    CFGBlock,
    ControlFlowGraph,
    build_cfg,
)
from cheerleader.libs.types import BinaryInfo, DisasmInstruction, Section, Segment

BASE = 0x100000000
NOP = b"\x1f\x20\x03\xd5"


@pytest.fixture()
def dpg_context():
    dpg.create_context()
    yield
    dpg.destroy_context()


def _insn(addr: int, mnemonic: str = "nop", op_str: str = "") -> DisasmInstruction:
    return DisasmInstruction(
        addr=addr, size=4, mnemonic=mnemonic, op_str=op_str, raw=NOP
    )


def _loop_cfg() -> ControlFlowGraph:
    """A real 4-block CFG: entry → cbz(cond)/fall → b(back-edge) / ret."""
    instrs = [
        _insn(0x100, "stp", "x29, x30, [sp, #-16]!"),
        _insn(0x104, "cbz", "x0, #0x118"),
        _insn(0x108, "add", "x0, x0, #1"),
        _insn(0x10C, "b", "#0x104"),
        _insn(0x118, "mov", "x0, #1"),
        _insn(0x11C, "ret"),
    ]
    return build_cfg(instrs, "_loop", 0x100)


def _hand_cfg() -> ControlFlowGraph:
    """Hand-built CFG exercising every edge type + an unreachable block."""
    blocks = {
        0x0: CFGBlock(0x0, [_insn(0x0)], succs=[(0x10, "fall")], preds=[0x30]),
        0x10: CFGBlock(
            0x10,
            [_insn(0x10, "cbz", "x0, #0x20")],
            succs=[(0x20, "cond"), (0x99, "jump")],
            preds=[0x0],
        ),
        0x20: CFGBlock(0x20, [_insn(0x20, "ret")], succs=[(0, "ret")], preds=[0x10]),
        0x30: CFGBlock(0x30, [_insn(0x30, "b", "#0x0")], succs=[(0x0, "jump")]),
        0x40: CFGBlock(0x40, [_insn(0x40, "br", "x8")], succs=[(0, "indirect")]),
    }
    return ControlFlowGraph(func_name="_f", func_addr=0x0, blocks=blocks, entry=0x0)


def _graph() -> CallGraph:
    """_main@0x100 (calls _printf external, _helper, itself), _helper@0x200."""
    a2n = {0x100: "_main", 0x200: "_helper", 0x300: "_loop"}
    return CallGraph(
        addr_to_name=a2n,
        name_to_addr={v: k for k, v in a2n.items()},
        callees={
            "_main": [("_helper", 0x200), ("_printf", 0), ("_main", 0x100)],
            "_helper": [("_main", 0x100)],
            "_loop": [],
        },
        callers={
            "_main": [("_helper", 0x200)],
            "_helper": [("_main", 0x100)],
            "_printf": [("_main", 0x100)],
            "_loop": [],
        },
        _sorted_addrs=sorted(a2n),
    )


def _tree_texts(tag: int | str) -> list[str]:
    """Labels of the tree's root → section nodes → leaves, flattened."""
    out = []
    for root in dpg.get_item_children(tag)[1]:
        out.append(dpg.get_item_configuration(root)["label"])
        for section in dpg.get_item_children(root)[1]:
            out.append(dpg.get_item_configuration(section)["label"])
            for leaf in dpg.get_item_children(section)[1]:
                out.append(dpg.get_item_configuration(leaf)["label"])
    return out


def _leaf_tags(tag: int | str) -> list[int]:
    leaves = []
    for root in dpg.get_item_children(tag)[1]:
        for section in dpg.get_item_children(root)[1]:
            leaves.extend(dpg.get_item_children(section)[1])
    return leaves


# ── pure layout ───────────────────────────────────────────────────────────────


def test_edge_colors_match_tui() -> None:
    """fall/cond/jump/indirect/ret → green/yellow/blue/dim/red (TUI parity)."""
    assert edge_color("fall") == (120, 200, 120, 255)
    assert edge_color("cond") == (250, 210, 80, 255)
    assert edge_color("jump") == (110, 170, 255, 255)
    assert edge_color("indirect") == COL_DIM
    assert edge_color("ret") == (240, 120, 120, 255)
    assert edge_color("??") == COL_DIM  # unknown types fall back to dim


def test_cfg_blocks_ordered_depth_then_addr() -> None:
    """Display order: BFS depth from the entry, ties by address."""
    # hand cfg: entry 0x0 → 0x10 (fall) → 0x20 (cond+ext); 0x30/0x40 unreached
    assert cfg_blocks_ordered(_hand_cfg()) == [0x0, 0x10, 0x20, 0x30, 0x40]
    # loop cfg: 0x100(0) → 0x104(1) → {0x108, 0x118}(2)
    assert cfg_blocks_ordered(_loop_cfg()) == [0x100, 0x104, 0x108, 0x118]


def test_cfg_layout_columns_by_depth() -> None:
    """BFS depth → column (x); entry at the origin."""
    layout = cfg_layout(_loop_cfg())
    assert layout[0x100] == (0, 0)  # entry at origin
    assert layout[0x104][0] == NODE_W + X_GAP  # depth 1
    col2 = 2 * (NODE_W + X_GAP)
    assert layout[0x108][0] == col2 and layout[0x118][0] == col2  # depth 2


def test_cfg_layout_stacks_within_column() -> None:
    """Blocks sharing a column stack top-down by estimated height."""
    layout = cfg_layout(_loop_cfg())
    y_a, y_b = layout[0x108][1], layout[0x118][1]
    assert y_b > y_a  # 0x118 below 0x108 (higher address → lower stack slot)


def test_cfg_layout_unreached_trailing_column() -> None:
    """Unreachable blocks land in one column after the last reached one."""
    layout = cfg_layout(_hand_cfg())
    reached_max_x = max(p[0] for a, p in layout.items() if a in (0x0, 0x10, 0x20))
    assert layout[0x30][0] > reached_max_x
    assert layout[0x40][0] == layout[0x30][0]  # same trailing column
    assert layout[0x40][1] > layout[0x30][1]  # stacked by address


def test_cfg_layout_deterministic_and_total() -> None:
    """Same input → same coordinates; every block gets a position."""
    cfg = _loop_cfg()
    assert cfg_layout(cfg) == cfg_layout(cfg)
    assert set(cfg_layout(cfg)) == set(cfg.blocks)


def test_cfg_layout_empty() -> None:
    assert cfg_layout(ControlFlowGraph("_f", 0x0, {}, 0x0)) == {}


def test_insn_text_format() -> None:
    """Instruction rows keep the TUI text format (addr, hex, mnemonic, op)."""
    row = _insn_text(_insn(0x10, "cbz", "x0, #0x20"))
    assert row.startswith("  0x10  1f 20 03 d5")
    assert "cbz" in row and "x0, #0x20" in row


# ── call-flow window ──────────────────────────────────────────────────────────


def test_callflow_window_built(dpg_context) -> None:
    app = GuiApp()
    open_call_flow(app, "/x/t.bin", _graph(), "_main")
    assert len(app.tools) == 1
    tool = app.tools[0]
    ctl = tool.controller
    assert ctl.view_kind == "callflow" and ctl.func_name == "_main"
    assert dpg.does_item_exist(tool.window)
    assert "Call flow — _main" == dpg.get_item_configuration(tool.window)["label"]
    texts = _tree_texts(ctl.tree_tag)
    assert texts[0] == "_main  0x100"  # root title
    assert "Calls (3)" in texts and "Called by (1)" in texts
    # callees sorted by name (TUI parity: key=lambda x: x[0] over (name, addr))
    leaves = _leaf_tags(ctl.tree_tag)
    labels = [dpg.get_item_configuration(t)["label"] for t in leaves]
    assert labels[:3] == ["_helper  0x200", "_main  0x100", "_printf"]
    # leaves carry their function name for the drill-in
    printf_leaf = next(t for t in leaves if dpg.get_item_user_data(t) == "_printf")
    assert dpg.get_item_configuration(printf_leaf)["label"] == "_printf"


def test_callflow_external_title(dpg_context) -> None:
    """An external function (addr 0) gets the 'external' title + no Calls."""
    app = GuiApp()
    open_call_flow(app, "/x/t.bin", _graph(), "_printf")
    ctl = app.tools[0].controller
    texts = _tree_texts(ctl.tree_tag)
    assert texts[0] == "_printf  external"
    assert "Calls (0)" in texts and "Called by (1)" in texts


def test_callflow_drill_in_and_back(dpg_context) -> None:
    app = GuiApp()
    open_call_flow(app, "/x/t.bin", _graph(), "_main")
    ctl = app.tools[0].controller
    # click the _helper leaf (DPG sets values, then fires — mirror that)
    helper_leaf = next(
        t for t in _leaf_tags(ctl.tree_tag) if dpg.get_item_user_data(t) == "_helper"
    )
    dpg.get_item_callback(helper_leaf)(helper_leaf, None)
    assert ctl.func_name == "_helper"
    assert ctl._history == ["_main"]
    assert _tree_texts(ctl.tree_tag)[0] == "_helper  0x200"
    # b: back to _main, history empty
    ctl.back()
    assert ctl.func_name == "_main"
    assert ctl._history == []
    assert _tree_texts(ctl.tree_tag)[0] == "_main  0x100"


def test_callflow_click_current_is_noop(dpg_context) -> None:
    app = GuiApp()
    open_call_flow(app, "/x/t.bin", _graph(), "_main")
    ctl = app.tools[0].controller
    main_leaf = next(
        t for t in _leaf_tags(ctl.tree_tag) if dpg.get_item_user_data(t) == "_main"
    )
    dpg.get_item_callback(main_leaf)(main_leaf, None)
    assert ctl.func_name == "_main"
    assert ctl._history == []


def test_callflow_back_at_root_closes(dpg_context) -> None:
    app = GuiApp()
    open_call_flow(app, "/x/t.bin", _graph(), "_main")
    tool = app.tools[0]
    ctl = tool.controller
    ctl.back()
    assert not dpg.does_item_exist(tool.window)
    assert app.tools == []
    assert not dpg.does_item_exist(f"callflow-keys-{ctl.ns}")  # teardown ran


def test_callflow_focus_guard(dpg_context) -> None:
    app = GuiApp()
    open_call_flow(app, "/x/t.bin", _graph(), "_main")
    open_call_flow(app, "/x/t.bin", _graph(), "_main")  # same → focused, not duplicated
    assert len(app.tools) == 1
    open_call_flow(app, "/x/t.bin", _graph(), "_helper")  # other function → new window
    assert len(app.tools) == 2
    assert {t.controller.func_name for t in app.tools} == {"_main", "_helper"}


# ── CFG window (node editor) ──────────────────────────────────────────────────


def _nodes(ctl) -> list[int]:
    return [
        c
        for c in dpg.get_item_children(ctl.editor_tag)[1]
        if dpg.get_item_type(c) == "mvAppItemType::mvNode"
    ]


def _links(ctl) -> list[int]:
    # DPG quirk: links are parented to the editor but hidden from
    # get_item_children — assert via the controller's endpoint map.
    return [l for l in ctl._links if dpg.does_item_exist(l)]


def test_cfg_window_built(dpg_context) -> None:
    app = GuiApp()
    open_cfg(app, "/x/t.bin", _loop_cfg())
    assert len(app.tools) == 1
    tool = app.tools[0]
    ctl = tool.controller
    assert ctl.view_kind == "cfg" and ctl.func_name == "_loop"
    assert (
        dpg.get_item_configuration(tool.window)["label"] == "CFG — _loop  ·  4 blocks"
    )
    assert dpg.get_value(tool.status_tag) == (
        "4 blocks  ·  read-only (nodes fixed)  ·  drag canvas to pan  ·  "
        "wheel zooms  ·  Esc close"
    )
    assert dpg.get_item_type(ctl.editor_tag) == "mvAppItemType::mvNodeEditor"
    assert len(_nodes(ctl)) == 4


def test_cfg_window_node_labels_and_positions(dpg_context) -> None:
    app = GuiApp()
    open_cfg(app, "/x/t.bin", _loop_cfg())
    ctl = app.tools[0].controller
    labels = {
        a: dpg.get_item_configuration(n)["label"] for a, n in ctl.node_tags.items()
    }
    assert labels[0x100] == "Block #1  0x100 ⬤ entry"
    assert labels[0x104] == "Block #2  0x104"
    assert labels[0x118] == "Block #4  0x118"
    # node positions come from the pure layout
    layout = cfg_layout(_loop_cfg())
    for addr, node in ctl.node_tags.items():
        assert list(dpg.get_item_pos(node)) == list(layout[addr])


def test_cfg_window_readonly(dpg_context) -> None:
    """Nodes can't be dragged; the delink self-heal is wired."""
    app = GuiApp()
    open_cfg(app, "/x/t.bin", _loop_cfg())
    ctl = app.tools[0].controller
    assert all(dpg.get_item_configuration(n)["draggable"] is False for n in _nodes(ctl))
    assert dpg.get_item_configuration(ctl.editor_tag)["delink_callback"] is not None


def test_cfg_window_pins_and_rows(dpg_context) -> None:
    """Each node: 1 input pin, instruction rows, 1 output pin per succ."""
    app = GuiApp()
    open_cfg(app, "/x/t.bin", _loop_cfg())
    ctl = app.tools[0].controller
    # entry block: stp + (no succ pins beyond...) — check structure of 0x104
    node = ctl.node_tags[0x104]  # cbz block: cond + fall
    attrs = dpg.get_item_children(node)[1]
    types = [dpg.get_item_configuration(a)["attribute_type"] for a in attrs]
    # in=0, static=2, out=1, out=1
    assert types == [
        dpg.mvNode_Attr_Input,
        dpg.mvNode_Attr_Static,
        dpg.mvNode_Attr_Output,
        dpg.mvNode_Attr_Output,
    ]
    static_texts = [dpg.get_value(t) for t in dpg.get_item_children(attrs[1])[1]]
    assert static_texts == [_insn_text(_insn(0x104, "cbz", "x0, #0x118"))]
    pin_labels = [dpg.get_value(t) for t in dpg.get_item_children(attrs[2])[1]]
    pin_labels += [dpg.get_value(t) for t in dpg.get_item_children(attrs[3])[1]]
    assert "cond → Block #4" in pin_labels
    assert "fall → Block #3" in pin_labels
    # ret block's pin is a bare "ret" (no target annotation)
    ret_node = ctl.node_tags[0x118]
    ret_attr = dpg.get_item_children(ret_node)[1][-1]
    assert dpg.get_value(dpg.get_item_children(ret_attr)[1][0]) == "ret"


def test_cfg_window_links(dpg_context) -> None:
    """One link per in-function edge, endpoint-mapped and edge-colored."""
    app = GuiApp()
    open_cfg(app, "/x/t.bin", _loop_cfg())
    ctl = app.tools[0].controller
    links = _links(ctl)
    assert len(links) == 4  # fall(0x100→0x104), cond+fall(0x104), jump(0x108→0x104)
    by_node = ctl.node_tags

    def _node_of(pin: int) -> int:
        return dpg.get_item_parent(pin)

    ends = {
        (
            dpg.get_item_configuration(l)["attr_1"],
            dpg.get_item_configuration(l)["attr_2"],
        )
        for l in links
    }
    # every link runs output-pin → the target block's input pin
    for out_pin, in_pin in ends:
        assert _node_of(in_pin) in by_node.values()
        assert _node_of(out_pin) in by_node.values()
    # distinct targets hit: 0x104 (twice), 0x108, 0x118
    in_targets = {_node_of(i) for _o, i in ends}
    assert in_targets == {by_node[0x104], by_node[0x108], by_node[0x118]}
    # per-edge-type themes bound
    for link in links:
        assert dpg.get_item_theme(link) is not None
        etype = ctl._links[link][2]
        assert etype in ("fall", "cond", "jump")


def test_cfg_window_external_and_backedge_pins(dpg_context) -> None:
    """External targets and back-jumps annotate the pin label; no link."""
    app = GuiApp()
    open_cfg(app, "/x/t.bin", _hand_cfg())
    ctl = app.tools[0].controller
    # 0x10 block: cond → #3 (link), jump → 0x99 external (no link)
    # 0x30 block: jump → #1 (back-edge label, link)
    link_count = len(_links(ctl))
    # edges: fall(0x0→0x10), cond(0x10→0x20), jump back(0x30→0x0) = 3 links;
    # external 0x99 has none, ret/indirect have none
    assert link_count == 3
    texts = []
    for node in _nodes(ctl):
        for attr in dpg.get_item_children(node)[1]:
            if (
                dpg.get_item_configuration(attr)["attribute_type"]
                == dpg.mvNode_Attr_Output
            ):
                for t in dpg.get_item_children(attr)[1]:
                    texts.append(dpg.get_value(t))
    assert "jump → 0x99 (external)" in texts
    assert "jump → Block #1 (back-edge)" in texts
    assert "fall → Block #2" in texts
    assert "cond → Block #3" in texts
    assert "ret" in texts and "indirect" in texts


def test_cfg_window_entry_theme(dpg_context) -> None:
    app = GuiApp()
    open_cfg(app, "/x/t.bin", _loop_cfg())
    ctl = app.tools[0].controller
    assert dpg.get_item_theme(ctl.node_tags[0x100]) is not None  # entry themed
    assert dpg.get_item_theme(ctl.node_tags[0x104]) is None  # others default


def test_cfg_window_delink_self_heals(dpg_context) -> None:
    """A detached link (deleted by DPG) is re-added from the endpoint map."""
    app = GuiApp()
    open_cfg(app, "/x/t.bin", _loop_cfg())
    ctl = app.tools[0].controller
    link = _links(ctl)[0]
    spec = ctl._links[link]
    # real detach: DPG deletes the link item, then fires delink_callback
    dpg.delete_item(link)
    assert not dpg.does_item_exist(link)
    ctl._on_delink(ctl.editor_tag, link)  # app_data = the deleted link's uuid
    assert len(_links(ctl)) == 4  # topology restored
    new_link = next(l for l in _links(ctl) if ctl._links[l] == spec)
    assert dpg.get_item_theme(new_link) is not None  # re-colored


def test_cfg_focus_guard(dpg_context) -> None:
    app = GuiApp()
    open_cfg(app, "/x/t.bin", _loop_cfg())
    open_cfg(app, "/x/t.bin", _loop_cfg())  # same function → focused
    assert len(app.tools) == 1
    other = ControlFlowGraph("_other", 0x500, {}, 0x500)
    open_cfg(app, "/x/t.bin", other)  # different function → new window
    assert len(app.tools) == 2


# ── parent-close independence + disasm c/f wiring ─────────────────────────────


def _macho_text(tmp_path) -> str:
    header = b"\xcf\xfa\xed\xfe" + struct.pack(
        "<IIIIIII", 0x0100000C, 0, 2, 1, 152, 0, 0
    )
    segment = (
        struct.pack("<II", 0x19, 152)
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack("<QQQQIIII", BASE, 0x1000, 0, 0x1000, 5, 5, 1, 0)
    )
    section = (
        b"__text".ljust(16, b"\x00")
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack("<QQIIIIIIII", BASE, 0x100, 0xB8, 3, 0, 0, 0, 0, 0, 0)
    )
    path = tmp_path / "text.bin"
    path.write_bytes(header + segment + section + NOP * 64)
    return str(path)


def _info(path: str) -> BinaryInfo:
    info = BinaryInfo(path=path, arch="arm64", bits=64, file_type="MH_EXECUTE")
    info.segments.append(
        Segment(
            "__TEXT",
            BASE,
            0x1000,
            0,
            0x1000,
            5,
            5,
            sections=[Section("__text", "__TEXT", BASE, 0x100, 0x40, 3, 0, 0, 0)],
        )
    )
    return info


def _settle(app: GuiApp, ctl) -> None:
    worker = ctl.busy.worker
    if worker is not None and worker.thread is not None:
        worker.thread.join(timeout=10.0)
    for event in app.bridge.drain():
        app.dispatch(event)


def _graph_at_base() -> CallGraph:
    a2n = {BASE: "_main", BASE + 0x40: "_helper"}
    return CallGraph(
        addr_to_name=a2n,
        name_to_addr={v: k for k, v in a2n.items()},
        callees={"_main": [("_helper", BASE + 0x40)], "_helper": []},
        callers={"_helper": [("_main", BASE)], "_main": []},
        _sorted_addrs=sorted(a2n),
    )


def test_disasm_c_and_f_open_windows(dpg_context, tmp_path) -> None:
    """c/f from the Disassembly window open the D4 windows as tools."""
    path = _macho_text(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = _info(path)
    open_disassembly(app, path)
    disasm = app.tools[0]
    ctl = disasm.controller
    _settle(app, ctl)
    assert len(ctl.grid.instrs()) == 64
    assert "c → call flow" in dpg.get_value(ctl.status_tag)
    ctl.call_graph = _graph_at_base()  # the 64-NOP fixture has no symbols

    ctl._open_call_flow()
    flow = [t for t in app.tools if t is not disasm]
    assert len(flow) == 1 and flow[0].controller.view_kind == "callflow"

    ctl._open_cfg()
    graphs = [t for t in app.tools if t is not disasm]
    assert len(graphs) == 2 and graphs[1].controller.view_kind == "cfg"

    # closing the parent Disassembly window leaves the D4 windows open
    app.close_tool(disasm)
    assert not dpg.does_item_exist(disasm.window)
    assert len(app.tools) == 2
    assert all(dpg.does_item_exist(t.window) for t in app.tools)


def test_disasm_c_f_guards(dpg_context, tmp_path) -> None:
    """TUI-parity guard messages land in the status strip."""
    path = _macho_text(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = _info(path)
    open_disassembly(app, path)
    ctl = app.tools[0].controller
    _settle(app, ctl)

    ctl._open_call_flow()  # graph exists but was built by the settle — no symbols
    assert "No function symbol found at 0x" in dpg.get_value(ctl.status_tag)
    ctl.call_graph = None
    ctl._open_call_flow()
    assert dpg.get_value(ctl.status_tag) == "Disassemble a section first"
    ctl._open_cfg()
    assert dpg.get_value(ctl.status_tag) == "Disassemble a section first"

    ctl.call_graph = _graph_at_base()  # _main@BASE, but the cursor row is stripped
    ctl.grid.set_instrs([_insn(0xABC, "nop")])  # single row at 0xABC
    ctl._open_call_flow()
    assert dpg.get_value(ctl.status_tag) == (
        "No function symbol found at 0xabc — binary may be stripped"
    )
    ctl._open_cfg()
    assert dpg.get_value(ctl.status_tag) == (
        "No function symbol at 0xabc — binary may be stripped"
    )


def test_key_handler_registries(dpg_context, tmp_path) -> None:
    """Esc+c+f on disasm, Esc+b on callflow, Esc on cfg."""
    path = _macho_text(tmp_path)
    app = GuiApp()
    app.current_file = path
    app.current_info = _info(path)
    open_disassembly(app, path)
    dctl = app.tools[0].controller
    assert len(dpg.get_item_children(f"disasm-keys-{dctl.ns}")[1]) == 3

    open_call_flow(app, path, _graph(), "_main")
    fctl = app.tools[1].controller
    assert len(dpg.get_item_children(f"callflow-keys-{fctl.ns}")[1]) == 2

    open_cfg(app, path, _loop_cfg())
    cctl = app.tools[2].controller
    assert len(dpg.get_item_children(f"cfg-keys-{cctl.ns}")[1]) == 1


def test_find_graph_tool_matching(dpg_context) -> None:
    app = GuiApp()
    open_call_flow(app, "/x/t.bin", _graph(), "_main")
    tool = app.tools[0]
    from cheerleader.gui.callflow import _find_graph_tool

    assert _find_graph_tool(app, "callflow", "/x/t.bin", "_main") is tool
    assert _find_graph_tool(app, "callflow", "/x/t.bin", "_helper") is None
    assert _find_graph_tool(app, "callflow", "/x/other.bin", "_main") is None
    assert _find_graph_tool(app, "cfg", "/x/t.bin", "_main") is None
