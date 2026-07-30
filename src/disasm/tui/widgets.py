"""Top-level info bar widget."""

from __future__ import annotations

import os

from textual.widgets import Static

from disasm.libs.types import BinaryInfo


class InfoBar(Static):
    """Top banner showing high-level binary metadata."""

    DEFAULT_CSS = """
    InfoBar {
        height: auto;
        padding: 0 1;
        background: $surface;
        color: $text;
    }
    """

    def update_info(self, info: BinaryInfo) -> None:
        lines = [
            f"[bold]{os.path.basename(info.path)}[/bold]  "
            f"[cyan]{info.arch}[/cyan]  "
            f"[green]{info.bits}-bit[/green]  "
            f"[yellow]{info.file_type}[/yellow]",
        ]
        # Mach-O specific metadata
        from disasm.formats.macho import MachOInfo
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
        self.update("\n".join(lines))
