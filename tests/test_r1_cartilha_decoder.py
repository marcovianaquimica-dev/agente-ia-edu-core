import unittest

from tools.extract_cartilha_enem import UNDECODED_MARKER, decode_subset


class TestCartilhaDecoder(unittest.TestCase):
    def test_decodes_the_shifted_ascii_run(self):
        self.assertEqual(decode_subset("'HPRQVWUD"), "Demonstra")

    def test_decodes_a_run_containing_the_fi_ligature_glyph(self):
        self.assertEqual(decode_subset("LQVX¿FLHQWH"), "insuficiente")

    def test_leaves_already_correct_text_untouched(self):
        clean = "Demonstra bom domínio da modalidade escrita formal"
        self.assertEqual(decode_subset(clean), clean)

    def test_marks_a_glyph_it_cannot_decode(self):
        decoded = decode_subset("'HPRQVWUD§")
        self.assertIn(UNDECODED_MARKER, decoded)
