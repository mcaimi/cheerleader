"""Parsing of user-provided byte sequences and offsets for the hex UI.

  Accepted byte-sequence forms:
    ``"text``          literal UTF-8 text (closing quote optional)
    ``de ad be ef``    hex bytes separated by spaces or commas
    ``deadbeef``       packed hex (even number of digits)
    ``0x7f`` / ``7``   single hex byte with optional prefix
    ``#127``           decimal byte

  Accepted offset forms:
    ``1a2b`` / ``0x1a2b``   hex (``0x`` prefix optional, ``_`` separators ok)
    ``#6699``               decimal

All functions raise ``ValueError`` with a user-facing message on bad input.
"""

from __future__ import annotations

import re

_HEX_TOKEN = re.compile(r"(?:0x)?([0-9a-fA-F]+)")


def parse_byte_sequence(text: str) -> bytes:
    """Parse user input into bytes; raises ValueError on bad input."""
    text = text.strip()
    if not text:
        raise ValueError("empty input")
    if text[0] in "\"'":
        body = text[1:]
        if len(body) >= 1 and body[-1] == text[0]:
            body = body[:-1]
        if not body:
            raise ValueError("empty string")
        return body.encode("utf-8")
    out = bytearray()
    for token in re.split(r"[\s,]+", text):
        if token.startswith("#"):
            try:
                value = int(token[1:], 10)
            except ValueError:
                raise ValueError(f"bad decimal value: {token}") from None
            if not 0 <= value <= 255:
                raise ValueError(f"decimal value out of range 0–255: {token}")
            out.append(value)
            continue
        m = _HEX_TOKEN.fullmatch(token)
        if not m:
            raise ValueError(f"not a hex byte: {token}")
        digits = m.group(1)
        if len(digits) <= 2:
            out.append(int(digits, 16))
        elif len(digits) % 2 == 0:
            out.extend(bytes.fromhex(digits))
        else:
            raise ValueError(f"odd number of hex digits: {token}")
    return bytes(out)


def parse_offset(text: str) -> int:
    """Parse an offset: hex by default (``0x`` optional), decimal with ``#``."""
    text = text.strip().replace("_", "")
    if not text:
        raise ValueError("empty offset")
    try:
        if text.startswith("#"):
            return int(text[1:], 10)
        return int(text, 16)
    except ValueError:
        raise ValueError(f"invalid offset: {text}") from None
