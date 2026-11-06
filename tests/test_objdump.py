"""Regression tests for objdump subprocess environment and engine selection.

Two related bugs motivated these tests:

1. With ``DEBUGINFOD_URLS`` set, LLVM 23's ``llvm-objdump`` treats a stripped
   binary as incomplete, fetches its (content-less, ``SHT_NOBITS``) debuginfod
   copy by build ID, and disassembles *that* — emitting a bare ``...`` with no
   instruction lines.  ``_run_objdump`` must therefore never leak
   ``DEBUGINFOD_URLS`` into the subprocess environment.

2. ``_detect_objdump`` used to prefer ``llvm-objdump`` everywhere.  GNU
   objdump is now preferred on non-Darwin systems for stable output, while
   macOS accepts LLVM objdump only (it selects fat-binary slices).
"""

from __future__ import annotations

import subprocess

import pytest

from cheerleader.libs import objdump as od

# ── helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_objdump_cache(monkeypatch):
    """Give every test a fresh detection cache and restore it afterwards."""
    monkeypatch.setattr(od, "_objdump_info", None)


def _fake_run(captured: dict):
    """Return a subprocess.run stub that records the env kwarg."""

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return fake_run


def _which_factory(paths: dict[str, str]):
    """Build a shutil.which stub from a {name: path} mapping."""

    def fake_which(name: str):
        return paths.get(name)

    return fake_which


# ── subprocess environment sanitization ──────────────────────────────────────


class TestRunObjdumpEnv:
    def test_strips_debuginfod_urls(self, monkeypatch):
        monkeypatch.setenv("DEBUGINFOD_URLS", "https://debuginfod.example.org")
        captured: dict = {}
        monkeypatch.setattr(od.subprocess, "run", _fake_run(captured))

        assert od._run_objdump(["objdump", "-d", "/bin/ls"]) == ""
        assert captured["env"] is not None
        assert "DEBUGINFOD_URLS" not in captured["env"]

    def test_keeps_other_env_vars(self, monkeypatch):
        monkeypatch.setenv("CHEERLEADER_KEEP_ME", "yes")
        captured: dict = {}
        monkeypatch.setattr(od.subprocess, "run", _fake_run(captured))

        od._run_objdump(["objdump", "-d", "/bin/ls"])
        assert captured["env"]["CHEERLEADER_KEEP_ME"] == "yes"

    def test_returncode_nonzero_returns_none(self, monkeypatch):
        def failing_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="x", stderr="boom")

        monkeypatch.setattr(od.subprocess, "run", failing_run)
        assert od._run_objdump(["objdump", "-d", "/missing"]) is None


# ── engine selection preference ──────────────────────────────────────────────


class TestDetectObjdumpPreference:
    def test_gnu_objdump_preferred_on_linux(self, monkeypatch):
        monkeypatch.setattr(od.platform, "system", lambda: "Linux")
        monkeypatch.setattr(
            od.shutil,
            "which",
            _which_factory(
                {
                    "objdump": "/usr/bin/objdump",
                    "llvm-objdump": "/usr/bin/llvm-objdump",
                }
            ),
        )
        monkeypatch.setattr(od, "_check_llvm", lambda path: False)

        assert od._detect_objdump() == ("/usr/bin/objdump", False)

    def test_falls_back_to_llvm_on_linux(self, monkeypatch):
        monkeypatch.setattr(od.platform, "system", lambda: "Linux")
        monkeypatch.setattr(
            od.shutil,
            "which",
            _which_factory(
                {
                    "llvm-objdump": "/usr/bin/llvm-objdump",
                }
            ),
        )
        monkeypatch.setattr(od, "_check_llvm", lambda path: True)

        assert od._detect_objdump() == ("/usr/bin/llvm-objdump", True)

    def test_llvm_objdump_preferred_on_darwin(self, monkeypatch):
        monkeypatch.setattr(od.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            od.shutil,
            "which",
            _which_factory(
                {
                    "objdump": "/usr/bin/objdump",
                    "llvm-objdump": "/usr/bin/llvm-objdump",
                }
            ),
        )
        monkeypatch.setattr(od, "_check_llvm", lambda path: True)

        assert od._detect_objdump() == ("/usr/bin/llvm-objdump", True)

    def test_returns_none_when_missing(self, monkeypatch):
        monkeypatch.setattr(od.platform, "system", lambda: "Linux")
        monkeypatch.setattr(od.shutil, "which", _which_factory({}))

        assert od._detect_objdump() is None

    def test_gnu_objdump_rejected_on_darwin(self, monkeypatch):
        """A GNU objdump on macOS cannot pick a fat slice: never used."""
        monkeypatch.setattr(od.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            od.shutil, "which", _which_factory({"objdump": "/opt/bin/objdump"})
        )
        monkeypatch.setattr(od, "_check_llvm", lambda path: False)

        assert od._detect_objdump() is None

    def test_llvm_based_objdump_accepted_on_darwin(self, monkeypatch):
        """Xcode's ``objdump`` is LLVM: fine as the fallback."""
        monkeypatch.setattr(od.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            od.shutil, "which", _which_factory({"objdump": "/usr/bin/objdump"})
        )
        monkeypatch.setattr(od, "_check_llvm", lambda path: True)

        assert od._detect_objdump() == ("/usr/bin/objdump", True)


# ── fat-slice selection ──────────────────────────────────────────────────────


class TestArchSelector:
    @pytest.mark.parametrize(
        "arch, expected",
        [
            ("arm64", "--arch=arm64"),
            ("arm64e", "--arch=arm64e"),  # exact: arm64 does not match it
            ("arm64e.x1", "--arch=arm64e.x1"),
            ("x86_64h", "--arch=x86_64h"),
            ("x86", "--arch=i386"),
        ],
    )
    def test_macho_arch_flag(self, arch, expected):
        cmd = od._build_command("llvm-objdump", "/b", arch, False, True)
        assert expected in cmd

    @pytest.mark.parametrize("arch", ["0x0000002A", "?", ""])
    def test_unknown_arch_has_no_flag(self, arch):
        cmd = od._build_command("llvm-objdump", "/b", arch, False, True)
        assert not any(c.startswith("--arch") for c in cmd)

    def test_elf_and_gnu_never_get_arch(self):
        assert not any(
            c.startswith("--arch")
            for c in od._build_command("objdump", "/b", "arm64", True, True)
            + od._build_command("objdump", "/b", "arm64", False, False)
        )


class TestParseOutput:
    def test_fat_binary_double_listing_is_deduplicated(self):
        """llvm-objdump repeats the section for universal binaries."""
        block = (
            "0000000100000460 <_main>:\n"
            "100000460: ff 43 00 d1\tsub\tsp, sp, #0x10\n"
            "100000464: c0 03 5f d6\tret\n"
        )
        text = (
            "/usr/bin/fat:\n" + block + "Contents of (__TEXT,__text) section\n" + block
        )
        instrs, labels = od._parse_output(text)
        assert [i.addr for i in instrs] == [0x100000460, 0x100000464]
        assert labels == {0x100000460: "_main"}
