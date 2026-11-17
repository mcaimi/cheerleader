"""
Dear PyGui application shell for the cheerleader hex UI.

Constants
"""

from importlib.metadata import PackageNotFoundError, version


def _app_version() -> str:
    try:
        return version("cheerleader")
    except PackageNotFoundError:  # running from a source tree, not installed
        return "dev"


VIEWPORT_TITLE = "Cheerleader: Your Friendly Binary Inspector"
VIEWPORT_W, VIEWPORT_H = 1280, 800
MIN_VIEWPORT_W, MIN_VIEWPORT_H = 640, 420

INPUT_W = 300

FONT_ENV_VAR = "CHEERLEADER_GUI_FONT"
FONT_SIZE = 15
APP_TITLE = "CHEERLEADER"
APP_VERSION = _app_version()
SRC_URL = "https://github.com/mcaimi/cheerleader"

# Reserve below the content child window for separator + status strip.
STATUS_RESERVE = 36

WIN_TAG = "gui-main-window"
MENU_TAG = "gui-menubar"

# Shell bottom bar: shows the loaded file's path once File → Open File…
# succeeds. The filler child reserves the area above it (same negative-
# height pattern as the tool windows' status strips).
SHELL_FILL = "gui-shell-fill"
SHELL_STATUS_BAR = "gui-shell-status-bar"
SHELL_STATUS = "gui-shell-status"  # left: the file path (+ a note)
SHELL_STATUS_RIGHT = "gui-shell-status-right"  # right: architecture · engine

# Shell-level file dialog (File → Open File…). The compare picker owns its
# two file dialogs privately (see dialogs.CompareOpenDialog).
DLG_OPEN = "gui-dlg-open-file"

# Tools menu items that change enabled-state at runtime.
MI_EDITOR = "gui-mi-hex-editor"

# Disassembler menu (plan v3, docs/gui-disasm-plan.md). Every item is grayed
# until a *supported binary* (Mach-O / ELF, parseable without error) is
# loaded via File → Open File…; see GuiApp.current_info.
DISASM_MENU = "gui-disasm-menu"
MI_DISASM = "gui-mi-disasm"
MI_SEGMENTS = "gui-mi-segments"
MI_SECTIONS = "gui-mi-sections"
MI_IMPORTS = "gui-mi-imports"
MI_SYMBOLS = "gui-mi-symbols"
MI_EXPORTS = "gui-mi-exports"
MI_FIXUPS = "gui-mi-fixups"
MI_STRINGS = "gui-mi-strings"
MI_INFO = "gui-mi-info"
MI_SHORTCUTS = "gui-mi-shortcuts"
MI_RECENT = "gui-mi-recent"  # File ▸ Open Recent submenu (rebuilt on change)
WINDOW_MENU = "gui-window-menu"  # Window menu (rebuilt when tools open/close)
SLICE_MENU = "gui-slice-menu"  # Disassembler ▸ Slice radio submenu (fat files)
DISASM_MENU_ITEMS: tuple[tuple[str, str, str], ...] = (
    # (label, menu-item tag, inspector kind — key of app.INSPECTOR_OPENERS)
    ("Disassembly…", MI_DISASM, "disasm"),
    ("Segments…", MI_SEGMENTS, "segments"),
    ("Sections…", MI_SECTIONS, "sections"),
    ("Imports…", MI_IMPORTS, "imports"),
    ("Symbols…", MI_SYMBOLS, "symbols"),
    ("Exports…", MI_EXPORTS, "exports"),
    ("Fixups…", MI_FIXUPS, "fixups"),
    ("Strings…", MI_STRINGS, "strings"),
)
DISASM_ITEMS: tuple[str, ...] = tuple(tag for _, tag, _ in DISASM_MENU_ITEMS)

# Disassembler ▸ Engine radio submenu (plan v3 §5, D5). The submenu itself
# stays enabled with or without a loaded binary — engine choice is a
# process-global preference affecting *future* views. Radio state renders
# as a "● " label prefix (menu items have no check state).
ENGINE_MENU = "gui-engine-menu"
MI_ENGINE_AUTO = "gui-mi-engine-auto"
MI_ENGINE_CAPSTONE = "gui-mi-engine-capstone"
MI_ENGINE_OBJDUMP = "gui-mi-engine-objdump"
ENGINE_ITEMS: tuple[tuple[str, str, str], ...] = (
    # (label, override value, menu-item tag)
    ("Auto", "auto", MI_ENGINE_AUTO),
    ("Capstone", "capstone", MI_ENGINE_CAPSTONE),
    ("objdump", "objdump", MI_ENGINE_OBJDUMP),
)

# Floating tool windows. Tags are namespaced per tool:
# gui-tool-{ns} / gui-tool-{ns}-content / gui-tool-{ns}-status.
EDITOR_WIN_W, EDITOR_WIN_H = 960, 620
COMPARE_WIN_W, COMPARE_WIN_H = 1180, 620
TABLE_WIN_W, TABLE_WIN_H = 900, 520  # D2 data-table views (segments, …)
DISASM_WIN_W, DISASM_WIN_H = 1180, 620  # D3 Disassembly window
TOOL_MIN_W, TOOL_MIN_H = 560, 320

# Monospace candidates, first existing wins (env override → linux → macos →
# windows). Missing entirely? The built-in default font (fixed-width) is used.
FONT_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/dejavu-sans-mono-fonts/DejaVuSansMono.ttf",
    "/usr/share/fonts/noto/NotoSansMono-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
)

BPL = 16  # bytes per row
OFFSET_W = 8  # width of the "%08x" offset field

# Scroll / layout tuning.
WHEEL_ROWS = 3  # rows scrolled per wheel notch
SLIDER_RESERVE = 26  # px reserved right of the grid for the scrollbar
DEFAULT_VISIBLE_ROWS = 24  # pool size until the first real measurement
MAX_POOL_ROWS = 400  # hard cap: a degenerate measurement must not explode
FALLBACK_CHAR_W = 9.0  # px, when font metrics are unavailable
FALLBACK_CHAR_H = 16.0
INIT_GRID_W, INIT_GRID_H = 920, 480  # first-frame sizes before relayout

# dearpygui 2.3.1 ships mvKey_Prior/mvKey_Next with stale GLFW codes (266/267)
# that never match its internal ImGui key enum (upstream issue #2658); the
# real PageUp/PageDown codes continue the enum between Down (516) and Home
# (519). Both spellings are registered — the wrong one is inert.
KEY_PAGE_UP = 517
KEY_PAGE_DOWN = 518
KEY_ESCAPE = 526  # ImGui named key (dearpygui 2.3.1 ships no mvKey_Escape)
# dearpygui 2.3.1 defines no mvKey_Enter constant (mvKey_Space/Backspace are
# missing too); Enter is ImGui named key 525, between Space (524) and the
# confirmed Escape (526). A raw constant is future-proof: a later mvKey_Enter
# would carry the same value.
KEY_ENTER = 525
# ImGui named key for "=" (Apostrophe 596 … Slash 600, Semicolon 601,
# Equal 602); dearpygui 2.3.1 ships only the stale GLFW ``mvKey_Plus`` (61).
KEY_EQUAL = 602

# Floating tool windows cascade from here, stepping per opened tool.
CASCADE_ORIGIN = (40, 50)
CASCADE_STEP = 28
CASCADE_SLOTS = 10

# View ▸ zoom (multiplies the base global font scale).
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.6, 2.5, 0.1

RECENT_MAX = 10  # File ▸ Open Recent entries
CONFIG_DIR_ENV = "CHEERLEADER_CONFIG_DIR"  # settings dir override (tests)

# Help ▸ Keyboard shortcuts — one table per tool, shared by the help dialog
# and the per-tool menus' shortcut labels. (section, [(action, keys), …])
SHORTCUTS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Everywhere",
        (
            ("Open file", "Ctrl+O"),
            ("Quit", "Ctrl+Q"),
            ("Zoom in / out / reset", "Ctrl+= / Ctrl+- / Ctrl+0"),
            ("Keyboard shortcuts", "F1"),
        ),
    ),
    (
        "Hex editor",
        (
            ("Move the cursor", "Arrows, PgUp / PgDn"),
            ("Row start / end", "Home / End"),
            ("File start / end", "Ctrl+Home / Ctrl+End"),
            ("Overwrite with hex digits", "0-9, A-F"),
            ("Edit bytes at the cursor", "Enter"),
            ("Undo", "Ctrl+Z  or  U"),
            ("Save as", "Ctrl+S  or  S"),
            ("Go to offset / search", "Ctrl+F, Ctrl+G  or  /"),
            ("Next / previous match", "F3 / Shift+F3  or  N / Shift+N"),
            ("Reload from disk", "R"),
            ("Cancel search", "Esc"),
        ),
    ),
    (
        "Hex compare",
        (
            ("Scroll", "Arrows, PgUp / PgDn, Home / End"),
            ("Next / previous difference", "N / P"),
            ("Rescan both files", "R"),
            ("Cancel the scan", "Esc"),
        ),
    ),
    (
        "Disassembly",
        (
            ("Move", "Arrows, PgUp / PgDn, Home / End"),
            ("Call flow of the function", "C"),
            ("Control-flow graph", "F"),
            ("Go to address", "Ctrl+G"),
            ("More actions", "Right-click"),
            ("Cancel disassembly", "Esc"),
        ),
    ),
    (
        "Tables and Strings",
        (
            ("Sort", "Click a column header"),
            ("Show the row in the hex editor", "Click a row"),
            ("Cancel string extraction", "Esc"),
        ),
    ),
    (
        "Call flow / CFG",
        (
            ("Drill into a function", "Click its name"),
            ("Back (call flow)", "B"),
            ("Close", "Esc"),
        ),
    ),
)

# ── CFG node-graph layout (pure; unit-tested without a display) ──────────────

# Estimated node geometry (px). DPG nodes auto-size to their content; the
# layout only needs plausible boxes so columns/stacks don't overlap.
NODE_W = 480  # estimated content width of a full-instruction node + margin
LINE_H = 19  # one text row inside a node
NODE_PAD_H = 46  # title bar + attribute padding
X_GAP = 70  # horizontal gap between depth columns
Y_GAP = 40  # vertical gap between stacked nodes