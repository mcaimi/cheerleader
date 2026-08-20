## OUTPUT FORMAT

The skill produces C-like pseudocode with the following structure:

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

The output includes:

- Architecture and calling convention headers as comments
- Standard C types (`int`, `unsigned`, `char *`, `void *`, `int64_t`, etc.)
- Proper indentation according to control flow nesting
- Variable declarations at the top of the function body
- Comments for ambiguous constructs, unresolved indirect calls, or unclear type casts
- Known library function signatures when identifiable
- Placeholder names (`sub_XXXX` or `func_XXXX`) for unknown functions
