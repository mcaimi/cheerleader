"""Binary format detection and parser dispatcher."""

from __future__ import annotations

import struct

from cheerleader.libs.types import BinaryInfo

_MACHO_MAGICS = frozenset({
    0xFEEDFACE, 0xCEFAEDFE,   # 32-bit LE/BE
    0xFEEDFACF, 0xCFFAEDFE,   # 64-bit LE/BE
    0xCAFEBABE, 0xBEBAFECA,   # fat LE/BE
})


def detect_format(path: str) -> str:
    """Return "macho", "elf", or "unknown" by reading the first 4 bytes."""
    try:
        with open(path, "rb") as f:
            raw = f.read(4)
    except OSError:
        return "unknown"
    if len(raw) < 4:
        return "unknown"
    if raw[:4] == b'\x7fELF':
        return "elf"
    le_val = struct.unpack_from("<I", raw)[0]
    if le_val in _MACHO_MAGICS:
        return "macho"
    be_val = struct.unpack_from(">I", raw)[0]
    if be_val in _MACHO_MAGICS:
        return "macho"
    return "unknown"


def parse(path: str, **kwargs) -> BinaryInfo:
    """Detect format and dispatch to the appropriate parser."""
    fmt = detect_format(path)
    if fmt == "macho":
        from cheerleader.formats.macho import parse as _macho_parse
        return _macho_parse(path, **kwargs)
    if fmt == "elf":
        from cheerleader.formats.elf import parse as _elf_parse
        return _elf_parse(path, **kwargs)
    return BinaryInfo(path=path, error="Unrecognised file format")
