"""Structural regression tests for the tab classes.

A missing ``StatusMixin`` only fails at *runtime* — when a worker thread
lands on ``_set_status`` — which tab-level unit tests may never reach
(the SymbolsTab case survived a full green suite). These tests parse the
tabs module's AST so the invariant is enforced statically.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import cheerleader.tui.tabs as tabs_module


def _classes_with_set_status_or_mixin() -> dict[str, dict]:
    """Parse tabs.py: per class — calls _set_status? has StatusMixin? STATUS_ID?"""
    src = Path(tabs_module.__file__).read_text()
    tree = ast.parse(src)
    result: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        has_mixin = any(
            (isinstance(b, ast.Name) and b.id == "StatusMixin")
            or (isinstance(b, ast.Attribute) and b.attr == "StatusMixin")
            for b in node.bases
        )
        status_id: str | None = None
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "STATUS_ID" for t in stmt.targets
                )
                and isinstance(stmt.value, ast.Constant)
            ):
                status_id = stmt.value.value
        # _set_status calls anywhere inside the class body (incl. nested funcs)
        calls = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_set_status"
            for n in ast.walk(node)
        )
        result[node.name] = {"calls": calls, "mixin": has_mixin, "status_id": status_id}
    return result


class TestStatusMixinInvariant:
    def test_every_set_status_caller_has_mixin_and_id(self):
        classes = _classes_with_set_status_or_mixin()
        assert classes, "no classes found — AST parse failed?"
        for name, info in classes.items():
            if info["calls"]:
                assert info["mixin"], (
                    f"{name} calls _set_status but does not inherit StatusMixin"
                )
                assert info["status_id"], f"{name} calls _set_status but has no STATUS_ID"

    def test_every_mixin_user_has_status_id(self):
        classes = _classes_with_set_status_or_mixin()
        for name, info in classes.items():
            if info["mixin"]:
                assert info["status_id"], (
                    f"{name} inherits StatusMixin but has no STATUS_ID class attribute"
                )

    @pytest.mark.parametrize(
        "cls_name",
        [
            "SymbolsTab",
            "StringsTab",
            "FuncReversingTab",
            "DisasmTab",
            "HexEditorTab",
            "HexCompareTab",
        ],
    )
    def test_known_status_tabs_use_mixin(self, cls_name: str):
        classes = _classes_with_set_status_or_mixin()
        assert cls_name in classes, f"{cls_name} missing from tabs module"
        info = classes[cls_name]
        assert info["mixin"] and info["status_id"], f"{cls_name} lost its StatusMixin"

    def test_non_status_tabs_do_not_use_mixin(self):
        # tabs without a status line must not carry the mixin
        classes = _classes_with_set_status_or_mixin()
        for name, info in classes.items():
            if not info["calls"]:
                assert not info["mixin"], (
                    f"{name} inherits StatusMixin but never calls _set_status"
                )
