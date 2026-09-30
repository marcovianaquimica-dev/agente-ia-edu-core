# tests/test_mass_correction_chunking.py
import unittest

from scripts.run_mass_correction import _chunk_pending_lines


class ChunkPendingLinesTests(unittest.TestCase):
    def test_empty_input_returns_no_chunks(self):
        self.assertEqual(_chunk_pending_lines([]), [])

    def test_fits_in_one_chunk_when_under_both_limits(self):
        lines = [{"custom_id": f"id-{i}"} for i in range(5)]
        chunks = _chunk_pending_lines(lines, max_requests=10, max_bytes=10_000)
        self.assertEqual(chunks, [lines])

    def test_splits_by_request_count(self):
        lines = [{"custom_id": f"id-{i}"} for i in range(10)]
        chunks = _chunk_pending_lines(lines, max_requests=3, max_bytes=10_000)
        self.assertEqual([len(c) for c in chunks], [3, 3, 3, 1])
        self.assertEqual([item for chunk in chunks for item in chunk], lines)

    def test_splits_by_byte_size(self):
        # cada linha serializada tem uns 30 bytes - com max_bytes=100, cabem
        # no maximo 3 por fracao antes de estourar
        lines = [{"custom_id": f"id-{i}", "padding": "x" * 10} for i in range(10)]
        chunks = _chunk_pending_lines(lines, max_requests=1000, max_bytes=100)
        self.assertGreater(len(chunks), 1)
        self.assertEqual([item for chunk in chunks for item in chunk], lines)
        import json
        for chunk in chunks[:-1]:  # a ultima fracao pode ficar abaixo do teto
            total = sum(len(json.dumps(line, ensure_ascii=False).encode("utf-8")) + 1 for line in chunk)
            self.assertLessEqual(total, 100)

    def test_a_single_oversized_line_gets_its_own_chunk_instead_of_being_dropped(self):
        huge_line = {"custom_id": "huge", "padding": "x" * 1000}
        small_line = {"custom_id": "small"}
        chunks = _chunk_pending_lines([huge_line, small_line], max_requests=1000, max_bytes=100)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], [huge_line])
        self.assertEqual(chunks[1], [small_line])


if __name__ == "__main__":
    unittest.main()
