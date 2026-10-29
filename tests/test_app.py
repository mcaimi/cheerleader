"""Tests for the per-mode App split and the ``build_app`` factory.

Each launch mode (inspector / --hex / --hex-compare) is its own App
subclass; ``build_app`` is the dispatching entry point used by ``main()``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cheerleader.tui.app import DisasmApp, HexCompareApp, HexEditorApp, build_app
from cheerleader.tui.screens import ConfirmPrompt
from cheerleader.tui.tabs import HexEditorTab


def _make_file(tmp_path: Path, content: bytes, name: str = "f.bin") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def _drive(scenario) -> None:
    asyncio.run(scenario())


# ── build_app dispatch ────────────────────────────────────────────────────────


class TestBuildAppDispatch:
    def test_default_builds_inspector(self, tmp_path):
        binary = _make_file(tmp_path, b"\xcf\xfa\xed\xfe" + b"\x00" * 64, "bin")
        app = build_app(str(binary), env_file=None, disasm_engine="objdump")
        assert isinstance(app, DisasmApp)
        assert app._path == str(binary)
        assert app._env_file is None
        assert app._disasm_engine == "objdump"

    def test_hex_mode_builds_editor(self, tmp_path):
        f = _make_file(tmp_path, b"hello", "f.bin")
        app = build_app(str(f), hex_mode=True)
        assert isinstance(app, HexEditorApp)
        assert app._path == str(f)

    def test_hex_compare_builds_compare(self, tmp_path):
        a = _make_file(tmp_path, b"aaa", "a.bin")
        b = _make_file(tmp_path, b"bbb", "b.bin")
        app = build_app(str(a), hex_compare=str(b))
        assert isinstance(app, HexCompareApp)
        assert app._path == str(a)
        assert app._right_path == str(b)

    def test_hex_compare_takes_precedence(self, tmp_path):
        # argparse makes --hex / --hex-compare mutually exclusive; the factory
        # mirrors that precedence so a stray combination still picks compare
        a = _make_file(tmp_path, b"aaa", "a.bin")
        b = _make_file(tmp_path, b"bbb", "b.bin")
        app = build_app(str(a), hex_mode=True, hex_compare=str(b))
        assert isinstance(app, HexCompareApp)

    def test_returns_textual_app(self, tmp_path):
        from textual.app import App

        f = _make_file(tmp_path, b"x", "x.bin")
        assert isinstance(build_app(str(f)), App)


# ── hex editor app ────────────────────────────────────────────────────────────


# the 1-9 tab-switch keys; 's' is excluded because HexEditorTab legitimately
# binds 's' (save) — the App-level check is on the *slice* action instead
_HEX_KEYS = [str(d) for d in range(1, 10)]


class TestHexEditorApp:
    def test_boots_loads_doc_and_lacks_hex_mode_bindings(self, tmp_path):
        f = _make_file(tmp_path, b"hello world", "f.bin")

        async def scenario():
            app = HexEditorApp(str(f))
            async with app.run_test() as pilot:
                await pilot.pause()
                tab = app.query_one(HexEditorTab)
                assert tab._doc is not None
                assert tab._doc.size == len(b"hello world")
                assert app.title == "CHEERLEADER — hex editor"
                assert app.sub_title == "f.bin"
                # 1-9/slice bindings absent — the old check_action-False result
                keys = app.active_bindings
                for k in _HEX_KEYS:
                    assert k not in keys, f"unexpected binding {k!r} in editor"
                assert not any(
                    b.binding.action == "slice" for b in keys.values()
                ), "slice binding active in editor"
                assert "q" in keys and "r" in keys

        _drive(scenario)


# ── hex compare app ───────────────────────────────────────────────────────────


class TestHexCompareApp:
    def test_boots_with_two_files(self, tmp_path):
        a = _make_file(tmp_path, b"AAAA", "a.bin")
        b = _make_file(tmp_path, b"BBBB", "b.bin")

        async def scenario():
            app = HexCompareApp(str(a), str(b))
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.title == "CHEERLEADER — hex compare"
                assert app.sub_title == "a.bin ⇄ b.bin"
                keys = app.active_bindings
                for k in _HEX_KEYS:
                    assert k not in keys, f"unexpected binding {k!r} in compare"
                assert not any(
                    b.binding.action == "slice" for b in keys.values()
                ), "slice binding active in compare"
                assert "q" in keys and "r" in keys

        _drive(scenario)


# ── inspector app ─────────────────────────────────────────────────────────────


class TestDisasmApp:
    @pytest.mark.skipif(not Path("/bin/ls").exists(), reason="requires /bin/ls")
    def test_boots_parses_and_has_bindings(self):
        async def scenario():
            app = DisasmApp("/bin/ls", disasm_engine="objdump")
            async with app.run_test() as pilot:
                for _ in range(200):
                    await asyncio.sleep(0.05)
                    if app._info is not None:
                        break
                assert app._info is not None, "parse never landed"
                await pilot.pause()
                assert app.title == "CHEERLEADER — binary inspector"
                keys = app.active_bindings
                for k in (*_HEX_KEYS, "q", "r"):
                    assert k in keys, f"missing binding {k!r} in inspector"
                assert any(
                    b.binding.action == "slice" for b in keys.values()
                ), "slice binding missing in inspector"

        _drive(scenario)

    def test_slice_action_on_non_macho_notifies(self, tmp_path):
        f = _make_file(tmp_path, b"not a binary", "plain.bin")

        async def scenario():
            app = DisasmApp(str(f), disasm_engine="objdump")
            async with app.run_test() as pilot:
                await pilot.pause()
                app.action_slice()
                await pilot.pause()
                # no screen pushed — the MachOInfo guard fired first
                assert not isinstance(app.screen, ConfirmPrompt)

        _drive(scenario)


# ── discard confirmation ──────────────────────────────────────────────────────


class TestDiscardConfirm:
    def test_reload_without_edits_runs_directly(self, tmp_path):
        f = _make_file(tmp_path, b"hello", "f.bin")

        async def scenario():
            app = HexEditorApp(str(f))
            async with app.run_test() as pilot:
                await pilot.pause()
                loads: list[int] = []
                app._load = lambda: loads.append(1)
                app.action_reload()
                await pilot.pause()
                assert loads == [1]
                assert not isinstance(app.screen, ConfirmPrompt)

        _drive(scenario)

    def test_reload_with_unsaved_edits_prompts_then_runs_on_confirm(self, tmp_path):
        f = _make_file(tmp_path, b"hello", "f.bin")

        async def scenario():
            app = HexEditorApp(str(f))
            async with app.run_test() as pilot:
                await pilot.pause()
                tab = app.query_one(HexEditorTab)
                # simulate an unsaved edit via the document overlay
                tab._doc._set(0, tab._doc.original(0) ^ 0xFF)
                assert tab.modified
                loads: list[int] = []
                app._load = lambda: loads.append(1)
                app.action_reload()
                await pilot.pause()
                assert loads == []  # held until confirmation
                assert isinstance(app.screen, ConfirmPrompt)
                await pilot.press("y")  # confirm
                await pilot.pause()
                assert loads == [1]

        _drive(scenario)

    def test_reload_with_unsaved_edits_decline_skips_load(self, tmp_path):
        f = _make_file(tmp_path, b"hello", "f.bin")

        async def scenario():
            app = HexEditorApp(str(f))
            async with app.run_test() as pilot:
                await pilot.pause()
                tab = app.query_one(HexEditorTab)
                tab._doc._set(0, tab._doc.original(0) ^ 0xFF)
                loads: list[int] = []
                app._load = lambda: loads.append(1)
                app.action_reload()
                await pilot.pause()
                assert isinstance(app.screen, ConfirmPrompt)
                await pilot.press("n")  # decline
                await pilot.pause()
                assert loads == []

        _drive(scenario)

    def test_inspector_reload_never_prompts(self, tmp_path):
        f = _make_file(tmp_path, b"junk data here", "plain.bin")

        async def scenario():
            app = DisasmApp(str(f), disasm_engine="objdump")
            async with app.run_test() as pilot:
                await pilot.pause()
                app.action_reload()
                await pilot.pause()
                assert not isinstance(app.screen, ConfirmPrompt)

        _drive(scenario)
