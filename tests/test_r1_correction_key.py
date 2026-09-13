import unittest

from agente_ia_edu.services.essay_correction_key import (
    correction_key,
    essay_text_hash,
    normalize_essay_text,
)

BASE = dict(
    normalized_text_hash="a" * 64,
    essay_prompt_id="11111111-1111-1111-1111-111111111111",
    rubric_version="ENEM_2025",
    model_version="fake-model-1",
    prompt_version="v1",
    engine_version="r1.0.0",
)


class TestCorrectionKey(unittest.TestCase):
    def test_is_stable_for_the_same_input(self):
        self.assertEqual(correction_key(**BASE), correction_key(**BASE))

    def test_every_version_field_changes_the_key(self):
        baseline = correction_key(**BASE)
        for field in (
            "rubric_version", "model_version", "prompt_version", "engine_version",
            "essay_prompt_id", "normalized_text_hash",
        ):
            with self.subTest(field=field):
                changed = dict(BASE)
                changed[field] = changed[field] + "-x"
                self.assertNotEqual(correction_key(**changed), baseline)

    def test_normalisation_collapses_line_endings_but_not_words(self):
        self.assertEqual(
            normalize_essay_text("Primeira linha\r\nSegunda linha"),
            "Primeira linha\nSegunda linha",
        )

    def test_normalisation_is_unicode_stable(self):
        import unicodedata

        composed = unicodedata.normalize("NFC", "valorização")
        decomposed = unicodedata.normalize("NFD", "valorização")
        self.assertNotEqual(composed, decomposed)  # different bytes on the wire
        self.assertEqual(essay_text_hash(composed), essay_text_hash(decomposed))

    def test_normalisation_is_idempotent(self):
        cases = {
            "mixed line endings": "Linha um\r\nLinha dois\rLinha três\nLinha quatro",
            "trailing whitespace per line": "Linha um   \nLinha dois\t\nLinha três",
            "leading and trailing blank lines": "\n\n  Texto central  \n\n",
            "combining accent": "é acento combinante",
            "internal non-breaking space": "Espaço não quebra aqui",
            "empty string": "",
        }
        for label, raw in cases.items():
            with self.subTest(case=label):
                once = normalize_essay_text(raw)
                self.assertEqual(normalize_essay_text(once), once)

    def test_internal_double_spaces_survive_normalisation(self):
        text = "Este texto  tem espaço duplo interno."
        self.assertEqual(normalize_essay_text(text), text)

    def test_internal_triple_spaces_survive_normalisation(self):
        text = "Este texto   tem espaço triplo interno."
        self.assertEqual(normalize_essay_text(text), text)

    def test_internal_tab_survives_normalisation(self):
        text = "Coluna1\tColuna2\tColuna3"
        self.assertEqual(normalize_essay_text(text), text)

    def test_punctuation_is_untouched(self):
        text = 'Olá, mundo! Você está bem? Sim; "ótimo" — 100% (dez) [dois]... etc.'
        self.assertEqual(normalize_essay_text(text), text)
