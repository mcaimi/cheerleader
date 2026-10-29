"""Lazily-loaded, overwrite-only editable view of a file's bytes.

Also hosts the byte-sequence diff kernel (`compute_diff_blocks`) and the
streaming two-document diff (`iter_diff`) used by the hex compare view.
"""

from __future__ import annotations

import os
import tempfile
import threading
from bisect import bisect_left, insort
from collections import OrderedDict
from collections.abc import Callable, Iterator

PAGE_SIZE = 64 * 1024
MAX_CACHED_PAGES = 64
SEARCH_CHUNK = 1024 * 1024

# Returns True when the operation should stop early.
CancelFn = Callable[[], bool]
ProgressFn = Callable[[float], None]


class HexDocument:
    """File bytes read on demand in pages, with a sparse overlay of pending edits."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fh = open(path, "rb")
        self.size = os.fstat(self._fh.fileno()).st_size
        self._pages: OrderedDict[int, bytes] = OrderedDict()
        self._pages_lock = threading.Lock()
        self._edits: dict[int, int] = {}
        self._edit_keys: list[int] = []  # sorted keys of _edits
        self._undo: list[list[tuple[int, int | None]]] = []

    def close(self) -> None:
        self._fh.close()

    # ── reading ───────────────────────────────────────────────────────────────

    def _page(self, index: int) -> bytes:
        with self._pages_lock:
            page = self._pages.get(index)
            if page is not None:
                self._pages.move_to_end(index)
                return page
            page = os.pread(self._fh.fileno(), PAGE_SIZE, index * PAGE_SIZE)
            self._pages[index] = page
            if len(self._pages) > MAX_CACHED_PAGES:
                self._pages.popitem(last=False)
            return page

    def _read_original(self, offset: int, n: int) -> bytes:
        end = min(offset + n, self.size)
        if offset >= end:
            return b""
        if end - offset > PAGE_SIZE * 4:
            # Bulk reads (search, save) bypass the cache so they don't evict the view.
            return os.pread(self._fh.fileno(), end - offset, offset)
        parts = []
        pos = offset
        while pos < end:
            idx, start = divmod(pos, PAGE_SIZE)
            take = min(PAGE_SIZE - start, end - pos)
            parts.append(self._page(idx)[start : start + take])
            pos += take
        return b"".join(parts)

    def _apply_edits(self, offset: int, data: bytes) -> bytes:
        lo = bisect_left(self._edit_keys, offset)
        hi = bisect_left(self._edit_keys, offset + len(data))
        if lo == hi:
            return data
        buf = bytearray(data)
        for key in self._edit_keys[lo:hi]:
            buf[key - offset] = self._edits[key]
        return bytes(buf)

    def read(self, offset: int, n: int) -> bytes:
        """Return up to *n* bytes at *offset*, with pending edits applied."""
        return self._apply_edits(offset, self._read_original(offset, n))

    def original(self, offset: int) -> int:
        return self._read_original(offset, 1)[0]

    # ── editing ───────────────────────────────────────────────────────────────

    @property
    def modified(self) -> bool:
        return bool(self._edits)

    @property
    def edit_count(self) -> int:
        return len(self._edits)

    def is_edited(self, offset: int) -> bool:
        return offset in self._edits

    def _set(self, offset: int, value: int | None) -> None:
        if value is None or value == self.original(offset):
            if offset in self._edits:
                del self._edits[offset]
                self._edit_keys.pop(bisect_left(self._edit_keys, offset))
            return
        if offset not in self._edits:
            insort(self._edit_keys, offset)
        self._edits[offset] = value

    def write(self, offset: int, data: bytes) -> None:
        """Overwrite bytes starting at *offset*; the file never grows."""
        if offset < 0 or offset + len(data) > self.size:
            raise ValueError("write past end of file")
        if not data:
            return
        self._undo.append([(offset + i, self._edits.get(offset + i)) for i in range(len(data))])
        for i, value in enumerate(data):
            self._set(offset + i, value)

    def undo(self) -> int | None:
        """Revert the last write; return its start offset, or None if nothing to undo."""
        if not self._undo:
            return None
        step = self._undo.pop()
        for offset, prev in step:
            self._set(offset, prev)
        return step[0][0]

    # ── saving ────────────────────────────────────────────────────────────────

    def save_to(self, path: str, progress: ProgressFn | None = None) -> None:
        """Write the edited content to *path* atomically (temp file + rename).

        Safe to run in a worker thread as long as no edits happen meanwhile;
        call ``reopen(path)`` afterwards from the owning thread.
        """
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cheerleader-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as out:
                for offset in range(0, self.size, SEARCH_CHUNK):
                    out.write(self.read(offset, SEARCH_CHUNK))
                    if progress:
                        progress(min(offset + SEARCH_CHUNK, self.size) / max(self.size, 1))
            if os.path.exists(path):
                os.chmod(tmp, os.stat(path).st_mode & 0o7777)
            elif os.path.exists(self.path):
                os.chmod(tmp, os.stat(self.path).st_mode & 0o7777)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def reopen(self, path: str) -> None:
        """Switch to *path* (typically just saved) and drop all pending edits."""
        self._fh.close()
        self.path = path
        self._fh = open(path, "rb")
        self.size = os.fstat(self._fh.fileno()).st_size
        with self._pages_lock:
            self._pages.clear()
        self._edits.clear()
        self._edit_keys.clear()
        self._undo.clear()

    # ── searching ─────────────────────────────────────────────────────────────

    def find(
        self,
        pattern: bytes,
        start: int,
        end: int | None = None,
        cancelled: CancelFn | None = None,
        progress: ProgressFn | None = None,
    ) -> int | None:
        """First offset >= *start* where *pattern* fully fits before *end*."""
        end = self.size if end is None else min(end, self.size)
        overlap = len(pattern) - 1
        total = max(end - start, 1)
        pos = start
        while pos < end:
            if cancelled and cancelled():
                return None
            chunk = self.read(pos, min(SEARCH_CHUNK + overlap, end - pos))
            hit = chunk.find(pattern)
            if hit >= 0:
                return pos + hit
            pos += SEARCH_CHUNK
            if progress:
                progress(min(pos - start, total) / total)
        return None

    def rfind(
        self,
        pattern: bytes,
        start: int,
        end: int,
        cancelled: CancelFn | None = None,
        progress: ProgressFn | None = None,
    ) -> int | None:
        """Last offset in [start, end) where *pattern* fully fits before *end*."""
        overlap = len(pattern) - 1
        end = min(end, self.size)
        total = max(end - start, 1)
        hi = end
        while hi > start:
            if cancelled and cancelled():
                return None
            lo = max(start, hi - SEARCH_CHUNK)
            chunk = self.read(lo, min(hi + overlap, end) - lo)
            hit = chunk.rfind(pattern)
            if hit >= 0:
                return lo + hit
            hi = lo
            if progress:
                progress((end - hi) / total)
        return None


# ── diffing ───────────────────────────────────────────────────────────────────

# Diffs are tracked in 8-byte blocks so they map onto both 8- and 16-byte rows.
DIFF_BLOCK = 8
_CMP_CHUNK = 4096  # fast-skip granularity inside the common region
# Streaming scan window: sized above HexDocument's bulk-read threshold so
# scanning bypasses the page cache and does not evict the view's hot pages.
DIFF_SCAN_CHUNK = 512 * 1024


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


def iter_diff(
    left: HexDocument,
    right: HexDocument,
    scan_chunk: int | None = None,
    cancelled: CancelFn | None = None,
) -> Iterator[tuple[list[int], int, float]]:
    """Stream the diff of two documents chunk by chunk.

    Yields ``(new_blocks, new_diff_bytes, progress)`` per scan window, where
    *new_blocks* holds absolute DIFF_BLOCK indexes (ascending across yields)
    and *progress* is in 0..1. Bytes past the end of the shorter document
    count as differing (the length tail is delivered in the final yield).
    Returns early when *cancelled* fires; the consumer detects this by the
    generator ending without a final progress of 1.0 (or via its own cancel
    flag). The result accumulated over all yields equals
    ``compute_diff_blocks`` over both full contents.
    """
    if scan_chunk is None:
        scan_chunk = DIFF_SCAN_CHUNK  # read at call time (monkeypatch-friendly)
    common = min(left.size, right.size)
    longest = max(left.size, right.size)
    total = max(common, 1)
    blocks: list[int] = []
    diff_bytes = 0
    for start in range(0, common, scan_chunk):
        if cancelled and cancelled():
            return
        take = min(scan_chunk, common - start)
        lc = left.read(start, take)
        rc = right.read(start, take)
        new_blocks: list[int] = []
        n = 0
        if lc != rc:
            local, n = compute_diff_blocks(lc, rc)
            # *local* holds chunk-relative block indexes; translate to absolute
            new_blocks = [(start + b * DIFF_BLOCK) // DIFF_BLOCK for b in local]
            blocks.extend(new_blocks)
            diff_bytes += n
        yield new_blocks, n, min(start + take, common) / total
    if longest > common:
        first = common // DIFF_BLOCK
        if blocks and blocks[-1] == first:
            first += 1
        tail = list(range(first, (longest + DIFF_BLOCK - 1) // DIFF_BLOCK))
        blocks.extend(tail)
        yield tail, longest - common, 1.0
