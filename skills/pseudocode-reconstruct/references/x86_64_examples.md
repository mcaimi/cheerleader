## x86_64 Reconstruction Examples

---

### Example 1: Simple Arithmetic Addition

**Input:**

```asm
0x0000000000401000 <func_401000>:
  401000: push   rbp
  401001: mov    rbp,rsp
  401004: mov    QWORD PTR [rbp-0x10],rdi
  401007: mov    DWORD PTR [rbp-0x4],esi
  40100a: mov    eax,DWORD PTR [rbp-0x4]
  40100d: add    eax,DWORD PTR [rbp-0x10]
  401010: mov    DWORD PTR [rbp-0x8],eax
  401013: mov    eax,DWORD PTR [rbp-0x8]
  401016: leave
  401017: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI

int func_401000(int a, int b) {
    int local0 = a;
    int local1 = b;
    int local2 = local1 + local0;

    return local2;
}
```

---

### Example 2: Loop with Conditional Branch

**Input:**

```asm
0x0000000000401020 <count_digits>:
  401020: push   rbp
  401021: mov    rbp,rsp
  401024: mov    DWORD PTR [rbp-0x4],edi
  401027: mov    DWORD PTR [rbp-0x8],0x0
  40102e: jmp    401042
  401030: mov    eax,DWORD PTR [rbp-0x8]
  401033: add    eax,0x1
  401036: mov    DWORD PTR [rbp-0x8],eax
  401039: mov    eax,DWORD PTR [rbp-0x4]
  40103c: cdq
  40103d: idiv   DWORD PTR [rbp-0xc]
  401040: mov    eax,edx
  401042: cmp    DWORD PTR [rbp-0x4],0x0
  401046: jg     401030
  401048: mov    eax,DWORD PTR [rbp-0x8]
  40104b: pop    rbp
  40104c: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI

int count_digits(int n) {
    int count = 0;

    while (n > 0) {
        count = count + 1;
        n = n % 10;
    }

    return count;
}
```

---

### Example 3: Function Call

**Input:**

```asm
0x0000000000401060 <max_of_three>:
  401060: push   rbp
  401061: mov    rbp,rsp
  401064: sub    rsp,0x10
  401068: mov    QWORD PTR [rbp-0x10],rdi
  40106c: mov    QWORD PTR [rbp-0x8],rsi
  401070: mov    rdi,QWORD PTR [rbp-0x10]
  401074: mov    rsi,QWORD PTR [rbp-0x8]
  401078: call   4010a0 <max>
  40107d: mov    QWORD PTR [rbp-0x4],rax
  401081: mov    rdi,QWORD PTR [rbp-0x4]
  401085: mov    rsi,rdx
  401088: call   4010a0 <max>
  40108d: mov    rax,QWORD PTR [rbp-0x4]
  401091: leave
  401092: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI

long max_of_three(long a, long b, long c) {
    long first_max = max(a, b);
    long result = max(first_max, c);

    return result;
}
// Referenced function: long max(long a, long b)
```

---

### Example 4: Vector/SSE Operations

**Input:**

```asm
0x0000000000401100 <vector_add>:
  401100: push   rbp
  401101: mov    rbp,rsp
  401104: sub    rsp,0x20
  401108: movsd  XMM0,XMMWORD PTR [rdi]
  40110d: movsd  XMM1,XMMWORD PTR [rsi]
  401113: addsd  XMM0,XMM1
  401117: movsd  XMMWORD PTR [rbp-0x10],XMM0
  40111c: movsd  XMM0,XMMWORD PTR [rbp-0x10]
  401121: movsd  XMMWORD PTR [rdx],XMM0
  401126: nop
  401127: leave
  401128: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI
// Note: Uses SSE (Scalar Double) SIMD instructions

void vector_add(double *a, double *b, double *result) {
    double local0 = *a;
    double local1 = *b;
    double local2 = local0 + local1;

    *result = local2;
}
```

---

### Example 5: Switch/Case with Jump Table

**Input:**

```asm
0x0000000000401150 <handle_opcode>:
  401150: push   rbp
  401151: mov    rbp,rsp
  401154: mov    DWORD PTR [rbp-0x4],edi
  401157: cmp    DWORD PTR [rbp-0x4],0x3
  40115b: ja     401194
  40115d: mov    eax,DWORD PTR [rbp-0x4]
  401160: lea    rdx,[rip+0x200e99]
  401167: movsxd rax,DWORD PTR [rdx+rax*4]
  40116b: add    rax,rdx
  40116e: jmp    rax
  401170: mov    eax,0x1
  401175: jmp    40119c
  401177: mov    eax,0xa
  40117c: jmp    40119c
  40117e: mov    eax,0x64
  401183: jmp    40119c
  401185: mov    eax,0x3e8
  40118a: jmp    40119c
  401194: mov    eax,0xffffffff
  40119c: pop    rbp
  40119d: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI

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

### Example 6: Struct Field Access (Linked List Traversal)

**Input:**

```asm
0x00000000004011a0 <list_find>:
  4011a0: push   rbp
  4011a1: mov    rbp,rsp
  4011a4: mov    QWORD PTR [rbp-0x8],rdi
  4011a8: mov    DWORD PTR [rbp-0xc],esi
  4011ab: jmp    4011c8
  4011ad: mov    rax,QWORD PTR [rbp-0x8]
  4011b1: mov    eax,DWORD PTR [rax]
  4011b3: cmp    eax,DWORD PTR [rbp-0xc]
  4011b6: jne    4011be
  4011b8: mov    rax,QWORD PTR [rbp-0x8]
  4011bc: jmp    4011d2
  4011be: mov    rax,QWORD PTR [rbp-0x8]
  4011c2: mov    rax,QWORD PTR [rax+0x8]
  4011c6: mov    QWORD PTR [rbp-0x8],rax
  4011c8: cmp    QWORD PTR [rbp-0x8],0x0
  4011cd: jne    4011ad
  4011cf: xor    eax,eax
  4011d2: pop    rbp
  4011d3: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI
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

### Example 7: Conditional Move (Branchless Ternary)

**Input:**

```asm
0x00000000004011e0 <max_value>:
  4011e0: push   rbp
  4011e1: mov    rbp,rsp
  4011e4: mov    DWORD PTR [rbp-0x4],edi
  4011e7: mov    DWORD PTR [rbp-0x8],esi
  4011ea: mov    eax,DWORD PTR [rbp-0x4]
  4011ed: mov    ecx,DWORD PTR [rbp-0x8]
  4011f0: cmp    eax,ecx
  4011f2: cmovl  eax,ecx
  4011f5: pop    rbp
  4011f6: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI

int max_value(int a, int b) {
    int result = (a >= b) ? a : b;

    return result;
}
```

---

### Example 8: Nested Loops (Matrix Sum)

**Input:**

```asm
0x0000000000401200 <matrix_sum>:
  401200: push   rbp
  401201: mov    rbp,rsp
  401204: mov    QWORD PTR [rbp-0x18],rdi
  401208: mov    DWORD PTR [rbp-0x1c],esi
  40120b: mov    DWORD PTR [rbp-0x20],edx
  40120e: mov    DWORD PTR [rbp-0x4],0x0
  401215: mov    DWORD PTR [rbp-0x8],0x0
  40121c: jmp    401258
  40121e: mov    DWORD PTR [rbp-0xc],0x0
  401225: jmp    401248
  401227: mov    eax,DWORD PTR [rbp-0x8]
  40122a: imul   eax,DWORD PTR [rbp-0x20]
  40122e: add    eax,DWORD PTR [rbp-0xc]
  401231: cdqe
  401233: lea    rdx,[rax*4]
  40123a: mov    rax,QWORD PTR [rbp-0x18]
  40123e: add    rax,rdx
  401241: mov    eax,DWORD PTR [rax]
  401243: add    DWORD PTR [rbp-0x4],eax
  401246: add    DWORD PTR [rbp-0xc],0x1
  401248: mov    eax,DWORD PTR [rbp-0xc]
  40124b: cmp    eax,DWORD PTR [rbp-0x20]
  40124e: jl     401227
  401250: add    DWORD PTR [rbp-0x8],0x1
  401258: mov    eax,DWORD PTR [rbp-0x8]
  40125b: cmp    eax,DWORD PTR [rbp-0x1c]
  40125e: jl     40121e
  401260: mov    eax,DWORD PTR [rbp-0x4]
  401263: pop    rbp
  401264: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI

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

### Example 9: do-while Loop

**Input:**

```asm
0x0000000000401270 <sum_digits>:
  401270: push   rbp
  401271: mov    rbp,rsp
  401274: mov    DWORD PTR [rbp-0x4],edi
  401277: mov    DWORD PTR [rbp-0x8],0x0
  40127e: mov    eax,DWORD PTR [rbp-0x4]
  401281: cdq
  401282: mov    ecx,0xa
  401287: idiv   ecx
  401289: add    DWORD PTR [rbp-0x8],edx
  40128c: mov    eax,DWORD PTR [rbp-0x4]
  40128f: cdq
  401290: mov    ecx,0xa
  401295: idiv   ecx
  401297: mov    DWORD PTR [rbp-0x4],eax
  40129a: cmp    DWORD PTR [rbp-0x4],0x0
  40129e: jg     40127e
  4012a0: mov    eax,DWORD PTR [rbp-0x8]
  4012a3: pop    rbp
  4012a4: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI

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

### Example 10: Tail Call Optimization

**Input:**

```asm
0x00000000004012b0 <factorial_helper>:
  4012b0: push   rbp
  4012b1: mov    rbp,rsp
  4012b4: mov    DWORD PTR [rbp-0x4],edi
  4012b7: mov    DWORD PTR [rbp-0x8],esi
  4012ba: cmp    DWORD PTR [rbp-0x4],0x1
  4012be: jg     4012c6
  4012c0: mov    eax,DWORD PTR [rbp-0x8]
  4012c3: pop    rbp
  4012c4: ret
  4012c6: mov    eax,DWORD PTR [rbp-0x4]
  4012c9: sub    eax,0x1
  4012cc: mov    edi,eax
  4012ce: mov    eax,DWORD PTR [rbp-0x4]
  4012d1: imul   eax,DWORD PTR [rbp-0x8]
  4012d5: mov    esi,eax
  4012d7: pop    rbp
  4012d8: jmp    4012b0 <factorial_helper>
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI
// Note: Tail call optimization — jmp replaces call+ret

int factorial_helper(int n, int acc) {
    if (n <= 1) {
        return acc;
    }

    return factorial_helper(n - 1, n * acc);
}
```

---

### Example 11: Recursive Function (Self-Call)

**Input:**

```asm
0x0000000000401300 <power>:
  401300: push   rbp
  401301: mov    rbp,rsp
  401304: sub    rsp,0x10
  401308: mov    DWORD PTR [rbp-0x4],edi
  40130b: mov    DWORD PTR [rbp-0x8],esi
  40130e: cmp    DWORD PTR [rbp-0x8],0x0
  401312: jne    40131b
  401314: mov    eax,0x1
  401319: jmp    401332
  40131b: mov    eax,DWORD PTR [rbp-0x8]
  40131e: sub    eax,0x1
  401321: mov    esi,eax
  401323: mov    edi,DWORD PTR [rbp-0x4]
  401326: call   401300 <power>
  40132b: imul   eax,DWORD PTR [rbp-0x4]
  40132f: nop
  401332: leave
  401333: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI

int power(int base, int exp) {
    if (exp == 0) {
        return 1;
    }

    return base * power(base, exp - 1);
}
// Note: Recursive — call targets own address (0x401300)
```

---

### Example 12: Division-by-Constant (Magic Number Multiply)

**Input:**

```asm
0x0000000000401340 <divide_by_7>:
  401340: push   rbp
  401341: mov    rbp,rsp
  401344: mov    DWORD PTR [rbp-0x4],edi
  401347: mov    eax,DWORD PTR [rbp-0x4]
  40134a: mov    ecx,0x92492493
  40134f: imul   ecx
  401351: add    edx,DWORD PTR [rbp-0x4]
  401354: sar    edx,0x2
  401357: mov    eax,DWORD PTR [rbp-0x4]
  40135a: sar    eax,0x1f
  40135d: sub    edx,eax
  40135f: mov    eax,edx
  401361: pop    rbp
  401362: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI
// Note: Compiler replaces division by constant 7 with
//       multiply-by-magic-number idiom (magic=0x92492493, shift=2)

int divide_by_7(int n) {
    return n / 7;
}
```

---

### Example 13: Indirect Call / Vtable Dispatch

**Input:**

```asm
0x0000000000401370 <call_virtual>:
  401370: push   rbp
  401371: mov    rbp,rsp
  401374: sub    rsp,0x10
  401378: mov    QWORD PTR [rbp-0x8],rdi
  40137c: mov    DWORD PTR [rbp-0xc],esi
  40137f: mov    rax,QWORD PTR [rbp-0x8]
  401383: mov    rax,QWORD PTR [rax]
  401386: mov    rax,QWORD PTR [rax+0x10]
  40138a: mov    edi,QWORD PTR [rbp-0x8]
  40138e: mov    esi,DWORD PTR [rbp-0xc]
  401391: call   rax
  401393: leave
  401394: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI
// Note: Indirect call through vtable — likely C++ virtual method dispatch
//       obj -> vtable_ptr -> vtable[2] (offset 0x10 = slot index 2)

int call_virtual(void *obj, int arg) {
    void **vtable = *(void ***)obj;
    int (*method)(void *, int) = (int (*)(void *, int))vtable[2];

    return method(obj, arg);
}
```

---

### Example 14: Error-Path with Multiple Early Returns

**Input:**

```asm
0x00000000004013a0 <parse_header>:
  4013a0: push   rbp
  4013a1: mov    rbp,rsp
  4013a4: mov    QWORD PTR [rbp-0x8],rdi
  4013a8: mov    DWORD PTR [rbp-0xc],esi
  4013ab: cmp    QWORD PTR [rbp-0x8],0x0
  4013b0: jne    4013b9
  4013b2: mov    eax,0xffffffff
  4013b7: jmp    401400
  4013b9: cmp    DWORD PTR [rbp-0xc],0x4
  4013bd: jge    4013c6
  4013bf: mov    eax,0xfffffffe
  4013c4: jmp    401400
  4013c6: mov    rax,QWORD PTR [rbp-0x8]
  4013ca: movzx  eax,BYTE PTR [rax]
  4013cd: cmp    al,0x7f
  4013cf: je     4013d8
  4013d1: mov    eax,0xfffffffd
  4013d6: jmp    401400
  4013d8: mov    rax,QWORD PTR [rbp-0x8]
  4013dc: movzx  ecx,BYTE PTR [rax+0x1]
  4013e0: mov    rax,QWORD PTR [rbp-0x8]
  4013e4: movzx  edx,BYTE PTR [rax+0x2]
  4013e8: shl    edx,0x8
  4013eb: or     ecx,edx
  4013ed: mov    rax,QWORD PTR [rbp-0x8]
  4013f1: movzx  edx,BYTE PTR [rax+0x3]
  4013f5: shl    edx,0x10
  4013f8: or     ecx,edx
  4013fa: mov    eax,ecx
  401400: pop    rbp
  401401: ret
```

**Output:**

```c
// Architecture: x86_64
// Calling convention: System V AMD64 ABI

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
