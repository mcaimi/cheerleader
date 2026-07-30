"""disasm — binary inspector (Mach-O, ELF)."""

import sys
from cheerleader.tui.app import APP_TITLE


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {APP_TITLE} <binary>")
        sys.exit(1)

    path = sys.argv[1]

    try:
        open(path, "rb").close()
    except OSError as e:
        print(f"Error: {e}")
        sys.exit(1)

    from cheerleader.formats import detect_format

    fmt = detect_format(path)
    if fmt == "unknown":
        print("Warning: unrecognised binary format — continuing anyway")

    from cheerleader.tui import DisasmApp

    app = DisasmApp(path)
    app.run()
