"""Tests for compute_diff_blocks (hex-compare diff kernel).

Bytes past the end of the shorter input count as differing; blocks are
DIFF_BLOCK (8) - byte granular.
"""

from __future__ import annotations

from cheerleader.tui.widgets import compute_diff_blocks

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
