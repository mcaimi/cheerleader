## ARM64 Reconstruction Examples

---

### Example 1: Simple Register Move

**Input:**

```asm
0x0000000000000000 <strlen@plt>:
  0:   52800110    mov     x0, x1
  4:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64

size_t strlen(char *str) {
    return str;
}
```

---

### Example 2: Conditional Branch with Stack Frame

**Input:**

```asm
0x0000000000000020 <abs_value>:
  20:   a9bf7bfd    stp     x29, x30, [sp, #-16]!
  24:   910003fd    mov     x29, sp
  28:   f9000fe0    str     w0, [x29, #-4]
  2c:   b9000fe0    ldr     w0, [x29, #-4]
  30:   340000a1    cmp     w1, #0x0
  34:   54ffffa5    b.ls    44 <abs_value+0x24>
  38:   b9000fe0    ldr     w0, [x29, #-4]
  3c:   1a0000a0    neg     w0, w0
  40:   b9400fe0    ldr     w0, [x29, #-4]
  44:   a8c27bfd    ldp     x29, x30, [sp], #16
  48:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64

int abs_value(int n) {
    if (n < 0) {
        n = -n;
    }

    return n;
}
```

---

### Example 3: Function Call with Callee-Saved Registers

**Input:**

```asm
0x0000000000000050 <multiply_sum>:
  50:   a9bf7bfd    stp     x29, x30, [sp, #-32]!
  54:   910003fd    mov     x29, sp
  58:   f9000fe0    str     w0, [x29, #-4]
  5c:   f9000fe1    str     w1, [x29, #-8]
  60:   f9000fe0    ldr     w0, [x29, #-4]
  64:   f9000fe1    ldr     w1, [x29, #-8]
  68:   97ffffc2    bl      0 <multiply>
  6c:   f9000fe0    ldr     w0, [x29, #-4]
  70:   f9000fe1    ldr     w1, [x29, #-8]
  74:   97ffffc2    bl      0 <add>
  78:   910003fd    mov     x29, sp
  7c:   a8bf7bfd    ldp     x29, x30, [sp], #32
  80:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64

int multiply_sum(int a, int b) {
    int first_result = multiply(a, b);
    int second_result = add(a, b);

    return first_result + second_result;
}
// Referenced functions: int multiply(int a, int b), int add(int a, int b)
```

---

### Example 4: Loop with Array Traversal

**Input:**

```asm
0x0000000000000090 <array_sum>:
  90:   a9bf7bfd    stp     x29, x30, [sp, #-48]!
  94:   910003fd    mov     x29, sp
  98:   f9000fe0    str     x0, [x29, #-8]
  9c:   b9000fe1    str     w1, [x29, #-12]
  a0:   b9000fe2    str     w0, [x29, #-16]
  a4:   b9000fe3    str     w2, [x29, #-20]
  a8:   b9400fe4    ldr     w4, [x29, #-16]
  ac:   b9400fe5    ldr     w5, [x29, #-8]
  b0:   8b0402a5    add     x5, x5, w4, sxtw #2
  b4:   b9400fe6    ldr     w6, [x5]
  b8:   b9400fe4    ldr     w4, [x29, #-20]
  bc:   b9400fe7    ldr     w7, [x29, #-12]
  c0:   1b0707a4    add     w4, w4, w7
  c4:   b9000fe4    str     w4, [x29, #-16]
  c8:   b9400fe4    ldr     w4, [x29, #-16]
  cc:   b9400fe5    ldr     w5, [x29, #-20]
  d0:   1b0507a4    add     w4, w4, #0x1
  d4:   b9000fe4    str     w4, [x29, #-16]
  d8:   b9400fe4    ldr     w4, [x29, #-16]
  dc:   b9400fe5    ldr     w5, [x29, #-12]
  e0:   2b040000    cmp     w4, w5
  e4:   54ffffe4    b.lt    90 <array_sum>
  e8:   b9400fe2    ldr     w2, [x29, #-16]
  ec:   910003fd    mov     x29, sp
  f0:   a8c27bfd    ldp     x29, x30, [sp], #48
  f4:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64

int array_sum(int *array, int length) {
    int sum = 0;
    int *p = array;
    int limit = length;

    while (p < array + length) {
        sum = sum + *p;
        p = p + 1;
    }

    return sum;
}
```

---

### Example 5: NEON Vector Operations

**Input:**

```asm
0x0000000000000100 <neon_vector_add>:
  100:   a9bf7bfd    stp     x29, x30, [sp, #-16]!
  104:   910003fd    mov     x29, sp
  108:   f9000fe0    str     x0, [x29, #-8]
  10c:   f9000fe1    str     x1, [x29, #-16]
  110:   f9000fe2    str     x2, [x29, #-24]
  114:   f9400fe0    ldr     x0, [x29, #-8]
  118:   f9400fe1    ldr     x1, [x29, #-16]
  11c:   0e20f820    fmov    v0.2d, x0
  120:   0e20fc20    fmov    v0.2d, v1.2d
  124:   0e21fc21    fmov    v1.2d, x1
  128:   1e282000    fadd    v0.2s, v0.2s, v1.2s
  12c:   f9000fe0    str     x0, [x29, #-8]
  130:   f9400fe0    ldr     x0, [x29, #-24]
  134:   0e202820    fmov    v0.2d, x0
  138:   0e202c20    fmov    v0.2d, v0.2d
  13c:   f9000fe2    ldr     x2, [x29, #-24]
  140:   0e202c22    fmov    v2.2d, v0.2d
  144:   8b000220    add     x0, x0, x2
  148:   0e203821    fmov    v1.2d, v0.2d
  14c:   b9400fe0    ldr     w0, [x29, #-8]
  150:   910003fd    mov     x29, sp
  154:   a8c27bfd    ldp     x29, x30, [sp], #16
  158:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64
// Note: Uses ARM NEON (Advanced SIMD) instructions

void neon_vector_add(float *a, float *b, float *result) {
    float local0 = *a;
    float local1 = *b;
    float local2 = local0 + local1;

    *result = local2;
}
```
