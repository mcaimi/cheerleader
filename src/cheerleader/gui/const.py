"""
    Dear PyGui application shell for the cheerleader hex UI.

    Constants
"""
VIEWPORT_TITLE = "cheerleader — hex"
VIEWPORT_W, VIEWPORT_H = 1280, 800
MIN_VIEWPORT_W, MIN_VIEWPORT_H = 640, 420

ERR_COLOR = (240, 120, 120, 255)
INPUT_W = 300

FONT_ENV_VAR = "CHEERLEADER_GUI_FONT"
FONT_SIZE = 15
APP_TITLE = "CHEERLEADER - GUI"

# Reserve below the content child window for separator + status strip.
_STATUS_RESERVE = 36

WIN_TAG = "gui-main-window"
CONTENT_TAG = "gui-content"
STATUS_TAG = "gui-status"

# Monospace candidates, first existing wins (env override → linux → macos →
# windows). Missing entirely? The built-in default font (fixed-width) is used.
_FONT_CANDIDATES: tuple[str, ...] = (
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