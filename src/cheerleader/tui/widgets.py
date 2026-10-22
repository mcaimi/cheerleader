"""Reusable widgets for the binary inspector TUI."""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
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
