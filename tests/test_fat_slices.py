"""Fat (universal) Mach-O: slice listing, per-slice parsing, slice-aware strings."""

from __future__ import annotations

from machofixtures import (
    CPU_ARM64,
    CPU_X86_64,
    SUB_ARM64E,
    fat_macho,
    thin_macho,
    write_fat,
)

from cheerleader.formats.macho import cpu_arch_name, list_fat_slices, parse
from cheerleader.libs.disasm import extract_strings


def test_cpu_arch_name_decodes_subtypes() -> None:
    assert cpu_arch_name(CPU_ARM64, 0) == "arm64"
    assert cpu_arch_name(CPU_ARM64, 2) == "arm64e"
    assert cpu_arch_name(CPU_ARM64, 0x80000002 - (1 << 32)) == "arm64e"  # caps bits
    assert cpu_arch_name(CPU_ARM64, 12) == "arm64e.x1"
    assert cpu_arch_name(CPU_X86_64, 3) == "x86_64"
    assert cpu_arch_name(CPU_X86_64, 8) == "x86_64h"
    assert cpu_arch_name(CPU_ARM64, 99) == "arm64"  # unknown subtype: family
    assert cpu_arch_name(0x2A, 0) == "0x0000002A"


def test_list_fat_slices_distinguishes_arm64e(tmp_path) -> None:
    path = tmp_path / "u.bin"
    path.write_bytes(
        fat_macho(
            [
                (CPU_ARM64, 0, thin_macho(b"plain")),
                (CPU_ARM64, SUB_ARM64E, thin_macho(b"ptrauth", CPU_ARM64, SUB_ARM64E)),
            ]
        )
    )
    assert list_fat_slices(str(path)) == [(0, "arm64"), (1, "arm64e")]


def test_list_thin_slice_uses_subtype(tmp_path) -> None:
    path = tmp_path / "t.bin"
    path.write_bytes(thin_macho(b"x", CPU_ARM64, SUB_ARM64E))
    assert list_fat_slices(str(path)) == [(0, "arm64e")]


def test_parse_each_slice(tmp_path) -> None:
    path = write_fat(tmp_path / "fat.bin")
    x86, arm = parse(path, slice_index=0), parse(path, slice_index=1)
    assert (x86.arch, x86.slice_offset) == ("x86_64", 0x1000)
    assert (arm.arch, arm.slice_offset) == ("arm64", 0x2000)


def test_strings_read_the_selected_slice(tmp_path) -> None:
    """Offsets are slice-relative in the load commands: the scan must add
    slice_offset (even slice 0 of a fat file starts past the fat header)."""
    path = write_fat(tmp_path / "fat.bin")
    with open(path, "rb") as fh:
        raw = fh.read()
    for index, payload in ((0, "x86 payload"), (1, "arm payload")):
        info = parse(path, slice_index=index)
        strings = extract_strings(info)
        values = [s.value for s in strings]
        assert payload in values
        other = "arm payload" if index == 0 else "x86 payload"
        assert other not in values  # never the other slice's bytes
        hit = next(s for s in strings if s.value == payload)
        assert hit.file_offset == info.slice_offset + 0x208  # absolute
        assert raw[hit.file_offset : hit.file_offset + len(payload)] == payload.encode()
        assert hit.section == "__TEXT,__cstring"


def test_thin_strings_unchanged(tmp_path) -> None:
    path = tmp_path / "t.bin"
    path.write_bytes(thin_macho(b"thin payload"))
    strings = extract_strings(parse(str(path)))
    hit = next(s for s in strings if s.value == "thin payload")
    assert hit.file_offset == 0x208
