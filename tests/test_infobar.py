"""Tests for BinaryInfo.info_meta_lines() and the InfoBar rendering.

The info bar renders format-specific metadata polymorphically — the widget
never imports format classes. The harness test pins the exact rendered
markup so the refactor cannot drift.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from cheerleader.formats.macho import MachOInfo
from cheerleader.libs.types import BinaryInfo
from cheerleader.tui.widgets import InfoBar


class TestInfoMetaLines:
    def test_base_returns_empty(self):
        info = BinaryInfo(path="/tmp/x")
        assert info.info_meta_lines() == []

    def test_macho_all_fields(self):
        info = MachOInfo(
            path="/tmp/x",
            uuid="AAAA-BBBB",
            min_os="12.0",
            sdk="13.0",
            source_version="1.2.3",
            rpaths=["/r1", "/r2"],
            dylinker="/usr/lib/dyld",
        )
        assert info.info_meta_lines() == [
            [("UUID", "AAAA-BBBB"), ("Min OS", "12.0"), ("SDK", "13.0"), ("SrcVer", "1.2.3")],
            [("Linker", "/usr/lib/dyld")],
            [("RPATHs", "/r1  /r2")],
        ]

    def test_macho_partial_fields_omit_empties(self):
        info = MachOInfo(path="/tmp/x", uuid="AAAA-BBBB", sdk="13.0")
        assert info.info_meta_lines() == [
            [("UUID", "AAAA-BBBB"), ("SDK", "13.0")],
        ]

    def test_macho_no_fields(self):
        info = MachOInfo(path="/tmp/x")
        assert info.info_meta_lines() == []

    def test_macho_only_rpaths(self):
        info = MachOInfo(path="/tmp/x", rpaths=["/opt/homebrew/lib"])
        assert info.info_meta_lines() == [
            [("RPATHs", "/opt/homebrew/lib")],
        ]

    def test_macho_only_dylinker(self):
        info = MachOInfo(path="/tmp/x", dylinker="/usr/lib/dyld")
        assert info.info_meta_lines() == [
            [("Linker", "/usr/lib/dyld")],
        ]


class _Harness(App):
    def compose(self) -> ComposeResult:
        yield InfoBar(id="info-bar")


def _drive(scenario) -> None:
    asyncio.run(scenario())


class TestInfoBarRender:
    def test_renders_polymorphic_meta_lines(self):
        async def scenario():
            app = _Harness()
            async with app.run_test() as pilot:
                info_bar = pilot.app.query_one(InfoBar)
                info = MachOInfo(
                    path="/tmp/foo.dylib",
                    arch="arm64",
                    bits=64,
                    file_type="dylib",
                    uuid="AAAA-BBBB",
                    min_os="12.0",
                    dylinker="/usr/lib/dyld",
                )
                info_bar.update_info(info)
                await pilot.pause()
                text = pilot.app.query_one("#info-bar-text").content
                lines = text.split("\n")
                # header line: path + arch + bits + file type
                assert "[bold]foo.dylib[/bold]" in lines[0]
                assert "[cyan]arm64[/cyan]" in lines[0]
                assert "[green]64-bit[/green]" in lines[0]
                assert "[yellow]dylib[/yellow]" in lines[0]
                # format-specific meta lines, rendered from the model's structure
                assert lines[1] == "UUID: [dim]AAAA-BBBB[/dim]  Min OS: [dim]12.0[/dim]"
                assert lines[2] == "Linker: [dim]/usr/lib/dyld[/dim]"
                assert len(lines) == 3

        _drive(scenario)

    def test_base_info_renders_header_only(self):
        async def scenario():
            app = _Harness()
            async with app.run_test() as pilot:
                info_bar = pilot.app.query_one(InfoBar)
                info_bar.update_info(BinaryInfo(path="/tmp/flat.bin", arch="x86_64", bits=64))
                await pilot.pause()
                text = pilot.app.query_one("#info-bar-text").content
                lines = text.split("\n")
                assert len(lines) == 1
                assert "[bold]flat.bin[/bold]" in lines[0]

        _drive(scenario)
