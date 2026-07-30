"""ELF/ELF64 binary parser stub."""

from __future__ import annotations

from dataclasses import dataclass

from disasm.libs.types import BinaryInfo

ELF_MAGIC = b'\x7fELF'

_ELF_ARCH = {
    0x03: "x86",
    0x28: "arm",
    0x3e: "x86_64",
    0xb7: "aarch64",
}


@dataclass
class ELFInfo(BinaryInfo):
    """ELF-specific metadata (parser not yet implemented)."""
    pass


def parse(path: str, **kwargs) -> ELFInfo:
    """Minimal ELF stub — returns ELFInfo with an error field set."""
    try:
        with open(path, "rb") as f:
            hdr = f.read(20)
    except OSError as e:
        return ELFInfo(path=path, error=str(e))

    bits = 64 if len(hdr) >= 5 and hdr[4] == 2 else 32
    e_machine = hdr[18] if len(hdr) >= 19 else 0
    arch = _ELF_ARCH.get(e_machine, f"0x{e_machine:02x}")

    return ELFInfo(
        path=path,
        arch=arch,
        bits=bits,
        file_type="ELF",
        error="ELF parsing not yet implemented",
    )
