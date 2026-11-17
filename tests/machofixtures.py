"""Synthetic Mach-O builders for tests: thin slices and fat (universal) files.

A thin slice is the minimal layout used across the GUI tests: a 64-bit
header, one ``__TEXT`` LC_SEGMENT_64 with ``__text`` (64 arm64 NOPs) and
``__cstring`` (a caller-chosen payload string). Segment and section file
offsets are slice-relative, exactly as in real fat binaries.
"""

from __future__ import annotations

import struct

BASE = 0x100000000
NOP = b"\x1f\x20\x03\xd5"

CPU_X86_64 = 0x01000007
CPU_ARM64 = 0x0100000C
SUB_X86_64_ALL = 3
SUB_ARM64_ALL = 0
SUB_ARM64E = 2

FAT_ALIGN = 0x1000  # slice alignment inside the fat file (2^12)


def thin_macho(
    payload: bytes = b"hello strings",
    cputype: int = CPU_ARM64,
    cpusubtype: int = SUB_ARM64_ALL,
) -> bytes:
    """One thin MH_EXECUTE slice; *payload* lands in ``__cstring``.

    Layout: 32-byte header + 72-byte LC_SEGMENT_64 + two 80-byte sections
    = 0x108; ``__text`` (64 NOPs) at 0x108, ``__cstring`` at 0x208.
    """
    cstring = payload + b"\x00"
    size = 0x208 + len(cstring)  # the whole slice; the segment covers it
    header = b"\xcf\xfa\xed\xfe" + struct.pack(
        "<IiIIIII", cputype, cpusubtype, 2, 1, 232, 0, 0
    )
    segment = (
        struct.pack("<II", 0x19, 232)
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack("<QQQQIIII", BASE, 0x2000, 0, size, 5, 5, 2, 0)
    )
    text_sect = (
        b"__text".ljust(16, b"\x00")
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack("<QQIIIIIIII", BASE + 0x108, 0x100, 0x108, 3, 0, 0, 0, 0, 0, 0)
    )
    cstring_sect = (
        b"__cstring".ljust(16, b"\x00")
        + b"__TEXT".ljust(16, b"\x00")
        + struct.pack(
            "<QQIIIIIIII", BASE + 0x208, len(cstring), 0x208, 0, 0, 2, 0, 0, 0, 0
        )
    )
    return header + segment + text_sect + cstring_sect + NOP * 64 + cstring


def fat_macho(slices: list[tuple[int, int, bytes]]) -> bytes:
    """A fat file of ``(cputype, cpusubtype, slice bytes)`` entries.

    Big-endian fat header; each slice starts on a FAT_ALIGN boundary (the
    first one at FAT_ALIGN, like real universal binaries).
    """
    entries = b""
    body = b""
    offset = FAT_ALIGN
    for cputype, cpusubtype, data in slices:
        entries += struct.pack(">iiIII", cputype, cpusubtype, offset, len(data), 12)
        pad = offset - FAT_ALIGN - len(body)
        body += b"\x00" * pad + data
        offset += -(-len(data) // FAT_ALIGN) * FAT_ALIGN
    header = struct.pack(">II", 0xCAFEBABE, len(slices)) + entries
    return header.ljust(FAT_ALIGN, b"\x00") + body


def write_fat(path, payloads: tuple[bytes, bytes] = (b"x86 payload", b"arm payload")):
    """Two-slice fat file (x86_64 + arm64) at *path*; returns ``str(path)``.

    Slice 0 starts at 0x1000, slice 1 at 0x2000.
    """
    data = fat_macho(
        [
            (CPU_X86_64, SUB_X86_64_ALL, thin_macho(payloads[0], CPU_X86_64, 3)),
            (CPU_ARM64, SUB_ARM64_ALL, thin_macho(payloads[1])),
        ]
    )
    with open(path, "wb") as fh:
        fh.write(data)
    return str(path)
