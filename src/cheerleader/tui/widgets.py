"""Reusable widgets for the binary inspector TUI."""

from __future__ import annotations

import os
from bisect import bisect_left, bisect_right
from typing import ClassVar

from rich.segment import Segment
from rich.style import Style
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import LoadingIndicator, Static

from cheerleader.libs.types import BinaryInfo


def build_hex_dump(data: bytes, base_addr: int) -> str:
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i : i + 16]
        addr = base_addr + i
        hex_left = " ".join(f"{b:02x}" for b in chunk[:8])
        hex_right = " ".join(f"{b:02x}" for b in chunk[8:])
        hex_part = f"{hex_left:<23}  {hex_right:<23}"
        ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{addr:08x}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)


class HexPane(VerticalScroll):
    """Toggleable hex dump viewer pane."""

    DEFAULT_CSS = """
    HexPane {
        width: 1fr;
        height: 40%;
        border-top: solid $primary;
        background: $panel;
        display: none;
    }
    HexPane Static {
        padding: 0 1;
    }
    """

    def __init__(self, no_data_message: str = "No data available", **kwargs) -> None:
        super().__init__(**kwargs)
        self._hex_bytes: bytes | None = None
        self._hex_addr: int = 0
        self._no_data_message = no_data_message

    def compose(self) -> ComposeResult:
        yield Static("", markup=False)

    def set_data(self, data: bytes, base_addr: int) -> None:
        self._hex_bytes = data
        self._hex_addr = base_addr
        if self.display:
            self._refresh_content()

    def toggle(self) -> None:
        if self.display:
            self.display = False
        else:
            if self._hex_bytes:
                self.display = True
                self._refresh_content()
            else:
                self.app.notify(self._no_data_message, timeout=3)

    def _refresh_content(self) -> None:
        if self._hex_bytes is None:
            return
        self.query_one(Static).update(build_hex_dump(self._hex_bytes, self._hex_addr))


# Diffs are tracked in 8-byte blocks so they map onto both 8- and 16-byte rows.
DIFF_BLOCK = 8
_CMP_CHUNK = 4096


def compute_diff_blocks(left: bytes, right: bytes) -> tuple[list[int], int]:
    """Return (sorted indexes of DIFF_BLOCK-sized blocks that differ, differing byte count).

    Bytes past the end of the shorter input count as differing.
    """
    common = min(len(left), len(right))
    blocks: list[int] = []
    diff_bytes = 0
    for start in range(0, common, _CMP_CHUNK):
        end = min(start + _CMP_CHUNK, common)
        if left[start:end] == right[start:end]:
            continue
        for b in range(start, end, DIFF_BLOCK):
            e = min(b + DIFF_BLOCK, end)
            if left[b:e] != right[b:e]:
                blocks.append(b // DIFF_BLOCK)
                diff_bytes += sum(1 for x, y in zip(left[b:e], right[b:e]) if x != y)
    longest = max(len(left), len(right))
    if longest > common:
        first = common // DIFF_BLOCK
        if blocks and blocks[-1] == first:
            first += 1
        blocks.extend(range(first, (longest + DIFF_BLOCK - 1) // DIFF_BLOCK))
        diff_bytes += longest - common
    return blocks, diff_bytes


class HexCompareView(ScrollView, can_focus=True):
    """Side-by-side hex dump of two byte strings, scrolled as one.

    Matching bytes are green, differing bytes red. Only visible rows are rendered.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("n", "next_diff", "Next diff"),
        Binding("p", "prev_diff", "Prev diff"),
    ]

    DEFAULT_CSS = """
    HexCompareView {
        height: 1fr;
        padding: 0 1;
    }
    """

    _ST_ADDR = Style(dim=True)
    _ST_SAME = Style(color="green")
    _ST_DIFF = Style(color="red", bold=True)
    _ST_SEP = Style(dim=True)
    _ST_HEADER = Style(bold=True, reverse=True)
    _DIVIDER = " │ "

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._left = b""
        self._right = b""
        self._left_name = ""
        self._right_name = ""
        self._diff_blocks: list[int] = []
        self._diff_rows: list[int] = []
        self._bpl = 16

    def set_data(
        self,
        left: bytes,
        right: bytes,
        left_name: str,
        right_name: str,
        diff_blocks: list[int],
    ) -> None:
        self._left, self._right = left, right
        self._left_name, self._right_name = left_name, right_name
        self._diff_blocks = diff_blocks
        self._relayout()

    # ── layout ────────────────────────────────────────────────────────────────

    @staticmethod
    def _pane_width(bpl: int) -> int:
        # offset(8) + 2 + hex (3/byte, +1 gap per extra 8-byte group, -1) + 2 + |ascii|
        hex_w = bpl * 3 - 1 + (bpl // 8 - 1)
        return 8 + 2 + hex_w + 2 + bpl + 2

    def _rows(self) -> int:
        longest = max(len(self._left), len(self._right))
        return (longest + self._bpl - 1) // self._bpl

    def _relayout(self) -> None:
        width = self.scrollable_content_region.width
        bpl = 16 if width >= self._pane_width(16) * 2 + len(self._DIVIDER) else 8
        top_offset = int(self.scroll_y) * self._bpl
        self._bpl = bpl
        per_row = bpl // DIFF_BLOCK
        self._diff_rows = sorted({b // per_row for b in self._diff_blocks})
        # +1 row for the fixed header line
        self.virtual_size = Size(self._pane_width(bpl) * 2 + len(self._DIVIDER), self._rows() + 1)
        self.scroll_to(y=top_offset // bpl, animate=False)
        self.refresh()

    def on_resize(self) -> None:
        self._relayout()

    # ── rendering ─────────────────────────────────────────────────────────────

    def render_line(self, y: int) -> Strip:
        pane_w = self._pane_width(self._bpl)
        if y == 0:
            segs = [
                Segment(f" {self._left_name}"[:pane_w].ljust(pane_w), self._ST_HEADER),
                Segment(self._DIVIDER, self._ST_SEP),
                Segment(f" {self._right_name}"[:pane_w].ljust(pane_w), self._ST_HEADER),
            ]
            return Strip(segs)
        row = int(self.scroll_y) + y - 1
        if row >= self._rows():
            return Strip.blank(self.size.width, self.rich_style)
        offset = row * self._bpl
        a = self._left[offset : offset + self._bpl]
        b = self._right[offset : offset + self._bpl]
        segs = self._pane_segments(offset, a, b)
        segs.append(Segment(self._DIVIDER, self._ST_SEP))
        segs.extend(self._pane_segments(offset, b, a))
        return Strip(segs).crop(int(self.scroll_x), int(self.scroll_x) + self.size.width)

    def _pane_segments(self, offset: int, mine: bytes, other: bytes) -> list[Segment]:
        bpl = self._bpl
        if not mine:
            return [Segment(" " * self._pane_width(bpl))]
        segs = [Segment(f"{offset:08x}", self._ST_ADDR), Segment("  ")]
        styles = [
            self._ST_SAME if i < len(other) and other[i] == mine[i] else self._ST_DIFF
            for i in range(len(mine))
        ]
        for i in range(bpl):
            if i:
                segs.append(Segment("  " if i % 8 == 0 else " "))
            if i < len(mine):
                segs.append(Segment(f"{mine[i]:02x}", styles[i]))
            else:
                segs.append(Segment("  "))
        segs.append(Segment("  |"))
        for i in range(bpl):
            if i < len(mine):
                c = mine[i]
                segs.append(Segment(chr(c) if 0x20 <= c < 0x7F else ".", styles[i]))
            else:
                segs.append(Segment(" "))
        segs.append(Segment("|"))
        return segs

    # ── diff navigation ───────────────────────────────────────────────────────

    def action_next_diff(self) -> None:
        idx = bisect_right(self._diff_rows, int(self.scroll_y))
        if idx >= len(self._diff_rows):
            self.app.notify("No further differences", timeout=2)
            return
        self.scroll_to(y=self._diff_rows[idx], animate=False)

    def action_prev_diff(self) -> None:
        idx = bisect_left(self._diff_rows, int(self.scroll_y)) - 1
        if idx < 0:
            self.app.notify("No previous differences", timeout=2)
            return
        self.scroll_to(y=self._diff_rows[idx], animate=False)


class InfoBar(Horizontal):
    """Top banner showing high-level binary metadata, plus an analysis spinner."""

    DEFAULT_CSS = """
    InfoBar {
        height: auto;
        padding: 0 1;
        background: $surface;
        color: $text;
    }
    InfoBar > #info-bar-text {
        width: 4fr;
        height: auto;
    }
    InfoBar > #info-bar-loading {
        width: 1fr;
        height: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="info-bar-text")
        yield LoadingIndicator(id="info-bar-loading")

    def set_analyzing(self, analyzing: bool) -> None:
        """Show or hide the spinner while background analysis is running."""
        self.query_one("#info-bar-loading", LoadingIndicator).display = analyzing

    def update_info(self, info: BinaryInfo) -> None:
        lines = [
            f"[bold]{os.path.basename(info.path)}[/bold]  "
            f"[cyan]{info.arch}[/cyan]  "
            f"[green]{info.bits}-bit[/green]  "
            f"[yellow]{info.file_type}[/yellow]",
        ]
        # Mach-O specific metadata
        from cheerleader.formats.macho import MachOInfo
        if isinstance(info, MachOInfo):
            meta = []
            if info.uuid:
                meta.append(f"UUID: [dim]{info.uuid}[/dim]")
            if info.min_os:
                meta.append(f"Min OS: [dim]{info.min_os}[/dim]")
            if info.sdk:
                meta.append(f"SDK: [dim]{info.sdk}[/dim]")
            if info.source_version:
                meta.append(f"SrcVer: [dim]{info.source_version}[/dim]")
            if meta:
                lines.append("  ".join(meta))
            if info.dylinker:
                lines.append(f"Linker: [dim]{info.dylinker}[/dim]")
            if info.rpaths:
                lines.append("RPATHs: " + "  ".join(f"[dim]{r}[/dim]" for r in info.rpaths))
        self.query_one("#info-bar-text", Static).update("\n".join(lines))
