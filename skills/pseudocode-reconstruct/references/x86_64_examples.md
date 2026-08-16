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
