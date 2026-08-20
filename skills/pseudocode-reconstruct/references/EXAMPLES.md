## Architecture-Aware Reconstruction Examples

This file serves as a dispatcher: during pseudocode reconstruction, detect the
source architecture from the instruction patterns and register names, then load
the corresponding architecture-specific examples file from this directory.

### Architecture Detection

| Architecture | Key Identifiers |
|---|---|
| **x86 / x86_64** | `push`/`pop`/`call` instructions; 32/64-bit registers (eax, rbx, rbp, rsp, rdi, rsi); Intel or AT&T syntax instructions like `mov`, `add`, `sub`, `cmp` |
| **ARM / AArch64** | `blr`/`ret`/`stp`/`ldp` instructions; x0–x30 general-purpose registers; w0–w31 (wide) and v0–v31 (NEON) registers; UAL syntax with mnemonic prefixes (e.g., `mov`, `cbz`, `ldrb`) |

**Detection heuristics:**

- If the listing contains `push rbp` or 32/64-bit integer registers (rax, rdi, rsp) → **x86_64**
- If the listing contains `blr x30` or `stp x29, x30, [sp, #...]` → **AArch64**
- If the listing contains 32-bit AT&T syntax (e.g., `movl %eax, %edx`) → **x86**
- If the listing contains NEON/SIMD registers (v0–v31, `fmov`, `fadd`) → **ARM64 NEON**

### Example File Mapping

Use this table to select the correct examples file for your reconstruction task:

| Architecture | Example File |
|---|---|
| x86 / x86_64 | [`x86_64_examples.md`](./x86_64_examples.md) |
| ARM / AArch64 | [`arm64_examples.md`](./arm64_examples.md) |

### Workflow

When the user provides a disassembly dump and asks for pseudocode reconstruction:

1. **Detect architecture** — Scan the first 5–10 instructions for the identifiers above.
2. **Load reference examples** — Open the corresponding file from the mapping table.
3. **Apply patterns** — Use the examples in that file as stylistic and structural references (naming, C control-flow translation, function call handling) when producing the final pseudocode.

### Example Files

- **[`x86_64_examples.md`](./x86_64_examples.md)** — 4 examples covering simple arithmetic, loops with conditional branches, function calls, and SSE vector operations.
- **[`arm64_examples.md`](./arm64_examples.md)** — 5 examples covering simple register moves, conditional branches with stack frames, function calls with callee-saved registers, array traversal loops, and NEON vector operations.
