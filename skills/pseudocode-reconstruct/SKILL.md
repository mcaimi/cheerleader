---
name: pseudocode-reconstruct
description: Use this skill to reconstruct C-like pseudocode from a piece of assembly dump.
---

# Pseudocode Reconstruct — Disassembly to Pseudocode Reconstruction

## Purpose

Reconstruct readable C-like pseudocode from raw disassembly listings. Supports **ARM**, **AArch64 (arm64)**, **x86 (32-bit)**, and **x86_64 (64-bit)** instruction sets.

## Input

The user provides a block of disassembly text. This can be:

- Raw output from a disassembler (objdump, radare2, Ghidra, IDA, Binary Ninja, llvm-objdump)
- A single function or a code fragment
- Intel or AT&T syntax (x86/x86_64)
- ARM UAL (Unified Assembly Language) syntax

The user may optionally specify:

- The target architecture if it cannot be inferred from the instructions
- The calling convention in use (cdecl, stdcall, fastcall, System V AMD64 ABI, AAPCS, AAPCS64)
- Known struct layouts or type information
- The name of the function being reversed

## Procedure

### 1. Identify Architecture and Calling Convention

Determine the ISA from instruction mnemonics:

| Indicator | Architecture |
|---|---|
| `mov eax`, `push ebp`, `call`, `ret`, 32-bit registers (`eax`, `ebx`, `ecx`, `edx`, `esp`, `ebp`, `esi`, `edi`) | x86 (32-bit) |
| `mov rax`, `push rbp`, `callq`, `retq`, 64-bit registers (`rax`, `rbx`, `rcx`, `rdi`, `rsi`, `r8`–`r15`) | x86_64 |
| `mov r0`, `ldr`, `str`, `bx lr`, `bl`, registers `r0`–`r15`, `sp`, `lr`, `pc` | ARM (32-bit) |
| `mov x0`, `ldr`, `str`, `ret`, `bl`, registers `x0`–`x30`, `sp`, `lr`, `xzr`, `wzr` | AArch64 (arm64) |

Infer the calling convention from the architecture unless the user overrides it:

- **x86 (Linux/ELF):** cdecl — args on stack, return in `eax`
- **x86 (Windows):** stdcall/cdecl — args on stack, return in `eax`
- **x86_64 (System V):** args in `rdi`, `rsi`, `rdx`, `rcx`, `r8`, `r9`; return in `rax`
- **x86_64 (Windows):** args in `rcx`, `rdx`, `r8`, `r9`; return in `rax`
- **ARM (AAPCS):** args in `r0`–`r3`; return in `r0`
- **AArch64 (AAPCS64):** args in `x0`–`x7`; return in `x0`

### 2. Recover the Function Prologue and Epilogue

Identify the stack frame setup and teardown:

- **x86/x86_64:** `push rbp; mov rbp, rsp; sub rsp, N` ... `leave; ret`
- **ARM:** `push {fp, lr}; add fp, sp, #4; sub sp, sp, #N` ... `pop {fp, pc}`
- **AArch64:** `stp x29, x30, [sp, #-N]!; mov x29, sp` ... `ldp x29, x30, [sp], #N; ret`

From the prologue, determine:

- Number and size of local variables (from the stack frame size)
- Which callee-saved registers are preserved (they likely hold local variables across calls)

### 3. Map Registers and Stack Slots to Variables

Assign symbolic names to registers and stack locations:

- Function arguments → `arg0`, `arg1`, ... (or meaningful names if context is available)
- Local stack slots → `local0`, `local1`, ... ordered by stack offset
- Callee-saved registers holding values across calls → named local variables
- Return value register → `result` or the function's return expression

### 4. Reconstruct Control Flow

Translate branch instructions into C control-flow constructs:

| Assembly Pattern | C Construct |
|---|---|
| `cmp` / `test` followed by conditional jump (`je`, `jne`, `b.eq`, `cbz`, etc.) with a forward target | `if (...) { ... }` or `if (...) { ... } else { ... }` |
| Conditional backward branch (target address < current address) | `while (...)` or `do { ... } while (...)` loop |
| Unconditional backward branch | `for (;;)` / `while (1)` or loop continuation |
| Indexed jump via jump table (`jmp [rax*8 + table]`, `ldr pc, [rN, rM, lsl #2]`) | `switch (...) { case ...: }` |
| `cmp` + `cmov` / conditional select (`csel`) | Ternary `cond ? a : b` or branchless assignment |

Nested and chained conditions:

- Sequential `cmp`/branch pairs that skip to the same target → `if (a && b)`
- Sequential `cmp`/branch pairs where either leads to the body → `if (a || b)`

### 5. Reconstruct Expressions and Statements

Translate arithmetic, memory access, and function calls:

| Assembly | C Pseudocode |
|---|---|
| `add rax, rbx` / `add x0, x1, x2` | `a = b + c` |
| `sub`, `imul`, `mul`, `sdiv`, `udiv` | corresponding arithmetic operators |
| `shl` / `lsl`, `shr` / `lsr`, `sar` / `asr` | `<<`, `>>` (unsigned), `>>` (signed) |
| `and`, `or`, `xor` / `eor` | `&`, `\|`, `^` |
| `lea rax, [rbx + rcx*4 + 8]` | `a = &b[c + 2]` or `a = base + c * 4 + 8` (context-dependent) |
| `mov [rbp - 0x10], rax` / `str x0, [sp, #16]` | `local_var = value` |
| `mov rax, [rbp - 0x10]` / `ldr x0, [sp, #16]` | `value = local_var` |
| `call func` / `bl func` | `result = func(arg0, arg1, ...)` |
| `mov rdi, rax; call malloc` | `ptr = malloc(size)` |
| `test rax, rax; je ...` / `cbz x0, ...` | `if (ptr == NULL)` |

### 6. Recognize Common Idioms

Apply pattern matching for known compiler idioms:

- **Multiply by constant via shift-add:** `lea rax, [rax + rax*4]` → `a * 5`
- **Division by constant via magic number multiply:** `imul` with a magic constant followed by shift → integer division
- **Sign extension:** `cdq` / `cqo` before `idiv` → signed division
- **memcpy/memset inlined:** `rep movsb` / `rep stosb` or NEON/SVE store loops
- **String length:** loop scanning for zero byte → `strlen()`
- **Virtual function call:** load from vtable pointer (`mov rax, [rdi]; call [rax + offset]`) → `obj->vtable->method(obj, ...)`
- **PIC/GOT access:** `ldr x0, [x0, :got_lo12:symbol]` or `mov rax, [rip + offset]` → global variable access via GOT
- **Stack canary:** load from `fs:[0x28]` or `__stack_chk_guard`, compare before return → stack protection (omit from pseudocode or add as comment)

### 7. Infer Types

Deduce types from instruction width and usage patterns:

- Register width and load/store size → `char` (8-bit), `short` (16-bit), `int` (32-bit), `long`/`int64_t` (64-bit)
- Floating-point registers (`xmm`, `ymm` / `s0`–`s31`, `d0`–`d31`, `v0`–`v31`) → `float`, `double`
- Zero extension (`movzx` / `uxtb`, `uxth`) → unsigned type
- Sign extension (`movsx` / `sxtb`, `sxth`, `sxtw`) → signed type
- Pointer arithmetic and dereference patterns → pointer types
- Array access patterns (`base + index * element_size`) → array types with inferred element size

### 8. Emit Pseudocode

Produce the final C-like output:

- Use standard C types (`int`, `unsigned`, `char *`, `void *`, `int64_t`, etc.)
- Indent according to control flow nesting
- Place variable declarations at the top of the function body
- Add comments for ambiguous constructs, unresolved indirect calls, or unclear type casts
- If a library function is called by name, use its known signature
- If a function is called by address, use a placeholder name (`sub_XXXX` or `func_XXXX`)

## Output Format

```c
// Architecture: {detected_arch}
// Calling convention: {detected_cc}

{return_type} {function_name}({param_list}) {
    // local variable declarations
    {type} {name};

    // reconstructed body
    ...

    return {result};
}
```

## Limitations and Caveats

Report these to the user when applicable:

- **Optimized code:** Aggressive compiler optimizations (inlining, loop unrolling, vectorization, tail calls) may produce pseudocode that does not resemble the original source.
- **Stripped binaries:** Without symbol names, function and variable names are synthetic.
- **Indirect calls and jump tables:** These may not be fully resolvable from a static listing alone.
- **Struct layout:** Without debug info or type definitions, struct field accesses appear as raw offset arithmetic. Ask the user for struct definitions if the code heavily uses pointer+offset patterns.
- **Compiler-specific idioms:** Different compilers (GCC, Clang, MSVC) emit different idioms for the same source construct. State assumptions when a pattern is compiler-dependent.
- **Partial input:** If the disassembly is incomplete (missing the prologue, epilogue, or called functions), note which parts of the pseudocode are uncertain.
