"""Tests for HexDocument: paged reading, sparse edit overlay, undo, save, search."""

from __future__ import annotations

import os
import threading

import pytest

from cheerleader.libs import hexdoc
from cheerleader.libs.hexdoc import HexDocument


PAGE = hexdoc.PAGE_SIZE  # 64 KiB


def make_file(tmp_path, data: bytes, name: str = "blob.bin") -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


@pytest.fixture()
def doc(tmp_path):
    """A 300-byte document with recognisable content."""
    data = bytes(range(256)) + b"HELLO WORLD, HELLO WORLD" + b"\x00" * 20
    path = make_file(tmp_path, data)
    d = HexDocument(path)
    yield d
    d.close()


# ── reading ───────────────────────────────────────────────────────────────────


class TestRead:
    def test_read_full(self, doc, tmp_path):
        assert doc.read(0, 300) == (tmp_path / "blob.bin").read_bytes()

    def test_read_slice(self, doc):
        assert doc.read(256, 11) == b"HELLO WORLD"
        assert doc.read(262, 5) == b"WORLD"

    def test_read_clamped_at_eof(self, doc):
        assert len(doc.read(295, 100)) == 5
        assert doc.read(300, 10) == b""
        assert doc.read(1000, 10) == b""

    def test_read_zero_length(self, doc):
        assert doc.read(10, 0) == b""

    def test_read_across_page_boundary(self, tmp_path):
        data = bytes(range(256)) * (PAGE // 256 + 1)  # > 64 KiB
        d = HexDocument(make_file(tmp_path, data))
        try:
            size = d.size
            assert size > PAGE
            # span that crosses the first page boundary
            out = d.read(PAGE - 3, 6)
            assert out == data[PAGE - 3 : PAGE + 3]
            # last partial page
            out = d.read(size - 5, 100)
            assert out == data[-5:]
        finally:
            d.close()

    def test_bulk_read_bypasses_cache_but_matches_file(self, tmp_path):
        data = bytes(range(256)) * (PAGE * 5 // 256 + 1)  # ~ 5 pages
        d = HexDocument(make_file(tmp_path, data))
        try:
            # > PAGE_SIZE * 4 → bulk pread path
            out = d.read(0, PAGE * 4 + 1)
            assert out == data[: PAGE * 4 + 1]
        finally:
            d.close()

    def test_original_returns_pre_edit_byte(self, doc):
        doc.write(0, b"\xff")
        assert doc.original(0) == 0  # file byte 0 is 0x00
        assert doc.read(0, 1) == b"\xff"

    def test_size(self, doc):
        assert doc.size == 300


# ── editing ───────────────────────────────────────────────────────────────────


class TestEdit:
    def test_write_and_read_overlay(self, doc):
        doc.write(10, b"\xde\xad\xbe\xef")
        assert doc.read(10, 4) == b"\xde\xad\xbe\xef"
        # neighbours untouched
        assert doc.read(9, 1) == b"\x09"
        assert doc.read(14, 1) == b"\x0e"

    def test_edit_state_tracking(self, doc):
        assert not doc.modified
        assert doc.edit_count == 0
        doc.write(5, b"\xaa\xbb")
        assert doc.modified
        assert doc.edit_count == 2
        assert doc.is_edited(5)
        assert doc.is_edited(6)
        assert not doc.is_edited(4)
        assert not doc.is_edited(7)

    def test_writing_original_value_clears_edit(self, doc):
        doc.write(5, b"\xaa")
        assert doc.is_edited(5)
        doc.write(5, b"\x05")  # 0x05 is the original byte at offset 5
        assert not doc.is_edited(5)
        assert not doc.modified
        assert doc.edit_count == 0

    def test_write_empty_is_noop(self, doc):
        doc.write(10, b"")
        assert not doc.modified

    def test_write_past_end_raises(self, doc):
        with pytest.raises(ValueError):
            doc.write(299, b"\x00\x00")  # one byte past the end
        with pytest.raises(ValueError):
            doc.write(-1, b"\x00")

    def test_write_at_exact_end_is_valid(self, doc):
        doc.write(299, b"\xff")
        assert doc.read(299, 1) == b"\xff"

    def test_write_spanning_existing_edit(self, doc):
        data = doc.read(0, 300)
        doc.write(2, b"\xaa")
        doc.write(1, b"\xbb\xcc")  # overwrites byte 2's edit with a new value
        assert doc.read(1, 2) == b"\xbb\xcc"
        # undo of the second write restores byte 1 to original, byte 2 to 0xaa
        assert doc.undo() == 1
        assert doc.read(1, 2) == bytes([data[1], 0xAA])
        # undo again clears byte 2's original edit
        assert doc.undo() == 2
        assert doc.read(1, 2) == data[1:3]
        assert not doc.modified


# ── undo ──────────────────────────────────────────────────────────────────────


class TestUndo:
    def test_undo_empty_stack(self, doc):
        assert doc.undo() is None

    def test_undo_single_write(self, doc):
        doc.write(10, b"\xff")
        assert doc.undo() == 10
        assert doc.read(10, 1) == b"\x0a"
        assert not doc.modified

    def test_undo_is_lifo(self, doc):
        doc.write(0, b"\x01")
        doc.write(0, b"\x02")
        assert doc.undo() == 0
        assert doc.read(0, 1) == b"\x01"
        assert doc.undo() == 0
        assert doc.read(0, 1) == b"\x00"
        assert doc.undo() is None

    def test_undo_restores_all_bytes_of_multi_write(self, doc):
        doc.write(100, b"\x01\x02\x03\x04")
        doc.write(102, b"\xff\xff")  # second write overlapping the first
        assert doc.undo() == 102
        assert doc.read(100, 4) == b"\x01\x02\x03\x04"
        assert doc.undo() == 100
        assert doc.read(100, 4) == b"\x64\x65\x66\x67"

    def test_undo_after_write_original(self, doc):
        # write X, then write back the original: undo must restore X
        doc.write(7, b"\xaa")
        doc.write(7, b"\x07")
        assert not doc.modified
        assert doc.undo() == 7
        assert doc.read(7, 1) == b"\xaa"
        assert doc.modified


# ── saving ────────────────────────────────────────────────────────────────────


class TestSave:
    def test_save_to_new_file(self, doc, tmp_path):
        doc.write(256, b"JELLO")
        out = tmp_path / "out.bin"
        doc.save_to(str(out))
        expected = doc.read(0, 300)
        assert out.read_bytes() == expected
        assert out.read_bytes() != (tmp_path / "blob.bin").read_bytes()

    def test_save_no_edits_copies_file(self, doc, tmp_path):
        out = tmp_path / "copy.bin"
        doc.save_to(str(out))
        assert out.read_bytes() == (tmp_path / "blob.bin").read_bytes()

    def test_save_preserves_target_mode(self, doc, tmp_path):
        target = tmp_path / "target.bin"
        target.write_bytes(b"x" * 300)
        os.chmod(target, 0o600)
        doc.save_to(str(target))
        assert (os.stat(target).st_mode & 0o777) == 0o600

    def test_save_new_file_inherits_source_mode(self, doc, tmp_path):
        os.chmod(doc.path, 0o644)
        out = tmp_path / "fresh.bin"
        doc.save_to(str(out))
        assert (os.stat(out).st_mode & 0o777) == 0o644

    def test_save_progress_reaches_one(self, doc):
        calls: list[float] = []
        doc.save_to(doc.path + ".copy", progress=calls.append)
        assert calls
        assert calls[-1] == pytest.approx(1.0)
        assert calls == sorted(calls)

    def test_failed_save_leaves_no_temp_files(self, doc, tmp_path):
        target = tmp_path / "never-written.bin"

        def boom(_frac: float) -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            doc.save_to(str(target), progress=boom)

        # target untouched, no .cheerleader-*.tmp leftovers
        assert not target.exists()
        leftovers = list(tmp_path.glob(".cheerleader-*"))
        assert leftovers == []

    def test_reopen_clears_state(self, doc, tmp_path):
        other = make_file(tmp_path, b"short file", name="other.bin")
        doc.write(0, b"\xff\xff")
        doc.reopen(other)
        assert doc.path == other
        assert doc.size == len("short file")
        assert not doc.modified
        assert doc.edit_count == 0
        assert doc.read(0, 5) == b"short"
        # undo history cleared too
        assert doc.undo() is None


# ── searching ─────────────────────────────────────────────────────────────────


class TestFind:
    def test_find_basic(self, doc):
        assert doc.find(b"HELLO", 0) == 256
        assert doc.find(b"HELLO", 257) == 269  # second occurrence
        assert doc.find(b"HELLO", 270) is None

    def test_find_respects_start(self, doc):
        # 0x00 appears only at offset 0 in the ascending body; next zero run
        # starts at 280 (the 20-byte trailing zero block)
        assert doc.find(bytes([0]), 1) == 280

    def test_find_whole_file(self, doc):
        assert doc.find(bytes(range(256)), 0) == 0

    def test_find_sees_pending_edits(self, doc):
        # edits are part of the searched content
        doc.write(300 - 20, b"NEEDLE")
        assert doc.find(b"NEEDLE", 0) == 280

    def test_find_no_match(self, doc):
        assert doc.find(b"NOT PRESENT", 0) is None

    def test_find_pattern_longer_than_file(self, doc):
        assert doc.find(b"x" * 1000, 0) is None

    def test_find_crosses_chunk_boundary(self, tmp_path, monkeypatch):
        # shrink the search chunk so the pattern straddles chunk boundaries
        monkeypatch.setattr(hexdoc, "SEARCH_CHUNK", 16)
        data = b"\x00" * 200
        data = bytearray(data)
        data[30:36] = b"MARKER"
        data[62:68] = b"MARKER"
        d = HexDocument(make_file(tmp_path, bytes(data)))
        try:
            # 30..36 straddles the chunk starting at 16 (window 16..37)
            assert d.find(b"MARKER", 0) == 30
            # 62..68 straddles the chunk starting at 48 (window 48..69)
            assert d.find(b"MARKER", 31) == 62
        finally:
            d.close()

    def test_find_cancelled(self, doc):
        assert doc.find(b"HELLO", 0, cancelled=lambda: True) is None

    def test_find_progress(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hexdoc, "SEARCH_CHUNK", 16)
        d = HexDocument(make_file(tmp_path, b"\x00" * 200))
        try:
            calls: list[float] = []
            assert d.find(b"MISSING", 0, progress=calls.append) is None
            assert calls
            assert calls[-1] == pytest.approx(1.0)
        finally:
            d.close()


class TestRfind:
    def test_rfind_basic(self, doc):
        assert doc.rfind(b"HELLO", 0, doc.size) == 269

    def test_rfind_respects_end(self, doc):
        assert doc.rfind(b"HELLO", 0, 268) == 256

    def test_rfind_respects_start(self, doc):
        assert doc.rfind(b"HELLO", 257, doc.size) == 269

    def test_rfind_no_match_in_range(self, doc):
        assert doc.rfind(b"HELLO", 270, doc.size) is None

    def test_rfind_whole_range_of_bytes(self, doc):
        assert doc.rfind(bytes(range(256)), 0, doc.size) == 0

    def test_rfind_sees_pending_edits(self, doc):
        doc.write(280, b"NEEDLE")
        assert doc.rfind(b"NEEDLE", 0, doc.size) == 280

    def test_rfind_crosses_chunk_boundary(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hexdoc, "SEARCH_CHUNK", 16)
        data = bytearray(b"\x00" * 200)
        data[90:96] = b"MARKER"   # window of last chunk: 84..99
        data[58:64] = b"MARKER"   # straddles chunks at 48/64
        d = HexDocument(make_file(tmp_path, bytes(data)))
        try:
            assert d.rfind(b"MARKER", 0, 200) == 90
            assert d.rfind(b"MARKER", 0, 90) == 58
            assert d.rfind(b"MARKER", 0, 58) is None
        finally:
            d.close()

    def test_rfind_cancelled(self, doc):
        assert doc.rfind(b"HELLO", 0, doc.size, cancelled=lambda: True) is None

    def test_rfind_progress(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hexdoc, "SEARCH_CHUNK", 16)
        d = HexDocument(make_file(tmp_path, b"\x00" * 200))
        try:
            calls: list[float] = []
            assert d.rfind(b"MISSING", 0, 200, progress=calls.append) is None
            assert calls
            assert calls[-1] == pytest.approx(1.0)
        finally:
            d.close()


# ── paging / cache ────────────────────────────────────────────────────────────


class TestPaging:
    def test_reads_survive_page_eviction(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hexdoc, "PAGE_SIZE", 1024)
        monkeypatch.setattr(hexdoc, "MAX_CACHED_PAGES", 2)
        data = bytes(range(256)) * 40  # 10 KiB → 10 pages
        d = HexDocument(make_file(tmp_path, data))
        try:
            # walk forward twice, forcing evictions and cache misses
            for _ in range(2):
                for off in range(0, len(data), 1024):
                    assert d.read(off, 1) == data[off : off + 1]
            # interleaved reads still correct after eviction
            assert d.read(0, 4) == data[0:4]
            assert d.read(9216, 4) == data[9216:9220]
        finally:
            d.close()

    def test_cache_respects_max_pages(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hexdoc, "MAX_CACHED_PAGES", 3)
        data = bytes(range(256)) * 1024  # 256 KiB → 4 pages
        d = HexDocument(make_file(tmp_path, data))
        try:
            for i in range(4):
                d._page(i)
            with d._pages_lock:
                assert len(d._pages) <= 3
                assert 3 in d._pages  # most recent retained
                assert 0 not in d._pages  # oldest evicted
        finally:
            d.close()

    def test_concurrent_reads(self, doc):
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(50):
                    out = doc.read(0, 300)
                    assert len(out) == 300
                    assert out[256:267] == b"HELLO WORLD"
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ── SparseOverlay ─────────────────────────────────────────────────────────────


class TestSparseOverlay:
    def test_set_and_get_roundtrip(self):
        ov = hexdoc.SparseOverlay()
        assert not ov
        assert len(ov) == 0
        ov.set(10, 0xAB)
        assert bool(ov)
        assert len(ov) == 1
        assert 10 in ov
        assert ov.get(10) == 0xAB
        assert ov.get(9) is None
        assert ov.get(9, 0x42) == 0x42

    def test_set_same_offset_twice_no_duplicate_keys(self):
        ov = hexdoc.SparseOverlay()
        ov.set(5, 0x01)
        ov.set(5, 0x02)
        assert len(ov) == 1
        assert ov.get(5) == 0x02
        assert ov.keys_in_range(0, 100) == [5]

    def test_set_none_removes(self):
        ov = hexdoc.SparseOverlay()
        ov.set(5, 0x01)
        ov.set(5, None)
        assert 5 not in ov
        assert ov.get(5) is None
        assert len(ov) == 0
        assert ov.keys_in_range(0, 100) == []

    def test_set_none_absent_is_noop(self):
        ov = hexdoc.SparseOverlay()
        ov.set(5, None)
        assert len(ov) == 0

    def test_keys_in_range_boundaries(self):
        ov = hexdoc.SparseOverlay()
        for off in (10, 20, 30, 40):
            ov.set(off, 0xFF)
        assert ov.keys_in_range(0, 100) == [10, 20, 30, 40]
        assert ov.keys_in_range(0, 0) == []
        assert ov.keys_in_range(11, 20) == []   # start exclusive of 10, end exclusive of 20
        assert ov.keys_in_range(10, 20) == [10]
        assert ov.keys_in_range(10, 21) == [10, 20]
        assert ov.keys_in_range(31, 39) == []  # gap between 30 and 40
        assert ov.keys_in_range(30, 31) == [30]
        assert ov.keys_in_range(41, 100) == []

    def test_keys_stay_sorted_after_mixed_ops(self):
        ov = hexdoc.SparseOverlay()
        for off in (50, 10, 30, 20, 40):
            ov.set(off, 0x01)
        for off in (30, 10):
            ov.set(off, None)
        ov.set(25, 0x02)
        ov.set(25, None)
        ov.set(5, 0x03)
        # the core invariant: _keys is always the sorted keys of _values
        assert ov._keys == sorted(ov._values)
        assert ov.keys_in_range(0, 100) == [5, 20, 40, 50]

    def test_clear(self):
        ov = hexdoc.SparseOverlay()
        for off in (1, 2, 3):
            ov.set(off, 0xFF)
        ov.clear()
        assert not ov and len(ov) == 0
        assert ov.keys_in_range(0, 10) == []

    def test_document_uses_overlay(self, doc):
        assert isinstance(doc._edits, hexdoc.SparseOverlay)
        doc.write(5, b"\xaa\xbb")
        doc.write(7, b"\xcc")
        assert doc._edits.get(5) == 0xAA
        assert doc._edits.get(6) == 0xBB
        assert doc._edits.get(7) == 0xCC
        assert doc.edit_count == len(doc._edits) == 3
        # undo pops the last write (byte 7) via the overlay
        doc.undo()
        assert doc._edits.get(7) is None
        assert doc._edits.get(6) == 0xBB
        assert doc.modified
