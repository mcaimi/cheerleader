"""
Front-end-neutral disassembly syntax classification.

Both front-ends highlight instructions the same way: the TUI maps the kinds
below to rich styles (:mod:`cheerleader.tui.highlight`), the GUI to RGBA
colors (:mod:`cheerleader.gui.theme`). This module only classifies — no
styling, no rich/Dear PyGui imports.

Mnemonic kinds: ``ret``, ``branch``, ``nop``, ``stack``, ``cmp``, ``mem``,
``arith``, ``plain``. Operand piece kinds: ``imm``, ``reg``, ``plain``.
"""

from __future__ import annotations

import re

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


def is_arm_arch(arch: str) -> bool:
    """Whether *arch* names an ARM target (``arm64``, ``arm64e``, ``aarch64``…)."""
    a = arch.lower()
    return "arm" in a or "aarch64" in a


def mnemonic_kind(mnemonic: str) -> str:
    """Highlight class of one mnemonic (see the module docstring)."""
    m = mnemonic.lower()
    if m in _MNEM_RET:
        return "ret"
    if m in _MNEM_BRANCH or m.startswith("b.") or (m.startswith("j") and len(m) > 1):
        return "branch"
    if m in _MNEM_NOP:
        return "nop"
    if m in _MNEM_STACK:
        return "stack"
    if m in _MNEM_CMP:
        return "cmp"
    if m.startswith(("mov", "ldr", "str", "ldp", "stp", "lea", "ld", "st")):
        return "mem"
    if m in _MNEM_LOGIC or m.startswith(("add", "sub", "mul", "imul", "div", "idiv")):
        return "arith"
    return "plain"


def operand_spans(op_str: str, arch: str) -> list[tuple[str, str]]:
    """Split *op_str* into ``(text, kind)`` pieces.

    Immediates (hex, ``#``-decimal) are ``imm``, registers of *arch* are
    ``reg``, everything between is ``plain``. Overlapping matches keep the
    leftmost; joining the texts reproduces *op_str* exactly.
    """
    if not op_str:
        return []
    reg_re = _ARM_REG_RE if is_arm_arch(arch) else _X86_REG_RE

    spans: list[tuple[int, int, str]] = []
    for m in _HEX_RE.finditer(op_str):
        spans.append((m.start(), m.end(), "imm"))
    for m in _DEC_IMM_RE.finditer(op_str):
        spans.append((m.start(), m.end(), "imm"))
    for m in reg_re.finditer(op_str):
        spans.append((m.start(), m.end(), "reg"))
    spans.sort(key=lambda x: x[0])

    pieces: list[tuple[str, str]] = []
    pos = 0
    for s, e, kind in spans:
        if s < pos:
            continue  # overlaps an earlier match
        if pos < s:
            pieces.append((op_str[pos:s], "plain"))
        pieces.append((op_str[s:e], kind))
        pos = e
    if pos < len(op_str):
        pieces.append((op_str[pos:], "plain"))
    return pieces
