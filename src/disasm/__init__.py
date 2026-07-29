"""disasm — Mach-O binary inspector."""

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

    from disasm.tui import DisasmApp
    app = DisasmApp(path)
    app.run()
