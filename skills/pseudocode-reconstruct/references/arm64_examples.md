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

---

### Example 6: Switch/Case with Branch Table

**Input:**

```asm
0x0000000000001160 <handle_opcode>:
  1160:   71000c1f    cmp     w0, #0x3
  1164:   54000228    b.hi    11a8
  1168:   10000081    adr     x1, 1178
  116c:   b8a06820    ldrsw   x0, [x1, x0, lsl #2]
  1170:   8b000020    add     x0, x1, x0
  1174:   d61f0000    br      x0
  ; --- jump table (4 x 32-bit signed offsets) ---
  1178:   00000010    .word   0x10
  117c:   00000018    .word   0x18
  1180:   00000020    .word   0x20
  1184:   00000028    .word   0x28
  ; --- case blocks ---
  1188:   52800020    mov     w0, #0x1
  118c:   14000009    b       11b0
  1190:   528000a0    mov     w0, #0xa
  1194:   14000007    b       11b0
  1198:   52800c80    mov     w0, #0x64
  119c:   14000005    b       11b0
  11a0:   52807d00    mov     w0, #0x3e8
  11a4:   14000003    b       11b0
  ; --- default ---
  11a8:   12800000    mov     w0, #0xffffffff
  11ac:   14000001    b       11b0
  ; --- epilogue ---
  11b0:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64
// Note: Uses adr+ldrsw+br computed branch for jump table dispatch

int handle_opcode(int op) {
    switch (op) {
        case 0:
            return 1;
        case 1:
            return 10;
        case 2:
            return 100;
        case 3:
            return 1000;
        default:
            return -1;
    }
}
```

---

### Example 7: Struct Field Access (Linked List Traversal)

**Input:**

```asm
0x00000000000011c0 <list_find>:
  11c0:   a9bd7bfd    stp     x29, x30, [sp, #-48]!
  11c4:   910003fd    mov     x29, sp
  11c8:   f90013e0    str     x0, [sp, #32]
  11cc:   b9001fe1    str     w1, [sp, #28]
  11d0:   f94013e0    ldr     x0, [sp, #32]
  11d4:   f9000fe0    str     x0, [sp, #24]
  11d8:   1400000b    b       1204
  11dc:   f9400fe0    ldr     x0, [sp, #24]
  11e0:   b9400000    ldr     w0, [x0]
  11e4:   b9401fe1    ldr     w1, [sp, #28]
  11e8:   6b01001f    cmp     w0, w1
  11ec:   54000061    b.ne    11f8
  11f0:   f9400fe0    ldr     x0, [sp, #24]
  11f4:   14000007    b       1210
  11f8:   f9400fe0    ldr     x0, [sp, #24]
  11fc:   f9400400    ldr     x0, [x0, #8]
  1200:   f9000fe0    str     x0, [sp, #24]
  1204:   f9400fe0    ldr     x0, [sp, #24]
  1208:   b5fffea0    cbnz    x0, 11dc
  120c:   d2800000    mov     x0, #0x0
  1210:   a8c37bfd    ldp     x29, x30, [sp], #48
  1214:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64
// Inferred struct layout:
//   struct node {
//       int value;         /* offset 0x0 */
//       int _pad;          /* offset 0x4 (alignment padding) */
//       struct node *next; /* offset 0x8 */
//   };

struct node *list_find(struct node *head, int key) {
    struct node *current = head;

    while (current != NULL) {
        if (current->value == key) {
            return current;
        }
        current = current->next;
    }

    return NULL;
}
```

---

### Example 8: Conditional Select (Branchless Ternary)

**Input:**

```asm
0x0000000000001220 <max_value>:
  1220:   6b01001f    cmp     w0, w1
  1224:   1a81a000    csel    w0, w0, w1, ge
  1228:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64

int max_value(int a, int b) {
    int result = (a >= b) ? a : b;

    return result;
}
```

---

### Example 9: Nested Loops (Matrix Sum)

**Input:**

```asm
0x0000000000001240 <matrix_sum>:
  1240:   a9bb7bfd    stp     x29, x30, [sp, #-80]!
  1244:   910003fd    mov     x29, sp
  1248:   f90027e0    str     x0, [x29, #72]
  124c:   b90043e1    str     w1, [x29, #64]
  1250:   b9003fe2    str     w2, [x29, #60]
  1254:   b90037ff    str     wzr, [x29, #52]
  1258:   b90033ff    str     wzr, [x29, #48]
  125c:   1400001a    b       12c4
  1260:   b9002fff    str     wzr, [x29, #44]
  1264:   14000011    b       12a8
  1268:   b94033e0    ldr     w0, [x29, #48]
  126c:   b9403fe1    ldr     w1, [x29, #60]
  1270:   1b017c00    mul     w0, w0, w1
  1274:   b9402fe1    ldr     w1, [x29, #44]
  1278:   0b010000    add     w0, w0, w1
  127c:   93407c00    sxtw    x0, w0
  1280:   d37ef400    lsl     x0, x0, #2
  1284:   f94027e1    ldr     x1, [x29, #72]
  1288:   8b000020    add     x0, x1, x0
  128c:   b9400000    ldr     w0, [x0]
  1290:   b94037e1    ldr     w1, [x29, #52]
  1294:   0b000020    add     w0, w1, w0
  1298:   b90037e0    str     w0, [x29, #52]
  129c:   b9402fe0    ldr     w0, [x29, #44]
  12a0:   11000400    add     w0, w0, #0x1
  12a4:   b9002fe0    str     w0, [x29, #44]
  12a8:   b9402fe0    ldr     w0, [x29, #44]
  12ac:   b9403fe1    ldr     w1, [x29, #60]
  12b0:   6b01001f    cmp     w0, w1
  12b4:   54fff6ab    b.lt    1268
  12b8:   b94033e0    ldr     w0, [x29, #48]
  12bc:   11000400    add     w0, w0, #0x1
  12c0:   b90033e0    str     w0, [x29, #48]
  12c4:   b94033e0    ldr     w0, [x29, #48]
  12c8:   b94043e1    ldr     w1, [x29, #64]
  12cc:   6b01001f    cmp     w0, w1
  12d0:   54fffc8b    b.lt    1260
  12d4:   b94037e0    ldr     w0, [x29, #52]
  12d8:   a8c57bfd    ldp     x29, x30, [sp], #80
  12dc:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64

int matrix_sum(int *matrix, int rows, int cols) {
    int sum = 0;

    for (int i = 0; i < rows; i++) {
        for (int j = 0; j < cols; j++) {
            sum += matrix[i * cols + j];
        }
    }

    return sum;
}
```

---

### Example 10: do-while Loop

**Input:**

```asm
0x00000000000012e0 <sum_digits>:
  12e0:   a9be7bfd    stp     x29, x30, [sp, #-32]!
  12e4:   910003fd    mov     x29, sp
  12e8:   b9001fe0    str     w0, [x29, #28]
  12ec:   b9001bff    str     wzr, [x29, #24]
  12f0:   b9401fe0    ldr     w0, [x29, #28]
  12f4:   528000a1    mov     w1, #0xa
  12f8:   1ac10c02    sdiv    w2, w0, w1
  12fc:   1b018040    msub    w0, w2, w1, w0
  1300:   b9401be1    ldr     w1, [x29, #24]
  1304:   0b000020    add     w0, w1, w0
  1308:   b9001be0    str     w0, [x29, #24]
  130c:   b9401fe0    ldr     w0, [x29, #28]
  1310:   528000a1    mov     w1, #0xa
  1314:   1ac10c00    sdiv    w0, w0, w1
  1318:   b9001fe0    str     w0, [x29, #28]
  131c:   b9401fe0    ldr     w0, [x29, #28]
  1320:   7100001f    cmp     w0, #0x0
  1324:   54fffe6c    b.gt    12f0
  1328:   b9401be0    ldr     w0, [x29, #24]
  132c:   a8c27bfd    ldp     x29, x30, [sp], #32
  1330:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64

int sum_digits(int n) {
    int sum = 0;

    do {
        sum += n % 10;
        n = n / 10;
    } while (n > 0);

    return sum;
}
```

---

### Example 11: Tail Call Optimization

**Input:**

```asm
0x0000000000001340 <factorial_helper>:
  1340:   a9be7bfd    stp     x29, x30, [sp, #-32]!
  1344:   910003fd    mov     x29, sp
  1348:   b9001fe0    str     w0, [x29, #28]
  134c:   b9001be1    str     w1, [x29, #24]
  1350:   b9401fe0    ldr     w0, [x29, #28]
  1354:   7100041f    cmp     w0, #0x1
  1358:   5400006c    b.gt    1364
  135c:   b9401be0    ldr     w0, [x29, #24]
  1360:   a8c27bfd    ldp     x29, x30, [sp], #32
  1364:   d65f03c0    ret
  1368:   b9401fe0    ldr     w0, [x29, #28]
  136c:   51000400    sub     w0, w0, #0x1
  1370:   b9401fe1    ldr     w1, [x29, #28]
  1374:   b9401be2    ldr     w2, [x29, #24]
  1378:   1b027c21    mul     w1, w1, w2
  137c:   a8c27bfd    ldp     x29, x30, [sp], #32
  1380:   17fffff0    b       1340 <factorial_helper>
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64
// Note: Tail call optimization — b replaces bl+ret

int factorial_helper(int n, int acc) {
    if (n <= 1) {
        return acc;
    }

    return factorial_helper(n - 1, n * acc);
}
```

---

### Example 12: Recursive Function (Self-Call)

**Input:**

```asm
0x0000000000001440 <power>:
  1440:   a9be7bfd    stp     x29, x30, [sp, #-32]!
  1444:   910003fd    mov     x29, sp
  1448:   b9001fe0    str     w0, [x29, #28]
  144c:   b9001be1    str     w1, [x29, #24]
  1450:   b9401be0    ldr     w0, [x29, #24]
  1454:   35000060    cbnz    w0, 1460
  1458:   52800020    mov     w0, #0x1
  145c:   14000009    b       1480
  1460:   b9401fe0    ldr     w0, [x29, #28]
  1464:   b9401be1    ldr     w1, [x29, #24]
  1468:   51000421    sub     w1, w1, #0x1
  146c:   97fffff5    bl      1440 <power>
  1470:   b9401fe1    ldr     w1, [x29, #28]
  1474:   1b017c00    mul     w0, w0, w1
  1478:   d503201f    nop
  147c:   d503201f    nop
  1480:   a8c27bfd    ldp     x29, x30, [sp], #32
  1484:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64

int power(int base, int exp) {
    if (exp == 0) {
        return 1;
    }

    return base * power(base, exp - 1);
}
// Note: Recursive — bl targets own address (0x1440)
```

---

### Example 13: Division-by-Constant (Magic Number Multiply)

**Input:**

```asm
0x0000000000001490 <divide_by_7>:
  1490:   52824920    mov     w1, #0x2493
  1494:   72b24921    movk    w1, #0x9249, lsl #16
  1498:   9b217c01    smull   x1, w0, w1
  149c:   9360fc21    asr     x1, x1, #32
  14a0:   0b000021    add     w1, w1, w0
  14a4:   13027c21    asr     w1, w1, #2
  14a8:   531f7c00    lsr     w0, w0, #31
  14ac:   0b000020    add     w0, w1, w0
  14b0:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64
// Note: Compiler replaces division by constant 7 with
//       multiply-by-magic-number idiom (magic=0x92492493, shift=2)

int divide_by_7(int n) {
    return n / 7;
}
```

---

### Example 14: Indirect Call / Vtable Dispatch

**Input:**

```asm
0x00000000000014c0 <call_virtual>:
  14c0:   a9be7bfd    stp     x29, x30, [sp, #-32]!
  14c4:   910003fd    mov     x29, sp
  14c8:   f90013e0    str     x0, [sp, #32]
  14cc:   b9001fe1    str     w1, [sp, #28]
  14d0:   f9400000    ldr     x0, [x0]
  14d4:   f9400808    ldr     x8, [x0, #16]
  14d8:   f94013e0    ldr     x0, [sp, #32]
  14dc:   b9401fe1    ldr     w1, [sp, #28]
  14e0:   d63f0100    blr     x8
  14e4:   a8c27bfd    ldp     x29, x30, [sp], #32
  14e8:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64
// Note: Indirect call through vtable — likely C++ virtual method dispatch
//       obj -> vtable_ptr -> vtable[2] (offset 0x10 = slot index 2)

int call_virtual(void *obj, int arg) {
    void **vtable = *(void ***)obj;
    int (*method)(void *, int) = (int (*)(void *, int))vtable[2];

    return method(obj, arg);
}
```

---

### Example 15: Error-Path with Multiple Early Returns

**Input:**

```asm
0x0000000000001500 <parse_header>:
  1500:   a9bd7bfd    stp     x29, x30, [sp, #-48]!
  1504:   910003fd    mov     x29, sp
  1508:   f90017e0    str     x0, [x29, #40]
  150c:   b90027e1    str     w1, [x29, #36]
  1510:   f94017e0    ldr     x0, [x29, #40]
  1514:   b5000080    cbnz    x0, 1524
  1518:   12800000    mov     w0, #0xffffffff
  151c:   14000015    b       1570
  1520:   d503201f    nop
  1524:   b94027e0    ldr     w0, [x29, #36]
  1528:   7100101f    cmp     w0, #0x4
  152c:   5400006a    b.ge    1538
  1530:   12800020    mov     w0, #0xfffffffe
  1534:   1400000f    b       1570
  1538:   f94017e0    ldr     x0, [x29, #40]
  153c:   39400000    ldrb    w0, [x0]
  1540:   7101fc1f    cmp     w0, #0x7f
  1544:   54000060    b.eq    1550
  1548:   12800040    mov     w0, #0xfffffffd
  154c:   14000009    b       1570
  1550:   f94017e0    ldr     x0, [x29, #40]
  1554:   39400400    ldrb    w0, [x0, #1]
  1558:   f94017e1    ldr     x1, [x29, #40]
  155c:   39400821    ldrb    w1, [x1, #2]
  1560:   2a012000    orr     w0, w0, w1, lsl #8
  1564:   f94017e1    ldr     x1, [x29, #40]
  1568:   39400c21    ldrb    w1, [x1, #3]
  156c:   2a014000    orr     w0, w0, w1, lsl #16
  1570:   a8c37bfd    ldp     x29, x30, [sp], #48
  1574:   d65f03c0    ret
```

**Output:**

```c
// Architecture: AArch64
// Calling convention: AAPCS64

int parse_header(char *buf, int len) {
    if (buf == NULL) {
        return -1;
    }

    if (len < 4) {
        return -2;
    }

    if (buf[0] != 0x7f) {
        return -3;
    }

    int value = buf[1] | (buf[2] << 8) | (buf[3] << 16);

    return value;
}
```
