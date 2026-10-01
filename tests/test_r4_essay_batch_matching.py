"""R4 lote - normalizacao de nome/CPF, leitura do cabecalho e match contra a turma.

Funcoes puras, sem banco e sem IA: sao a parte do sistema que decide se uma
folha vira redacao de alguem automaticamente ou cai na fila do professor, e por
isso e a parte que mais precisa ser deterministica e testada a fundo.
"""

import unittest
import uuid

from agente_ia_edu.services.essay_batch import (
    match_student,
    normalize_cpf,
    normalize_person_name,
    parse_header_text,
    text_from_ocr_tokens,
)


class NormalizePersonNameTests(unittest.TestCase):
    def test_uppercases(self):
        self.assertEqual(normalize_person_name("joao da silva"), "JOAO DA SILVA")

    def test_strips_accents(self):
        self.assertEqual(
            normalize_person_name("Antônio José Gonçalves"), "ANTONIO JOSE GONCALVES"
        )

    def test_collapses_whitespace(self):
        self.assertEqual(
            normalize_person_name("  Maria   Clara \t Souza \n"), "MARIA CLARA SOUZA"
        )

    def test_none_and_empty_become_empty_string(self):
        self.assertEqual(normalize_person_name(None), "")
        self.assertEqual(normalize_person_name("   "), "")

    def test_is_idempotent(self):
        once = normalize_person_name("Ana Lúcia  Ferreira")
        self.assertEqual(normalize_person_name(once), once)


class NormalizeCpfTests(unittest.TestCase):
    def test_keeps_digits_only(self):
        self.assertEqual(normalize_cpf("123.456.789-00"), "12345678900")

    def test_handles_spaces_and_none(self):
        self.assertEqual(normalize_cpf(" 111 222 333 44 "), "11122233344")
        self.assertEqual(normalize_cpf(None), "")


class ParseHeaderTextTests(unittest.TestCase):
    def test_reads_name_from_the_line_below_the_label(self):
        header = (
            "FOLHA DE REDACAO\n"
            "Os desafios da mobilidade urbana\n"
            "NOME COMPLETO DO PARTICIPANTE\n"
            "Joao da Silva Pereira\n"
            "CPF\n"
            "123.456.789-00\n"
        )
        name, cpf = parse_header_text(header)
        self.assertEqual(name, "JOAO DA SILVA PEREIRA")
        self.assertEqual(cpf, "12345678900")

    def test_reads_name_from_the_same_line_as_the_label(self):
        name, cpf = parse_header_text("NOME: Maria Clara Souza\nCPF: 98765432100")
        self.assertEqual(name, "MARIA CLARA SOUZA")
        self.assertEqual(cpf, "98765432100")

    def test_a_single_line_header_does_not_swallow_the_cpf_into_the_name(self):
        # O OCR de uma regiao pequena frequentemente devolve TUDO numa linha so -
        # sem cortar no rotulo CPF, o nome viraria "JOAO DA SILVA CPF 123..." e
        # nunca casaria com aluno nenhum.
        name, cpf = parse_header_text(
            "NOME COMPLETO DO PARTICIPANTE Joao da Silva CPF 123.456.789-00"
        )
        self.assertEqual(name, "JOAO DA SILVA")
        self.assertEqual(cpf, "12345678900")

    def test_a_single_line_header_without_the_cpf_label_still_cuts_at_the_digits(self):
        name, cpf = parse_header_text("NOME COMPLETO Ana Lucia Ferreira 111.222.333-44")
        self.assertEqual(name, "ANA LUCIA FERREIRA")
        self.assertEqual(cpf, "11122233344")

    def test_never_picks_the_prompt_title_as_the_name(self):
        header = (
            "FOLHA DE REDACAO\n"
            "Os desafios da mobilidade urbana no Brasil\n"
            "NOME COMPLETO DO PARTICIPANTE\n"
            "Pedro Alves\n"
        )
        name, _ = parse_header_text(header)
        self.assertEqual(name, "PEDRO ALVES")

    def test_falls_back_to_the_longest_wordy_line_when_the_label_was_misread(self):
        # OCR comeu a palavra NOME; ainda assim ha uma unica linha que parece
        # um nome de pessoa (duas ou mais palavras, sem digito).
        name, cpf = parse_header_text("N0ME C0MPLET0\nRoberto Carlos Nascimento\nCPF 11122233344")
        self.assertEqual(name, "ROBERTO CARLOS NASCIMENTO")
        self.assertEqual(cpf, "11122233344")

    def test_cpf_is_optional(self):
        name, cpf = parse_header_text("NOME COMPLETO DO PARTICIPANTE\nCarla Dias")
        self.assertEqual(name, "CARLA DIAS")
        self.assertIsNone(cpf)

    def test_empty_header_returns_two_nones(self):
        self.assertEqual(parse_header_text(""), (None, None))
        self.assertEqual(parse_header_text(None), (None, None))

    def test_header_with_no_name_like_line_returns_none_name(self):
        name, cpf = parse_header_text("CPF\n12345678900")
        self.assertIsNone(name)
        self.assertEqual(cpf, "12345678900")


class MatchStudentTests(unittest.TestCase):
    def setUp(self):
        self.ana = uuid.uuid4()
        self.joao = uuid.uuid4()
        self.joao2 = uuid.uuid4()
        self.roster = [
            (self.ana, "Ana Lúcia Ferreira"),
            (self.joao, "João da Silva"),
        ]

    def test_exactly_one_match_wins(self):
        self.assertEqual(match_student("ANA LUCIA FERREIRA", self.roster), self.ana)

    def test_match_is_accent_and_case_insensitive(self):
        self.assertEqual(match_student("joão da silva", self.roster), self.joao)

    def test_no_match_returns_none(self):
        self.assertIsNone(match_student("Carlos Mendes", self.roster))

    def test_homonyms_return_none(self):
        roster = self.roster + [(self.joao2, "Joao Da Silva")]
        self.assertIsNone(match_student("João da Silva", roster))

    def test_empty_name_returns_none(self):
        self.assertIsNone(match_student(None, self.roster))
        self.assertIsNone(match_student("   ", self.roster))

    def test_empty_roster_returns_none(self):
        self.assertIsNone(match_student("Ana Lúcia Ferreira", []))


class TextFromOcrTokensTests(unittest.TestCase):
    def test_rebuilds_text_from_offsets(self):
        tokens = [
            {"text": "Ola", "confidence": 0.9, "start": 0, "end": 3},
            {"text": "mundo", "confidence": 0.9, "start": 4, "end": 9},
        ]
        self.assertEqual(text_from_ocr_tokens(tokens), "Ola mundo")

    def test_uncovered_gap_defaults_to_a_space(self):
        tokens = [
            {"text": "a", "confidence": 1.0, "start": 0, "end": 1},
            {"text": "b", "confidence": 1.0, "start": 2, "end": 3},
        ]
        self.assertEqual(text_from_ocr_tokens(tokens), "a b")

    def test_empty_and_none_become_empty_string(self):
        self.assertEqual(text_from_ocr_tokens([]), "")
        self.assertEqual(text_from_ocr_tokens(None), "")


if __name__ == "__main__":
    unittest.main()
