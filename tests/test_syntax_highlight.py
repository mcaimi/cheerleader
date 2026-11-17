"""Tests for the front-end-neutral disassembly classifier (libs.highlight)
and the TUI's rich wrappers built on it."""

from __future__ import annotations

import pytest

from cheerleader.libs.highlight import is_arm_arch, mnemonic_kind, operand_spans
from cheerleader.tui.highlight import _colorize_mnemonic, _colorize_operands


@pytest.mark.parametrize(
    ("mnemonic", "kind"),
    [
        ("ret", "ret"),
        ("RETQ", "ret"),
        ("bl", "branch"),
        ("b.eq", "branch"),
        ("jne", "branch"),
        ("call", "branch"),
        ("nop", "nop"),
        ("push", "stack"),
        ("cmp", "cmp"),
        ("mov", "mem"),
        ("ldp", "mem"),
        ("lea", "mem"),
        ("add", "arith"),
        ("eor", "arith"),
        ("pacibsp", "plain"),
    ],
)
def test_mnemonic_kind(mnemonic: str, kind: str) -> None:
    assert mnemonic_kind(mnemonic) == kind


@pytest.mark.parametrize(
    ("arch", "arm"),
    [("arm64", True), ("arm64e", True), ("aarch64", True), ("x86_64", False)],
)
def test_is_arm_arch(arch: str, arm: bool) -> None:
    assert is_arm_arch(arch) is arm


@pytest.mark.parametrize(
    ("op_str", "arch"),
    [
        ("x29, sp, #0x10", "arm64"),
        ("w0, [x1, #-4]", "aarch64"),
        ("rax, qword ptr [rbp - 0x18]", "x86_64"),
        ("", "arm64"),
        ("0x100003f40", "arm64"),
    ],
)
def test_operand_spans_round_trip(op_str: str, arch: str) -> None:
    assert "".join(t for t, _ in operand_spans(op_str, arch)) == op_str


def test_operand_spans_arm_kinds() -> None:
    assert operand_spans("x29, sp, #0x10", "arm64") == [
        ("x29", "reg"),
        (", ", "plain"),
        ("sp", "reg"),
        (", ", "plain"),
        ("#0x10", "imm"),
    ]
    assert ("#-4", "imm") in operand_spans("w0, [x1, #-4]", "aarch64")


def test_operand_spans_x86_kinds() -> None:
    spans = operand_spans("rax, qword ptr [rbp - 0x18]", "x86_64")
    assert ("rax", "reg") in spans and ("rbp", "reg") in spans
    assert ("0x18", "imm") in spans
    # x86 register set is not applied to ARM operands and vice versa
    assert all(k != "reg" for _, k in operand_spans("rax", "arm64"))


def test_operand_spans_overlap_keeps_leftmost() -> None:
    # "#0x10" matches both the hex and the decimal-immediate patterns
    # ("#0" alone would be a decimal imm): only one piece survives.
    spans = operand_spans("#0x10", "arm64")
    assert spans == [("#0x10", "imm")]


def test_tui_wrappers_keep_styles() -> None:
    assert str(_colorize_mnemonic("ret").style) == "bold red"
    assert str(_colorize_mnemonic("pacibsp").style) == ""
    text = _colorize_operands("x0, #0x1", "arm64")
    assert text.plain == "x0, #0x1"
    assert [str(s.style) for s in text.spans] == ["bright_green", "yellow"]
