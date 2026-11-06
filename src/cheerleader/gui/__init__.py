"""
Dear PyGui front-end for cheerleader.
"""

import sys


def main() -> None:
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

    run_gui()
