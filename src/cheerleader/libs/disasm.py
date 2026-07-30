"""Disassembly and string-extraction utilities (format-agnostic)."""

from __future__ import annotations

from cheerleader.libs.types import BinaryInfo, BinaryString, DisasmInstruction, Section


def _cs_arch_mode(arch: str, bits: int) -> tuple[int, int]:
    import capstone
    if arch == "x86_64" or (bits == 64 and "x86" in arch):
        return capstone.CS_ARCH_X86, capstone.CS_MODE_64
    if "x86" in arch:
        return capstone.CS_ARCH_X86, capstone.CS_MODE_32
    if arch in ("arm64", "arm64e", "arm64_32"):
        return capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM
    if arch.startswith("arm"):
        return capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM
    return capstone.CS_ARCH_X86, capstone.CS_MODE_64


def disassemble_section(
    info: BinaryInfo, seg_name: str, sect_name: str
) -> list[DisasmInstruction]:
    """Disassemble one section using capstone; returns [] if capstone missing."""
    target: Section | None = None
    for seg in info.segments:
        if seg.name == seg_name:
            for s in seg.sections:
                if s.name == sect_name:
                    target = s
                    break
        if target:
            break

    if target is None or target.offset == 0 or target.size == 0:
        return []

    try:
        import capstone
    except ImportError:
        return []

    arch_id, mode_id = _cs_arch_mode(info.arch, info.bits)
    md = capstone.Cs(arch_id, mode_id)
    md.detail = False

    with open(info.path, "rb") as fh:
        fh.seek(info.slice_offset + target.offset)
        code = fh.read(target.size)

    return [
        DisasmInstruction(
            addr=insn.address,
            size=insn.size,
            mnemonic=insn.mnemonic,
            op_str=insn.op_str,
            raw=bytes(insn.bytes),
        )
        for insn in md.disasm(code, target.addr)
    ]


def extract_strings(info: BinaryInfo, min_len: int = 4) -> list[BinaryString]:
    """Scan all data-bearing segments for printable ASCII strings (≥ min_len chars)."""
    results: list[BinaryString] = []
    _PRINTABLE = frozenset(range(0x20, 0x7F)) | {0x09}

    with open(info.path, "rb") as fh:
        data = fh.read()

    # Build a sorted list of (file_start, file_end, "seg,sect") for annotation.
    sec_ranges: list[tuple[int, int, str]] = []
    for seg in info.segments:
        for sect in seg.sections:
            if sect.offset and sect.size:
                sec_ranges.append((sect.offset, sect.offset + sect.size,
                                   f"{sect.segment},{sect.name}"))
    sec_ranges.sort()

    def _section_at(abs_off: int) -> str:
        lo, hi = 0, len(sec_ranges) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            s, e, lbl = sec_ranges[mid]
            if abs_off < s:
                hi = mid - 1
            elif abs_off >= e:
                lo = mid + 1
            else:
                return lbl
        return "—"

    for seg in info.segments:
        if seg.filesize == 0:
            continue
        seg_data = data[seg.fileoff: seg.fileoff + seg.filesize]
        i = 0
        while i < len(seg_data):
            if seg_data[i] in _PRINTABLE:
                j = i
                while j < len(seg_data) and seg_data[j] in _PRINTABLE:
                    j += 1
                if j - i >= min_len:
                    abs_off = seg.fileoff + i
                    vaddr = (seg.vmaddr + i) if seg.vmaddr else 0
                    results.append(BinaryString(
                        file_offset=abs_off,
                        addr=vaddr,
                        section=_section_at(abs_off),
                        value=seg_data[i:j].decode("ascii", errors="replace"),
                    ))
                i = j + 1
            else:
                i += 1

    results.sort(key=lambda s: s.file_offset)
    return results
