# GUI: choosing the slice of a fat (universal) Mach-O binary

Status: **implemented** (see "Implementation notes" at the end).

## Context

The TUI already lets the user pick the architecture slice of a macOS fat
binary (`s` → `SlicePicker`, backed by `formats.macho.list_fat_slices()` and
`formats.macho.parse(path, slice_index=…)`). The GUI does not: the shell
always parses slice 0 (`GuiApp._start_parse` → `formats.parse(path)`), and
its inspection views identify themselves only by `(view kind, path)`, so two
slices of the same file could not even be open side by side.

Most code that turns slice bytes into file offsets is already slice-aware
through `BinaryInfo.slice_offset` (the absolute offset of the parsed slice):
jump-to-hex from Segments / Sections / Imports / Symbols (`views.py`), and
Capstone disassembly (`libs/disasm.disassemble_section`).

## Decisions

- **Default slice on open: the native architecture.** A fat binary loads the
  slice matching this machine (`platform.machine()`: `arm64` on Apple
  Silicon — also matching `arm64e` — `x86_64` on Intel), falling back to
  slice 0. The user can switch at any time.
- **Switching keeps existing windows.** Windows opened for the previous
  slice stay open as snapshots (the GUI's existing rule for re-opened
  files), titled with their architecture, so two slices can be compared
  side by side.
- **Disassembly engines.** Capstone works on any slice: it reads the slice
  bytes directly through `slice_offset`. On macOS the objdump engine is
  **llvm-objdump only**, and it picks the slice with `--arch=<arch>`
  (already passed by `libs/objdump._build_command`). GNU objdump is used
  on Linux only, where fat binaries do not exist, so it never needs slice
  support.
- **No keyboard shortcut.** The TUI's `s` already means "save" in the GUI
  hex editor; switching is done from the menu and the summary card.

## Changes

### 1. Shared fixes (TUI benefits too)

1. **Strings ignore the slice offset.** `libs/disasm.extract_strings` reads
   `data[seg.fileoff : …]` and reports `file_offset = seg.fileoff + i`, but
   segment file offsets are relative to the slice. For a fat binary — even
   slice 0, which usually starts at `0x4000` — the scan reads the wrong
   bytes. Add `info.slice_offset` to the read window, to the reported
   offsets and to the section ranges used for annotation.
2. **Ambiguous slice labels.** `arm64` and `arm64e` share a CPU type, so
   `list_fat_slices("/usr/bin/lipo")` currently labels two different slices
   both `arm64`. Decode the CPU subtype too and return labels such as
   `arm64e`; the `(index, label)` return shape stays the same, so the TUI
   picker improves unchanged.
3. **llvm-objdump only on macOS.** `libs/objdump._detect_objdump` tries
   `llvm-objdump`, then `objdump`, on Darwin. Accept the `objdump` fallback
   there only if it is LLVM-based (`_check_llvm`, which is what Xcode
   ships); otherwise report the engine as unavailable, so a GNU objdump
   (e.g. Homebrew binutils) is never used on Mach-O files.

### 2. Shell (`gui/app.py`, `gui/const.py`)

- `GuiApp` gains `current_slices: list[tuple[int, str]]` and
  `current_slice: int`.
- `open_file()` lists the slices for Mach-O files and picks the default
  with a new `default_slice(slices)` helper (native arch, else 0).
- `_start_parse(path, slice_index)` passes the slice to the Mach-O parser.
- New `select_slice(index)`: no-op for the current slice; otherwise grays
  the Disassembler menu, clears `current_info` and starts a new parse. A
  parse superseded by a quick second switch is already dropped by the
  parse worker's identity check. On success the new slice's Disassembly
  view opens, as for any successful open.
- **Disassembler ▸ Slice** radio submenu, built like **Engine**
  (`● [1] arm64e`), rebuilt on every open; disabled for single-arch files.
  New menu tag in `const.py`.
- **Summary card:** for fat files, a "Universal binary — N slices" line
  and a dropdown to switch slice; the format line shows the current
  slice's architecture.
- **Status bar:** `path [arch]` for fat files.

### 3. Telling slices apart (`views.py`, `strings.py`, `disasm.py`, `callflow.py`)

- Open views are found by `slice_offset` as well as kind and path. This
  needs no new field — a slice's offset identifies it uniquely. The table,
  Strings and Disassembly openers pass it to `tooling.find_tool`; call flow
  and CFG take it from the Disassembly window's snapshot.
- Window titles of fat files carry the architecture, e.g.
  `Symbols — lipo [x86_64]`, through one shared title helper.

### Unchanged

- The hex editor always shows the whole file; jumps from views already use
  absolute offsets.
- Hex compare.
- Recent files store only the path (the slice is re-picked on open).

## Verification

- **Fixture:** a synthetic two-slice fat binary (x86_64 + arm64) built from
  the existing `_macho_text` test builder.
- **Parser / strings tests:** subtype labels (`arm64` vs `arm64e`);
  `extract_strings` on slice 1 returns absolute offsets that read back the
  right bytes; on macOS a non-LLVM `objdump` is rejected.
- **GUI tests:**
  - the native slice is picked by default (`platform.machine` faked) and
    falls back to slice 0;
  - switching re-parses, opens the new slice's Disassembly and leaves the
    old window open;
  - the same view for two slices gives two windows, and re-opening the same
    slice focuses the existing one;
  - titles carry the architecture; the Slice radio marker moves;
  - single-arch files get a disabled Slice menu and no dropdown;
  - a quick double switch drops the stale parse.
- **Smoke:** a fat-binary step in `scripts/gui_smoke.py`, plus a manual run
  against `/usr/bin/lipo` (three slices: x86_64, arm64e, arm64e.x1).

## Implementation notes

Done as planned, plus three findings from testing against
`/usr/bin/lipo` (slices `x86_64`, `arm64e`, `arm64e.x1`):

- **`--arch` needs the exact slice name.** `llvm-objdump --arch=arm64`
  fails on a file containing only `arm64e` slices, and the old mapping
  sent `arm64e` as `arm64`. The Mach-O parser now reports the precise
  name in `BinaryInfo.arch` (`formats.macho.cpu_arch_name`, matching
  `lipo -archs`), and `libs/objdump` passes it through unchanged (only
  `x86` is renamed to `i386`); unknown architectures get no `--arch`.
- **Capstone matched arm64 variants by exact name**, so `arm64e.x1` would
  have been decoded as 32-bit ARM; `_cs_arch_mode` now matches the
  `arm64` prefix. Capstone still cannot decode the `arm64e.x1`-only
  instructions (e.g. `pacibsppc`, shown as `.byte`); llvm-objdump can.
- **llvm-objdump lists a requested section twice for a fat binary**
  (plain disassembly, then "Contents of (__TEXT,__text) section"), which
  doubled every instruction in the objdump engine's output.
  `libs/objdump._parse_output` now keeps each address once.

Tests: `tests/test_fat_slices.py` (labels, per-slice parsing, slice-aware
strings), `tests/test_gui_slices.py` (GUI flow), new cases in
`tests/test_objdump.py`, and a fat-binary step in `scripts/gui_smoke.py`;
fixtures in `tests/machofixtures.py`.
