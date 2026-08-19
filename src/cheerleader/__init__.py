"""disasm — binary inspector (Mach-O, ELF)."""

import argparse
import sys

from cheerleader.tui.app import APP_TITLE


def main() -> None:
    parser = argparse.ArgumentParser(
        prog=APP_TITLE,
        description="Binary inspector TUI",
    )
    parser.add_argument("binary", help="path to the binary to inspect")
    parser.add_argument(
        "--hex",
        action="store_true",
        default=False,
        help="open in hex editor mode (raw file hex dump only)",
    )
    parser.add_argument(
        "--env",
        default=".env",
        metavar="FILE",
        help="path to .env file for AI agent config (default: .env)",
    )
    args = parser.parse_args()

    try:
        open(args.binary, "rb").close()
    except OSError as e:
        print(f"Error: {e}")
        sys.exit(1)

    from cheerleader.formats import detect_format

    fmt = detect_format(args.binary)
    if fmt == "unknown":
        print("Warning: unrecognised binary format — continuing anyway")

    from cheerleader.tui import DisasmApp

    app = DisasmApp(args.binary, env_file=args.env, hex_mode=args.hex)
    app.run()
