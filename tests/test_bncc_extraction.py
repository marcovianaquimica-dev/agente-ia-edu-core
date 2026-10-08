"""CEREBRO - Fase 4: extracao deterministica da BNCC / CNT.

Verificado na Fase 0 contra o arquivo real
(``BNCC_EnsinoMedio_embaixa_site_110518.pdf``, 154 paginas): 3/3 competencias
especificas (p.116, 118, 120) e 23/23 habilidades (p.117, 119, 121), com a
hierarquia 6/7/10 derivavel do PROPRIO CODIGO e confirmada
independentemente pela paginacao.

Duas fontes concordando e o que permite um teste que falha se a extracao
degradar. Um teste que so conferisse "achou 23" passaria com 23 habilidades
erradas.

PURO: sem banco, sem I/O de arquivo - recebe ``page_texts``.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.bncc_contract.v1 import BNCC_TAXONOMY_CODE, BNCC_VERSION_EM_2018
from agente_ia_edu.services.knowledge_engine.bncc_extraction import (
    BnccExtractionError,
    BnccFramework,
    EXTRACTOR_VERSION,
    dehyphenate,
    extract_bncc_cnt,
)


def _pages() -> list[str]:
    """Estrutura do arquivo real, em miniatura.

    Inclui, de proposito, competencias de OUTRA area (p.3) para provar que a
    janela da CNT as exclui: o documento real tem 27 habilidades de LGG, 45
    de MAT e 31 de CHS, e um extrator que as misturasse pareceria funcionar.
    """
    return [
        "BASE NACIONAL COMUM CURRICULAR\nSumario",
        "5.2. A AREA DE LINGUAGENS\nCOMPETENCIA ESPECIFICA 1\n"
        "Compreender o funcionamento das linguagens.",
        "HABILIDADES\n(EM13LGG101) Compreender o processo de producao.\n"
        "(EM13LGG102) Analisar visoes de mundo.",
        "5.3. A AREA DE CIENCIAS DA NATUREZA E SUAS TECNOLOGIAS\n"
        "Apresentacao da area.",
        "5.3.1. CIENCIAS DA NATUREZA E SUAS TECNOLOGIAS NO ENSINO MEDIO: "
        "COMPETENCIAS ESPECIFICAS E HABILIDADES\n"
        "COMPETENCIA ESPECIFICA 1\n"
        "Analisar fenomenos naturais e pro- cessos tecnologicos. "
        "Nesta competencia especifica, os fenomenos sao analisados.",
        "HABILIDADES\n"
        "(EM13CNT101) Analisar e representar as transformacoes e con- servacoes.\n"
        "(EM13CNT102) Realizar previsoes e avaliar intervencoes.",
        "COMPETENCIA ESPECIFICA 2\n"
        "Construir e utilizar interpretacoes sobre a dinamica da Vida. "
        "Ao reconhecerem que os processos permeiam a natureza.",
        "HABILIDADES\n"
        "(EM13CNT201) Analisar e utilizar modelos cientificos.\n"
        "(EM13CNT202) Interpretar formas de manifestacao da vida.\n"
        "(EM13CNT203) Avaliar e prever efeitos de intervencoes.",
        "COMPETENCIA ESPECIFICA 3\n"
        "Analisar situacoes-problema e avaliar aplicacoes do conhecimento. "
        "Em um mundo repleto de informacoes.",
        "HABILIDADES\n"
        "(EM13CNT301) Construir questoes, elaborar hipoteses e estimativas.",
        "5.4. A AREA DE CIENCIAS HUMANAS\nCOMPETENCIA ESPECIFICA 1\n"
        "Analisar processos politicos.",
        "HABILIDADES\n(EM13CHS101) Identificar processos.",
    ]


class DehyphenationTests(unittest.TestCase):
    """spec 22.5 - exige whitespace apos o hifen."""

    def test_a_line_break_hyphen_is_joined(self):
        for broken, whole in (
            ("pro- cessos", "processos"),
            ("desen- volvimento", "desenvolvimento"),
            ("con- servacoes", "conservacoes"),
            ("matu- ridade", "maturidade"),
        ):
            joined, count = dehyphenate(broken)
            self.assertEqual(joined, whole)
            self.assertEqual(count, 1)

    def test_a_legitimate_compound_hyphen_is_preserved(self):
        """Composto real aparece SEM espaco. Juntar seria estragar."""
        for intact in ("socio-economico", "situacoes-problema", "pos-graduacao"):
            joined, count = dehyphenate(intact)
            self.assertEqual(joined, intact)
            self.assertEqual(count, 0)

    def test_a_newline_hyphen_is_also_joined(self):
        joined, count = dehyphenate("desen-\nvolvimento")
        self.assertEqual(joined, "desenvolvimento")
        self.assertEqual(count, 1)

    def test_the_count_is_reported_for_audit(self):
        joined, count = dehyphenate("pro- cessos e desen- volvimento")
        self.assertEqual(joined, "processos e desenvolvimento")
        self.assertEqual(count, 2)

    def test_text_without_hyphens_is_untouched(self):
        self.assertEqual(dehyphenate("texto simples"), ("texto simples", 0))


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.framework = extract_bncc_cnt(
            _pages(), taxonomy_version=BNCC_VERSION_EM_2018
        )

    def test_it_returns_a_frozen_framework(self):
        self.assertIsInstance(self.framework, BnccFramework)
        with self.assertRaises(Exception):
            self.framework.area_name = "outra"  # type: ignore[misc]

    def test_the_taxonomy_identity_travels_with_the_framework(self):
        self.assertEqual(self.framework.taxonomy_code, BNCC_TAXONOMY_CODE)
        self.assertEqual(self.framework.taxonomy_version, BNCC_VERSION_EM_2018)
        self.assertEqual(self.framework.extractor_version, EXTRACTOR_VERSION)

    def test_the_area_name_is_canonical_not_read_from_the_pdf(self):
        """O cabecalho corrido da area repete em TODA pagina da secao no
        arquivo real, entao procura-lo nao daria pagina significativa - e
        herdar a tipografia do PDF faria o no da taxonomia variar com o
        arquivo. O nome e canonico e a pagina e a da 1a competencia, que e
        onde a estrutura normativa comeca."""
        self.assertEqual(
            self.framework.area_name, "Ciências da Natureza e suas Tecnologias"
        )
        self.assertEqual(self.framework.area_code, "EM13CNT")
        self.assertEqual(self.framework.area_page, self.framework.competencies[0].page)
        self.assertEqual(self.framework.area_page, 5)

    def test_three_competencies_are_found_each_with_its_page(self):
        self.assertEqual([c.number for c in self.framework.competencies], [1, 2, 3])
        self.assertEqual([c.page for c in self.framework.competencies], [5, 7, 9])

    def test_each_competency_has_a_stable_code(self):
        self.assertEqual(
            [c.code for c in self.framework.competencies],
            ["CNT-CE1", "CNT-CE2", "CNT-CE3"],
        )

    def test_the_competency_statement_is_the_first_sentence_only(self):
        """O comentario que segue o enunciado ("Nesta competencia
        especifica...") nao faz parte dele."""
        first = self.framework.competencies[0]
        self.assertIn("Analisar fenomenos naturais", first.statement)
        self.assertNotIn("Nesta competencia", first.statement)

    def test_competency_statements_are_dehyphenated(self):
        self.assertIn("processos tecnologicos", self.framework.competencies[0].statement)
        self.assertGreater(self.framework.competencies[0].dehyphenations, 0)

    def test_every_skill_is_found_with_code_statement_and_page(self):
        skills = self.framework.skills
        self.assertEqual(
            [s.code for s in skills],
            ["EM13CNT101", "EM13CNT102", "EM13CNT201", "EM13CNT202",
             "EM13CNT203", "EM13CNT301"],
        )
        self.assertEqual([s.page for s in skills], [6, 6, 8, 8, 8, 10])
        for skill in skills:
            self.assertTrue(skill.statement.strip())
            self.assertEqual(len(skill.source_text_sha256), 64)

    def test_skill_statements_are_dehyphenated(self):
        skill = next(s for s in self.framework.skills if s.code == "EM13CNT101")
        self.assertIn("conservacoes", skill.statement)
        self.assertEqual(skill.dehyphenations, 1)

    def test_the_hierarchy_comes_from_the_code_itself(self):
        """O 1o digito depois de EM13CNT e o numero da competencia."""
        for competency in self.framework.competencies:
            for skill in competency.skills:
                self.assertTrue(skill.code.startswith(f"EM13CNT{competency.number}"))
                self.assertEqual(skill.competency_number, competency.number)

    def test_the_counts_per_competency_match_the_codes(self):
        self.assertEqual(
            [len(c.skills) for c in self.framework.competencies], [2, 3, 1]
        )

    def test_other_areas_are_excluded_by_the_cnt_window(self):
        """LGG e CHS tem "COMPETENCIA ESPECIFICA 1" tambem. Misturar faria o
        extrator parecer funcionar e produzir norma errada."""
        codes = {s.code for s in self.framework.skills}
        self.assertTrue(all(code.startswith("EM13CNT") for code in codes))
        self.assertEqual(len(self.framework.competencies), 3)


class DeterminismTests(unittest.TestCase):
    def test_two_runs_give_identical_hashes(self):
        first = extract_bncc_cnt(_pages(), taxonomy_version=BNCC_VERSION_EM_2018)
        second = extract_bncc_cnt(_pages(), taxonomy_version=BNCC_VERSION_EM_2018)
        self.assertEqual(
            [s.source_text_sha256 for s in first.skills],
            [s.source_text_sha256 for s in second.skills],
        )

    def test_the_version_is_part_of_the_framework_not_a_default(self):
        with self.assertRaises(ValueError):
            extract_bncc_cnt(_pages(), taxonomy_version="")


class FailureTests(unittest.TestCase):
    def test_a_document_without_cnt_codes_is_refused(self):
        with self.assertRaises(BnccExtractionError) as caught:
            extract_bncc_cnt(
                ["texto qualquer", "sem habilidade alguma"],
                taxonomy_version=BNCC_VERSION_EM_2018,
            )
        self.assertEqual(caught.exception.code, "NO_CNT_SKILLS_FOUND")

    def test_a_skill_without_a_matching_competency_heading_is_refused(self):
        """Falha ALTO: uma habilidade orfa significa que a extracao perdeu a
        estrutura, e inventar o pai produziria norma errada."""
        pages = [
            "5.3.1. CIENCIAS DA NATUREZA\nHABILIDADES\n"
            "(EM13CNT901) Habilidade de uma competencia que nao existe."
        ]
        with self.assertRaises(BnccExtractionError) as caught:
            extract_bncc_cnt(pages, taxonomy_version=BNCC_VERSION_EM_2018)
        self.assertEqual(caught.exception.code, "ORPHAN_SKILL")

    def test_an_empty_document_is_refused(self):
        with self.assertRaises(BnccExtractionError):
            extract_bncc_cnt([], taxonomy_version=BNCC_VERSION_EM_2018)


if __name__ == "__main__":
    unittest.main()
