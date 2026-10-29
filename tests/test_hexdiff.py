"""Tests for the diff kernel: compute_diff_blocks and the streaming iter_diff.

Bytes past the end of the shorter input count as differing; blocks are
DIFF_BLOCK (8) - byte granular. The streaming ``iter_diff`` must accumulate
to exactly the same result as ``compute_diff_blocks`` over the full buffers.
"""

from __future__ import annotations

import pytest

from cheerleader.libs import hexdoc
from cheerleader.libs.hexdoc import HexDocument, compute_diff_blocks, iter_diff

DIFF_BLOCK = 8


class TestIdentical:
    def test_empty(self):
        assert compute_diff_blocks(b"", b"") == ([], 0)

    def test_identical(self):
        data = bytes(range(256)) * 4
        assert compute_diff_blocks(data, data) == ([], 0)

    def test_both_within_one_chunk(self):
        assert compute_diff_blocks(b"abc", b"abc") == ([], 0)


class TestSingleDiffs:
    def test_single_byte_first_block(self):
        left = b"\x00" * 16
        right = b"\x00" * 7 + b"\xff" + b"\x00" * 8
        blocks, count = compute_diff_blocks(left, right)
        assert blocks == [0]
        assert count == 1

    def test_single_byte_in_later_block(self):
        left = b"\x00" * 100
        right = bytearray(b"\x00" * 100)
        right[40] = 0xFF
        blocks, count = compute_diff_blocks(left, right)
        assert blocks == [5]  # 40 // 8
        assert count == 1

    def test_byte_count_accurate(self):
        left = bytes(16)
        right = b"\x01" * 4 + bytes(12)
        blocks, count = compute_diff_blocks(left, right)
        assert blocks == [0]
        assert count == 4


class TestWidespreadDiffs:
    def test_all_different(self):
        left = b"\x00" * 16
        right = b"\xff" * 16
        blocks, count = compute_diff_blocks(left, right)
        assert blocks == [0, 1]
        assert count == 16

    def test_blocks_are_block_granular(self):
        left = bytes(range(64))
        right = bytes(reversed(range(64)))
        blocks, count = compute_diff_blocks(left, right)
        assert blocks == sorted(blocks)
        assert blocks == list(range(8))
        assert count == 64

    def test_scattered_diffs(self):
        left = bytearray(b"\x00" * 64)
        right = bytearray(b"\x00" * 64)
        for off in (3, 9, 33, 63):
            right[off] = 0xAB
        blocks, count = compute_diff_blocks(bytes(left), bytes(right))
        assert blocks == [0, 1, 4, 7]
        assert count == 4


class TestUnequalLengths:
    def test_right_longer_same_prefix(self):
        # bytes past the end of the shorter input count as differing
        left = b"abcde"
        right = b"abcdefghij" + b"\x00" * 10
        blocks, count = compute_diff_blocks(left, right)
        assert blocks == [0, 1, 2]  # blocks 0..(20//8 + 1) cover the tail
        assert count == 15

    def test_left_longer_same_prefix(self):
        right = b"abcde"
        left = b"abcdefghij" + b"\x00" * 10
        blocks, count = compute_diff_blocks(left, right)
        assert blocks == [0, 1, 2]
        assert count == 15

    def test_tail_block_dedup(self):
        # differing byte inside the last *common* block must not be listed
        # twice once the length tail starts at the same block index
        left = b"\x00" * 8 + b"\x00\x00\x00\x00\x00\x00\xff\x00" + b"\x00"  # 17
        right = b"\x00" * 8 + b"\x00\x00\x00\x00\x00\x00\x00\x00"  # 16
        blocks, count = compute_diff_blocks(left, right)
        # byte 14 differs (block 1); tail byte 16 also differs (block 2)
        assert blocks == [1, 2]
        assert count == 2

    def test_tail_block_dedup_merged(self):
        # the differing common byte sits in the block where the tail begins:
        # the dedup (`first += 1`) must avoid double-listing it
        left = b"\x00" * 8 + b"\xff" + b"\x00" * 6  # 15 bytes, byte 8 differs
        right = b"\x00" * 9  # 9 bytes
        blocks, count = compute_diff_blocks(left, right)
        # common = 9; block 1 (bytes 8..14 in left) differs, tail covers
        # bytes 9..14 → same block 1 must appear exactly once
        assert blocks == [1]
        assert count == 7  # byte 8 + 6 tail bytes


class TestChunkBoundary:
    def test_diff_across_cmp_chunk(self):
        # > _CMP_CHUNK (4096) with one differing byte past the chunk edge
        size = 4096 * 2 + 100
        left = b"\x00" * size
        right = bytearray(b"\x00" * size)
        right[4097] = 0xFF
        blocks, count = compute_diff_blocks(left, bytes(right))
        assert blocks == [512]  # 4097 // 8
        assert count == 1

    def test_identical_across_many_chunks(self):
        data = bytes(range(256)) * 32  # 8192 bytes
        assert compute_diff_blocks(data, data) == ([], 0)


class TestIterDiff:
    """The streaming two-document diff must accumulate to exactly the same
    result as compute_diff_blocks over the full buffers."""

    @staticmethod
    def _docs(tmp_path, left: bytes, right: bytes) -> tuple[HexDocument, HexDocument]:
        lp = tmp_path / "left.bin"
        lp.write_bytes(left)
        rp = tmp_path / "right.bin"
        rp.write_bytes(right)
        return HexDocument(str(lp)), HexDocument(str(rp))

    @staticmethod
    def _collect(left: HexDocument, right: HexDocument, **kwargs):
        blocks: list[int] = []
        total = 0
        last_frac = -1.0
        fracs: list[float] = []
        for new_blocks, n, frac in iter_diff(left, right, **kwargs):
            blocks.extend(new_blocks)
            total += n
            last_frac = frac
            fracs.append(frac)
        return blocks, total, last_frac, fracs

    def test_identical_files(self, tmp_path):
        data = bytes(range(256)) * 3
        l, r = self._docs(tmp_path, data, data)
        blocks, total, last_frac, _ = self._collect(l, r)
        assert blocks == []
        assert total == 0
        assert last_frac == 1.0
        l.close()
        r.close()

    def test_both_empty(self, tmp_path):
        l, r = self._docs(tmp_path, b"", b"")
        blocks, total, _, fracs = self._collect(l, r)
        assert blocks == [] and total == 0 and fracs == []
        l.close()
        r.close()

    def test_one_empty(self, tmp_path):
        l, r = self._docs(tmp_path, b"", b"abcdefghij")
        blocks, total, _, _ = self._collect(l, r)
        exp_blocks, exp_total = compute_diff_blocks(b"", b"abcdefghij")
        assert (blocks, total) == (exp_blocks, exp_total) == ([0, 1], 10)
        l.close()
        r.close()

    def test_progress_advances(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hexdoc, "DIFF_SCAN_CHUNK", 16)
        l, r = self._docs(tmp_path, b"\x00" * 100, b"\x00" * 100)
        _, _, _, fracs = self._collect(l, r)
        assert fracs == sorted(fracs)
        assert fracs[-1] == pytest.approx(1.0)
        l.close()
        r.close()

    def test_cancelled_yields_nothing(self, tmp_path):
        l, r = self._docs(tmp_path, b"x" * 100, b"y" * 100)
        blocks, total, _, _ = self._collect(l, r, cancelled=lambda: True)
        assert blocks == [] and total == 0
        l.close()
        r.close()

    def test_matches_full_buffer_diff(self, tmp_path, monkeypatch):
        # golden equivalence across boundary-straddling size combinations,
        # with multi-page reads (PAGE_SIZE shrunk) and a small scan chunk
        monkeypatch.setattr(hexdoc, "PAGE_SIZE", 32)
        scan = 16
        base = bytes(range(30))
        cases = [
            (b"", b""),
            (b"", b"abcdefghij"),
            (b"abcdefghij", b""),
            (base, base),  # identical
            (base, base[:20] + bytes(range(100, 117))),  # same prefix, longer right
            (base[:15] + bytes([0xFF]) + base[16:20], base[:20]),  # diff in last common block
            (bytes(8) + b"\xff" + bytes(6), bytes(9)),  # tail-block dedup (15 vs 9 bytes)
            (bytes(range(50)), bytes(range(50, 100))),  # all different, multi-chunk
            (bytes(range(50)), bytes(range(50))[:48] + b"\xff\xff"),  # sparse multi-chunk diffs
            (bytes(range(16)), bytes(range(16)) + b"extra"),  # exact-block boundary tail
        ]
        for i, (ld, rd) in enumerate(cases):
            l, r = self._docs(tmp_path, ld, rd, )
            blocks, total, _, _ = self._collect(l, r, scan_chunk=scan)
            exp_blocks, exp_total = compute_diff_blocks(ld, rd)
            assert blocks == exp_blocks, f"case {i}: blocks mismatch"
            assert total == exp_total, f"case {i}: byte count mismatch"
            l.close()
            r.close()

    def test_sparse_diffs_across_scan_chunks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hexdoc, "DIFF_SCAN_CHUNK", 16)
        size = 100
        left = bytes(size)
        right = bytearray(bytes(size))
        for off in (5, 40, 97):
            right[off] = 0xAB
        l, r = self._docs(tmp_path, left, bytes(right))
        blocks, total, _, _ = self._collect(l, r)
        assert blocks == [0, 5, 12]  # 5//8, 40//8, 97//8
        assert total == 3
        l.close()
        r.close()

