"""Tests for CallGraph function bounds and the shared instruction slicer.

Covers ``func_bounds`` (module-level), ``CallGraph.func_range`` (public API
replacing the old ``_sorted_addrs`` reach-in), and ``slice_function_instrs``.
"""

from __future__ import annotations

import pytest

from cheerleader.libs.cfg import (
    CallGraph,
    func_bounds,
    slice_function_instrs,
)
from cheerleader.libs.types import DisasmInstruction


def make_graph(sorted_addrs: list[int], named: dict[int, str] | None = None) -> CallGraph:
    named = named or {}
    addr_to_name = {a: named.get(a, f"sub_{a:x}") for a in sorted_addrs}
    return CallGraph(
        addr_to_name=addr_to_name,
        name_to_addr={v: k for k, v in addr_to_name.items()},
        callees={n: [] for n in addr_to_name.values()},
        callers={n: [] for n in addr_to_name.values()},
        _sorted_addrs=list(sorted_addrs),
    )


def make_insn(addr: int, size: int = 4) -> DisasmInstruction:
    return DisasmInstruction(
        addr=addr, size=size, mnemonic="nop", op_str="", raw=b"\x90" * size
    )


# ── func_bounds ───────────────────────────────────────────────────────────────


class TestFuncBounds:
    SORTED = [0x100, 0x200, 0x300, 0x400]

    def test_first_function(self):
        assert func_bounds(self.SORTED, 0x100) == (0x100, 0x200)

    def test_middle_function(self):
        assert func_bounds(self.SORTED, 0x200) == (0x200, 0x300)

    def test_last_function_next_is_none(self):
        assert func_bounds(self.SORTED, 0x400) == (0x400, None)

    def test_single_function(self):
        assert func_bounds([0x42], 0x42) == (0x42, None)

    def test_unknown_start_raises(self):
        with pytest.raises(ValueError):
            func_bounds(self.SORTED, 0x150)  # not a start

    def test_gap_raises(self):
        with pytest.raises(ValueError):
            func_bounds(self.SORTED, 0x500)  # past the end

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            func_bounds([], 0x100)


# ── CallGraph.func_range ──────────────────────────────────────────────────────


class TestCallGraphFuncRange:
    def test_delegates_to_bounds(self):
        graph = make_graph([0x100, 0x200, 0x300], named={0x100: "_foo", 0x200: "_bar"})
        assert graph.func_range(0x100) == (0x100, 0x200)
        assert graph.func_range(0x200) == (0x200, 0x300)
        assert graph.func_range(0x300) == (0x300, None)

    def test_unknown_raises(self):
        graph = make_graph([0x100])
        with pytest.raises(ValueError):
            graph.func_range(0x101)

    def test_works_for_sub_functions(self):
        # _sorted_addrs includes synthesized sub_ entries too
        graph = make_graph([0x100, 0x180, 0x200])
        assert graph.func_range(0x180) == (0x180, 0x200)


# ── slice_function_instrs ─────────────────────────────────────────────────────


class TestSliceFunctionInstrs:
    def test_basic_bounds(self):
        instrs = [make_insn(a) for a in (0x100, 0x104, 0x108, 0x200, 0x204)]
        got = slice_function_instrs(instrs, 0x100, 0x200)
        assert [i.addr for i in got] == [0x100, 0x104, 0x108]

    def test_open_ended_when_next_is_none(self):
        instrs = [make_insn(a) for a in (0x100, 0x104, 0x200)]
        got = slice_function_instrs(instrs, 0x100, None)
        assert [i.addr for i in got] == [0x100, 0x104, 0x200]

    def test_instruction_at_next_boundary_excluded(self):
        instrs = [make_insn(a) for a in (0x100, 0x104, 0x200)]
        got = slice_function_instrs(instrs, 0x100, 0x200)
        assert all(i.addr < 0x200 for i in got)

    def test_empty_result(self):
        instrs = [make_insn(a) for a in (0x200, 0x204)]
        assert slice_function_instrs(instrs, 0x100, 0x200) == []

    def test_empty_input(self):
        assert slice_function_instrs([], 0x100, None) == []

    def test_preserves_instruction_objects(self):
        instrs = [make_insn(0x100), make_insn(0x104)]
        got = slice_function_instrs(instrs, 0x100, None)
        assert got[0] is instrs[0] and got[1] is instrs[1]


# ── bounds + slicer compose ───────────────────────────────────────────────────


class TestBoundsAndSliceCompose:
    def test_full_pipeline(self):
        """The exact pattern used by DisasmTab._open_cfg and
        FuncReversingTab._show_function: bounds from a sorted addr list,
        then the instruction slice in between."""
        sorted_addrs = [0x100, 0x200, 0x400]
        instrs = [make_insn(a) for a in (0x100, 0x104, 0x1FC, 0x200, 0x204, 0x3F8, 0x400)]

        start, nxt = func_bounds(sorted_addrs, 0x200)
        assert start == 0x200 and nxt == 0x400
        got = slice_function_instrs(instrs, start, nxt)
        assert [i.addr for i in got] == [0x200, 0x204, 0x3F8]

        start, nxt = func_bounds(sorted_addrs, 0x400)
        got = slice_function_instrs(instrs, start, nxt)
        assert [i.addr for i in got] == [0x400]

    def test_superset_addr_list(self):
        """FuncReversingTab._sorted_func_addrs is a superset of the graph's
        addresses (sub_ callees/callers merged) — bounds must work on it too."""
        graph = make_graph([0x100, 0x200])
        superset = [0x100, 0x150, 0x200, 0x250]  # + synthesized sub_ starts
        # graph bounds: 0x150 is not a graph function
        with pytest.raises(ValueError):
            graph.func_range(0x150)
        # but bounds on the superset work
        assert func_bounds(superset, 0x150) == (0x150, 0x200)
