# cheerleader

A terminal-based binary inspector for macOS and Linux. Opens Mach-O executables, dylibs, and object files as well as ELF executables and shared libraries, and presents their internal structure — segments, sections, dynamic libraries, symbol tables, exports, dynamic relocations / dyld chained fixups, **disassembled code**, **interactive call-flow graphs**, and **control flow graphs (CFG)** — in an interactive text UI.

---

## Requirements

- macOS (arm64 or x86_64) or Linux (x86_64, aarch64, …)
- [uv](https://github.com/astral-sh/uv) ≥ 0.12

Python and all dependencies are managed by `uv`; no manual `pip install` is needed.

---

## Installation

```sh
git clone <repo>
cd cheerleader
uv sync
```

---

## Usage

```sh
uv run cheerleader <binary>
```

Examples:

```sh
# Mach-O
uv run cheerleader /bin/ls
uv run cheerleader /opt/homebrew/lib/libuv.1.0.0.dylib
uv run cheerleader ./MyApp.app/Contents/MacOS/MyApp

# ELF
uv run cheerleader /usr/bin/ls
uv run cheerleader /lib/x86_64-linux-gnu/libc.so.6
uv run cheerleader ./myprogram
```

---

## TUI layout

```
┌─ cheerleader — binary inspector ─────────────────────────── 12:34:56 ─┐
│ libfoo.dylib  arm64  64-bit  MH_DYLIB                                  │  ← InfoBar
│ UUID: AABBCC…  Min OS: 14.0.0  SDK: 15.0.0                             │
├────────────────────────────────────────────────────────────────────────┤
│ Segments │ Sections │ Libraries │ Symbols │ Exports │ Fixups │ Disasm  │  ← tabs
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   (scrollable DataTable for the active tab)                            │  ← content
│                                                                        │
├────────────────────────────────────────────────────────────────────────┤
│ q Quit  r Reload  s Switch slice  1-7 Tabs                             │  ← Footer
└────────────────────────────────────────────────────────────────────────┘
```

The **Disasm tab** uses a split layout:

```
├─────────────────────────────────────────────────────────────────────────┤
│ __TEXT,__text       │  Address           │ Bytes    │ Mnemonic │ Ops   │
│ __TEXT,__stubs      │  0x100003f44       │ ff 25 …  │ jmp      │ …     │
│ LOAD_0,.text        │  0x100003f4a       │ 68 00 …  │ push     │ 0     │
│ …                   │  …                 │ …        │ …        │ …     │
│                     ├────────────────────────────────────────────────── │
│                     │ __TEXT,__text — 3,817 instructions                │
└────────────────────────────────────────────────────────────────────────┘
  ← section list (28)  ← disassembly table (remaining width) → status bar
```

For Mach-O binaries sections are listed as `SegmentName,SectionName` (e.g. `__TEXT,__text`). For ELF binaries they appear as `LoadSegment,SectionName` (e.g. `LOAD_0,.text`).

### Global keybindings

| Key         | Action                                          |
|-------------|-------------------------------------------------|
| `q`         | Quit                                            |
| `r`         | Reload file from disk                           |
| `s`         | Open slice picker (fat/universal binaries only) |
| `1`         | Segments tab                                    |
| `2`         | Sections tab                                    |
| `3`         | Libraries tab                                   |
| `4`         | Symbols tab                                     |
| `5`         | Exports tab                                     |
| `6`         | Fixups tab                                      |
| `7`         | Disasm tab                                      |
| `↑↓`        | Scroll table rows                               |
| `PgUp/PgDn` | Page through table                              |

### Tab-local keybindings

**Symbols tab** — filter visible rows:

| Key | Filter              |
|-----|---------------------|
| `a` | All symbols         |
| `e` | External (global)   |
| `u` | Undefined (imports) |
| `n` | No stabs (default)  |

**Fixups tab** — filter visible rows:

| Key | Filter    |
|-----|-----------|
| `a` | All       |
| `b` | Binds     |
| `r` | Rebases   |

**Disasm tab** — click or navigate the section list on the left to switch sections; disassembly loads in the background. Press `c` to open the call-flow panel or `f` to open the control flow graph for the enclosing function.

**Call flow panel** (`c` from Disasm tab):

| Key    | Action                                           |
|--------|--------------------------------------------------|
| `↑↓`   | Move through the tree                            |
| `Enter`| Drill into the selected function's call flow     |
| `b`    | Go back to the previous function                 |
| `Esc`  | Close the panel                                  |

**Control flow graph** (`f` from Disasm tab):

| Key               | Action                         |
|-------------------|--------------------------------|
| `↑↓` / `PgUp/PgDn` | Scroll through the graph     |
| `Esc`             | Close the panel                |

---

## Tab reference

### 1 · Segments

One row per memory segment.

- **Mach-O**: one row per `LC_SEGMENT_64` / `LC_SEGMENT` load command.
- **ELF**: one row per `PT_LOAD` program header. Named `LOAD_0`, `LOAD_1`, … in order of appearance. Sections not mapped to any `PT_LOAD` segment (e.g. debug sections) are grouped under `OTHER`.

| Column        | Description                                          |
|---------------|------------------------------------------------------|
| Segment       | Segment name (`__TEXT`, `LOAD_0`, …)                 |
| VM Addr       | Virtual memory base address                          |
| VM Size       | Size in virtual memory (may be larger than on disk)  |
| File Off      | Byte offset of segment data within the file          |
| File Size     | Byte size of segment data on disk                    |
| Prot init/max | `rwx` permission bits: initial / maximum             |
| Sections      | Number of sections inside this segment               |

### 2 · Sections

One row per section, flattened across all segments.

- **Mach-O**: section name and parent segment come directly from the section header.
- **ELF**: section names are the raw ELF section names (`.text`, `.data`, `.rodata`, …). The parent segment is the `PT_LOAD` segment whose virtual address range contains the section, or `OTHER` for unmapped sections.

| Column   | Description                                             |
|----------|---------------------------------------------------------|
| Segment  | Parent segment name                                     |
| Section  | Section name                                            |
| Addr     | Virtual address                                         |
| Size     | Byte size                                               |
| File Off | File offset of section content                          |
| Align    | Alignment expressed as power of two (`2^n`)             |
| Type     | Section type decoded from flags                         |
| Relocs   | Number of relocation entries (Mach-O only)              |

### 3 · Libraries

One row per dynamic library dependency.

- **Mach-O**: one row per `LC_LOAD_DYLIB` / `LC_LOAD_WEAK_DYLIB` / `LC_REEXPORT_DYLIB` / `LC_LAZY_LOAD_DYLIB` load command.
- **ELF**: one row per `DT_NEEDED` entry in the `.dynamic` section.

| Column      | Description                                              |
|-------------|----------------------------------------------------------|
| #           | Library ordinal                                          |
| Name        | Library path or SONAME                                   |
| Current Ver | Version string (Mach-O only; empty for ELF)              |
| Compat Ver  | Minimum compatibility version (Mach-O only)              |
| Load Type   | `LOAD_DYLIB`, `DT_NEEDED`, `REEXPORT_DYLIB`, etc.       |
| LC Offset   | File offset of the load command or dynamic entry         |

### 4 · Symbols

- **Mach-O**: parsed from `LC_SYMTAB` (`nlist_64` / `nlist` entries).
- **ELF**: parsed from `.symtab` when present; falls back to `.dynsym` for stripped binaries.

| Column  | Description                                    |
|---------|------------------------------------------------|
| Address | Virtual address (0 for undefined symbols)      |
| Type    | `UNDEF`, `ABS`, `SECT` (ELF: derived from `st_shndx`) |
| Sect    | Section index (0 = no section / undefined)     |
| Binding | `global`, `private`, or `local`                |
| Name    | Symbol name from the string table              |

Default filter hides debug stab entries (Mach-O `N_STAB`). ELF symbols never set the stab flag so the filter has no effect on them.

### 5 · Exports

- **Mach-O**: walked from the compressed exports trie pointed to by `LC_DYLD_EXPORTS_TRIE`.
- **ELF**: global, defined symbols from `.dynsym` (symbols with non-zero address and global/weak binding).

| Column  | Description                                        |
|---------|----------------------------------------------------|
| Address | VM address of the exported symbol                  |
| Flags   | Export flags (Mach-O) or sym_type (ELF)            |
| Name    | Fully-qualified mangled export name                |

### 6 · Fixups

Dynamic pointer fixups resolved at load time.

- **Mach-O**: parsed from `LC_DYLD_CHAINED_FIXUPS`. Encodes both **bind** (import from library) and **rebase** (image-internal pointer) slots in a compact linked-list chain per page.
- **ELF**: parsed from all `SHT_RELA` and `SHT_REL` sections (`.rela.dyn`, `.rela.plt`, `.rel.dyn`, `.rel.plt`, etc.). Each relocation entry maps to a bind (undefined symbol reference) or rebase (defined symbol reference / image-internal pointer).

| Column          | Description                                                      |
|-----------------|------------------------------------------------------------------|
| Segment         | Segment containing the fixup slot                                |
| Address         | VM address of the slot                                           |
| Kind            | Pointer format (Mach-O: `64_OFFSET`, `ARM64E`, …; ELF: `R_N`)  |
| Type            | `bind` (import) or `rebase` (internal)                          |
| Library         | Source library for binds (Mach-O: resolved from ordinal; ELF: n/a) |
| Symbol / Target | Symbol name (binds) or target VM address (rebases)              |
| Addend          | Constant added to the resolved value                             |

### 7 · Disasm

Interactive disassembler powered by [capstone](https://www.capstone-engine.org/). The tab shows two panels:

- **Left (28 cols)**: list of all sections in executable segments (`initprot & 0x4`). For Mach-O, `__TEXT,__text` is selected automatically on load.
- **Right**: disassembly table for the selected section. Populated in a background thread so the UI stays responsive for large sections.

| Column   | Description                                               |
|----------|-----------------------------------------------------------|
| Address  | Virtual address of the instruction                        |
| Bytes    | Raw instruction bytes as hex pairs (e.g. `55 48 89 e5`)  |
| Mnemonic | Instruction mnemonic (e.g. `push`, `mov`, `bl`)           |
| Operands | Decoded operands in AT&T / Intel / ARM syntax             |

The status bar at the bottom shows the section name, instruction count, and the `c → call flow   f → cfg` hints once disassembly completes.

Capstone architecture mapping:

| Binary arch                              | Capstone arch / mode          |
|------------------------------------------|-------------------------------|
| `x86_64`                                 | `CS_ARCH_X86 / CS_MODE_64`    |
| `x86`                                    | `CS_ARCH_X86 / CS_MODE_32`    |
| `arm64`, `arm64e`, `arm64_32`, `aarch64` | `CS_ARCH_ARM64 / CS_MODE_ARM` |
| `arm`                                    | `CS_ARCH_ARM / CS_MODE_ARM`   |
| `riscv` / `riscv64`                      | `CS_ARCH_RISCV / CS_MODE_RISCV32/64` |

#### Call flow panel

Press `c` while an instruction row is selected to open the **call flow panel** — a centred modal overlay showing the call graph for the function that contains the selected instruction.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    _uv_fs_poll_init  0xf40                               │
├──────────────────────────────────────────────────────────────────────────┤
│  Call Graph                                                              │
│  ▼ ▶ Calls (4)                                                           │
│  │   _uv__handle_init   0x56e4                                           │
│  │   _uv__fs_poll_cb    0x1234                                           │
│  │   _uv_fs_poll_stop   0x5a10                                           │
│  │   <indirect:x8>                                                       │
│  ▼ ▶ Called by (2)                                                       │
│      _uv_fs_poll_start  0x1000                                           │
│      sub_3a90           0x3a90                                           │
├──────────────────────────────────────────────────────────────────────────┤
│          ↑↓ navigate  Enter drill in  b back  Esc close                  │
└──────────────────────────────────────────────────────────────────────────┘
```

- **▶ Calls** (green) — functions directly called by the current function. Indirect calls (e.g. `blr x8`, `call rax`) appear as `<indirect:operand>`.
- **▶ Called by** (yellow) — functions that call the current function within the disassembled section.
- Selecting a leaf and pressing **Enter** navigates to that function's call flow.
- **b** steps back through the navigation history.
- Functions not in the symbol table are named `sub_ADDR` using their hex address. Stripped binaries will show fewer named functions.

> **Note:** the call graph is built from the currently disassembled section only. For the most complete graph, disassemble the main code section (`.text` / `__TEXT,__text`).

#### Control flow graph

Press `f` while an instruction row is selected to open the **control flow graph (CFG)** — a scrollable modal showing the function's basic blocks and the branches between them.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                  CFG:  _process_args  ·  0x100003f44  ·  4 blocks        │
├──────────────────────────────────────────────────────────────────────────┤
│ Block #1  0x100003f44  ⬤ entry                                           │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │  0x100003f44  55             push    rbp                             │ │
│ │  0x100003f45  48 89 e5       mov     rbp, rsp                        │ │
│ │  0x100003f48  48 85 ff       test    rdi, rdi                        │ │
│ │  0x100003f4b  74 10          je      0x100003f5d                     │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│    ├── cond  →  Block #3  0x100003f5d                                    │
│    └── fall  →  Block #2  0x100003f4d                                    │
│                                                                          │
│ Block #2  0x100003f4d  ← from #1                                         │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │  0x100003f4d  48 8b 07       mov     rax, qword ptr [rdi]            │ │
│ │  0x100003f50  ff d0          call    rax                             │ │
│ │  0x100003f52  eb 06          jmp     0x100003f5a                     │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│    └── jump  →  Block #4  0x100003f5a                                    │
│                                                                          │
│ Block #3  0x100003f5d  ← from #1                                         │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │  0x100003f5d  5d             pop     rbp                             │ │
│ │  0x100003f5e  c3             ret                                     │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│    └── ret                                                               │
│                                                                          │
│ Block #4  0x100003f5a  ← from #2                                         │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │  0x100003f5a  5d             pop     rbp                             │ │
│ │  0x100003f5b  c3             ret                                     │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│    └── ret                                                               │
├──────────────────────────────────────────────────────────────────────────┤
│              ↑↓ / PgUp / PgDn  scroll    Esc  close                      │
└──────────────────────────────────────────────────────────────────────────┘
```

Edge types and their colours:

| Edge       | Colour | Meaning                                               |
|------------|--------|-------------------------------------------------------|
| `fall`     | green  | Sequential execution after a conditional branch       |
| `cond`     | yellow | Taken branch of a conditional (`je`, `cbz`, …)        |
| `jump`     | blue   | Unconditional jump (`jmp`, `b`)                       |
| `ret`      | red    | Return instruction — no successor                     |
| `indirect` | dim    | Branch through a register (`br x8`, `jmp rax`)       |

Back-edges (jumps to a block with a lower address — typical of loops) are annotated with `(back-edge)`.

Each block header lists its predecessor block numbers (`← from #N, #M, …`) so you can trace how control arrives without scrolling.

> **Note:** the CFG is built for the function that contains the selected instruction. Function boundaries are derived from the symbol table, so stripped binaries will show the entire section as one large function.

---

## Architecture

### Source layout

```
src/cheerleader/
├── __init__.py              # CLI entry point (main())
├── formats/
│   ├── __init__.py          # Format detection + parser dispatcher
│   ├── base.py              # FormatParser protocol
│   ├── macho.py             # Mach-O parser (fat, thin, 32/64, LE/BE)
│   └── elf.py               # ELF/ELF64 parser (LE/BE, all common arches)
├── libs/
│   ├── __init__.py
│   ├── types.py             # Shared dataclasses (BinaryInfo, Section, Symbol, …)
│   ├── disasm.py            # Format-agnostic disassembly + string extraction
│   └── cfg.py               # Call graph + control flow graph builders
└── tui/
    ├── __init__.py
    ├── app.py               # Textual App + top-level widgets
    ├── tabs.py              # One TabPane per data view
    ├── screens.py           # Modal screens (SlicePicker, CallFlow, CFG)
    ├── widgets.py           # Shared widget helpers
    └── highlight.py         # Syntax highlighting for disassembly
```

### Format detection

`cheerleader.formats.detect_format(path)` reads the first 4 bytes:

| Magic bytes        | Format   |
|--------------------|----------|
| `\x7fELF`          | `"elf"`  |
| Mach-O magic (any) | `"macho"` |
| anything else      | `"unknown"` |

`cheerleader.formats.parse(path, **kwargs)` dispatches to the appropriate parser and always returns a `BinaryInfo` (or subclass). Callers are format-agnostic.

### Class diagram

```
cheerleader.libs.types
─────────────────────────────────────────────────────────────────────────

  ┌─────────────────────────────────────────────────────────────────────┐
  │ BinaryInfo  (base)                                                  │
  │─────────────────────────────────────────────────────────────────────│
  │ path: str                                                           │
  │ arch: str              e.g. "arm64", "x86_64", "aarch64"           │
  │ bits: int              32 or 64                                     │
  │ file_type: str         "MH_EXECUTE", "ET_DYN", …                   │
  │ slice_offset: int      absolute file offset of this slice (0 = thin)│
  │ exports: list[dict]    {"name", "addr", "flags"}                    │
  │ error: str | None      set if parse fails non-fatally               │
  │─────────────────────────────────────────────────────────────────────│
  │ segments: list[Segment]                                             │
  │ libraries: list[Library]                                            │
  │ symbols: list[Symbol]                                               │
  │ chained_fixups: list[ChainedFixup]                                  │
  └─────────────────────────────────────────────────────────────────────┘
           ▲                          ▲
           │                          │
  ┌────────┴───────────┐   ┌──────────┴──────────────┐
  │ MachOInfo          │   │ ELFInfo                  │
  │────────────────────│   │──────────────────────────│
  │ flags: int         │   │ flags: int   e_flags      │
  │ ncmds: int         │   │ entry: int   e_entry      │
  │ uuid: str | None   │   │ os_abi: str               │
  │ min_os: str | None │   │ soname: str | None        │
  │ sdk: str | None    │   │ interp: str | None        │
  │ source_version     │   │ rpath: str | None         │
  │ dylinker           │   │ runpath: str | None       │
  │ rpaths: list[str]  │   └──────────────────────────┘
  └────────────────────┘

  ┌─────┬──────────┐          ┌────┬─────────────────────────────────┐
  │ Segment        │          │ Library                              │
  │────────────────│          │──────────────────────────────────────│
  │ name           │          │ name: str     install path / SONAME  │
  │ vmaddr         │          │ current_version: str                 │
  │ vmsize         │          │ compat_version: str                  │
  │ fileoff        │          │ load_type: str  "LOAD_DYLIB"/        │
  │ filesize       │          │                 "DT_NEEDED", …       │
  │ maxprot        │          │ offset: int     file offset          │
  │ initprot       │          └──────────────────────────────────────┘
  │ prot_str ──────┤ property → "r-x/r-x"
  │────────────────│
  │ sections: list[Section]                                          │
  └──────┬─────────┘
         │ 0..*
  ┌──────▼──────────────────────────────────────────────────────────┐
  │ Section                                                         │
  │─────────────────────────────────────────────────────────────────│
  │ name: str      "__text" / ".text"                               │
  │ segment: str   parent segment name                              │
  │ addr: int      virtual address                                  │
  │ size: int                                                       │
  │ offset: int    file offset (0 for BSS / SHT_NOBITS)            │
  │ align: int     alignment (power-of-two exponent for Mach-O;     │
  │                actual byte alignment for ELF)                   │
  │ flags: int     type + attribute bitmask                         │
  │ reloff: int    relocation entries offset (Mach-O only)          │
  │ nreloc: int    relocation count (Mach-O only)                   │
  │ type_str ──────property → decoded section type string           │
  └─────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────┐
  │ Symbol                                                          │
  │─────────────────────────────────────────────────────────────────│
  │ name: str       from string table                               │
  │ addr: int       virtual address (0 = undefined)                 │
  │ sym_type: int   Mach-O n_type byte, or mapped ELF equivalent:   │
  │                   0x00 (N_UNDF) = SHN_UNDEF                     │
  │                   0x02 (N_ABS)  = SHN_ABS                       │
  │                   0x0E (N_SECT) = defined in a section          │
  │ sect: int       section index (st_shndx for ELF)                │
  │ desc: int       n_desc / st_other                               │
  │ external: bool  global or weak binding                          │
  │ stab: bool      debug stab entry (always False for ELF)         │
  │ type_str ───────property → "UNDEF" / "ABS" / "SECT" / "STAB"   │
  │ binding ────────property → "global" / "private" / "local"       │
  └─────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────┐
  │ ChainedFixup                                                    │
  │─────────────────────────────────────────────────────────────────│
  │ segment: str        segment containing the fixup slot           │
  │ offset: int         VM address of the slot                      │
  │ kind: str           Mach-O: pointer format; ELF: "R_N"          │
  │ lib_ordinal: int | None  1-based library index (Mach-O binds)   │
  │ name: str | None    symbol name (binds) or lib name             │
  │ addend: int         value added to the resolved address         │
  │ is_rebase: bool     True = internal pointer, False = import     │
  │ target: int | None  rebased target VM address (Mach-O rebases)  │
  └─────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────┐
  │ DisasmInstruction                                               │
  │─────────────────────────────────────────────────────────────────│
  │ addr: int       virtual address of the instruction              │
  │ size: int       byte length                                     │
  │ mnemonic: str   e.g. "mov", "bl", "push"                        │
  │ op_str: str     operand string in capstone syntax               │
  │ raw: bytes      raw instruction bytes                           │
  └─────────────────────────────────────────────────────────────────┘
```

---

## Parser internals

### Mach-O

#### Magic and endianness detection

The fat binary magic (`0xCAFEBABE`) is always stored big-endian. Each embedded thin Mach-O slice has its own magic in its own native byte order. The parser reads the slice magic as **little-endian** and maps it to a `struct` endian prefix:

| Value (read LE) | Constant       | File byte order |
|-----------------|----------------|-----------------|
| `0xFEEDFACE`    | `MH_MAGIC`     | little-endian   |
| `0xCEFAEDFE`    | `MH_CIGAM`     | big-endian      |
| `0xFEEDFACF`    | `MH_MAGIC_64`  | little-endian   |
| `0xCFFAEDFE`    | `MH_CIGAM_64`  | big-endian      |

#### Load command walk

After the Mach-O header, load commands are laid out contiguously. Each begins with `(cmd: u32, cmdsize: u32)`. The parser dispatches on `cmd`:

| `cmd`                      | What is extracted                                  |
|----------------------------|----------------------------------------------------|
| `LC_SEGMENT_64`            | Segment fields + nested section structs            |
| `LC_LOAD_DYLIB` (variants) | Library name, version fields                       |
| `LC_SYMTAB`                | `symoff`, `nsyms`, `stroff`, `strsize`             |
| `LC_UUID`                  | 16-byte UUID                                       |
| `LC_BUILD_VERSION`         | Platform, `minos`, `sdk`                           |
| `LC_SOURCE_VERSION`        | Packed 40-bit A.B.C.D.E version                    |
| `LC_RPATH`                 | Runtime search path string                         |
| `LC_LOAD_DYLINKER`         | Dynamic linker path                                |
| `LC_DYLD_EXPORTS_TRIE`     | Blob offset + size for export trie                 |
| `LC_DYLD_CHAINED_FIXUPS`   | Blob offset for chained fixup header               |

#### Exports trie

The exports trie is a compressed prefix tree. Each node has a ULEB128 `terminal_size`, optional export record (flags + address), a child count, and per-child NUL-terminated label strings with ULEB128 node offsets. The parser walks it recursively accumulating the prefix string and emitting a record at every terminal node.

#### Dyld chained fixups

`LC_DYLD_CHAINED_FIXUPS` points to a blob with a `dyld_chained_fixups_header`, a `dyld_chained_starts_in_image` (one offset per segment), and per-segment `dyld_chained_starts_in_segment` (page size, pointer format, per-page chain start offsets). For each page the parser follows a singly-linked chain of 8-byte pointer slots; each slot is either a **bind** (high bit set, carries import-table ordinal + symbol) or a **rebase** (image-internal pointer). The next-slot offset is packed into unused bits of the pointer value.

---

### ELF

#### Header and identity

The 16-byte ELF ident sets `EI_CLASS` (1 = 32-bit, 2 = 64-bit) and `EI_DATA` (1 = LE, 2 = BE). All subsequent parsing uses the derived `is64` flag and `endian` prefix (`<` / `>`). The remaining header fields supply `e_type` (file type), `e_machine` (architecture), `e_entry` (entry point), `e_phoff` / `e_phnum` / `e_phentsize` (program headers), and `e_shoff` / `e_shnum` / `e_shentsize` / `e_shstrndx` (section headers).

#### Program headers → Segments

Each `PT_LOAD` program header becomes a `Segment`. For 64-bit: `p_type(I) p_flags(I) p_offset(Q) p_vaddr(Q) p_paddr(Q) p_filesz(Q) p_memsz(Q) p_align(Q)`. For 32-bit: `p_type(I) p_offset(I) p_vaddr(I) p_paddr(I) p_filesz(I) p_memsz(I) p_flags(I) p_align(I)` (note `p_flags` position differs between 32 and 64-bit ELF).

ELF permission bits (`PF_R=4`, `PF_W=2`, `PF_X=1`) are remapped to Mach-O-style VM protection bits (`VM_PROT_READ=1`, `VM_PROT_WRITE=2`, `VM_PROT_EXECUTE=4`) so the shared UI code (which tests `initprot & 0x4` for executable sections) works correctly for both formats.

`PT_INTERP` and `PT_DYNAMIC` program headers are noted for later parsing.

#### Section headers → Sections

For 64-bit: `sh_name(I) sh_type(I) sh_flags(Q) sh_addr(Q) sh_offset(Q) sh_size(Q) sh_link(I) sh_info(I) sh_addralign(Q) sh_entsize(Q)` (40 bytes). For 32-bit: all fields are 32-bit (40 bytes total). `sh_name` is an offset into the section-header string table (`.shstrtab`), identified by `e_shstrndx`.

Each section is assigned to the `PT_LOAD` segment whose virtual address range contains `sh_addr`. Sections outside any PT_LOAD range (typically debug sections with `sh_addr == 0`) are collected into a virtual `OTHER` segment. `SHT_NOBITS` sections (`.bss`) have their `offset` set to 0 since they occupy no file space.

#### Dynamic section

The `.dynamic` section (or `PT_DYNAMIC` program header for stripped binaries) contains an array of `(d_tag, d_val)` pairs. The parser makes two passes: first to locate `DT_STRTAB` (virtual address of the dynamic string table) and `DT_STRSZ`, then to extract `DT_NEEDED` entries (each a string-table offset → `Library`), `DT_SONAME`, `DT_RPATH`, and `DT_RUNPATH`. The `DT_STRTAB` virtual address is converted to a file offset via the segment map.

#### Symbol table

Both `.symtab` (full symbol table) and `.dynsym` (dynamic symbol table) share the same `Elf_Sym` layout.

64-bit (`Elf64_Sym`, 24 bytes): `st_name(I) st_info(B) st_other(B) st_shndx(H) st_value(Q) st_size(Q)`

32-bit (`Elf32_Sym`, 16 bytes): `st_name(I) st_value(I) st_size(I) st_info(B) st_other(B) st_shndx(H)` (field order differs from 64-bit)

`st_info` encodes `st_bind = st_info >> 4` (0=LOCAL, 1=GLOBAL, 2=WEAK) and `st_type = st_info & 0xF`. The `st_shndx` is mapped to the generic `sym_type` field using Mach-O constants for UI filter compatibility: `SHN_UNDEF → N_UNDF (0x00)`, `SHN_ABS → N_ABS (0x02)`, anything else → `N_SECT (0x0E)`. The `external` flag is set for GLOBAL and WEAK bindings. `.symtab` is preferred; `.dynsym` is used as a fallback for stripped binaries.

#### Dynamic relocations → ChainedFixup

All sections of type `SHT_RELA` and `SHT_REL` are parsed. `sh_link` points to the associated symbol table section (used to resolve symbol names).

`Elf64_Rela` (24 bytes): `r_offset(Q) r_info(Q) r_addend(q)`. `Elf64_Rel` (16 bytes): `r_offset(Q) r_info(Q)`. For 64-bit: `sym_idx = r_info >> 32`, `rel_type = r_info & 0xFFFFFFFF`. For 32-bit: `sym_idx = r_info >> 8`, `rel_type = r_info & 0xFF`.

Each entry becomes a `ChainedFixup` with `kind = "R_<type>"`, `name` resolved from the symbol table, `is_rebase = False` when the symbol is undefined (library import), `is_rebase = True` for defined symbols and image-internal relocations. The `r_offset` virtual address is converted to a file offset to identify the containing segment.

---

## Shared utilities (`cheerleader.libs`)

### `disasm.disassemble_section(info, seg_name, sect_name)`

Locates the named section in `info.segments`, reads raw bytes from disk (`info.slice_offset + section.offset`), and passes them to `capstone.Cs.disasm()` with the section's virtual address so that decoded instruction addresses are correct VM addresses. Returns `[]` if capstone is unavailable or the section has no on-disk content.

### `disasm.extract_strings(info, min_len=4)`

Scans all data-bearing segments for printable ASCII strings of at least `min_len` characters. For each string, binary-searches the sorted section address range list to annotate the owning `segment,section`. Returns a sorted `list[BinaryString]`.

### `cfg.build_call_graph(info, instrs)`

Seeds a function address map from `N_SECT`-typed symbols and export entries, then walks every call/bl/blx/blr instruction to build forward (callees) and reverse (callers) adjacency lists. `CallGraph.func_at(addr)` binary-searches the sorted address list to map any instruction address to its enclosing function.

### `cfg.build_cfg(instrs, func_name, func_addr)`

Four-pass basic-block builder: (1) identify leaders from branch targets and post-branch instructions, (2) group instructions into `CFGBlock`s, (3) compute successor edges (`fall`, `cond`, `jump`, `ret`, `indirect`), (4) back-fill predecessor lists. The CFG screen renders blocks in BFS order from the entry block.

---

## Dependencies

| Package    | Role                                                       |
|------------|------------------------------------------------------------|
| `textual`  | TUI framework (widgets, layout, events)                    |
| `capstone` | Cross-platform disassembly engine (x86, ARM, ARM64, RISC-V)|
| `rich`     | Rich text rendering (pulled in by textual)                 |

The parser cores (`macho.py`, `elf.py`) use only Python stdlib (`struct`, `os`, `dataclasses`, `enum`). `capstone` is imported lazily inside `disassemble_section()` so all other features work even without it.

---

## License

This software is released under the GPL v3 license (see LICENSE file)

---

## Wtf about the name??

I am not at all good at choosing names for code repos. For this one I asked my 5-yo daughter... it is good enough for me :)
