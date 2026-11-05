"""
    Dear PyGui front-end for cheerleader.

    Launched as the ``cheerleader-gui`` console script, separate from the
    terminal UI so the Textual app stays the default front-end::

    Requires the optional ``gui`` dependency group (``uv pip install
    'cheerleader[gui]'``); dearpygui is imported lazily so the rest of
    cheerleader works without it.
"""

import argparse
import sys

from cheerleader.gui.const import APP_TITLE

def main() -> None:
    parser = argparse.ArgumentParser(
        prog=APP_TITLE,
        description="Binary Inspector GUI",
    )
    parser.add_argument(
        "binary",
        nargs="?",
        default=None,
        help="binary file to open (omit to start from the launcher window)",
    )
    hex_group = parser.add_mutually_exclusive_group()
    hex_group.add_argument(
        "--hex",
        action="store_true",
        default=False,
        help="open in hex editor mode (edit, search, save raw bytes)",
    )
    hex_group.add_argument(
        "--hex-compare",
        default=None,
        metavar="TARGET_FILE",
        dest="hex_compare",
        help="open in hex compare mode: side-by-side diff of binary vs TARGET_FILE",
    )
    
    args = parser.parse_args()

    if args.hex_compare and not args.binary:
        parser.error("--hex-compare requires a binary file")

    # Mirror the TUI entry point: fail fast on unreadable paths.
    for path in (args.binary, args.hex_compare):
        if path is None:
            continue
        try:
            open(path, "rb").close()
        except OSError as e:
            print(f"Error: {e}")
            sys.exit(1)

    try:
        import dearpygui  # noqa: F401
    except ImportError:
        print(
            "Error: dearpygui is not installed.\n"
            "Install it with:  uv pip install 'cheerleader[gui]'\n"
            "Or use the terminal UI:  cheerleader --hex"
        )
        sys.exit(1)

    from cheerleader.gui.app import run_gui

    run_gui(binary=args.binary, hex_compare=args.hex_compare)
