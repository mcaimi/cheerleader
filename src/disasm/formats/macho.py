"""Mach-O binary parser — sections, libraries, symbols, chained fixups, and disassembly."""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

from disasm.libs.types import (
    BinaryInfo,
    ChainedFixup,
    Library,
    N_EXT,
    N_STAB,
    Section,
    Segment,
    Symbol,
)

# Mach-O magic values
MH_MAGIC = 0xFEEDFACE
MH_CIGAM = 0xCEFAEDFE
MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM_64 = 0xCFFAEDFE
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECA

CPU_TYPE = {
    0x00000007: "x86",
    0x01000007: "x86_64",
    0x0000000C: "arm",
    0x0100000C: "arm64",
    0x0200000C: "arm64_32",
}

FILE_TYPE = {
    0x1: "MH_OBJECT",
    0x2: "MH_EXECUTE",
    0x6: "MH_DYLIB",
    0x7: "MH_DYLINKER",
    0x8: "MH_BUNDLE",
    0xA: "MH_DYLIB_STUB",
    0xB: "MH_DSYM",
}


class LC(IntEnum):
    SEGMENT = 0x01
    SYMTAB = 0x02
    DYSYMTAB = 0x0B
    LOAD_DYLIB = 0x0C
    ID_DYLIB = 0x0D
    LOAD_DYLINKER = 0x0E
    SEGMENT_64 = 0x19
    UUID = 0x1B
    CODE_SIGNATURE = 0x1D
    LOAD_WEAK_DYLIB = 0x80000018
    RPATH = 0x8000001C
    REEXPORT_DYLIB = 0x8000001F
    LAZY_LOAD_DYLIB = 0x20
    DYLD_INFO = 0x22
    DYLD_INFO_ONLY = 0x80000022
    VERSION_MIN_MACOSX = 0x24
    FUNCTION_STARTS = 0x26
    MAIN = 0x80000028
    DATA_IN_CODE = 0x29
    SOURCE_VERSION = 0x2A
    DYLIB_CODE_SIGN_DRS = 0x2B
    LINKER_OPTION = 0x2D
    BUILD_VERSION = 0x32
    DYLD_EXPORTS_TRIE = 0x80000033
    DYLD_CHAINED_FIXUPS = 0x80000034
    FILESET_ENTRY = 0x80000035


LC_NAMES = {v.value: v.name for v in LC}

# Chained fixup pointer kinds
DYLD_CHAINED_PTR_ARM64E = 1
DYLD_CHAINED_PTR_64 = 2
DYLD_CHAINED_PTR_32 = 3
DYLD_CHAINED_PTR_32_CACHE = 4
DYLD_CHAINED_PTR_32_FIRMWARE = 5
DYLD_CHAINED_PTR_64_OFFSET = 6
DYLD_CHAINED_PTR_ARM64E_KERNEL = 7
DYLD_CHAINED_PTR_64_KERNEL_CACHE = 8
DYLD_CHAINED_PTR_ARM64E_USERLAND = 9
DYLD_CHAINED_PTR_ARM64E_FIRMWARE = 10
DYLD_CHAINED_PTR_X86_64_CACHE = 11
DYLD_CHAINED_PTR_ARM64E_USERLAND24 = 12

CHAINED_PTR_NAMES = {
    1: "ARM64E",
    2: "64",
    3: "32",
    4: "32_CACHE",
    5: "32_FIRMWARE",
    6: "64_OFFSET",
    7: "ARM64E_KERNEL",
    8: "64_KERNEL_CACHE",
    9: "ARM64E_USERLAND",
    10: "ARM64E_FIRMWARE",
    11: "X86_64_CACHE",
    12: "ARM64E_USERLAND24",
}


@dataclass
class MachOInfo(BinaryInfo):
    """Mach-O specific metadata extending the common BinaryInfo base."""

    flags: int = 0
    ncmds: int = 0
    uuid: Optional[str] = None
    min_os: Optional[str] = None
    sdk: Optional[str] = None
    source_version: Optional[str] = None
    rpaths: list[str] = field(default_factory=list)
    dylinker: Optional[str] = None


def _ver(v: int) -> str:
    return f"{(v >> 16) & 0xFFFF}.{(v >> 8) & 0xFF}.{v & 0xFF}"


def _parse_sections(
    data: bytes, off: int, nsects: int, is64: bool, endian: str
) -> list[Section]:
    sections = []
    fmt = f"{endian}16s16sQQIIIIIIII" if is64 else f"{endian}16s16sIIIIIIIII"
    sz = struct.calcsize(fmt)
    for _ in range(nsects):
        raw = struct.unpack_from(fmt, data, off)
        name = raw[0].rstrip(b"\x00").decode("ascii", errors="replace")
        segname = raw[1].rstrip(b"\x00").decode("ascii", errors="replace")
        addr, size, fileoff, align, reloff, nreloc, flags = (
            raw[2],
            raw[3],
            raw[4],
            raw[5],
            raw[6],
            raw[7],
            raw[8],
        )
        sections.append(
            Section(
                name=name,
                segment=segname,
                addr=addr,
                size=size,
                offset=fileoff,
                align=align,
                flags=flags,
                reloff=reloff,
                nreloc=nreloc,
            )
        )
        off += sz
    return sections


def _parse_string_table(data: bytes, stroff: int, strsize: int) -> dict[int, str]:
    strtab: dict[int, str] = {}
    pos = 0
    while pos < strsize:
        end = (
            data.index(b"\x00", stroff + pos)
            if b"\x00" in data[stroff + pos : stroff + strsize]
            else stroff + strsize
        )
        strtab[pos] = data[stroff + pos : end].decode("utf-8", errors="replace")
        pos = end - stroff + 1
    return strtab


def _parse_symtab(
    data: bytes,
    symoff: int,
    nsyms: int,
    stroff: int,
    strsize: int,
    is64: bool,
    endian: str,
) -> list[Symbol]:
    strtab = _parse_string_table(data, stroff, strsize)
    syms: list[Symbol] = []
    if is64:
        fmt = f"{endian}IBBHQ"
    else:
        fmt = f"{endian}IBBHI"
    sz = struct.calcsize(fmt)
    for i in range(nsyms):
        off = symoff + i * sz
        strx, n_type, n_sect, n_desc, n_value = struct.unpack_from(fmt, data, off)
        name = strtab.get(strx, f"<{strx}>")
        stab = bool(n_type & N_STAB)
        ext = bool(n_type & N_EXT)
        syms.append(
            Symbol(
                name=name,
                addr=n_value,
                sym_type=n_type,
                sect=n_sect,
                desc=n_desc,
                external=ext,
                stab=stab,
            )
        )
    return syms


def _parse_exports_trie(data: bytes, off: int, size: int) -> list[dict]:
    """Walk the exports trie and return exported symbol entries."""
    exports: list[dict] = []
    trie = data[off : off + size]

    def _walk(node_off: int, prefix: str):
        if node_off >= len(trie):
            return
        terminal_sz, n = _read_uleb128(trie, node_off)
        if terminal_sz != 0:
            flags, n2 = _read_uleb128(trie, node_off + n)
            addr, _ = _read_uleb128(trie, node_off + n + n2)
            exports.append({"name": prefix, "addr": addr, "flags": flags})
        child_count = (
            trie[node_off + terminal_sz + n]
            if (node_off + terminal_sz + n) < len(trie)
            else 0
        )
        child_off = node_off + terminal_sz + n + 1
        for _ in range(child_count):
            end = (
                trie.index(b"\x00", child_off)
                if b"\x00" in trie[child_off:]
                else len(trie)
            )
            label = trie[child_off:end].decode("utf-8", errors="replace")
            child_off = end + 1
            next_node, nb = _read_uleb128(trie, child_off)
            child_off += nb
            _walk(next_node, prefix + label)

    _walk(0, "")
    return exports


def _read_uleb128(data: bytes, off: int) -> tuple[int, int]:
    result = 0
    shift = 0
    nb = 0
    while off < len(data):
        b = data[off]
        off += 1
        nb += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            break
    return result, nb


def _parse_chained_fixups(
    data: bytes, lc_off: int, lc_size: int, segments: list[Segment], libs: list[Library]
) -> list[ChainedFixup]:
    """Parse LC_DYLD_CHAINED_FIXUPS payload."""
    fixups: list[ChainedFixup] = []
    try:
        dataoff, datasize = struct.unpack_from("<II", data, lc_off + 8)
        if dataoff == 0 or datasize == 0:
            return fixups

        hdr = data[dataoff : dataoff + datasize]
        if len(hdr) < 32:
            return fixups

        (
            fixups_version,
            starts_offset,
            imports_offset,
            symbols_offset,
            imports_count,
            imports_format,
            symbols_format,
        ) = struct.unpack_from("<IIIIIII", hdr, 0)

        entry_sz = {1: 4, 2: 8, 3: 8}.get(imports_format, 4)
        import_names: list[tuple[int, int, int]] = []
        for i in range(imports_count):
            raw = hdr[
                imports_offset + i * entry_sz : imports_offset + i * entry_sz + entry_sz
            ]
            if imports_format == 1:
                val = struct.unpack_from("<I", raw)[0]
                lib_ord = val & 0xFF
                weak = (val >> 8) & 0x1
                name_off = (val >> 9) & 0x7FFFFF
            elif imports_format == 2:
                val = struct.unpack_from("<Q", raw)[0]
                lib_ord = val & 0xFF
                weak = (val >> 8) & 0x1
                name_off = (val >> 9) & 0x7FFFFF
            else:
                lib_ord, name_off = struct.unpack_from("<HI", raw)[:2]
                weak = 0
            import_names.append((lib_ord, weak, name_off))

        def _sym_name(noff: int) -> str:
            base = symbols_offset + noff
            end = hdr.index(b"\x00", base) if b"\x00" in hdr[base:] else len(hdr)
            return hdr[base:end].decode("utf-8", errors="replace")

        def _lib_name(ord: int) -> str:
            if ord == 0:
                return "<self>"
            if ord == 0xFE:
                return "<weak>"
            if ord == 0xFF:
                return "<flat>"
            idx = ord - 1
            if 0 <= idx < len(libs):
                return os.path.basename(libs[idx].name)
            return f"<lib#{ord}>"

        if starts_offset == 0:
            return fixups

        seg_count = struct.unpack_from("<I", hdr, starts_offset)[0]
        seg_infos_off = starts_offset + 4
        for si in range(seg_count):
            seg_off_delta = struct.unpack_from("<I", hdr, seg_infos_off + si * 4)[0]
            if seg_off_delta == 0:
                continue
            seg_info_off = starts_offset + seg_off_delta
            if seg_info_off + 22 > len(hdr):
                continue

            (seg_sz, page_sz, ptr_format, seg_offset, max_ptr, page_count) = (
                struct.unpack_from("<IHHQII", hdr, seg_info_off)
            )

            ptr_kind = CHAINED_PTR_NAMES.get(ptr_format, str(ptr_format))
            seg = segments[si] if si < len(segments) else None
            seg_name = seg.name if seg else f"seg{si}"

            page_starts_off = seg_info_off + 22
            for pi in range(page_count):
                page_start = struct.unpack_from("<H", hdr, page_starts_off + pi * 2)[0]
                if page_start == 0xFFFF:
                    continue

                chain_off = (seg.fileoff if seg else 0) + pi * page_sz + page_start
                visited: set[int] = set()

                while chain_off not in visited and chain_off < len(data):
                    visited.add(chain_off)
                    if chain_off + 8 > len(data):
                        break
                    raw_ptr = struct.unpack_from("<Q", data, chain_off)[0]

                    is_bind = False
                    ordinal = 0
                    sym_name_str = None
                    addend = 0
                    target = None

                    if ptr_format in (DYLD_CHAINED_PTR_64, DYLD_CHAINED_PTR_64_OFFSET):
                        is_bind = bool(raw_ptr >> 63)
                        if is_bind:
                            ordinal = (raw_ptr >> 0) & 0xFFFF
                            addend = (raw_ptr >> 16) & 0xFF
                            _name_idx = (raw_ptr >> 32) & 0x7FFFFFFF
                            if ordinal < len(import_names):
                                lib_ord, _, noff = import_names[ordinal]
                                sym_name_str = _sym_name(noff)
                                ordinal = lib_ord
                        else:
                            target = (raw_ptr >> 0) & 0xFFFFFFFFF
                            hi = (raw_ptr >> 36) & 0xFFFFFF
                            target |= hi << 36
                    elif ptr_format in (
                        DYLD_CHAINED_PTR_ARM64E,
                        DYLD_CHAINED_PTR_ARM64E_USERLAND,
                        DYLD_CHAINED_PTR_ARM64E_USERLAND24,
                    ):
                        bind_flag = (raw_ptr >> 62) & 0x1
                        _auth_flag = (raw_ptr >> 63) & 0x1
                        is_bind = bool(bind_flag)
                        if is_bind:
                            ordinal = raw_ptr & 0xFFFF
                            addend = (raw_ptr >> 32) & 0x7FFFF
                            if ordinal < len(import_names):
                                lib_ord, _, noff = import_names[ordinal]
                                sym_name_str = _sym_name(noff)
                                ordinal = lib_ord
                        else:
                            target = raw_ptr & 0xFFFFFFFFF

                    vaddr = (
                        (seg.vmaddr if seg else 0)
                        + pi * page_sz
                        + (chain_off - (seg.fileoff if seg else 0) - pi * page_sz)
                    )

                    fixups.append(
                        ChainedFixup(
                            segment=seg_name,
                            offset=vaddr,
                            kind=ptr_kind,
                            lib_ordinal=ordinal if is_bind else None,
                            name=sym_name_str
                            if not is_bind or sym_name_str
                            else (_lib_name(ordinal) if is_bind else None),
                            addend=addend,
                            is_rebase=not is_bind,
                            target=target,
                        )
                    )

                    if ptr_format in (
                        DYLD_CHAINED_PTR_64,
                        DYLD_CHAINED_PTR_64_OFFSET,
                        DYLD_CHAINED_PTR_ARM64E,
                        DYLD_CHAINED_PTR_ARM64E_USERLAND,
                        DYLD_CHAINED_PTR_ARM64E_USERLAND24,
                    ):
                        stride = 4
                        next_off = (
                            (raw_ptr >> 51) & 0x7FF
                            if not (
                                (raw_ptr >> 63) & 1
                                and ptr_format
                                in (
                                    DYLD_CHAINED_PTR_ARM64E,
                                    DYLD_CHAINED_PTR_ARM64E_USERLAND,
                                    DYLD_CHAINED_PTR_ARM64E_USERLAND24,
                                )
                            )
                            else (raw_ptr >> 32) & 0x7FFFF
                        )
                        if ptr_format in (
                            DYLD_CHAINED_PTR_64,
                            DYLD_CHAINED_PTR_64_OFFSET,
                        ):
                            next_off = (raw_ptr >> 51) & 0x7FF
                        if next_off == 0:
                            break
                        chain_off += next_off * stride
                    else:
                        break

    except Exception:
        pass
    return fixups


def parse(path: str, slice_index: int = 0) -> MachOInfo:
    """Parse a Mach-O binary (or one slice of a fat binary)."""
    with open(path, "rb") as f:
        data = f.read()

    fat_magic = struct.unpack_from(">I", data, 0)[0]
    arch_offset = 0
    arch_size = len(data)

    if fat_magic in (FAT_MAGIC, FAT_CIGAM):
        fat_endian = ">" if fat_magic == FAT_MAGIC else "<"
        nfat = struct.unpack_from(f"{fat_endian}I", data, 4)[0]
        slices = []
        for i in range(nfat):
            off = 8 + i * 20
            cputype, cpusubtype, fat_off, fat_sz, align = struct.unpack_from(
                f"{fat_endian}iiIII", data, off
            )
            slices.append((cputype, fat_off, fat_sz))
        if slice_index >= len(slices):
            slice_index = 0
        _, arch_offset, arch_size = slices[slice_index]

    magic_at = struct.unpack_from("<I", data, arch_offset)[0]
    if magic_at == MH_MAGIC:
        is64, endian = False, "<"
    elif magic_at == MH_CIGAM:
        is64, endian = False, ">"
    elif magic_at == MH_MAGIC_64:
        is64, endian = True, "<"
    elif magic_at == MH_CIGAM_64:
        is64, endian = True, ">"
    else:
        return MachOInfo(
            path=path,
            arch="?",
            bits=0,
            file_type="?",
            error=f"Unrecognized magic: 0x{magic_at:08X}",
        )

    hdr_fmt = f"{endian}IIIIIII" + ("I" if is64 else "")
    hdr_sz = struct.calcsize(hdr_fmt)
    hdr = struct.unpack_from(hdr_fmt, data, arch_offset)
    _, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags = hdr[:7]

    arch = CPU_TYPE.get(cputype, f"0x{cputype:08X}")
    file_type = FILE_TYPE.get(filetype, f"0x{filetype:02X}")

    info = MachOInfo(
        path=path,
        arch=arch,
        bits=64 if is64 else 32,
        file_type=file_type,
        flags=flags,
        ncmds=ncmds,
        slice_offset=arch_offset,
    )

    lc_off = arch_offset + hdr_sz
    symoff = nsyms = stroff = strsize = 0
    exports_trie_off = exports_trie_size = 0
    chained_fixups_lc_off = 0

    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from(f"{endian}II", data, lc_off)

        if cmd == LC.SEGMENT or cmd == LC.SEGMENT_64:
            seg_fmt = f"{endian}16sQQQQIIII" if is64 else f"{endian}16sIIIIIIII"
            raw = struct.unpack_from(seg_fmt, data, lc_off + 8)
            segname = raw[0].rstrip(b"\x00").decode("ascii", errors="replace")
            vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, segflags = (
                raw[1:]
            )
            seg = Segment(
                name=segname,
                vmaddr=vmaddr,
                vmsize=vmsize,
                fileoff=fileoff,
                filesize=filesize,
                maxprot=maxprot,
                initprot=initprot,
            )
            sect_off = lc_off + 8 + struct.calcsize(seg_fmt)
            seg.sections = _parse_sections(data, sect_off, nsects, is64, endian)
            info.segments.append(seg)

        elif cmd in (
            LC.LOAD_DYLIB,
            LC.ID_DYLIB,
            LC.LOAD_WEAK_DYLIB,
            LC.REEXPORT_DYLIB,
            LC.LAZY_LOAD_DYLIB,
        ):
            name_off, ts, cur_ver, compat_ver = struct.unpack_from(
                f"{endian}IIII", data, lc_off + 8
            )
            abs_name_off = lc_off + name_off
            end = (
                data.index(b"\x00", abs_name_off)
                if b"\x00" in data[abs_name_off : abs_name_off + 256]
                else abs_name_off
            )
            lib_name = data[abs_name_off:end].decode("utf-8", errors="replace")
            load_type = LC_NAMES.get(cmd, f"0x{cmd:08X}")
            info.libraries.append(
                Library(
                    name=lib_name,
                    current_version=_ver(cur_ver),
                    compat_version=_ver(compat_ver),
                    load_type=load_type,
                    offset=lc_off - arch_offset,
                )
            )

        elif cmd == LC.LOAD_DYLINKER:
            name_off = struct.unpack_from(f"{endian}I", data, lc_off + 8)[0]
            abs_off = lc_off + name_off
            end = (
                data.index(b"\x00", abs_off)
                if b"\x00" in data[abs_off : abs_off + 256]
                else abs_off
            )
            info.dylinker = data[abs_off:end].decode("utf-8", errors="replace")

        elif cmd == LC.RPATH:
            name_off = struct.unpack_from(f"{endian}I", data, lc_off + 8)[0]
            abs_off = lc_off + name_off
            end = (
                data.index(b"\x00", abs_off)
                if b"\x00" in data[abs_off : abs_off + 256]
                else abs_off
            )
            info.rpaths.append(data[abs_off:end].decode("utf-8", errors="replace"))

        elif cmd == LC.SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from(
                f"{endian}IIII", data, lc_off + 8
            )
            symoff += arch_offset
            stroff += arch_offset

        elif cmd == LC.UUID:
            raw_uuid = data[lc_off + 8 : lc_off + 24]
            info.uuid = "-".join(
                [
                    raw_uuid[0:4].hex(),
                    raw_uuid[4:6].hex(),
                    raw_uuid[6:8].hex(),
                    raw_uuid[8:10].hex(),
                    raw_uuid[10:16].hex(),
                ]
            ).upper()

        elif cmd in (LC.VERSION_MIN_MACOSX,):
            ver, sdk = struct.unpack_from(f"{endian}II", data, lc_off + 8)
            info.min_os = _ver(ver)
            info.sdk = _ver(sdk)

        elif cmd == LC.BUILD_VERSION:
            platform, minos, sdk, ntools = struct.unpack_from(
                f"{endian}IIII", data, lc_off + 8
            )
            info.min_os = _ver(minos)
            info.sdk = _ver(sdk)

        elif cmd == LC.SOURCE_VERSION:
            val = struct.unpack_from(f"{endian}Q", data, lc_off + 8)[0]
            a = (val >> 40) & 0xFFFFFF
            b = (val >> 30) & 0x3FF
            c = (val >> 20) & 0x3FF
            d = (val >> 10) & 0x3FF
            e = val & 0x3FF
            info.source_version = f"{a}.{b}.{c}.{d}.{e}"

        elif cmd in (LC.DYLD_EXPORTS_TRIE,):
            exports_trie_off, exports_trie_size = struct.unpack_from(
                f"{endian}II", data, lc_off + 8
            )
            exports_trie_off += arch_offset

        elif cmd == LC.DYLD_CHAINED_FIXUPS:
            chained_fixups_lc_off = lc_off

        lc_off += cmdsize

    if nsyms > 0:
        info.symbols = _parse_symtab(data, symoff, nsyms, stroff, strsize, is64, endian)

    if exports_trie_off and exports_trie_size:
        info.exports = _parse_exports_trie(data, exports_trie_off, exports_trie_size)

    if chained_fixups_lc_off:
        info.chained_fixups = _parse_chained_fixups(
            data, chained_fixups_lc_off, 0, info.segments, info.libraries
        )

    return info


def list_fat_slices(path: str) -> list[tuple[int, str]]:
    """Return (index, arch) pairs for a fat binary, or single entry for thin."""
    with open(path, "rb") as f:
        fat_magic = struct.unpack_from(">I", f.read(4))[0]

    if fat_magic not in (FAT_MAGIC, FAT_CIGAM):
        with open(path, "rb") as f:
            hdr = f.read(8)
        thin_magic = struct.unpack_from("<I", hdr, 0)[0]
        if thin_magic in (MH_MAGIC, MH_CIGAM, MH_MAGIC_64, MH_CIGAM_64):
            endian = "<" if thin_magic in (MH_MAGIC, MH_MAGIC_64) else ">"
            ct = struct.unpack_from(f"{endian}I", hdr, 4)[0]
            return [(0, CPU_TYPE.get(ct, f"0x{ct:08X}"))]
        return [(0, "unknown")]

    fat_endian = ">" if fat_magic == FAT_MAGIC else "<"
    with open(path, "rb") as f:
        nfat = struct.unpack_from(f"{fat_endian}I", f.read(8), 4)[0]
        full = f.read(nfat * 20)
    slices = []
    for i in range(nfat):
        off = i * 20
        cputype = struct.unpack_from(f"{fat_endian}i", full, off)[0]
        slices.append((i, CPU_TYPE.get(cputype, f"0x{cputype:08X}")))
    return slices
