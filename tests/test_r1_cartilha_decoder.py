import unittest

from tools.extract_cartilha_enem import UNDECODED_MARKER, decode_subset


class TestCartilhaDecoder(unittest.TestCase):
    def test_decodes_the_shifted_ascii_run(self):
        self.assertEqual(decode_subset("'HPRQVWUD"), "Demonstra")

    def test_decodes_a_run_containing_the_fi_ligature_glyph(self):
        self.assertEqual(decode_subset("LQVX¿FLHQWH"), "insuficiente")

    def test_leaves_already_correct_accented_text_untouched(self):
        clean = "Demonstra bom domínio da modalidade escrita formal"
        self.assertEqual(decode_subset(clean), clean)

    def test_leaves_already_correct_non_accented_text_untouched(self):
        # No accent to short-circuit on: this exercises the ratio computation
        # (and, now, the space/digit/length/charset guards) directly.
        clean = "Texto de exemplo sem acentuacao nenhuma aqui"
        self.assertEqual(decode_subset(clean), clean)

    def test_marks_a_glyph_it_cannot_decode(self):
        decoded = decode_subset("'HPRQVWUD§")
        self.assertIn(UNDECODED_MARKER, decoded)

    def test_leaves_a_number_with_trailing_space_untouched(self):
        self.assertEqual(decode_subset("200 "), "200 ")

    def test_leaves_a_word_with_a_digit_untouched(self):
        self.assertEqual(decode_subset("ENEM 2025"), "ENEM 2025")

    def test_leaves_uppercase_words_with_a_space_untouched(self):
        self.assertEqual(
            decode_subset("CARTILHA DO PARTICIPANTE"),
            "CARTILHA DO PARTICIPANTE",
        )

    def test_leaves_another_uppercase_run_with_a_space_untouched(self):
        self.assertEqual(decode_subset("VOL TAR PARA "), "VOL TAR PARA ")

    def test_leaves_a_short_run_untouched(self):
        self.assertEqual(decode_subset("INEP"), "INEP")

    def test_leaves_another_short_run_untouched(self):
        self.assertEqual(decode_subset("SIM"), "SIM")


if __name__ == "__main__":
    unittest.main()
