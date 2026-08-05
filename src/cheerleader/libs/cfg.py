"""Control-flow graph and call graph construction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cheerleader.libs.types import BinaryInfo, DisasmInstruction, N_TYPE, N_SECT

CALL_MNEMONICS = frozenset({"call", "bl", "blx", "blr"})
_CALL_TARGET_RE = re.compile(r"#?(0x[0-9a-fA-F]+)")

_CFG_TARGET_RE = re.compile(r"#?(0x[0-9a-fA-F]+)")
_CFG_RET   = frozenset({"ret", "retq", "retn", "eret", "iret", "iretq"})
_CFG_UNCOND = frozenset({"jmp", "b", "br"})
_CFG_COND  = frozenset({
    "je", "jne", "jz", "jnz", "jl", "jg", "jle", "jge",
    "ja", "jb", "jae", "jbe", "jc", "jnc", "js", "jns",
    "jo", "jno", "jp", "jnp", "jcxz", "jecxz", "jrcxz",
    "cbz", "cbnz", "tbz", "tbnz",
    "loop", "loope", "loopne",
})


@dataclass
class CallGraph:
    addr_to_name: dict[int, str]                     # func start addr → symbol name
    name_to_addr: dict[str, int]                     # symbol name → func start addr
    callees: dict[str, list[tuple[str, int]]]        # name → [(callee_name, callee_addr)]
    callers: dict[str, list[tuple[str, int]]]        # name → [(caller_name, caller_addr)]
    _sorted_addrs: list[int] = field(default_factory=list)

    def func_at(self, addr: int) -> tuple[str, int] | None:
        """Return (name, start_addr) of the function whose body contains *addr*."""
        sa = self._sorted_addrs
        if not sa:
            return None
        lo, hi = 0, len(sa) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if sa[mid] <= addr:
                lo = mid + 1
            else:
                hi = mid - 1
        idx = lo - 1
        if idx < 0:
            return None
        a = sa[idx]
        name = self.addr_to_name.get(a)
        return (name, a) if name is not None else None


@dataclass
class CFGBlock:
    addr: int
    instrs: list[DisasmInstruction]
    succs: list[tuple[int, str]]            # (target_addr, "fall"|"jump"|"cond"|"ret"|"indirect")
    preds: list[int] = field(default_factory=list)


@dataclass
class ControlFlowGraph:
    func_name: str
    func_addr: int
    blocks: dict[int, "CFGBlock"]           # start_addr → block
    entry: int


def build_cfg(
    instrs: list[DisasmInstruction],
    func_name: str,
    func_addr: int,
) -> ControlFlowGraph:
    """Build a control-flow graph from instructions belonging to one function."""
    if not instrs:
        return ControlFlowGraph(func_name=func_name, func_addr=func_addr,
                                blocks={}, entry=func_addr)

    addr_set = {insn.addr for insn in instrs}

    # Pass 1 — identify basic-block leaders
    leaders: set[int] = {instrs[0].addr}
    for insn in instrs:
        m = insn.mnemonic.lower()
        is_cond  = m in _CFG_COND or m.startswith("b.")
        is_uncond = m in _CFG_UNCOND
        is_ret   = m in _CFG_RET
        if is_cond or is_uncond or is_ret:
            fall = insn.addr + insn.size
            if fall in addr_set:
                leaders.add(fall)
            tm = _CFG_TARGET_RE.search(insn.op_str)
            if tm:
                tgt = int(tm.group(1), 16)
                if tgt in addr_set:
                    leaders.add(tgt)

    sorted_leaders = sorted(leaders)
    leader_set = set(sorted_leaders)
    addr_to_idx = {insn.addr: i for i, insn in enumerate(instrs)}

    # Pass 2 — group instructions into blocks
    blocks: dict[int, CFGBlock] = {}
    for li, laddr in enumerate(sorted_leaders):
        start = addr_to_idx.get(laddr)
        if start is None:
            continue
        block_instrs: list[DisasmInstruction] = []
        idx = start
        while idx < len(instrs):
            cur = instrs[idx]
            if idx > start and cur.addr in leader_set:
                break
            block_instrs.append(cur)
            idx += 1
        blocks[laddr] = CFGBlock(addr=laddr, instrs=block_instrs, succs=[])

    # Pass 3 — compute successor edges
    for block in blocks.values():
        if not block.instrs:
            continue
        last = block.instrs[-1]
        m = last.mnemonic.lower()
        is_cond  = m in _CFG_COND or m.startswith("b.")
        is_uncond = m in _CFG_UNCOND
        is_ret   = m in _CFG_RET
        fall = last.addr + last.size
        if is_ret:
            block.succs.append((0, "ret"))
        elif is_uncond:
            tm = _CFG_TARGET_RE.search(last.op_str)
            if tm:
                block.succs.append((int(tm.group(1), 16), "jump"))
            else:
                block.succs.append((0, "indirect"))
        elif is_cond:
            tm = _CFG_TARGET_RE.search(last.op_str)
            if tm:
                block.succs.append((int(tm.group(1), 16), "cond"))
            if fall in addr_set:
                block.succs.append((fall, "fall"))
        else:
            if fall in addr_set:
                block.succs.append((fall, "fall"))

    # Pass 4 — back-fill predecessor lists
    for baddr, block in blocks.items():
        for tgt, _ in block.succs:
            if tgt in blocks:
                blocks[tgt].preds.append(baddr)

    return ControlFlowGraph(func_name=func_name, func_addr=func_addr,
                            blocks=blocks, entry=instrs[0].addr)


def build_call_graph(info: BinaryInfo, instrs: list[DisasmInstruction]) -> CallGraph:
    """Build a call graph from a disassembled instruction list."""
    addr_to_name: dict[int, str] = {}
    for sym in info.symbols:
        if not sym.stab and sym.addr and sym.name and (sym.sym_type & N_TYPE) == N_SECT:
            addr_to_name.setdefault(sym.addr, sym.name)
    for exp in info.exports:
        a, n = exp.get("addr", 0), exp.get("name", "")
        if a and n:
            addr_to_name.setdefault(a, n)

    graph = CallGraph(
        addr_to_name=addr_to_name,
        name_to_addr={v: k for k, v in addr_to_name.items()},
        callees={n: [] for n in addr_to_name.values()},
        callers={n: [] for n in addr_to_name.values()},
        _sorted_addrs=sorted(addr_to_name),
    )

    seen: set[tuple[str, str]] = set()
    for insn in instrs:
        if insn.mnemonic not in CALL_MNEMONICS:
            continue
        result = graph.func_at(insn.addr)
        if result is None:
            continue
        caller_name, caller_addr = result

        m = _CALL_TARGET_RE.search(insn.op_str)
        if m and "[" not in insn.op_str:
            tgt = int(m.group(1), 16)
            callee_name = addr_to_name.get(tgt, f"sub_{tgt:x}")
            callee_addr = tgt
        else:
            callee_name = f"<indirect:{insn.op_str.strip()}>"
            callee_addr = 0

        edge = (caller_name, callee_name)
        if edge in seen:
            continue
        seen.add(edge)

        graph.callees.setdefault(caller_name, []).append((callee_name, callee_addr))
        graph.callers.setdefault(callee_name, []).append((caller_name, caller_addr))

    return graph
