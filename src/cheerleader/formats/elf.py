"""ELF/ELF64 binary parser — sections, libraries, symbols, and dynamic relocations."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

from cheerleader.libs.types import (
    BinaryInfo,
    ChainedFixup,
    Library,
    N_ABS,
    N_SECT,
    N_UNDF,
    Section,
    Segment,
    Symbol,
)

ELF_MAGIC = b'\x7fELF'

_ELF_ARCH = {
    0x03: "x86",
    0x08: "mips",
    0x14: "powerpc",
    0x15: "powerpc64",
    0x16: "s390",
    0x28: "arm",
    0x2B: "sparc",
    0x3E: "x86_64",
    0xB7: "aarch64",
    0xF3: "riscv",
}

_EI_OSABI = {
    0x00: "System V",
    0x01: "HP-UX",
    0x02: "NetBSD",
    0x03: "Linux",
    0x06: "Solaris",
    0x07: "AIX",
    0x08: "IRIX",
    0x09: "FreeBSD",
    0x0C: "OpenBSD",
    0x0D: "OpenVMS",
    0x0E: "NonStop Kernel",
    0x0F: "AROS",
    0x10: "Fenix OS",
    0x11: "CloudABI",
}

_ET_NAME = {
    0: "ET_NONE",
    1: "ET_REL",
    2: "ET_EXEC",
    3: "ET_DYN",
    4: "ET_CORE",
}

# Program header types
PT_NULL    = 0
PT_LOAD    = 1
PT_DYNAMIC = 2
PT_INTERP  = 3
PT_NOTE    = 4
PT_PHDR    = 6
PT_TLS     = 7

# Section header types
SHT_NULL     = 0
SHT_PROGBITS = 1
SHT_SYMTAB   = 2
SHT_STRTAB   = 3
SHT_RELA     = 4
SHT_DYNAMIC  = 6
SHT_NOBITS   = 8
SHT_REL      = 9
SHT_DYNSYM   = 11

# Dynamic tags
DT_NULL    = 0
DT_NEEDED  = 1
DT_STRTAB  = 5
DT_STRSZ   = 10
DT_SONAME  = 14
DT_RPATH   = 15
DT_RUNPATH = 29

# ELF segment permission flags (p_flags)
PF_X = 0x1
PF_W = 0x2
PF_R = 0x4

# ELF symbol binding
STB_LOCAL  = 0
STB_GLOBAL = 1
STB_WEAK   = 2

# ELF symbol type
STT_NOTYPE  = 0
STT_OBJECT  = 1
STT_FUNC    = 2
STT_SECTION = 3
STT_FILE    = 4
STT_COMMON  = 5
STT_TLS     = 6

_STT_NAMES = {
    STT_NOTYPE:  "NOTYPE",
    STT_OBJECT:  "OBJECT",
    STT_FUNC:    "FUNC",
    STT_SECTION: "SECTION",
    STT_FILE:    "FILE",
    STT_COMMON:  "COMMON",
    STT_TLS:     "TLS",
}

# Special section indices
SHN_UNDEF  = 0
SHN_ABS    = 0xFFF1
SHN_COMMON = 0xFFF2


@dataclass
class _RawSection:
    """Internal ELF section with all header fields, before being exposed as Section."""
    name: str
    sh_type: int
    flags: int
    addr: int
    offset: int
    size: int
    link: int    # index of an associated section (e.g. symbol table for a reloc section)
    info: int
    addralign: int
    entsize: int
    index: int   # position in the section header table


@dataclass
class ELFInfo(BinaryInfo):
    """ELF-specific metadata extending the common BinaryInfo base."""
    flags: int = 0
    entry: int = 0
    os_abi: str = "System V"
    soname: Optional[str] = None
    interp: Optional[str] = None
    rpath: Optional[str] = None
    runpath: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _elf_prot(p_flags: int) -> int:
    """Map ELF p_flags to Mach-O-style VM protection bits used by the shared UI."""
    prot = 0
    if p_flags & PF_R:
        prot |= 0x1   # VM_PROT_READ
    if p_flags & PF_W:
        prot |= 0x2   # VM_PROT_WRITE
    if p_flags & PF_X:
        prot |= 0x4   # VM_PROT_EXECUTE
    return prot


def _read_strtab(data: bytes, off: int, size: int) -> dict[int, str]:
    """Parse an ELF string table into {byte-offset: string} mapping."""
    result: dict[int, str] = {}
    pos = 0
    while pos < size:
        end = (
            data.index(b"\x00", off + pos)
            if b"\x00" in data[off + pos: off + size]
            else off + size
        )
        result[pos] = data[off + pos: end].decode("utf-8", errors="replace")
        pos = end - off + 1
    return result


def _vaddr_to_offset(vaddr: int, segments: list[Segment]) -> int:
    """Convert a virtual address to a file offset using the segment mapping table."""
    for seg in segments:
        if seg.vmaddr <= vaddr < seg.vmaddr + seg.vmsize:
            return seg.fileoff + (vaddr - seg.vmaddr)
    return 0


# ---------------------------------------------------------------------------
# Section header parsing
# ---------------------------------------------------------------------------

def _parse_raw_sections(
    data: bytes,
    shoff: int,
    shnum: int,
    shentsize: int,
    shstrndx: int,
    is64: bool,
    endian: str,
) -> list[_RawSection]:
    if shoff == 0 or shnum == 0:
        return []

    # 64-bit: sh_name(I) sh_type(I) sh_flags(Q) sh_addr(Q) sh_offset(Q)
    #         sh_size(Q) sh_link(I) sh_info(I) sh_addralign(Q) sh_entsize(Q)
    # 32-bit: sh_name(I) sh_type(I) sh_flags(I) sh_addr(I) sh_offset(I)
    #         sh_size(I) sh_link(I) sh_info(I) sh_addralign(I) sh_entsize(I)
    fmt = f"{endian}IIQQQQI IQQ" if is64 else f"{endian}IIIIIIII II"
    fmt = f"{endian}IIQQQQIIQQ" if is64 else f"{endian}IIIIIIIIII"

    # Read shstrtab to resolve section names
    shstr_off_in_table = shoff + shstrndx * shentsize
    shstr_raw = struct.unpack_from(fmt, data, shstr_off_in_table)
    shstr_file_off = shstr_raw[4]
    shstr_size     = shstr_raw[5]
    strtab = _read_strtab(data, shstr_file_off, shstr_size)

    sections: list[_RawSection] = []
    for i in range(shnum):
        raw = struct.unpack_from(fmt, data, shoff + i * shentsize)
        sh_name_off, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, sh_addralign, sh_entsize = raw
        name = strtab.get(sh_name_off, f"<{sh_name_off}>")
        # Don't skip SHT_NULL — keep index stable; expose as _RawSection anyway
        sections.append(_RawSection(
            name=name,
            sh_type=sh_type,
            flags=sh_flags,
            addr=sh_addr,
            offset=sh_offset if sh_type != SHT_NOBITS else 0,
            size=sh_size,
            link=sh_link,
            info=sh_info,
            addralign=sh_addralign,
            entsize=sh_entsize,
            index=i,
        ))

    return sections


# ---------------------------------------------------------------------------
# Program header (segment) parsing
# ---------------------------------------------------------------------------

def _parse_elf_segments(
    data: bytes,
    phoff: int,
    phnum: int,
    phentsize: int,
    is64: bool,
    endian: str,
) -> tuple[list[Segment], Optional[int], Optional[int]]:
    """
    Parse ELF program headers.

    Returns (segments, dynamic_offset, interp_offset) where the last two are
    file offsets for PT_DYNAMIC and PT_INTERP respectively (None if absent).
    """
    segments: list[Segment] = []
    dynamic_offset: Optional[int] = None
    interp_offset:  Optional[int] = None
    interp_size:    Optional[int] = None

    load_idx = 0
    for i in range(phnum):
        off = phoff + i * phentsize
        if is64:
            # p_type(I) p_flags(I) p_offset(Q) p_vaddr(Q) p_paddr(Q)
            # p_filesz(Q) p_memsz(Q) p_align(Q)
            fmt = f"{endian}IIQQQQQQ"
            p_type, p_flags, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_align = struct.unpack_from(fmt, data, off)
        else:
            # p_type(I) p_offset(I) p_vaddr(I) p_paddr(I)
            # p_filesz(I) p_memsz(I) p_flags(I) p_align(I)
            fmt = f"{endian}IIIIIIII"
            p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = struct.unpack_from(fmt, data, off)

        if p_type == PT_INTERP:
            interp_offset = p_offset
            interp_size   = p_filesz

        if p_type == PT_DYNAMIC:
            dynamic_offset = p_offset

        if p_type != PT_LOAD:
            continue

        prot = _elf_prot(p_flags)
        seg = Segment(
            name=f"LOAD_{load_idx}",
            vmaddr=p_vaddr,
            vmsize=p_memsz,
            fileoff=p_offset,
            filesize=p_filesz,
            maxprot=prot,
            initprot=prot,
        )
        segments.append(seg)
        load_idx += 1

    return segments, dynamic_offset, interp_offset


# ---------------------------------------------------------------------------
# Dynamic section parsing → libraries
# ---------------------------------------------------------------------------

def _parse_dynamic(
    data: bytes,
    dyn_offset: int,
    dyn_size: int,
    is64: bool,
    endian: str,
    segments: list[Segment],
    info: ELFInfo,
) -> None:
    """Extract DT_NEEDED libraries, SONAME, RPATH, RUNPATH from the .dynamic section."""
    entry_fmt = f"{endian}QQ" if is64 else f"{endian}II"
    entry_sz  = struct.calcsize(entry_fmt)
    count     = dyn_size // entry_sz

    # First pass: locate DT_STRTAB and DT_STRSZ
    strtab_vaddr = strsz = 0
    for i in range(count):
        tag, val = struct.unpack_from(entry_fmt, data, dyn_offset + i * entry_sz)
        if tag == DT_NULL:
            break
        if tag == DT_STRTAB:
            strtab_vaddr = val
        elif tag == DT_STRSZ:
            strsz = int(val)

    strtab_foff = _vaddr_to_offset(strtab_vaddr, segments) if strtab_vaddr else 0

    def _dyn_str(n: int) -> str:
        if not strtab_foff or not strsz:
            return f"<{n}>"
        pos = strtab_foff + n
        end = data.index(b"\x00", pos) if b"\x00" in data[pos: pos + 512] else pos + 512
        return data[pos:end].decode("utf-8", errors="replace")

    # Second pass: extract entries
    for i in range(count):
        tag, val = struct.unpack_from(entry_fmt, data, dyn_offset + i * entry_sz)
        if tag == DT_NULL:
            break
        if tag == DT_NEEDED:
            info.libraries.append(Library(
                name=_dyn_str(int(val)),
                current_version="",
                compat_version="",
                load_type="DT_NEEDED",
                offset=dyn_offset + i * entry_sz,
            ))
        elif tag == DT_SONAME:
            info.soname = _dyn_str(int(val))
        elif tag == DT_RPATH:
            info.rpath = _dyn_str(int(val))
        elif tag == DT_RUNPATH:
            info.runpath = _dyn_str(int(val))


# ---------------------------------------------------------------------------
# Symbol table parsing
# ---------------------------------------------------------------------------

def _parse_elf_symtab(
    data: bytes,
    sym_off: int,
    sym_size: int,
    str_off: int,
    str_size: int,
    is64: bool,
    endian: str,
) -> list[Symbol]:
    """Parse an ELF symbol table (.symtab or .dynsym) into generic Symbol objects."""
    if not sym_off or not sym_size:
        return []

    strtab = _read_strtab(data, str_off, str_size)

    if is64:
        # st_name(I) st_info(B) st_other(B) st_shndx(H) st_value(Q) st_size(Q)
        fmt = f"{endian}IBBHQQ"
    else:
        # st_name(I) st_value(I) st_size(I) st_info(B) st_other(B) st_shndx(H)
        fmt = f"{endian}IIIBBH"

    sz   = struct.calcsize(fmt)
    syms: list[Symbol] = []

    for i in range(sym_size // sz):
        raw = struct.unpack_from(fmt, data, sym_off + i * sz)
        if is64:
            st_name_off, st_info, st_other, st_shndx, st_value, st_size = raw
        else:
            st_name_off, st_value, st_size, st_info, st_other, st_shndx = raw

        name    = strtab.get(st_name_off, f"<{st_name_off}>")
        st_type = st_info & 0xF
        st_bind = st_info >> 4

        # Map ELF section index to Mach-O-style sym_type so the shared UI
        # filters (UNDEF, ABS, SECT) work correctly for ELF binaries too.
        if st_shndx == SHN_UNDEF:
            sym_type = N_UNDF   # 0x00
        elif st_shndx == SHN_ABS:
            sym_type = N_ABS    # 0x02
        else:
            sym_type = N_SECT   # 0x0E

        external = st_bind in (STB_GLOBAL, STB_WEAK)

        syms.append(Symbol(
            name=name,
            addr=st_value,
            sym_type=sym_type,
            sect=st_shndx,
            desc=st_other,
            external=external,
            stab=False,
        ))

    return syms


# ---------------------------------------------------------------------------
# Dynamic relocation parsing → ChainedFixup
# ---------------------------------------------------------------------------

def _parse_elf_relocations(
    data: bytes,
    raw_sections: list[_RawSection],
    is64: bool,
    endian: str,
    segments: list[Segment],
) -> list[ChainedFixup]:
    """
    Parse all RELA/REL sections and return ChainedFixup entries.

    For each relocation we resolve the symbol name (from the linked symbol
    table) and the library name (from the import list already on info).
    """
    fixups: list[ChainedFixup] = []

    # Index raw sections by their position so sh_link can be resolved
    sec_by_idx: dict[int, _RawSection] = {s.index: s for s in raw_sections}

    # Cache parsed string and symbol tables to avoid re-reading
    _strtab_cache: dict[int, dict[int, str]] = {}
    _symtab_cache: dict[int, list[Symbol]]   = {}

    def _get_strtab(sec: _RawSection) -> dict[int, str]:
        if sec.index not in _strtab_cache:
            _strtab_cache[sec.index] = _read_strtab(data, sec.offset, sec.size)
        return _strtab_cache[sec.index]

    def _get_syms(symtab_sec: _RawSection) -> list[Symbol]:
        if symtab_sec.index not in _symtab_cache:
            str_sec = sec_by_idx.get(symtab_sec.link)
            if str_sec is None:
                _symtab_cache[symtab_sec.index] = []
            else:
                _symtab_cache[symtab_sec.index] = _parse_elf_symtab(
                    data,
                    symtab_sec.offset,
                    symtab_sec.size,
                    str_sec.offset,
                    str_sec.size,
                    is64,
                    endian,
                )
        return _symtab_cache[symtab_sec.index]

    def _seg_name_at(foff: int) -> str:
        for seg in segments:
            if seg.fileoff <= foff < seg.fileoff + seg.filesize:
                return seg.name
        return "?"

    for sec in raw_sections:
        if sec.sh_type not in (SHT_RELA, SHT_REL):
            continue
        if sec.offset == 0 or sec.size == 0:
            continue

        is_rela   = sec.sh_type == SHT_RELA
        symtab_sec = sec_by_idx.get(sec.link)
        syms       = _get_syms(symtab_sec) if symtab_sec else []

        if is64:
            if is_rela:
                entry_fmt = f"{endian}QQq"  # r_offset(Q) r_info(Q) r_addend(q)
            else:
                entry_fmt = f"{endian}QQ"   # r_offset(Q) r_info(Q)
        else:
            if is_rela:
                entry_fmt = f"{endian}IIi"  # r_offset(I) r_info(I) r_addend(i)
            else:
                entry_fmt = f"{endian}II"   # r_offset(I) r_info(I)

        entry_sz = struct.calcsize(entry_fmt)

        for i in range(sec.size // entry_sz):
            raw = struct.unpack_from(entry_fmt, data, sec.offset + i * entry_sz)
            r_offset = raw[0]
            r_info   = raw[1]
            r_addend = raw[2] if is_rela else 0

            if is64:
                sym_idx  = r_info >> 32
                rel_type = r_info & 0xFFFFFFFF
            else:
                sym_idx  = r_info >> 8
                rel_type = r_info & 0xFF

            sym_name = syms[sym_idx].name if sym_idx < len(syms) else ""
            is_undef = sym_idx < len(syms) and syms[sym_idx].sym_type == N_UNDF

            foff = _vaddr_to_offset(r_offset, segments)
            seg_name = _seg_name_at(foff) if foff else "?"

            fixups.append(ChainedFixup(
                segment=seg_name,
                offset=r_offset,
                kind=f"R_{rel_type}",
                lib_ordinal=None,
                name=sym_name if sym_name else None,
                addend=r_addend,
                is_rebase=not is_undef,
                target=None,
            ))

    return fixups


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(path: str, **kwargs) -> ELFInfo:
    """Parse an ELF binary and return an ELFInfo with all available metadata."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return ELFInfo(path=path, error=str(e))

    if len(data) < 16 or data[:4] != ELF_MAGIC:
        return ELFInfo(path=path, error="Not an ELF file")

    ei_class = data[4]   # 1 = 32-bit, 2 = 64-bit
    ei_data  = data[5]   # 1 = little-endian, 2 = big-endian
    ei_osabi = data[7]

    is64   = (ei_class == 2)
    endian = "<" if ei_data == 1 else ">"

    # ELF header fields after the 16-byte ident
    # 64-bit: e_type(H) e_machine(H) e_version(I) e_entry(Q) e_phoff(Q)
    #         e_shoff(Q) e_flags(I) e_ehsize(H) e_phentsize(H) e_phnum(H)
    #         e_shentsize(H) e_shnum(H) e_shstrndx(H)
    # 32-bit: e_type(H) e_machine(H) e_version(I) e_entry(I) e_phoff(I)
    #         e_shoff(I) e_flags(I) e_ehsize(H) e_phentsize(H) e_phnum(H)
    #         e_shentsize(H) e_shnum(H) e_shstrndx(H)
    hdr_fmt = f"{endian}HHIQQQIHHHHHH" if is64 else f"{endian}HHIIIIIHHHHHH"
    hdr_sz  = struct.calcsize(hdr_fmt)

    if len(data) < 16 + hdr_sz:
        return ELFInfo(path=path, error="ELF header truncated")

    hdr = struct.unpack_from(hdr_fmt, data, 16)
    (e_type, e_machine, e_version,
     e_entry, e_phoff, e_shoff,
     e_flags, e_ehsize,
     e_phentsize, e_phnum,
     e_shentsize, e_shnum, e_shstrndx) = hdr

    arch      = _ELF_ARCH.get(e_machine, f"0x{e_machine:04x}")
    os_abi    = _EI_OSABI.get(ei_osabi, f"0x{ei_osabi:02x}")
    file_type = _ET_NAME.get(e_type, f"0x{e_type:04x}")

    info = ELFInfo(
        path=path,
        arch=arch,
        bits=64 if is64 else 32,
        file_type=file_type,
        flags=e_flags,
        entry=e_entry,
        os_abi=os_abi,
    )

    # --- Parse program headers → segments + note PT_INTERP / PT_DYNAMIC offsets
    segments, dyn_foff, interp_foff = _parse_elf_segments(
        data, e_phoff, e_phnum, e_phentsize, is64, endian
    )
    info.segments = segments

    # --- Parse section headers (internal representation)
    try:
        raw_sections = _parse_raw_sections(
            data, e_shoff, e_shnum, e_shentsize, e_shstrndx, is64, endian
        )
    except Exception:
        raw_sections = []

    # --- Assign sections to segments and expose them as Section objects
    sec_by_name: dict[str, _RawSection] = {}
    for rs in raw_sections:
        if rs.sh_type == SHT_NULL:
            continue
        sec_by_name[rs.name] = rs
        if rs.offset == 0 or rs.size == 0:
            continue
        # Find the containing PT_LOAD segment by virtual address
        parent_name = ""
        for seg in segments:
            if rs.addr and seg.vmaddr <= rs.addr < seg.vmaddr + seg.vmsize:
                parent_name = seg.name
                break
        sect = Section(
            name=rs.name,
            segment=parent_name,
            addr=rs.addr,
            size=rs.size,
            offset=rs.offset,
            align=rs.addralign,
            flags=rs.flags,
            reloff=0,
            nreloc=0,
        )
        # Attach section to its parent segment
        attached = False
        for seg in segments:
            if seg.name == parent_name:
                seg.sections.append(sect)
                attached = True
                break
        # Sections not covered by any PT_LOAD (e.g. debug) go into a virtual segment
        if not attached:
            # Find or create an "OTHER" catch-all segment
            other_seg = next((s for s in info.segments if s.name == "OTHER"), None)
            if other_seg is None:
                other_seg = Segment(
                    name="OTHER",
                    vmaddr=0, vmsize=0,
                    fileoff=0, filesize=0,
                    maxprot=0, initprot=0,
                )
                info.segments.append(other_seg)
            sect.segment = "OTHER"
            other_seg.sections.append(sect)

    # --- Interpreter path
    if interp_foff is not None:
        rs_interp = sec_by_name.get(".interp")
        if rs_interp and rs_interp.offset:
            raw_interp = data[rs_interp.offset: rs_interp.offset + rs_interp.size]
        else:
            raw_interp = data[interp_foff: interp_foff + 256]
        info.interp = raw_interp.rstrip(b"\x00").decode("utf-8", errors="replace") or None

    # --- Dynamic section: libraries, soname, rpath
    dyn_rs = sec_by_name.get(".dynamic")
    if dyn_rs and dyn_rs.offset and dyn_rs.size:
        _parse_dynamic(data, dyn_rs.offset, dyn_rs.size, is64, endian, segments, info)
    elif dyn_foff is not None:
        # Fall back to the PT_DYNAMIC file offset when no section header is present
        # (stripped binary). Scan for DT_NULL to determine size.
        entry_fmt = f"{endian}QQ" if is64 else f"{endian}II"
        entry_sz  = struct.calcsize(entry_fmt)
        end = dyn_foff
        while end + entry_sz <= len(data):
            tag, _ = struct.unpack_from(entry_fmt, data, end)
            end += entry_sz
            if tag == DT_NULL:
                break
        _parse_dynamic(data, dyn_foff, end - dyn_foff, is64, endian, segments, info)

    # --- Symbol table (.symtab preferred; fall back to .dynsym for stripped bins)
    symtab_rs = sec_by_name.get(".symtab")
    strtab_rs = sec_by_name.get(".strtab")
    if symtab_rs and strtab_rs:
        info.symbols = _parse_elf_symtab(
            data,
            symtab_rs.offset, symtab_rs.size,
            strtab_rs.offset, strtab_rs.size,
            is64, endian,
        )

    if not info.symbols:
        dynsym_rs = sec_by_name.get(".dynsym")
        dynstr_rs = sec_by_name.get(".dynstr")
        if dynsym_rs and dynstr_rs:
            info.symbols = _parse_elf_symtab(
                data,
                dynsym_rs.offset, dynsym_rs.size,
                dynstr_rs.offset, dynstr_rs.size,
                is64, endian,
            )

    # --- Exports: globally defined symbols from .dynsym
    dynsym_rs = sec_by_name.get(".dynsym")
    dynstr_rs = sec_by_name.get(".dynstr")
    if dynsym_rs and dynstr_rs:
        dynsyms = _parse_elf_symtab(
            data,
            dynsym_rs.offset, dynsym_rs.size,
            dynstr_rs.offset, dynstr_rs.size,
            is64, endian,
        )
        info.exports = [
            {"name": s.name, "addr": s.addr, "flags": s.sym_type}
            for s in dynsyms
            if s.external and s.addr != 0
        ]

    # --- Dynamic relocations → ChainedFixup equivalents
    try:
        info.chained_fixups = _parse_elf_relocations(
            data, raw_sections, is64, endian, segments
        )
    except Exception:
        info.chained_fixups = []

    return info
