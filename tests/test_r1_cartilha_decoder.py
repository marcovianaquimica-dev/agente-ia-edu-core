import unittest

from tools.extract_cartilha_enem import (
    UNDECIDED_MARKER,
    UNDECODED_MARKER,
    decode_subset,
)


class TestCartilhaDecoder(unittest.TestCase):
    def test_decodes_the_shifted_ascii_run(self):
        self.assertEqual(decode_subset("'HPRQVWUD"), "Demonstra")

    def test_decodes_a_run_containing_the_fi_ligature_glyph(self):
        self.assertEqual(decode_subset("LQVX¿FLHQWH"), "insuficiente")

    def test_decodes_a_genuinely_encoded_run_containing_a_digit_and_spaces(self):
        # Regression case: an encoded digit is not evidence of correct text.
        # Encoded 0x30-0x39 ('0'-'9') decode to 'M'-'V', so any genuinely
        # encoded run whose plaintext contains an uppercase M-V will itself
        # contain literal ASCII digits.
        self.assertEqual(
            decode_subset("2 DYHVVR GR PHVPR OXJDU"),
            "O avesso do mesmo lugar",
        )

    def test_decodes_a_genuinely_encoded_run_with_preserved_spaces(self):
        # Regression case: some encoded runs in this PDF preserve literal
        # spaces between encoded words (the "TEXTO V" poem).
        self.assertEqual(decode_subset("%UDVLO PHX QHJR"), "Brasil meu nego")

    def test_leaves_already_correct_accented_text_untouched(self):
        clean = "Demonstra bom domínio da modalidade escrita formal"
        self.assertEqual(decode_subset(clean), clean)

    def test_leaves_already_correct_non_accented_text_untouched(self):
        # No accent to short-circuit on: this exercises the ratio computation
        # and the post-decode charset/lowercase guards directly.
        clean = "Texto de exemplo sem acentuacao nenhuma aqui"
        self.assertEqual(decode_subset(clean), clean)

    def test_leaves_a_single_word_untouched_via_the_in_range_ratio_alone(self):
        # Long enough to pass the length guard, no accent, no digit, no
        # space, single word - so the in-range ratio computation itself is
        # what must reject it (lowercase ASCII letters sit outside the
        # encoded range 0x21-0x5F, so the ratio comes out at 0%).
        self.assertEqual(decode_subset("acentuacao"), "acentuacao")

    def test_marks_a_glyph_it_cannot_decode(self):
        decoded = decode_subset("'HPRQVWUD§")
        self.assertIn(UNDECODED_MARKER, decoded)

    def test_leaves_a_number_with_trailing_space_untouched_via_length_guard(self):
        # Measured, not assumed: len("200 ") == 4 < 8, so _looks_encoded
        # rejects it outright on the length guard - same reason as "INEP"
        # below, not the in-range ratio and not a post-decode guard. No
        # decode is ever attempted, so no marker is expected either.
        self.assertEqual(decode_subset("200 "), "200 ")

    def test_leaves_a_short_run_untouched_via_length_guard(self):
        self.assertEqual(decode_subset("INEP"), "INEP")

    def test_leaves_another_short_run_untouched_via_length_guard(self):
        self.assertEqual(decode_subset("SIM"), "SIM")

    def test_leaves_a_parenthesized_year_untouched_via_length_guard(self):
        # len("(2025)") == 6 < 8: same length guard, verified rather than
        # assumed.
        self.assertEqual(decode_subset("(2025)"), "(2025)")

    def test_marks_an_all_caps_run_rejected_by_the_lowercase_guard(self):
        # "ENEM 2025" passes _looks_encoded and _decoded_looks_sane (it
        # decodes to the printable-but-wrong "bkbj OMOR"), then is rejected
        # by _decoded_mostly_lowercase (50% lowercase, below the 0.7
        # threshold). That used to come back silently unchanged; it must
        # now carry UNDECIDED_MARKER so a human knows the tool was unsure,
        # and the original text must still be recoverable from the result.
        result = decode_subset("ENEM 2025")
        self.assertTrue(result.startswith(UNDECIDED_MARKER))
        self.assertIn("ENEM 2025", result)

    def test_marks_a_correct_all_caps_string_rejected_by_the_charset_guard(self):
        # "CARTILHA DO PARTICIPANTE" is genuinely correct text, but
        # _looks_encoded accepts it (100% of its letters sit in the shifted
        # range) and the shift produces punctuation outside
        # _ACCEPTABLE_PUNCTUATION (e.g. 'A'+29 -> '^'), so it is rejected by
        # _decoded_looks_sane - a different guard than the lowercase one
        # above, but the same rejection path and the same fix: mark it,
        # don't silently return it bare.
        result = decode_subset("CARTILHA DO PARTICIPANTE")
        self.assertTrue(result.startswith(UNDECIDED_MARKER))
        self.assertIn("CARTILHA DO PARTICIPANTE", result)

    def test_marks_another_all_caps_run_rejected_by_the_charset_guard(self):
        result = decode_subset("VOL TAR PARA ")
        self.assertTrue(result.startswith(UNDECIDED_MARKER))
        self.assertIn("VOL TAR PARA ", result)


if __name__ == "__main__":
    unittest.main()
