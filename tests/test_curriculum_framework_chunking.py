"""CEREBRO - Fase 4: chunking do documento normativo.

A BNCC nao e prosa. A diferenca em relacao ao ``ProseChunker`` nao e de
parametro, e de TIPO: a unidade natural e UMA HABILIDADE, e janelar
habilidades por tamanho cortaria habilidade ao meio e perderia o codigo, que
e a unica chave util.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.bncc_contract.v1 import BNCC_VERSION_EM_2018
from agente_ia_edu.knowledge_chunking_policy.v1 import (
    INDIVISIBLE_CHUNK_TYPES,
    retrieval_text_hash,
)
from agente_ia_edu.services.knowledge_engine.bncc_extraction import extract_bncc_cnt
from agente_ia_edu.services.knowledge_engine.chunking import CurriculumFrameworkChunker

_PAGES = [
    "5.3.1. CIENCIAS DA NATUREZA\nCOMPETENCIA ESPECIFICA 1\n"
    "Analisar fenomenos naturais e processos tecnologicos. Comentario.",
    "HABILIDADES\n(EM13CNT101) Analisar transformacoes e con- servacoes.\n"
    "(EM13CNT102) Realizar previsoes e avaliar intervencoes.",
    "COMPETENCIA ESPECIFICA 2\nConstruir interpretacoes sobre a Vida. Comentario.",
    "HABILIDADES\n(EM13CNT201) Analisar modelos cientificos.",
]


class FrameworkChunkingTests(unittest.TestCase):
    def setUp(self):
        self.framework = extract_bncc_cnt(
            _PAGES, taxonomy_version=BNCC_VERSION_EM_2018
        )
        self.chunks = CurriculumFrameworkChunker().chunk(framework=self.framework)

    def test_one_chunk_per_skill(self):
        self.assertEqual(len(self.chunks), 3)
        self.assertEqual(len(self.chunks), len(self.framework.skills))

    def test_every_chunk_is_a_curriculum_item(self):
        for chunk in self.chunks:
            self.assertEqual(chunk.chunk_type, "CURRICULUM_ITEM")

    def test_curriculum_item_is_indivisible_by_policy(self):
        self.assertIn("CURRICULUM_ITEM", INDIVISIBLE_CHUNK_TYPES)

    def test_every_chunk_has_a_page(self):
        for chunk in self.chunks:
            self.assertIsNotNone(chunk.page_start)
            self.assertEqual(chunk.page_start, chunk.page_end)

    def test_the_page_offset_is_applied(self):
        shifted = CurriculumFrameworkChunker().chunk(
            framework=self.framework, page_offset=112
        )
        self.assertEqual(shifted[0].page_start, self.chunks[0].page_start + 112)

    def test_the_heading_path_is_area_then_competency(self):
        first = self.chunks[0]
        self.assertEqual(first.heading_path[0], "Ciências da Natureza e suas Tecnologias")
        self.assertEqual(first.heading_path[1], "Competência específica 1")

    def test_skills_of_the_second_competency_carry_its_heading(self):
        last = self.chunks[-1]
        self.assertEqual(last.heading_path[1], "Competência específica 2")

    def test_bncc_node_codes_holds_only_the_code(self):
        """Sua funcao e recuperacao; o codigo e estavel entre versoes."""
        self.assertEqual(self.chunks[0].metadata["bncc_node_codes"], ["EM13CNT101"])

    def test_the_metadata_holds_the_full_normative_triple(self):
        """Ajuste 4: o codigo isolado nao e identidade eterna."""
        bncc = self.chunks[0].metadata["bncc"]
        self.assertEqual(bncc["taxonomy_code"], "bncc")
        self.assertEqual(bncc["taxonomy_version"], "EM-2018")
        self.assertEqual(bncc["node_code"], "EM13CNT101")
        self.assertEqual(bncc["urn"], "bncc:EM-2018:EM13CNT101")
        self.assertEqual(bncc["competency_code"], "CNT-CE1")
        self.assertEqual(bncc["area_code"], "EM13CNT")
        self.assertEqual(bncc["page"], 2)

    def test_the_raw_text_is_the_statement_without_the_code(self):
        """O codigo vive em metadata; o texto e o enunciado normativo."""
        chunk = self.chunks[0]
        self.assertNotIn("EM13CNT101", chunk.raw_text)
        self.assertIn("conservacoes", chunk.raw_text)

    def test_the_raw_text_never_contains_the_heading_path(self):
        for chunk in self.chunks:
            for heading in chunk.heading_path:
                self.assertNotIn(heading, chunk.raw_text)

    def test_the_hash_is_over_the_retrieval_text(self):
        chunk = self.chunks[0]
        self.assertEqual(
            chunk.text_hash,
            retrieval_text_hash(
                raw_text=chunk.raw_text, heading_path=chunk.heading_path
            ),
        )

    def test_the_dehyphenation_count_travels_with_the_chunk(self):
        self.assertEqual(self.chunks[0].metadata["dehyphenations"], 1)

    def test_no_chunk_carries_overlap(self):
        for chunk in self.chunks:
            self.assertFalse(chunk.metadata.get("overlap_prefix_chars"))

    def test_boundaries_are_never_approximate(self):
        """A estrutura vem do codigo, nao de localizar titulo em texto cru."""
        for chunk in self.chunks:
            self.assertFalse(chunk.metadata["boundary_approximate"])

    def test_ordinals_are_dense_and_start_at_one(self):
        self.assertEqual(
            [c.ordinal for c in self.chunks], list(range(1, len(self.chunks) + 1))
        )

    def test_two_runs_give_the_same_hashes(self):
        again = CurriculumFrameworkChunker().chunk(framework=self.framework)
        self.assertEqual(
            [c.text_hash for c in self.chunks], [c.text_hash for c in again]
        )

    def test_the_version_changes_nothing_in_the_text_but_shows_in_metadata(self):
        """Mesma habilidade em outra versao: texto igual, referencia
        diferente."""
        other = extract_bncc_cnt(_PAGES, taxonomy_version="EM-2026")
        chunks = CurriculumFrameworkChunker().chunk(framework=other)
        self.assertEqual(chunks[0].raw_text, self.chunks[0].raw_text)
        self.assertEqual(chunks[0].metadata["bncc"]["urn"], "bncc:EM-2026:EM13CNT101")


if __name__ == "__main__":
    unittest.main()
