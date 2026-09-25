"""Disassembly via external objdump (GNU binutils or LLVM)."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from typing import Optional

from cheerleader.libs.types import BinaryInfo, DisasmInstruction

# Match instruction lines from both GNU and LLVM objdump.
# GNU:  "    1149:\tf3 0f 1e fa          \tendbr64"
# LLVM: "100000460: d10043ff    \tsub\tsp, sp, #0x10"   (packed hex on ARM)
# LLVM: "100003f50: 55                           pushq   %rbp" (spaced hex on x86)
#
# Hex pairs are space-separated ONLY (no tabs between bytes).  The boundary
# before the mnemonic is always a tab or 2+ consecutive spaces — this
# disambiguates mnemonics whose characters are valid hex (add, dec, fabs…).
_INSN_RE = re.compile(
    r"^\s*([0-9a-fA-F]+):\s+"                         # address
    r"((?:[0-9a-fA-F]{2} )*[0-9a-fA-F]{2,})"          # hex bytes (space-only separators)
    r"[ ]*(?:\t|  )[ \t]*"                              # boundary: tab or 2+ spaces
    r"([a-zA-Z_\.]\S*)"                                 # mnemonic
    r"(?:\s+(.*\S))?\s*$"                               # operands (optional)
)

# Match function label lines emitted by objdump between instruction blocks.
# "0000000100000460 <_helper>:"  or  "0000000000004ad0 <main>:"
_FUNC_LABEL_RE = re.compile(
    r"^([0-9a-fA-F]+)\s+<([^>]+)>:\s*$"
)

# Cached objdump path and type (populated once per process)
_objdump_info: Optional[tuple[str, bool]] = None


def _detect_objdump() -> Optional[tuple[str, bool]]:
    """Locate objdump on PATH and detect whether it is LLVM-based.

    Returns ``(path, is_llvm)`` or ``None`` if not found.
    """
    global _objdump_info
    if _objdump_info is not None:
        return _objdump_info

    for name in ("llvm-objdump", "objdump"):
        path = shutil.which(name)
        if path is None:
            continue
        is_llvm = _check_llvm(path)
        _objdump_info = (path, is_llvm)
        return _objdump_info
    return None


def _check_llvm(path: str) -> bool:
    try:
        out = subprocess.run(
            [path, "--version"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        return "LLVM" in out or "llvm" in out.lower()
    except Exception:
        return platform.system() == "Darwin"


# ---- arch mapping ----------------------------------------------------------

_LLVM_ARCH = {
    "x86_64": "x86_64",
    "x86":    "i386",
    "arm64":  "arm64",
    "arm64e": "arm64",
    "arm64_32": "arm64_32",
    "aarch64": "aarch64",
    "arm":    "arm",
    "riscv":  "riscv64",
    "riscv64": "riscv64",
}


# ---- command building ------------------------------------------------------

def _build_command(
    objdump: str,
    binary: str,
    arch: str,
    is_elf: bool,
    is_llvm: bool,
    *,
    seg_name: str | None = None,
    sect_name: str | None = None,
) -> list[str]:
    """Build the objdump command line.

    When *seg_name*/*sect_name* are ``None`` the binary is disassembled
    in full (all executable sections).
    """
    cmd = [objdump, "-d"]

    # Intel syntax for x86 to match Capstone defaults
    if "x86" in arch or arch in ("i386", "x86_64"):
        if is_llvm:
            cmd.append("--x86-asm-syntax=intel")
        else:
            cmd.extend(["-M", "intel"])

    # Section filter (only when a specific section is requested)
    if sect_name is not None:
        if is_elf:
            cmd.extend(["-j", sect_name])
        else:
            # LLVM objdump on Mach-O needs just the section name
            # (e.g. --section=__text), NOT segment,section.
            if is_llvm:
                cmd.append(f"--section={sect_name}")
            else:
                spec = f"{seg_name},{sect_name}" if seg_name else sect_name
                cmd.extend(["-j", spec])

    # Architecture selector for fat Mach-O
    if not is_elf and is_llvm:
        llvm_arch = _LLVM_ARCH.get(arch)
        if llvm_arch:
            cmd.append(f"--arch={llvm_arch}")

    cmd.append(binary)
    return cmd


# ---- output parsing --------------------------------------------------------

def _parse_output(
    text: str,
) -> tuple[list[DisasmInstruction], dict[int, str]]:
    """Turn objdump text into instructions and function labels.

    Returns ``(instructions, func_labels)`` where *func_labels* maps
    virtual addresses to function names extracted from label lines.
    """
    instructions: list[DisasmInstruction] = []
    func_labels: dict[int, str] = {}

    for line in text.splitlines():
        # Try function label first (cheaper check)
        lm = _FUNC_LABEL_RE.match(line)
        if lm:
            addr = int(lm.group(1), 16)
            name = lm.group(2)
            func_labels[addr] = name
            continue

        m = _INSN_RE.match(line)
        if m is None:
            continue

        addr_s, hex_s, mnemonic, op_str = m.groups()
        addr = int(addr_s, 16)
        raw = bytes.fromhex(hex_s.replace(" ", "").replace("\t", ""))
        mnemonic = mnemonic.strip()
        op_str = (op_str or "").strip()

        # Strip <symbol> annotations added by both GNU and LLVM objdump
        op_str = re.sub(r"\s*<[^>]*>", "", op_str).strip()
        # Strip trailing inline comments (# ...)
        op_str = re.sub(r"\s*#\s.*$", "", op_str).strip()

        # GNU objdump may show call/jump targets as bare hex without 0x;
        # add prefix for CFG regex compatibility
        if op_str and re.match(r"^[0-9a-fA-F]+$", op_str):
            op_str = f"0x{op_str}"

        instructions.append(DisasmInstruction(
            addr=addr,
            size=len(raw),
            mnemonic=mnemonic,
            op_str=op_str,
            raw=raw,
        ))

    return instructions, func_labels


# ---- helpers ---------------------------------------------------------------

def _run_objdump(cmd: list[str]) -> str | None:
    """Run an objdump command and return stdout, or ``None`` on failure."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


# ---- public API -------------------------------------------------------------

def disassemble_section_objdump(
    info: BinaryInfo, seg_name: str, sect_name: str,
) -> list[DisasmInstruction]:
    """Disassemble one section via objdump; returns ``[]`` when unavailable."""
    from cheerleader.formats.elf import ELFInfo

    detected = _detect_objdump()
    if detected is None:
        return []
    objdump, is_llvm = detected
    is_elf = isinstance(info, ELFInfo)

    # Validate that the section exists
    target = None
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

    cmd = _build_command(
        objdump, info.path, info.arch, is_elf, is_llvm,
        seg_name=seg_name, sect_name=sect_name,
    )

    stdout = _run_objdump(cmd)
    if stdout is None:
        return []

    instrs, _ = _parse_output(stdout)
    return instrs


def disassemble_full_objdump(
    info: BinaryInfo,
) -> tuple[list[DisasmInstruction], dict[int, str]]:
    """Disassemble all executable sections via a single objdump invocation.

    Returns ``(instructions, func_labels)``.  *func_labels* maps virtual
    addresses to function names extracted from objdump label lines
    (e.g. ``0000000100000460 <_helper>:``).
    """
    from cheerleader.formats.elf import ELFInfo

    detected = _detect_objdump()
    if detected is None:
        return [], {}
    objdump, is_llvm = detected
    is_elf = isinstance(info, ELFInfo)

    cmd = _build_command(
        objdump, info.path, info.arch, is_elf, is_llvm,
    )

    stdout = _run_objdump(cmd)
    if stdout is None:
        return [], {}

    return _parse_output(stdout)
