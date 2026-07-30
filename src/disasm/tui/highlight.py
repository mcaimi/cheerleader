"""Disassembly syntax highlighting and rendering utilities."""

from __future__ import annotations

import re

from rich.text import Text

from disasm.libs.cfg import ControlFlowGraph

_MNEM_RET = frozenset({"ret", "retq", "retn", "eret", "iret", "iretq"})
_MNEM_NOP = frozenset({"nop", "nopw", "nopl", "fnop"})
_MNEM_STACK = frozenset({"push", "pop", "pushf", "popf", "pushfq", "popfq"})
_MNEM_CMP = frozenset({
    "cmp", "test", "tst", "cmn", "fcmp", "ftst",
    "ucomisd", "ucomiss", "comisd", "comiss",
})
_MNEM_BRANCH = frozenset({
    "call", "bl", "blr", "blx", "br", "b",
    "jmp", "je", "jne", "jz", "jnz", "jl", "jg", "jle", "jge",
    "ja", "jb", "jae", "jbe", "jc", "jnc", "js", "jns", "jo", "jno",
    "jp", "jnp", "jcxz", "jecxz", "jrcxz",
    "cbz", "cbnz", "tbz", "tbnz",
})
_MNEM_LOGIC = frozenset({
    "and", "or", "xor", "not", "neg", "inc", "dec", "adc", "sbc",
    "madd", "msub", "sdiv", "udiv", "lsl", "lsr", "asr", "ror",
    "shl", "shr", "sar", "rol", "adcs", "subs", "adds", "ands",
    "orr", "eor", "bic", "mvn",
})

_X86_REG_RE = re.compile(
    r'\b('
    r'r(?:ax|bx|cx|dx|si|di|sp|bp|ip|flags)|'
    r'r(?:1[0-5]|[89])[bwd]?|'
    r'e(?:ax|bx|cx|dx|si|di|sp|bp|ip|flags)|'
    r'[abcd][xhl]|(?:si|di|sp|bp)l?|'
    r'(?:xmm|ymm|zmm)(?:[12]?\d|3[01])|'
    r'mm[0-7]|[cdefgs]s|cr[0-8]|dr[0-7]'
    r')\b',
    re.IGNORECASE,
)
_ARM_REG_RE = re.compile(
    r'\b('
    r'[xw](?:[12]?\d|30)|sp|lr|xzr|wzr|fp|pc|'
    r'[vqdsb](?:[12]?\d|3[01])(?:\.\d[bBhHsS])?'
    r')\b',
)
_HEX_RE = re.compile(r'#?-?0x[0-9a-fA-F]+')
_DEC_IMM_RE = re.compile(r'#-?\d+(?!\w)')

_EDGE_STYLE = {
    "fall":     ("green",   "fall"),
    "cond":     ("yellow",  "cond"),
    "jump":     ("blue",    "jump"),
    "indirect": ("dim",     "indirect"),
    "ret":      ("red",     "ret"),
}


def _colorize_mnemonic(mnemonic: str) -> Text:
    m = mnemonic.lower()
    if m in _MNEM_RET:
        return Text(mnemonic, style="bold red")
    if m in _MNEM_BRANCH or m.startswith("b.") or (m.startswith("j") and len(m) > 1):
        return Text(mnemonic, style="bold yellow")
    if m in _MNEM_NOP:
        return Text(mnemonic, style="dim")
    if m in _MNEM_STACK:
        return Text(mnemonic, style="cyan")
    if m in _MNEM_CMP:
        return Text(mnemonic, style="magenta")
    if m.startswith(("mov", "ldr", "str", "ldp", "stp", "lea", "ld", "st")):
        return Text(mnemonic, style="green")
    if m in _MNEM_LOGIC or m.startswith(("add", "sub", "mul", "imul", "div", "idiv")):
        return Text(mnemonic, style="blue")
    return Text(mnemonic)


def _colorize_operands(op_str: str, arch: str) -> Text:
    if not op_str:
        return Text("")
    reg_re = _ARM_REG_RE if "arm" in arch.lower() else _X86_REG_RE

    spans: list[tuple[int, int, str]] = []
    for m in _HEX_RE.finditer(op_str):
        spans.append((m.start(), m.end(), "yellow"))
    for m in _DEC_IMM_RE.finditer(op_str):
        spans.append((m.start(), m.end(), "yellow"))
    for m in reg_re.finditer(op_str):
        spans.append((m.start(), m.end(), "bright_green"))

    spans.sort(key=lambda x: x[0])
    deduped: list[tuple[int, int, str]] = []
    last = 0
    for s, e, style in spans:
        if s >= last:
            deduped.append((s, e, style))
            last = e

    result = Text()
    pos = 0
    for s, e, style in deduped:
        if pos < s:
            result.append(op_str[pos:s])
        result.append(op_str[s:e], style=style)
        pos = e
    if pos < len(op_str):
        result.append(op_str[pos:])
    return result


def _fmt_addr(v: int | None) -> str:
    if v is None:
        return "—"
    return f"0x{v:016x}"


def _fmt_size(v: int) -> str:
    if v == 0:
        return "0"
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{v} {unit}"
        v //= 1024
    return f"{v} TB"


def _esc_markup(s: str) -> str:
    return s.replace("[", r"\[")


def _render_cfg(cfg: ControlFlowGraph) -> str:
    """Render a ControlFlowGraph as a Rich markup string."""
    if not cfg.blocks:
        return "[dim]No blocks found.[/dim]"

    visited: list[int] = []
    seen: set[int] = set()
    queue = [cfg.entry]
    while queue:
        addr = queue.pop(0)
        if addr in seen or addr not in cfg.blocks:
            continue
        seen.add(addr)
        visited.append(addr)
        for tgt, _ in cfg.blocks[addr].succs:
            if tgt not in seen and tgt in cfg.blocks:
                queue.append(tgt)
    for addr in sorted(cfg.blocks):
        if addr not in seen:
            visited.append(addr)

    block_idx = {addr: i + 1 for i, addr in enumerate(visited)}
    BOX_W = 62
    inner = BOX_W - 2
    lines: list[str] = []

    for addr in visited:
        block = cfg.blocks[addr]
        idx = block_idx[addr]
        entry_tag = " [bold yellow]⬤ entry[/bold yellow]" if addr == cfg.entry else ""
        preds = block.preds
        pred_s = (
            "  [dim]← from " + ", ".join(f"#{block_idx[p]}" for p in preds if p in block_idx) + "[/dim]"
            if preds else ""
        )

        lines.append(f" [bold cyan]Block #{idx}[/bold cyan]  [dim]0x{addr:x}[/dim]{entry_tag}{pred_s}")
        lines.append(f" ┌{'─' * inner}┐")

        for insn in block.instrs:
            raw_hex = " ".join(f"{b:02x}" for b in insn.raw[:6])
            if len(insn.raw) > 6:
                raw_hex += "…"
            row = f"  [dim]0x{insn.addr:x}[/dim]  {raw_hex:<20}  {_esc_markup(insn.mnemonic):<8}  {_esc_markup(insn.op_str)}"
            plain_len = len(f"  0x{insn.addr:x}  {raw_hex:<20}  {insn.mnemonic:<8}  {insn.op_str}")
            if plain_len > inner:
                budget = inner - (plain_len - len(insn.op_str)) - 1
                row = (
                    f"  [dim]0x{insn.addr:x}[/dim]  {raw_hex:<20}  "
                    f"{_esc_markup(insn.mnemonic):<8}  "
                    f"{_esc_markup(insn.op_str[:max(0, budget)])}…"
                )
            lines.append(f" │{row}")
            lines[-1] = lines[-1]

        lines.append(f" └{'─' * inner}┘")

        succs = block.succs
        for i, (tgt, etype) in enumerate(succs):
            prefix = "   └──" if i == len(succs) - 1 else "   ├──"
            color, label = _EDGE_STYLE.get(etype, ("dim", etype))
            if etype == "ret":
                lines.append(f"{prefix} [{color}]{label}[/{color}]")
            elif etype == "indirect":
                lines.append(f"{prefix} [{color}]{label}[/{color}]")
            else:
                tgt_num = block_idx.get(tgt)
                tgt_s = f"Block #{tgt_num}" if tgt_num else f"0x{tgt:x} (external)"
                back = " [dim](back-edge)[/dim]" if tgt <= addr and tgt in cfg.blocks else ""
                lines.append(
                    f"{prefix} [{color}]{label}[/{color}]"
                    f"  →  {tgt_s}  [dim]0x{tgt:x}[/dim]{back}"
                )
        lines.append("")

    return "\n".join(lines)
