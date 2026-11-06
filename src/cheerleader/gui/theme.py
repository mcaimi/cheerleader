"""
Colors and the global theme of the Dear PyGui front-end.

One palette for every view, in the spirit of the TUI's dark theme. The
grids paint foreground colors only (DPG text items carry no background),
so cursor/match highlights are bright text colors standing in for the
TUI's background-colored cells.
"""

try:
    import dearpygui.dearpygui as dpg
except ImportError:  # pragma: no cover - gui/__init__ prints the hint first
    dpg = None  # type: ignore[assignment]

Color = tuple[int, int, int, int]

# ── text ──────────────────────────────────────────────────────────────────────

COL_TEXT: Color = (216, 216, 222, 255)
COL_DIM: Color = (140, 140, 148, 255)  # addresses, secondary text
COL_SEP: Color = (110, 110, 118, 255)  # separators, raw bytes
COL_BRIGHT: Color = (255, 255, 255, 255)

COL_GREEN: Color = (120, 200, 120, 255)
COL_YELLOW: Color = (250, 210, 80, 255)
COL_BLUE: Color = (110, 170, 255, 255)
COL_RED: Color = (240, 120, 120, 255)

ERR_COLOR: Color = COL_RED  # inline dialog errors

# ── hex / disassembly grids ───────────────────────────────────────────────────

COL_ADDR: Color = COL_DIM
COL_ADDR_CURSOR: Color = COL_BRIGHT
COL_CURSOR: Color = COL_BLUE  # foreground-only cursor (no background drawn)
COL_CURSOR_BG: Color = (40, 80, 160, 255)  # drawn cursor background (editor)
COL_EDITED: Color = COL_YELLOW
COL_MATCH: Color = (255, 110, 255, 255)  # stands in for black-on-magenta

# ── hex compare (TUI HexCompareView parity) ───────────────────────────────────

COL_SAME: Color = (110, 200, 110, 255)
COL_DIFF: Color = (255, 100, 100, 255)

# ── call flow / CFG (TUI highlight._EDGE_STYLE parity) ────────────────────────

COL_ENTRY: Color = (255, 230, 120, 255)  # entry-block title bar
COL_GRID_BG: Color = (24, 24, 30, 255)
COL_GRID_LINE: Color = (36, 36, 44, 255)

EDGE_COLORS: dict[str, Color] = {
    "fall": COL_GREEN,
    "cond": COL_YELLOW,
    "jump": COL_BLUE,
    "indirect": COL_DIM,
    "ret": COL_RED,
}


def build_theme() -> None:
    """Bind the compact dark global theme."""
    with dpg.theme() as theme, dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (18, 18, 22, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (28, 28, 34, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (38, 38, 46, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Text, COL_TEXT)
        dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (30, 30, 37, 255))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (38, 38, 46, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Button, (44, 44, 54, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (58, 58, 70, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (72, 72, 86, 255))
        dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (68, 120, 190, 255))
        dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (88, 140, 210, 255))
        # Without these, selectables / table headers / tree nodes / tabs and
        # the radio dot fall back to ImGui's default blue.
        dpg.add_theme_color(dpg.mvThemeCol_Header, (44, 52, 68, 255))
        dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (56, 66, 86, 255))
        dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (68, 80, 104, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, (32, 32, 40, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt, (24, 24, 30, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Tab, (32, 32, 40, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TabHovered, (56, 66, 86, 255))
        dpg.add_theme_color(dpg.mvThemeCol_TabActive, (44, 52, 68, 255))
        dpg.add_theme_color(dpg.mvThemeCol_CheckMark, COL_BLUE)
        dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (24, 24, 30, 250))
        dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, (26, 26, 32, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (52, 52, 62, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, (66, 66, 78, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabActive, (80, 80, 94, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Separator, (44, 44, 54, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ModalWindowDimBg, (0, 0, 0, 110))
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 6, 4)
    dpg.bind_theme(theme)
