"""Unit tests for the hex grid: pure geometry/hit-testing plus headless
pool, paint, cursor and scroll behavior (DPG context, no display), and a
desktop-faithful simulation that monkeypatches DPG's geometry queries the
way a real frame would report them (layout, wheel, slider, paging)."""

from dataclasses import replace

import pytest

pytest.importorskip("dearpygui")

import dearpygui.dearpygui as dpg

from cheerleader.gui.hexgrid import (
    BPL,
    COL_CURSOR,
    COL_EDITED,
    COL_MATCH,
    COL_TEXT,
    KEY_PAGE_DOWN,
    KEY_PAGE_UP,
    MAX_POOL_ROWS,
    GridGeometry,
    HexGrid,
    ascii_cell_text,
    ascii_col_x,
    bpl_for_cols,
    bpl_for_width,
    hex_cell_text,
    hex_col_x,
    hit_test,
    offset_text,
    row_width,
    visible_rows_for,
)
from cheerleader.libs.hexdoc import HexDocument
from cheerleader.tui.hexrender import hex_line

CW, ROW_H, ORIGIN_Y = 9.0, 18.0, 6.0


def _make_doc(tmp_path, size=4096, seed=0x36) -> HexDocument:
    path = tmp_path / f"grid-{seed}.bin"
    path.write_bytes(bytes((i * 7 + seed) % 256 for i in range(size)))
    return HexDocument(str(path))


def _ideal_geometry(bpl=BPL) -> GridGeometry:
    """Geometry as if the ideal character grid were rendered at CW px/char."""
    return GridGeometry(
        cw=CW,
        row_h=ROW_H,
        origin_y=ORIGIN_Y,
        cell_x=tuple(hex_col_x(bpl, i) * CW for i in range(bpl)),
        ascii_x=tuple((ascii_col_x(bpl) + i) * CW for i in range(bpl)),
    )


def _cell_color(tag: str) -> tuple[int, int, int, int]:
    cfg = dpg.get_item_configuration(tag)["color"]
    return tuple(round(c * 255) for c in cfg)


# ── pure geometry ─────────────────────────────────────────────────────────────


def _compose_row(offset: int, data: bytes, bpl: int) -> str:
    parts = [offset_text(offset, bpl)]
    for i in range(bpl):
        parts.append(hex_cell_text(bpl, i, data[i] if i < len(data) else None))
    parts.append("  |")
    for i in range(bpl):
        parts.append(ascii_cell_text(data[i] if i < len(data) else None))
    parts.append("|")
    return "".join(parts)


@pytest.mark.parametrize("bpl", [8, 16])
@pytest.mark.parametrize("offset", [0, 0x1234, 0xDEADBEEF])
def test_cell_texts_match_tui_hex_line(bpl, offset):
    """The GUI cell texts must compose into the exact TUI character grid."""
    full = bytes(range(256))[:bpl]
    for data in (full, b"\x00\x7f\xff", b""):
        assert _compose_row(offset, data, bpl) == hex_line(offset, data, bpl)


def test_cell_widths_and_offset_text():
    assert hex_cell_text(16, 0, 0xAB) == "ab "
    assert hex_cell_text(16, 7, 0xAB) == "ab  "  # carries the group gap
    assert hex_cell_text(16, 8, 0xCD) == "cd "
    assert hex_cell_text(16, 15, 0xEF) == "ef"  # pipe is its own item
    assert hex_cell_text(16, 3, None) == "   "
    assert offset_text(1) == "00000001  "
    assert ascii_cell_text(0x41) == "A"
    assert ascii_cell_text(0x00) == "."
    assert ascii_cell_text(None) == " "


def test_bpl_and_visible_rows():
    assert bpl_for_cols(row_width(16)) == 16
    assert bpl_for_cols(row_width(16) - 1) == 8
    assert bpl_for_cols(row_width(8)) == 8
    assert bpl_for_width(row_width(16) * CW, CW) == 16
    assert bpl_for_width((row_width(16) - 1) * CW, CW) == 8
    assert bpl_for_width(500.0, 0.0) == 16  # degenerate metrics → default
    assert visible_rows_for(ROW_H * 5 + 17, ROW_H) == 5
    assert visible_rows_for(0.0, ROW_H) == 1
    assert visible_rows_for(480.0, 0.0) == 24  # no metrics yet → default pool


def test_geometry_is_sane_floors():
    """Degenerate measurements (tiny cells, wrong arity) are rejected."""
    good = _ideal_geometry()
    assert good.is_sane(BPL)
    assert not replace(good, cw=2.99).is_sane(BPL)
    assert not replace(good, row_h=2.99).is_sane(BPL)
    assert replace(good, cw=3.0, row_h=3.0).is_sane(BPL)
    assert not good.is_sane(8)  # cell arrays were built for 16 bytes
    assert not replace(good, cell_x=good.cell_x[:15]).is_sane(BPL)


# ── hit-testing ───────────────────────────────────────────────────────────────


def test_hit_test_hex_and_ascii_cells():
    geom = _ideal_geometry()
    for k in range(BPL):
        for d in (0, 1):  # either hex digit selects the byte
            x = (hex_col_x(BPL, k) + d) * CW + 0.5
            assert hit_test(geom, bpl=BPL, top_row=0, max_offset=4096, x=x, y=10.0) == k
        x = (ascii_col_x(BPL) + k) * CW + 0.5
        assert hit_test(geom, bpl=BPL, top_row=0, max_offset=4096, x=x, y=10.0) == k


def test_hit_test_non_byte_cells():
    geom = _ideal_geometry()
    kw = {"bpl": BPL, "top_row": 0, "max_offset": 4096}
    assert hit_test(geom, x=4 * CW, y=10.0, **kw) is None  # offset field
    # digit separator of byte 3
    assert hit_test(geom, x=(hex_col_x(BPL, 3) + 2) * CW + 0.5, y=10.0, **kw) is None
    # inter-group gap column between bytes 7 and 8
    assert hit_test(geom, x=(hex_col_x(BPL, 8) - 1) * CW + 0.5, y=10.0, **kw) is None
    assert hit_test(geom, x=(ascii_col_x(BPL) - 1) * CW, y=10.0, **kw) is None  # pipe
    assert hit_test(geom, x=(row_width(BPL) - 1) * CW, y=10.0, **kw) is None  # end |
    assert hit_test(geom, x=0.0, y=10.0, **kw) is None  # far left


def test_hit_test_rows_and_bounds():
    geom = _ideal_geometry()
    x = hex_col_x(BPL, 5) * CW + 0.5
    y = ORIGIN_Y + 2 * ROW_H + ROW_H // 2  # middle of row 2
    assert hit_test(geom, bpl=BPL, top_row=0, max_offset=4096, x=x, y=y) == 2 * BPL + 5
    assert (
        hit_test(geom, bpl=BPL, top_row=10, max_offset=4096, x=x, y=y) == 12 * BPL + 5
    )
    assert hit_test(geom, bpl=BPL, top_row=0, max_offset=4096, x=x, y=5.0) is None
    assert hit_test(geom, bpl=BPL, top_row=0, max_offset=4096, x=x, y=50000.0) is None
    # past EOF
    assert hit_test(geom, bpl=BPL, top_row=0, max_offset=2 * BPL + 5, x=x, y=y) is None
    assert (
        hit_test(geom, bpl=BPL, top_row=0, max_offset=2 * BPL + 6, x=x, y=y)
        == 2 * BPL + 5
    )


# ── headless widget behavior ──────────────────────────────────────────────────


@pytest.fixture()
def dpg_context():
    dpg.create_context()
    try:
        yield
    finally:
        dpg.destroy_context()


@pytest.fixture()
def grid_window(dpg_context):
    with dpg.window(tag="test-win"):
        pass
    yield "test-win"


def test_build_paints_first_rows(grid_window, tmp_path):
    doc = _make_doc(tmp_path)
    grid = HexGrid(doc)
    grid.build(grid_window)
    grid.tick()
    base = grid._grid_tag
    assert dpg.does_item_exist(f"{base}-r0-off")
    assert dpg.get_value(f"{base}-r0-off") == offset_text(0, 16)
    data = doc.read(0, 16)
    for i in (0, 7, 8, 15):
        assert dpg.get_value(f"{base}-r0-h{i}").startswith(f"{data[i]:02x}")
        assert dpg.get_value(f"{base}-r0-a{i}") == ascii_cell_text(data[i])
    # painting follows scrolling
    grid.scroll_by(3)
    grid.tick()
    assert dpg.get_value(f"{base}-r0-off") == offset_text(3 * 16, 16)


def test_move_to_clamps_and_reports(grid_window, tmp_path):
    doc = _make_doc(tmp_path)
    seen: list[int] = []
    grid = HexGrid(doc, on_cursor=seen.append)
    grid.build(grid_window)
    grid.move_to(-5)
    assert grid.cursor == 0
    grid.move_to(doc.size + 100)
    assert grid.cursor == doc.size - 1
    assert seen[-1] == doc.size - 1
    assert len(seen) == 2  # every move reported
    # keep-in-view scrolling
    grid._vis = 4
    grid.move_to(0, top=True)
    assert grid.top_row == 0
    grid.move_to(doc.size - 1)
    assert grid.top_row == grid._max_top()
    assert (doc.size - 1) // 16 - grid.top_row < 4  # cursor row on screen


def test_move_to_empty_file_is_noop(grid_window, tmp_path):
    doc = _make_doc(tmp_path, size=0)
    grid = HexGrid(doc)
    grid.build(grid_window)
    grid.move_to(5, top=True)
    assert grid.cursor == 0
    assert grid.top_row == 0


def test_scroll_by_clamps(grid_window, tmp_path):
    doc = _make_doc(tmp_path)
    grid = HexGrid(doc)
    grid.build(grid_window)
    grid.scroll_by(-100)
    assert grid.top_row == 0
    grid.scroll_by(10**9)
    assert grid.top_row == grid._max_top()
    grid.scroll_by(0)
    assert grid.top_row == grid._max_top()


def test_slider_pushes_immediately_and_tick_never_clobbers(grid_window, tmp_path):
    """Push model: programmatic scrolls push the slider at once, and tick()
    never writes the slider value (DPG delivers drags via a queued callback;
    a per-frame write-back would snap the thumb back mid-drag)."""
    doc = _make_doc(tmp_path)
    grid = HexGrid(doc)
    grid.build(grid_window)
    grid._vis = 4
    max_top = grid._max_top()  # 252 with vis=4
    grid.scroll_by(2)
    # pushed without a tick — and inverted: thumb at the widget *top* (value
    # max_top) ≙ top of the file, so 2 rows down read 250
    assert dpg.get_value(grid._scroll_tag) == max_top - 2
    grid.tick()
    assert dpg.get_value(grid._scroll_tag) == max_top - 2
    # User drag: DPG stores the dragged value first; the callback lands in
    # the next render — a tick in between must not clobber it.
    dpg.set_value(grid._scroll_tag, 200)
    grid.tick()
    assert dpg.get_value(grid._scroll_tag) == 200
    assert grid.top_row == 2  # callback still queued
    grid._on_scroll(grid._scroll_tag, 200)  # callback delivered
    grid.tick()
    assert grid.top_row == max_top - 200
    assert dpg.get_value(grid._scroll_tag) == 200
    grid.scroll_by(1)
    assert grid.top_row == max_top - 200 + 1
    assert dpg.get_value(grid._scroll_tag) == 199  # pushed again afterwards


def test_colors_cursor_match_edited(grid_window, tmp_path):
    doc = _make_doc(tmp_path)
    doc.write(3, b"\xff")
    grid = HexGrid(doc)
    grid.build(grid_window)
    grid.tick()
    base = grid._grid_tag
    assert _cell_color(f"{base}-r0-h3") == COL_EDITED
    grid.move_to(3)
    grid.tick()
    assert _cell_color(f"{base}-r0-h3") == COL_CURSOR
    grid.set_match((0, 2))
    grid.tick()
    assert _cell_color(f"{base}-r0-h0") == COL_MATCH
    assert _cell_color(f"{base}-r0-h1") == COL_MATCH
    assert _cell_color(f"{base}-r0-h2") == COL_TEXT
    # cursor outranks match; match outranks edited
    grid.set_match((0, 10))
    grid.tick()
    assert _cell_color(f"{base}-r0-h3") == COL_CURSOR
    # cursor row highlights the offset field
    assert _cell_color(f"{base}-r0-off") != _cell_color(f"{base}-r1-off")


def test_blank_rows_past_eof(grid_window, tmp_path):
    doc = _make_doc(tmp_path, size=100)  # 7 rows
    grid = HexGrid(doc)
    grid._vis = 4  # pool of 5 slots; top=3 renders rows 3..7
    grid.build(grid_window)
    grid.scroll_by(10**9)
    grid.tick()
    base = grid._grid_tag
    assert grid.top_row == 3
    assert dpg.get_value(f"{base}-r0-off") == offset_text(3 * 16, 16)
    assert dpg.get_value(f"{base}-r4-off") == " " * 10
    assert dpg.get_value(f"{base}-r4-h0") == "   "
    assert dpg.get_value(f"{base}-r4-pipe") == "   "
    assert dpg.get_value(f"{base}-r4-a0") == " "


def test_relayout_rebuilds_pool_on_narrow_width(grid_window, tmp_path, monkeypatch):
    doc = _make_doc(tmp_path)
    grid = HexGrid(doc)
    grid.build(grid_window)
    grid.tick()
    base = grid._grid_tag
    assert dpg.does_item_exist(f"{base}-r0-h15")
    # a 60-char-wide parent at the fallback 9px char width → 8 bytes per row
    monkeypatch.setattr(dpg, "get_item_rect_size", lambda _tag: (60 * 9, 480))
    grid.tick()
    assert grid.bpl == 8
    assert dpg.does_item_exist(f"{base}-r0-h7")
    assert not dpg.does_item_exist(f"{base}-r0-h8")
    assert dpg.does_item_exist(f"{base}-r0-a7")
    assert dpg.get_value(f"{base}-r0-off") == offset_text(0, 8)


def test_two_grids_coexist(dpg_context, tmp_path):
    with dpg.window(tag="w1"), dpg.window(tag="w2"):
        pass
    left = HexGrid(_make_doc(tmp_path, seed=1), name="left")
    right = HexGrid(_make_doc(tmp_path, seed=2), name="right")
    left.build("w1")
    right.build("w2")
    left.tick()
    right.tick()
    assert dpg.get_value("left-hexgrid-r0-h0") != dpg.get_value("right-hexgrid-r0-h0")
    assert dpg.does_item_exist("left-hexgrid-scroll")
    assert dpg.does_item_exist("right-hexgrid-scroll")


def test_build_is_idempotent(grid_window, tmp_path):
    doc = _make_doc(tmp_path)
    grid = HexGrid(doc)
    grid.build(grid_window)
    grid.build(grid_window)  # second call must not duplicate the pool
    grid.tick()
    assert len(grid._pool) == grid._vis + 1


# ── desktop-faithful simulation (monkeypatched DPG geometry) ─────────────────

PARENT_SIZE = (1200.0, 700.0)  # app window content area
GRID_SIZE = (1174.0, 698.0)  # child after _relayout: −SLIDER_RESERVE / −2
SIM_CW, SIM_ROW_H = 8.4, 19.5  # a plausible desktop font (matches get_text_size)
GRID_ORIGIN = (10.0, 20.0)  # grid child's screen position
PAD = (2.0, 6.0)  # intra-child padding before the pool


class _FakeScreen:
    """Synthesizes the DPG geometry queries HexGrid performs on a desktop.

    Positions mirror the ideal character grid (cell col × char width) offset
    by a fake child-window origin, the same coordinate frame the real
    ``get_item_rect_min`` / ``get_mouse_pos(local=False)`` pair shares.
    """

    def __init__(self, grid_tag: str, parent_tag: str) -> None:
        self.grid_tag = grid_tag
        self.parent_tag = parent_tag
        self.parent_size = list(PARENT_SIZE)
        self.ts = [SIM_CW, SIM_ROW_H]  # dpg.get_text_size result
        self.pitch = SIM_ROW_H  # row pitch of the synthesized pool
        self.mouse = [30.0, 50.0]  # inside the grid rect by default

    def size(self, tag):
        if tag == self.parent_tag:
            return list(self.parent_size)
        if tag == self.grid_tag:
            return list(GRID_SIZE)
        return [0.0, 0.0]

    def _pool_cell(self, tag: str):
        prefix = self.grid_tag + "-r"
        if not tag.startswith(prefix):
            return None
        row_s, item = tag[len(prefix) :].split("-", 1)
        s = int(row_s)
        if item == "off":
            col = 0.0
        elif item.startswith("h"):
            col = float(hex_col_x(BPL, int(item[1:])))
        elif item.startswith("a"):
            col = float(ascii_col_x(BPL) + int(item[1:]))
        elif item == "pipe":
            col = float(hex_col_x(BPL, BPL - 1) + 2)
        else:  # "end"
            col = float(row_width(BPL) - 1)
        return [
            GRID_ORIGIN[0] + PAD[0] + col * SIM_CW,
            GRID_ORIGIN[1] + PAD[1] + s * self.pitch,
        ]

    def rect_min(self, tag):
        if tag == self.grid_tag:
            return list(GRID_ORIGIN)
        pos = self._pool_cell(tag)
        return pos if pos is not None else [0.0, 0.0]

    def rect_max(self, tag):
        if tag == self.grid_tag:
            return [GRID_ORIGIN[0] + GRID_SIZE[0], GRID_ORIGIN[1] + GRID_SIZE[1]]
        pos = self._pool_cell(tag)
        if pos is None:
            return [0.0, 0.0]
        return [pos[0] + SIM_CW, pos[1] + self.pitch]


@pytest.fixture()
def desktop_grid(grid_window, monkeypatch, tmp_path):
    """A grid whose DPG geometry queries answer like a real desktop frame."""
    doc = _make_doc(tmp_path)
    grid = HexGrid(doc)
    grid.build(grid_window)
    screen = _FakeScreen(grid._grid_tag, grid_window)
    monkeypatch.setattr(dpg, "get_item_rect_size", screen.size)
    monkeypatch.setattr(dpg, "get_item_rect_min", screen.rect_min)
    monkeypatch.setattr(dpg, "get_item_rect_max", screen.rect_max)
    monkeypatch.setattr(dpg, "get_text_size", lambda _text: list(screen.ts))
    monkeypatch.setattr(dpg, "get_mouse_pos", lambda local=False: list(screen.mouse))
    monkeypatch.setattr(dpg, "is_item_hovered", lambda _tag: False)
    return grid, screen


def test_desktop_tick_converges(desktop_grid):
    """With a parent size available, layout, geometry and slider all settle."""
    grid, _screen = desktop_grid
    assert dpg.get_item_configuration(grid._scroll_tag)["format"] == " "
    geom = None
    for _ in range(3):  # must be stable by the third frame
        grid.tick()
        assert grid.bpl == 16
        assert grid._vis == 35  # 698 // 19.5
        assert len(grid._pool) == 36  # vis + 1 spare slot
        assert dpg.get_item_configuration(grid._scroll_tag)["max_value"] == 221
        # inverted mapping: viewing the top of the file ↔ thumb at the top
        assert dpg.get_value(grid._scroll_tag) == 221 - grid.top_row
        new_geom = grid._geometry
        assert new_geom is not None
        if geom is not None:
            assert new_geom is geom  # no re-measure once laid out
        geom = new_geom
    assert grid._geometry.cw == pytest.approx(SIM_CW)
    assert grid._geometry.row_h == pytest.approx(SIM_ROW_H)
    assert grid._geometry.cell_x[0] == pytest.approx(
        GRID_ORIGIN[0] + PAD[0] + hex_col_x(BPL, 0) * SIM_CW
    )


def test_desktop_wheel_scrolls_and_clamps(desktop_grid):
    grid, screen = desktop_grid
    grid.tick()
    for _ in range(10):  # 10 notches down → 30 rows, pushed without a tick
        grid._on_wheel(None, -1)
    assert grid.top_row == 30
    assert dpg.get_value(grid._scroll_tag) == 221 - 30
    grid.tick()
    assert dpg.get_value(f"{grid._grid_tag}-r0-off") == offset_text(30 * 16, 16)
    for _ in range(10):  # …and back up
        grid._on_wheel(None, 1)
    assert grid.top_row == 0
    assert dpg.get_value(grid._scroll_tag) == 221
    for _ in range(300):  # clamped at both ends
        grid._on_wheel(None, -1)
    assert grid.top_row == grid._max_top() == 221
    assert dpg.get_value(grid._scroll_tag) == 0  # thumb at the widget bottom
    for _ in range(300):
        grid._on_wheel(None, 1)
    assert grid.top_row == 0
    assert dpg.get_value(grid._scroll_tag) == 221
    screen.mouse = [5.0, 5.0]  # outside the grid rect: ignored, accumulator drops
    grid._on_wheel(None, -1)
    assert grid.top_row == 0
    assert grid._wheel_remain == 0.0


def test_desktop_wheel_accumulates_fractional_deltas(desktop_grid):
    """Trackpad ½-notch events must accumulate (the old int() truncation
    turned every sub-notch event into 0 rows — a dead wheel)."""
    grid, _screen = desktop_grid
    grid.tick()
    for _ in range(7):
        grid._on_wheel(None, -0.5)
    # 7 × ½ notch = 3½ notches × WHEEL_ROWS(3) = 10.5 → 10 whole rows moved,
    # ½ notch still held in the accumulator
    assert grid.top_row == 10
    assert grid._wheel_remain == pytest.approx(0.5)


def test_desktop_slider_drag_roundtrip(desktop_grid):
    """DPG stores the dragged value first and delivers the callback inside
    the next render — a frame in between must not snap the thumb back."""
    grid, _screen = desktop_grid
    grid.tick()
    base, tag = grid._grid_tag, grid._scroll_tag
    # user grabs the thumb and moves it down 100 rows: slider value 221-100
    dpg.set_value(tag, 121)  # user grabbed and moved: value stored…
    grid.tick()  # …callback still queued: tick must not clobber it
    assert dpg.get_value(tag) == 121
    assert grid.top_row == 0
    grid._on_scroll(tag, 121)  # callback delivered during the next render
    grid.tick()
    assert grid.top_row == 100
    assert dpg.get_value(f"{base}-r0-off") == offset_text(100 * 16, 16)
    bottom = grid._max_top()  # drag to the very bottom and back
    dpg.set_value(tag, 0)
    grid._on_scroll(tag, 0)
    grid.tick()
    assert grid.top_row == bottom
    assert dpg.get_value(f"{base}-r0-off") == offset_text(bottom * 16, 16)
    dpg.set_value(tag, bottom)
    grid._on_scroll(tag, bottom)
    grid.tick()
    assert grid.top_row == 0
    assert dpg.get_value(f"{base}-r0-off") == offset_text(0, 16)


def test_desktop_page_keys(desktop_grid):
    grid, _screen = desktop_grid
    grid.tick()
    grid._key_page_down()
    assert grid.cursor == 35 * 16  # one vis of bytes forward
    assert grid.top_row == 1
    assert grid.top_row + grid._vis - 1 == 35  # cursor row is the last visible
    assert dpg.get_value(grid._scroll_tag) == 221 - 1
    grid._key_page_up()
    assert grid.cursor == 0
    assert grid.top_row == 0
    assert dpg.get_value(grid._scroll_tag) == 221


def test_desktop_slider_max_shrink_pushes_value(desktop_grid):
    """Growing the window shrinks max_top; DPG does not auto-clamp the stored
    value when max_value shrinks, so the grid must push it (else the thumb
    stays pinned at the stale end position)."""
    grid, _screen = desktop_grid
    grid.tick()
    grid.move_to(grid._doc.size - 40 * 16)  # cursor row 216
    grid.tick()
    assert grid.top_row == 182
    assert dpg.get_value(grid._scroll_tag) == 221 - 182
    _screen.parent_size = [1200.0, 1000.0]  # vis 35 → 51
    grid.tick()
    assert grid._vis == 51
    assert len(grid._pool) == 52
    assert grid._max_top() == 205
    assert grid.top_row == 182  # cursor row 216 still on screen
    assert dpg.get_item_configuration(grid._scroll_tag)["max_value"] == 205
    # push fired with the new range (stale value would still read 39)
    assert dpg.get_value(grid._scroll_tag) == 205 - 182


def test_desktop_degenerate_metrics_cap_pool(desktop_grid):
    """A bogus 1 px row height must not explode the pool (hard cap 400)."""
    grid, screen = desktop_grid
    screen.ts = [SIM_CW, 1.0]
    screen.pitch = 1.0  # synthesized rows 1 px apart → geometry rejected
    grid.tick()
    assert grid._geometry is None  # is_sane floor rejects the measurement
    assert grid._vis == MAX_POOL_ROWS
    assert len(grid._pool) == MAX_POOL_ROWS + 1
    assert dpg.get_item_configuration(grid._scroll_tag)["max_value"] == 0
    grid.tick()
    assert len(grid._pool) == MAX_POOL_ROWS + 1  # stable, no rebuild thrash


def test_desktop_click_maps_to_byte(desktop_grid):
    grid, screen = desktop_grid
    grid.tick()
    # click the first digit of hex byte 5 on pool row 0
    screen.mouse = [
        GRID_ORIGIN[0] + PAD[0] + hex_col_x(BPL, 5) * SIM_CW + 1.0,
        GRID_ORIGIN[1] + PAD[1] + SIM_ROW_H / 2,
    ]
    grid._on_click(None, None)
    assert grid.cursor == 5
    # the inter-group gap column (before byte 8) selects nothing
    screen.mouse[0] = GRID_ORIGIN[0] + PAD[0] + (hex_col_x(BPL, 8) - 1) * SIM_CW + 0.5
    grid._on_click(None, None)
    assert grid.cursor == 5


def test_page_keys_registered_under_both_spellings(grid_window, tmp_path):
    """dearpygui 2.3.1's mvKey_Prior/Next constants are stale GLFW codes that
    never fire (upstream #2658); the grid also registers the correct ImGui
    codes 517/518 — on wheels where the constants are fixed the duplicate
    spelling must dedupe away."""
    doc = _make_doc(tmp_path)
    grid = HexGrid(doc)
    grid.build(grid_window)
    keys = {
        dpg.get_item_configuration(h).get("key")
        for h in dpg.get_item_children(grid._input_tag, 1)
    }
    assert {KEY_PAGE_UP, dpg.mvKey_Prior} <= keys
    assert {KEY_PAGE_DOWN, dpg.mvKey_Next} <= keys
    # today Prior/Next are stale (266/267 ≠ 517/518): exactly two spellings
    assert len({KEY_PAGE_UP, dpg.mvKey_Prior}) == 2
    assert len({KEY_PAGE_DOWN, dpg.mvKey_Next}) == 2


# ── compare-mode hooks ───────────────────────────────────────────────


def test_color_for_overrides_editor_colors(grid_window, tmp_path):
    doc = _make_doc(tmp_path)
    grid = HexGrid(doc)
    grid.build(grid_window)
    grid.tick()
    base = grid._grid_tag
    cell = f"{base}-r0-h5"  # not the cursor cell (offset 0 is the cursor)
    assert _cell_color(cell) == COL_TEXT
    red = (255, 0, 0, 255)
    grid.color_for = lambda offset: red
    grid.refresh()
    grid.tick()
    assert _cell_color(cell) == red
    assert grid._byte_color(123) == red
    grid.color_for = None
    assert grid._byte_color(123) == COL_TEXT  # falls back to editor colors


def test_max_top_rows_extends_range_and_blank_tail(grid_window, tmp_path):
    doc = _make_doc(tmp_path, size=256)  # 16 rows
    grid = HexGrid(doc)
    grid.build(grid_window)
    grid._vis = 4
    assert grid._max_top() == 12
    grid.max_top_rows = lambda: 32  # partner document is twice as long
    assert grid._max_top() == 28
    grid.set_top(10**6)
    assert grid.top_row == 28
    grid.tick()
    base = grid._grid_tag
    # Rows past the shorter document's own EOF render fully blank.
    assert dpg.get_value(f"{base}-r0-off") == " " * (8 + 2)


def test_set_top_clamps_and_pushes_slider(grid_window, tmp_path):
    doc = _make_doc(tmp_path, size=256)
    grid = HexGrid(doc)
    grid.build(grid_window)
    grid._vis = 4
    grid.set_top(2)
    assert grid.top_row == 2
    assert dpg.get_value(grid._scroll_tag) == grid._max_top() - 2  # inverted
    grid.set_top(-3)
    assert grid.top_row == 0
    grid.set_top(999)
    assert grid.top_row == grid._max_top()


def test_set_scrollbar_visible_roundtrip(grid_window, tmp_path):
    doc = _make_doc(tmp_path)
    grid = HexGrid(doc)
    grid.build(grid_window)
    assert dpg.get_item_configuration(grid._scroll_tag)["show"] is True
    grid.set_scrollbar_visible(False)
    assert dpg.get_item_configuration(grid._scroll_tag)["show"] is False
    grid.set_scrollbar_visible(True)
    assert dpg.get_item_configuration(grid._scroll_tag)["show"] is True


def test_scroll_mode_keys_scroll_without_cursor(grid_window, tmp_path):
    doc = _make_doc(tmp_path, size=4096)
    grid = HexGrid(doc)
    grid.build(grid_window)
    grid._vis = 4
    grid.scroll_mode = True
    cursor = grid.cursor
    grid._key_down()
    assert grid.top_row == 1 and grid.cursor == cursor
    grid._key_page_down()
    assert grid.top_row == 1 + 4
    grid._key_end()
    assert grid.top_row == grid._max_top()
    grid._key_home()
    assert grid.top_row == 0
    grid._key_up()
    assert grid.top_row == 0  # clamped at the top
    # editor mode unchanged: Home/End keep their cursor semantics
    grid.scroll_mode = False
    grid.move_to(35)
    grid._key_home()
    assert grid.cursor == 32  # row start
    grid._key_end()
    assert grid.cursor == 47  # row end
