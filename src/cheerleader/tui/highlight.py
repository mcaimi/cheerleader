"""Disassembly syntax highlighting and rendering utilities."""

from __future__ import annotations

from rich.text import Text

from cheerleader.libs.cfg import ControlFlowGraph
from cheerleader.libs.highlight import mnemonic_kind, operand_spans

_MNEM_STYLE = {
    "ret": "bold red",
    "branch": "bold yellow",
    "nop": "dim",
    "stack": "cyan",
    "cmp": "magenta",
    "mem": "green",
    "arith": "blue",
}
_OPERAND_STYLE = {"imm": "yellow", "reg": "bright_green"}

_EDGE_STYLE = {
    "fall":     ("green",   "fall"),
    "cond":     ("yellow",  "cond"),
    "jump":     ("blue",    "jump"),
    "indirect": ("dim",     "indirect"),
    "ret":      ("red",     "ret"),
}


def _colorize_mnemonic(mnemonic: str) -> Text:
    style = _MNEM_STYLE.get(mnemonic_kind(mnemonic))
    return Text(mnemonic, style=style) if style else Text(mnemonic)


def _colorize_operands(op_str: str, arch: str) -> Text:
    result = Text()
    for text, kind in operand_spans(op_str, arch):
        style = _OPERAND_STYLE.get(kind)
        if style:
            result.append(text, style=style)
        else:
            result.append(text)
    return result


def _fmt_addr(v: int | None) -> str:
    if v is None:
        return "—"
    return f"0x{v:016x}"


def human_size(size: int) -> str:
    """Human-readable byte size: '300 bytes', '1.0 KiB', '336.5 MiB', '2.0 GiB'."""
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.1f} GiB"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MiB"
    if size >= 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size} bytes"


def raw_hex(raw: bytes, limit: int | None = None) -> str:
    """Hex byte string ("de ad be ef"), optionally truncated to *limit* bytes."""
    if limit is not None:
        raw = raw[:limit]
    return " ".join(f"{b:02x}" for b in raw)


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
            hex_str = raw_hex(insn.raw, limit=6) + ("…" if len(insn.raw) > 6 else "")
            row = f"  [dim]0x{insn.addr:x}[/dim]  {hex_str:<20}  {_esc_markup(insn.mnemonic):<8}  {_esc_markup(insn.op_str)}"
            plain_len = len(f"  0x{insn.addr:x}  {hex_str:<20}  {insn.mnemonic:<8}  {insn.op_str}")
            if plain_len > inner:
                budget = inner - (plain_len - len(insn.op_str)) - 1
                row = (
                    f"  [dim]0x{insn.addr:x}[/dim]  {hex_str:<20}  "
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
            if etype == "ret" or etype == "indirect":
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
