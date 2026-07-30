"""Abstract parser protocol for binary formats."""

from __future__ import annotations

from typing import Protocol

from disasm.libs.types import BinaryInfo


class FormatParser(Protocol):
    def parse(self, path: str, **kwargs) -> BinaryInfo: ...
