# disasm

A terminal-based Mach-O binary inspector for macOS. Opens executables, dylibs, and object files and presents their internal structure — segments, sections, dynamic libraries, symbol tables, export tries, dyld chained fixups, and **disassembled code** — in an interactive text UI.

---

## Requirements

- macOS (arm64 or x86_64)
- [uv](https://github.com/astral-sh/uv) ≥ 0.12

Python and all dependencies are managed by `uv`; no manual `pip install` is needed.

---

## Installation

```sh
git clone <repo>
cd disasm
uv sync
```

---

## Usage

```sh
uv run disasm <binary>
```

Examples:

```sh
uv run disasm /bin/ls
uv run disasm /opt/homebrew/lib/libuv.1.0.0.dylib
uv run disasm ./MyApp.app/Contents/MacOS/MyApp
```

---

## TUI layout

```
┌─ disasm — Mach-O inspector ──────────────────────────────── 12:34:56 ─┐
│ libfoo.dylib  arm64  64-bit  MH_DYLIB                                 │  ← InfoBar
│ UUID: AABBCC…  Min OS: 14.0.0  SDK: 15.0.0                            │
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
│ __TEXT,__stub_helper│  0x100003f4a       │ 68 00 …  │ push     │ 0     │
│ …                   │  …                 │ …        │ …        │ …     │
│                     ├────────────────────────────────────────────────── │
│                     │ __TEXT,__text — 3,817 instructions                │
└────────────────────────────────────────────────────────────────────────┘
  ← section list (28)  ← disassembly table (remaining width) → status bar
```

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

**Disasm tab** — click or navigate the section list on the left to switch sections; disassembly loads in the background.

---

## Tab reference

### 1 · Segments

One row per `LC_SEGMENT_64` / `LC_SEGMENT` load command.

| Column        | Description                                          |
|---------------|------------------------------------------------------|
| Segment       | Segment name (e.g. `__TEXT`, `__DATA_CONST`)         |
| VM Addr       | Virtual memory base address                          |
| VM Size       | Size in virtual memory (may be larger than on disk)  |
| File Off      | Byte offset of segment data within the file          |
| File Size     | Byte size of segment data on disk                    |
| Prot init/max | `rwx` permission bits: initial / maximum             |
| Sections      | Number of sections inside this segment               |

### 2 · Sections

One row per section, flattened across all segments.

| Column   | Description                                             |
|----------|---------------------------------------------------------|
| Segment  | Parent segment name                                     |
| Section  | Section name (e.g. `__text`, `__got`, `__cstring`)      |
| Addr     | Virtual address                                         |
| Size     | Byte size                                               |
| File Off | File offset of section content (slice-relative)         |
| Align    | Alignment expressed as power of two (`2^n`)             |
| Type     | Section type decoded from `flags & 0xFF` (see below)    |
| Relocs   | Number of relocation entries                            |

Section types decoded: `regular`, `zerofill`, `cstring_literals`, `literal_pointers`, `non_lazy_symbol_pointers`, `lazy_symbol_pointers`, `symbol_stubs`, `mod_init_func_pointers`, `mod_term_func_pointers`, `interposing`, thread-local variants, and more.

### 3 · Libraries

One row per dynamic library load command.

| Column      | Description                                              |
|-------------|----------------------------------------------------------|
| #           | Library ordinal (matches dyld fixup `lib_ordinal`)       |
| Name        | Full install path of the library                         |
| Current Ver | `major.minor.patch` version of the library as built      |
| Compat Ver  | Minimum version required for compatibility               |
| Load Type   | `LOAD_DYLIB`, `LOAD_WEAK_DYLIB`, `REEXPORT_DYLIB`, etc. |
| LC Offset   | Byte offset of this load command from the Mach-O header  |

### 4 · Symbols

Parsed from `LC_SYMTAB`. One row per `nlist_64` / `nlist` entry.

| Column  | Description                                    |
|---------|------------------------------------------------|
| Address | `n_value` — virtual address or 0 for undefined |
| Type    | `UNDEF`, `ABS`, `SECT`, `PBUD`, `INDR`, `STAB` |
| Sect    | Section ordinal (1-indexed; 0 = no section)    |
| Binding | `global` (N_EXT), `private` (N_PEXT), `local`  |
| Name    | Symbol name from the string table              |

Default filter hides debug stab entries (type byte `N_STAB = 0xE0`), which are voluminous and rarely useful during binary inspection.

### 5 · Exports

Parsed by walking the compressed exports trie pointed to by `LC_DYLD_EXPORTS_TRIE`. Each node encodes a terminal size, export flags, and a ULEB128-encoded address.

| Column  | Description                                        |
|---------|----------------------------------------------------|
| Address | VM address of the exported symbol                  |
| Flags   | Export flags (0 = regular, 0x08 = weak stub, etc.) |
| Name    | Fully-qualified mangled export name                |

### 6 · Fixups (chained)

Parsed from `LC_DYLD_CHAINED_FIXUPS`. This is the modern dyld fixup format (replaces the older `LC_DYLD_INFO` rebase/bind opcodes). The binary embeds a compact linked list of pointer-sized slots that dyld patches at load time.

| Column          | Description                                                      |
|-----------------|------------------------------------------------------------------|
| Segment         | Segment that contains this fixup slot                            |
| Address         | VM address of the slot                                           |
| Kind            | Pointer encoding: `64_OFFSET`, `ARM64E`, `ARM64E_USERLAND`, etc. |
| Type            | `bind` (import from a library) or `rebase` (image-internal)     |
| Library         | For binds: source library name resolved from ordinal             |
| Symbol / Target | For binds: symbol name; for rebases: target VM address           |
| Addend          | Constant added to the bound or rebased value                     |

### 7 · Disasm

Interactive disassembler powered by [capstone](https://www.capstone-engine.org/). The tab shows two panels:

- **Left (28 cols)**: list of all sections in executable segments (`initprot & 0x4`). `__TEXT,__text` is selected automatically on load.
- **Right**: disassembly table for the selected section. Populated in a background thread so the UI stays responsive for large sections.

| Column   | Description                                               |
|----------|-----------------------------------------------------------|
| Address  | Virtual address of the instruction                        |
| Bytes    | Raw instruction bytes as hex pairs (e.g. `55 48 89 e5`)  |
| Mnemonic | Instruction mnemonic (e.g. `push`, `mov`, `bl`)           |
| Operands | Decoded operands in AT&T / Intel / ARM syntax             |

A status bar at the bottom shows the active section name and instruction count once disassembly completes.

Capstone architecture mapping:

| Mach-O arch            | Capstone arch / mode          |
|------------------------|-------------------------------|
| `x86_64`               | `CS_ARCH_X86 / CS_MODE_64`    |
| `x86`                  | `CS_ARCH_X86 / CS_MODE_32`    |
| `arm64`, `arm64e`, `arm64_32` | `CS_ARCH_ARM64 / CS_MODE_ARM` |
| `arm`                  | `CS_ARCH_ARM / CS_MODE_ARM`   |

---

## Architecture

### Source layout

```
src/disasm/
├── __init__.py      # CLI entry point (main())
├── macho.py         # Mach-O parser — pure stdlib + capstone for disasm
└── tui.py           # Textual TUI — widgets, layout, keybindings
```

### Class diagram

```
disasm.macho
─────────────────────────────────────────────────────────────────────────

  ┌─────────────────────────────────────────────────────────────────────┐
  │ MachOInfo                                                           │
  │─────────────────────────────────────────────────────────────────────│
  │ path: str                                                           │
  │ arch: str              e.g. "arm64", "x86_64"                      │
  │ bits: int              32 or 64                                     │
  │ file_type: str         "MH_EXECUTE", "MH_DYLIB", …                 │
  │ flags: int             mach_header.flags bitmask                    │
  │ ncmds: int             number of load commands                      │
  │ uuid: str | None       formatted as XXXXXXXX-XXXX-…                │
  │ min_os: str | None     decoded from BUILD_VERSION or VERSION_MIN    │
  │ sdk: str | None        SDK version from BUILD_VERSION               │
  │ source_version: str | None  A.B.C.D.E from SOURCE_VERSION          │
  │ slice_offset: int      absolute file offset of this Mach-O slice   │
  │ dylinker: str | None   path from LC_LOAD_DYLINKER                  │
  │ rpaths: list[str]      paths from all LC_RPATH commands             │
  │ exports: list[dict]    {"name", "addr", "flags"} from trie         │
  │ error: str | None      set if parse fails non-fatally               │
  │─────────────────────────────────────────────────────────────────────│
  │ segments: list[Segment]                                             │
  │ libraries: list[Library]                                            │
  │ symbols: list[Symbol]                                               │
  │ chained_fixups: list[ChainedFixup]                                  │
  └──────┬──────────────────────┬──────────────────────────────────────┘
         │ 0..*                  │ 0..*
   ┌─────▼──────┐          ┌────▼─────────────────────────────────────┐
   │ Segment    │          │ Library                                   │
   │────────────│          │───────────────────────────────────────────│
   │ name       │          │ name: str          install path           │
   │ vmaddr     │          │ current_version: str                      │
   │ vmsize     │          │ compat_version: str                       │
   │ fileoff    │          │ load_type: str     LC name                │
   │ filesize   │          │ offset: int        LC byte offset         │
   │ maxprot    │          └──────────────────────────────────────────-┘
   │ initprot   │
   │ prot_str ──┤ property → "r-x/r-x"
   │────────────│
   │ sections: list[Section]                                           │
   └─────┬──────┘
         │ 0..*
   ┌─────▼──────────────────────────────────────────────────────────┐
   │ Section                                                        │
   │────────────────────────────────────────────────────────────────│
   │ name: str          e.g. "__text"                               │
   │ segment: str       parent segment name                         │
   │ addr: int          virtual address                             │
   │ size: int                                                      │
   │ offset: int        slice-relative file offset                  │
   │ align: int         power-of-two exponent                       │
   │ flags: int         type + attribute bitmask                    │
   │ reloff: int        file offset of relocation entries           │
   │ nreloc: int        number of relocation entries                │
   │ type_str ──────────property → decoded section type string      │
   └────────────────────────────────────────────────────────────────┘

   ┌────────────────────────────────────────────────────────────────┐
   │ Symbol                                                         │
   │────────────────────────────────────────────────────────────────│
   │ name: str          from string table                           │
   │ addr: int          n_value                                     │
   │ sym_type: int      n_type byte                                 │
   │ sect: int          n_sect (1-indexed section ordinal)          │
   │ desc: int          n_desc (reference/lazy flags)               │
   │ external: bool     N_EXT flag set                              │
   │ stab: bool         N_STAB mask set (debug entry)               │
   │ type_str ──────────property → "UNDEF"/"SECT"/…/"STAB"         │
   │ binding ───────────property → "global"/"private"/"local"       │
   └────────────────────────────────────────────────────────────────┘

   ┌────────────────────────────────────────────────────────────────┐
   │ ChainedFixup                                                   │
   │────────────────────────────────────────────────────────────────│
   │ segment: str       segment containing the fixup slot           │
   │ offset: int        virtual address of the slot                 │
   │ kind: str          pointer format name ("64_OFFSET", …)        │
   │ lib_ordinal: int | None  1-based library index for binds       │
   │ name: str | None   symbol name (binds) or lib name             │
   │ addend: int        value added to the resolved address         │
   │ is_rebase: bool    True = internal pointer, False = import     │
   │ target: int | None rebased target VM address                   │
   └────────────────────────────────────────────────────────────────┘

   ┌────────────────────────────────────────────────────────────────┐
   │ DisasmInstruction                                              │
   │────────────────────────────────────────────────────────────────│
   │ addr: int          virtual address of the instruction          │
   │ size: int          byte length                                 │
   │ mnemonic: str      e.g. "mov", "bl", "push"                   │
   │ op_str: str        operand string in capstone syntax           │
   │ raw: bytes         raw instruction bytes                       │
   └────────────────────────────────────────────────────────────────┘

  Public functions
  ────────────────
  parse(path, slice_index=0) → MachOInfo
      Full parse of a thin or fat Mach-O file. Sets slice_offset on
      MachOInfo so disassemble_section() can compute absolute file
      positions. All internal parsing functions are module-private.

  list_fat_slices(path) → list[tuple[int, str]]
      Returns [(index, arch_name), …] for fat binaries,
      or [(0, arch_name)] for thin binaries.

  disassemble_section(info, seg_name, sect_name) → list[DisasmInstruction]
      Reads raw bytes from the named section (seeking to
      info.slice_offset + section.offset) and runs them through
      capstone. Returns [] if capstone is not installed or the section
      is not found / has no file content.


disasm.tui
─────────────────────────────────────────────────────────────────────────

  textual.App
  └── DisasmApp
        │  state: path: str, _slice: int, _info: MachOInfo | None,
        │         _slices: list[tuple[int,str]]
        │  actions: action_reload(), action_tab(id), action_slice()
        │
        ├── Header                        textual built-in, shows clock
        ├── InfoBar(Static)               renders MachOInfo as Rich markup
        ├── TabbedContent
        │     ├── SegmentsTab(TabPane)    DataTable, columns fixed at mount
        │     ├── SectionsTab(TabPane)    DataTable
        │     ├── LibrariesTab(TabPane)   DataTable
        │     ├── SymbolsTab(TabPane)     filter bar + DataTable + on_key
        │     ├── ExportsTab(TabPane)     DataTable
        │     ├── ChainedFixupsTab(TabPane) filter bar + DataTable + on_key
        │     └── DisasmTab(TabPane)      section ListView + DataTable
        │           @work(thread=True) _disassemble() runs capstone off-thread
        │           _populate() marshals results back via call_from_thread()
        └── Footer                        textual built-in, shows bindings

  textual.Screen
  └── SlicePicker                modal pushed by DisasmApp.action_slice()
        └── ListView              one ListItem per fat binary slice;
                                  dismisses with the chosen slice index
```

---

## Parser internals

### Magic and endianness detection

The fat binary magic (`0xCAFEBABE`) is always stored big-endian. Each embedded thin Mach-O slice has its own magic in its own native byte order. The parser reads the slice magic as **little-endian** and maps it to a `struct` endian prefix:

| Value (read LE) | Constant       | File byte order |
|-----------------|----------------|-----------------|
| `0xFEEDFACE`    | `MH_MAGIC`     | little-endian   |
| `0xCEFAEDFE`    | `MH_CIGAM`     | big-endian      |
| `0xFEEDFACF`    | `MH_MAGIC_64`  | little-endian   |
| `0xCFFAEDFE`    | `MH_CIGAM_64`  | big-endian      |

`MAGIC` → `endian="<"`, `CIGAM` → `endian=">"`. All subsequent `struct.unpack_from` calls use that prefix. All modern Apple hardware (arm64, x86_64) is little-endian, so CIGAM paths apply only to exotic cross-compiled objects.

### Fat binary slices and slice_offset

A fat binary begins with a `fat_header` (big-endian `uint32` magic + `uint32 nfat_arch`), followed by `nfat_arch × fat_arch` structs (each 20 bytes: `cpu_type i32`, `cpu_subtype i32`, `offset u32`, `size u32`, `align u32`). The `offset` field is the **absolute file position** of each embedded thin Mach-O.

File offsets recorded inside a thin Mach-O's load commands (including section `fileoff` and symbol table `symoff`/`stroff`) are **relative to the slice start**, not to the beginning of the fat file. The parser stores `arch_offset` as `MachOInfo.slice_offset` so that consumers can compute absolute file positions:

```
absolute_file_pos = slice_offset + section.offset
```

`parse()` adds `arch_offset` to `symoff`/`stroff` itself. `disassemble_section()` uses `info.slice_offset + target.offset` when seeking into the file.

### Section struct size and stride

The `section_64` C struct is **80 bytes**:

```
sectname[16]  segname[16]  addr(u64)  size(u64)
offset(u32)  align(u32)  reloff(u32)  nreloc(u32)  flags(u32)
reserved1(u32)  reserved2(u32)  reserved3(u32)
```

The 32-bit `section` struct is **68 bytes** (same but `addr`/`size` are `u32`, and only `reserved1`/`reserved2`). The parser uses the correct full struct format including all reserved fields so that `struct.calcsize` produces the right stride when iterating over multiple sections within a segment.

### Load command walk

After the 32-byte `mach_header_64` (or 28-byte `mach_header` for 32-bit), load commands are laid out contiguously. Each begins with `(cmd: u32, cmdsize: u32)`. The parser advances `lc_off` by `cmdsize` for each of `ncmds` iterations and dispatches on `cmd`:

| `cmd`                      | What is extracted                                  |
|----------------------------|----------------------------------------------------|
| `LC_SEGMENT_64`            | Segment fields + nested section structs            |
| `LC_LOAD_DYLIB` (and variants) | Library name, version fields                   |
| `LC_SYMTAB`                | `symoff`, `nsyms`, `stroff`, `strsize`             |
| `LC_UUID`                  | 16-byte UUID                                       |
| `LC_BUILD_VERSION`         | Platform, `minos`, `sdk`                           |
| `LC_SOURCE_VERSION`        | Packed 40-bit A.B.C.D.E version                    |
| `LC_RPATH`                 | Runtime search path string                         |
| `LC_LOAD_DYLINKER`         | Dynamic linker path                                |
| `LC_DYLD_EXPORTS_TRIE`     | Blob offset + size for export trie                 |
| `LC_DYLD_CHAINED_FIXUPS`   | Blob offset for chained fixup header               |

### Symbol table

`LC_SYMTAB` records `symoff` and `stroff`/`strsize`. The string table is parsed into an `offset → name` dict. Each `nlist_64` entry is 16 bytes: `strx u32`, `n_type u8`, `n_sect u8`, `n_desc u16`, `n_value u64`. The `n_type` byte layout:

```
bits 7–5  N_STAB (0xE0) — if any are set, the entry is a debug stab
bit  4    N_PEXT (0x10) — private external (module-scoped global)
bits 3–1  N_TYPE (0x0E) — 0=UNDEF, 2=ABS, 0xA=INDR, 0xC=PBUD, 0xE=SECT
bit  0    N_EXT  (0x01) — globally visible
```

### Exports trie

The exports trie is a compressed prefix tree stored as a contiguous byte blob. Each node:

1. A ULEB128 `terminal_size` (0 = non-terminal)
2. If terminal: ULEB128 flags, then ULEB128 address (and optionally a stub offset or re-export name)
3. A `u8` child count
4. For each child: NUL-terminated label string + ULEB128 offset to child node

The parser walks this tree recursively, accumulating the prefix string, and emits an export record at every terminal node. ULEB128 decoding is done inline without external dependencies.

### Dyld chained fixups

`LC_DYLD_CHAINED_FIXUPS` points to a blob in `__LINKEDIT` with this layout:

```
dyld_chained_fixups_header   (28 bytes)
  fixups_version   u32
  starts_offset    u32  → dyld_chained_starts_in_image
  imports_offset   u32  → import table
  symbols_offset   u32  → NUL-terminated symbol name pool
  imports_count    u32
  imports_format   u32  (1 = 4-byte entries, 2/3 = 8-byte entries)
  symbols_format   u32

dyld_chained_starts_in_image
  seg_count        u32
  seg_info_offsets u32[seg_count]   (0 = no fixups in that segment)

dyld_chained_starts_in_segment
  size             u32
  page_size        u16   (0x1000 or 0x4000)
  pointer_format   u16   (DYLD_CHAINED_PTR_* constant)
  segment_offset   u64
  max_valid_pointer u32
  page_count       u32
  page_start       u16[page_count]   (0xFFFF = no fixups on this page)
```

For each page with fixups, the parser follows the singly-linked chain of pointer slots. Each 8-byte slot is either a **bind** or a **rebase**:

- **Bind** — high bit (or a format-specific bind flag) set. Low bits carry an import-table ordinal; the import entry maps to a library ordinal + symbol-pool offset.
- **Rebase** — no bind flag. Low bits carry the target VM address (possibly image-relative for `64_OFFSET` format).

The next slot's offset is packed into otherwise-unused bits of the pointer value; the stride is 4 bytes for 64-bit formats. The chain terminates when the next-offset field is zero.

### Disassembly

`disassemble_section()` reads the raw bytes of a section and passes them to capstone's `Cs.disasm()`:

1. Locate the target `Section` by `(seg_name, sect_name)` in `info.segments`.
2. Seek to `info.slice_offset + section.offset` in the file and read `section.size` bytes.
3. Instantiate `capstone.Cs(arch, mode)` based on `info.arch` (see mapping table in tab reference).
4. Iterate `md.disasm(code, section.addr)` — capstone advances through the byte stream, decoding one instruction at a time. The start virtual address (`section.addr`) is passed so that decoded `insn.address` values are correct VM addresses, not file offsets.
5. Return a `list[DisasmInstruction]` with `addr`, `size`, `mnemonic`, `op_str`, and raw `bytes` for each decoded instruction.

The function returns an empty list gracefully if capstone is not importable (e.g. the package was removed from the venv after installation) or if the section has no on-disk content (`offset == 0` or `size == 0`).

In the TUI, disassembly runs in a worker thread (`@work(thread=True)`) to avoid blocking the event loop for large sections. Results are marshalled back to the main thread via `call_from_thread()` before populating the `DataTable`.

---

## Dependencies

| Package    | Role                                                  |
|------------|-------------------------------------------------------|
| `textual`  | TUI framework (widgets, layout, events)               |
| `capstone` | Cross-platform disassembly engine (x86, ARM, ARM64)   |
| `macholib` | Installed, currently unused — reserved                |
| `rich`     | Rich text rendering (pulled in by textual)            |

The parser core (`macho.py`) uses only Python stdlib (`struct`, `os`, `dataclasses`, `enum`) for all parsing except disassembly. `capstone` is imported lazily inside `disassemble_section()` so the rest of the tool works even if it is somehow unavailable.
