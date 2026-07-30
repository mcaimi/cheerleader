"""Shared data types for all binary format parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Symbol type flags (n_type field in Mach-O nlist; reused as generic constants)
N_STAB  = 0xE0
N_PEXT  = 0x10
N_TYPE  = 0x0E
N_EXT   = 0x01

N_UNDF  = 0x00
N_ABS   = 0x02
N_SECT  = 0x0E
N_PBUD  = 0x0C
N_INDR  = 0x0A

SYM_TYPE = {
    N_UNDF: "UNDEF",
    N_ABS:  "ABS",
    N_SECT: "SECT",
    N_PBUD: "PBUD",
    N_INDR: "INDR",
}


@dataclass
class Section:
    name: str
    segment: str
    addr: int
    size: int
    offset: int
    align: int
    flags: int
    reloff: int
    nreloc: int

    @property
    def type_str(self) -> str:
        t = self.flags & 0xFF
        types = {
            0x00: "regular",
            0x01: "zerofill",
            0x02: "cstring_literals",
            0x03: "4byte_literals",
            0x04: "8byte_literals",
            0x05: "literal_pointers",
            0x06: "non_lazy_symbol_pointers",
            0x07: "lazy_symbol_pointers",
            0x08: "symbol_stubs",
            0x09: "mod_init_func_pointers",
            0x0A: "mod_term_func_pointers",
            0x0B: "coalesced",
            0x0C: "gb_zerofill",
            0x0D: "interposing",
            0x0E: "16byte_literals",
            0x0F: "dtrace_dof",
            0x10: "lazy_dylib_symbol_pointers",
            0x11: "thread_local_regular",
            0x12: "thread_local_zerofill",
            0x13: "thread_local_variables",
            0x14: "thread_local_variable_pointers",
            0x15: "thread_local_init_function_pointers",
        }
        return types.get(t, f"0x{t:02x}")


@dataclass
class Segment:
    name: str
    vmaddr: int
    vmsize: int
    fileoff: int
    filesize: int
    maxprot: int
    initprot: int
    sections: list[Section] = field(default_factory=list)

    @property
    def prot_str(self) -> str:
        def _p(p: int) -> str:
            return ("r" if p & 1 else "-") + ("w" if p & 2 else "-") + ("x" if p & 4 else "-")
        return f"{_p(self.initprot)}/{_p(self.maxprot)}"


@dataclass
class Library:
    name: str
    current_version: str
    compat_version: str
    load_type: str
    offset: int


@dataclass
class Symbol:
    name: str
    addr: int
    sym_type: int
    sect: int
    desc: int
    external: bool
    stab: bool

    @property
    def type_str(self) -> str:
        if self.stab:
            return "STAB"
        return SYM_TYPE.get(self.sym_type & N_TYPE, f"0x{self.sym_type:02x}")

    @property
    def binding(self) -> str:
        if self.external:
            return "global"
        if self.sym_type & N_PEXT:
            return "private"
        return "local"


@dataclass
class ChainedFixup:
    segment: str
    offset: int
    kind: str
    lib_ordinal: Optional[int]
    name: Optional[str]
    addend: int
    is_rebase: bool
    target: Optional[int]


@dataclass
class BinaryString:
    file_offset: int    # absolute file offset into the binary
    addr: int           # virtual address (0 if not mapped)
    section: str        # e.g. "__TEXT,__cstring"
    value: str


@dataclass
class DisasmInstruction:
    addr: int
    size: int
    mnemonic: str
    op_str: str
    raw: bytes


@dataclass
class BinaryInfo:
    """Base class for parsed binary metadata — format-agnostic fields."""
    path: str
    arch: str = "?"
    bits: int = 0
    file_type: str = "?"
    slice_offset: int = 0          # absolute file offset of this slice; 0 for non-fat
    segments: list[Segment] = field(default_factory=list)
    libraries: list[Library] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    chained_fixups: list[ChainedFixup] = field(default_factory=list)
    exports: list[dict] = field(default_factory=list)
    error: Optional[str] = None
