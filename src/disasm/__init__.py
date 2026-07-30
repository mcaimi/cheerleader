"""disasm — binary inspector (Mach-O, ELF)."""

import sys


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: disasm <binary>")
        sys.exit(1)

    path = sys.argv[1]

    try:
        open(path, "rb").close()
    except OSError as e:
        print(f"Error: {e}")
        sys.exit(1)

    from disasm.formats import detect_format
    fmt = detect_format(path)
    if fmt == "unknown":
        print(f"Warning: unrecognised binary format — continuing anyway")

    from disasm.tui import DisasmApp
    app = DisasmApp(path)
    app.run()
