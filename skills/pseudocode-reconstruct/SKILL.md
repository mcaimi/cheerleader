---
name: pseudocode-reconstruct
description: Reconstruct readable C-like pseudocode from raw disassembly listings. Supports ARM, AArch64 (arm64), x86 (32-bit), and x86_64 (64-bit) instruction sets. Use when working with assembly dumps from disassemblers like objdump, radare2, Ghidra, IDA, Binary Ninja, or llvm-objdump, or when users provide raw disassembly output for reverse engineering tasks.
license: MIT
metadata:
  author: mcaimi@redhat.com
  version: "1.0"
  category: reverse-engineering
  domains: "assembly, disassembly, reverse-engineering, binary-analysis"
---

# Pseudocode Reconstruct - Disassembly to Pseudocode Reconstruction

## Input format

The user provides a block of disassembly text. This can be:

- Raw output from a disassembler (objdump, radare2, Ghidra, IDA, Binary Ninja, llvm-objdump)
- A single function or a code fragment
- Intel or AT&T syntax (x86/x86_64)
- ARM UAL (Unified Assembly Language) syntax

Optional user specifications:

- Target architecture (if not inferable from instructions)
- Calling convention (cdecl, stdcall, fastcall, System V AMD64 ABI, AAPCS, AAPCS64)
- Known struct layouts or type information
- Function name for the code being reversed

## Procedure

For detailed step-by-step instructions, see [the procedure guide](references/PROCEDURE.md).

The reconstruction process follows these main phases:

1. **Architecture Detection:** Identify the instruction set architecture (ISA) from instruction patterns and register usage
2. **Stack Frame Analysis:** Recover function prologue/epilogue to understand local variable layout
3. **Symbol Mapping:** Map registers and stack slots to symbolic variable names
4. **Control Flow Reconstruction:** Translate branches into C control flow constructs
5. **Expression Translation:** Convert arithmetic and memory operations to C expressions
6. **Idiom Recognition:** Apply pattern matching for common compiler optimizations
7. **Type Inference:** Deduce variable types from instruction width and usage
8. **Pseudocode Generation:** Emit the final C-like pseudocode with proper formatting

## Output format

For detailed output specifications, see [the output format document](references/OUTPUT_FORMAT.md).

## Limitations and caveats

Report these to the user when applicable:

- **Optimized code:** Aggressive compiler optimizations (inlining, loop unrolling, vectorization, tail calls) may produce pseudocode that does not resemble the original source.
- **Stripped binaries:** Without symbol names, function and variable names are synthetic.
- **Indirect calls and jump tables:** These may not be fully resolvable from a static listing alone.
- **Struct layout:** Without debug info or type definitions, struct field accesses appear as raw offset arithmetic. Ask the user for struct definitions if the code heavily uses pointer+offset patterns.
- **Compiler-specific idioms:** Different compilers (GCC, Clang, MSVC) emit different idioms for the same source construct. State assumptions when a pattern is compiler-dependent.
- **Partial input:** If the disassembly is incomplete (missing the prologue, epilogue, or called functions), note which parts of the pseudocode are uncertain.

## Examples

Refer to the [examples doucument](references/EXAMPLES.md) for reconstruction examples.

## Best practices

When using this skill:

1. **Provide context:** Include as much disassembly as possible, especially the function prologue and epilogue
2. **Specify architecture:** If you know the architecture, mention it to help with accurate reconstruction
3. **Share type information:** If you have any knowledge about the function's purpose or expected types, provide it
4. **Ask for clarification:** If the pseudocode reconstruction is ambiguous, ask the skill to explain its assumptions
