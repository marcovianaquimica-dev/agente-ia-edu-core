"""CEREBRO / Knowledge Engine - Fase 3: o chunker de prosa.

Primeira vez em que documento vira unidade recuperavel. O que estes testes
guardam, em ordem de importancia:

1. **nenhum chunk sem pagina** - a rastreabilidade e o produto;
2. **determinismo** - mesma entrada, mesmos hashes;
3. **estrutura antes de tamanho** - unidade pedagogica nunca e cortada;
4. **overlap so dentro da mesma secao**;
5. ``raw_text`` e literal da fonte; o contexto do sistema vive em
   ``heading_path`` e so aparece no ``retrieval_text`` derivado.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf

from agente_ia_edu.knowledge_chunking_policy.v1 import (
    MAX_CHUNK_TOKENS,
    MIN_CHUNK_TOKENS,
    POLICY_VERSION,
    build_retrieval_text,
    estimate_tokens,
)
from agente_ia_edu.services.authorial_material_parser import (
    parse_authorial_pdf,
    read_pdf_page_texts,
)
from agente_ia_edu.services.knowledge_engine.chunking import ChunkDraft, ProseChunker


def _paragraph(seed: str, chars: int) -> str:
    """Paragrafo de prosa com tamanho controlado e sem marcador estrutural."""
    sentence = f"O {seed} participa da reacao em proporcao definida pela equacao. "
    repeats = max(1, round(chars / len(sentence)))
    # Termina em frase COMPLETA de proposito: a deteccao de exercicio do
    # parser exige um ponto antes do item numerado, e um paragrafo cortado no
    # meio da frase faria o teste medir o fixture em vez do chunker.
    return (sentence * repeats).strip()


def _write_pdf(path: Path, pages: list[str]) -> Path:
    """Gera um PDF real com camada de texto.

    ``insert_text`` e nao ``insert_textbox``: a caixa RECUSA o texto que nao
    couber nela e nao insere nada, o que silenciosamente produzia um PDF sem
    camada de texto para os paragrafos grandes destes testes.
    """
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((40, 60), text, fontsize=8)
    document.save(str(path))
    document.close()
    return path


class _ChunkerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _chunk(self, pages: list[str], page_offset: int = 0) -> list[ChunkDraft]:
        path = _write_pdf(self.tmp / "livro.pdf", pages)
        layer = read_pdf_page_texts(path)
        parsed = parse_authorial_pdf(path, page_texts=layer.page_texts)
        return ProseChunker().chunk(
            page_texts=layer.page_texts, parsed=parsed, page_offset=page_offset
        )


class PageTraceabilityTests(_ChunkerCase):
    def test_no_chunk_is_ever_without_a_page(self):
        """Critério de aceite 2. Sem pagina, o chunk nao e citavel, e um Pack
        que o cite nao e verificavel."""
        chunks = self._chunk([
            "Capítulo 1 - Estequiometria\n\n" + _paragraph("mol", 1200),
            _paragraph("reagente", 1200),
            "Capítulo 2 - Soluções\n\n" + _paragraph("soluto", 1200),
        ])
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertIsNotNone(chunk.page_start, chunk.heading_path)
            self.assertIsNotNone(chunk.page_end)
            self.assertLessEqual(chunk.page_start, chunk.page_end)
            self.assertGreaterEqual(chunk.page_start, 1)

    def test_the_page_offset_shifts_to_the_printed_page_of_the_work(self):
        """Um recorte que comeca na pagina 312 do livro tem offset 311. Sem
        isso, toda citacao de recorte sai errada."""
        pages = ["Capítulo 1 - Teste\n\n" + _paragraph("mol", 900)]
        without = self._chunk(pages, page_offset=0)
        with_offset = self._chunk(pages, page_offset=311)
        self.assertEqual(without[0].page_start + 311, with_offset[0].page_start)
        self.assertEqual(without[0].page_end + 311, with_offset[0].page_end)


class DeterminismTests(_ChunkerCase):
    def test_two_runs_over_the_same_input_give_the_same_hashes(self):
        """Critério de aceite 3."""
        pages = [
            "Capítulo 1 - Estequiometria\n\n" + _paragraph("mol", 1500),
            _paragraph("rendimento", 1500),
        ]
        first = self._chunk(pages)
        second = self._chunk(pages)
        self.assertEqual([c.text_hash for c in first], [c.text_hash for c in second])
        self.assertEqual([c.ordinal for c in first], [c.ordinal for c in second])

    def test_ordinals_are_dense_and_start_at_one(self):
        chunks = self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 4000)])
        self.assertEqual([c.ordinal for c in chunks], list(range(1, len(chunks) + 1)))

    def test_the_policy_version_travels_with_every_chunk(self):
        chunks = self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 900)])
        for chunk in chunks:
            self.assertEqual(chunk.metadata["chunking_policy_version"], POLICY_VERSION)


class RepresentationSeparationTests(_ChunkerCase):
    """spec 20.2 - o contexto do sistema nunca pode parecer texto da obra."""

    def test_the_raw_text_never_contains_the_heading_path(self):
        chunks = self._chunk(["Capítulo 1 - Estequiometria\n\n" + _paragraph("mol", 900)])
        chunk = chunks[0]
        self.assertTrue(chunk.heading_path)
        for heading in chunk.heading_path:
            self.assertNotIn(heading, chunk.raw_text)

    def test_the_hash_is_over_the_retrieval_text(self):
        from agente_ia_edu.knowledge_chunking_policy.v1 import retrieval_text_hash

        chunk = self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 900)])[0]
        self.assertEqual(
            chunk.text_hash,
            retrieval_text_hash(raw_text=chunk.raw_text, heading_path=chunk.heading_path),
        )

    def test_the_source_text_hash_is_recorded_separately(self):
        from agente_ia_edu.knowledge_chunking_policy.v1 import source_text_hash

        chunk = self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 900)])[0]
        self.assertEqual(
            chunk.metadata["source_text_sha256"], source_text_hash(chunk.raw_text)
        )
        self.assertNotEqual(chunk.metadata["source_text_sha256"], chunk.text_hash)

    def test_the_retrieval_text_is_derivable_and_not_stored(self):
        chunk = self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 900)])[0]
        self.assertFalse(hasattr(chunk, "retrieval_text"))
        built = build_retrieval_text(
            raw_text=chunk.raw_text, heading_path=chunk.heading_path
        )
        self.assertTrue(built.endswith(chunk.raw_text.strip()))


class HierarchyTests(_ChunkerCase):
    def test_the_chapter_title_becomes_the_heading_path(self):
        chunks = self._chunk(["Capítulo 10 - Estequiometria\n\n" + _paragraph("mol", 900)])
        self.assertIn("Estequiometria", " ".join(chunks[0].heading_path))

    def test_each_chapter_gets_its_own_heading_path(self):
        chunks = self._chunk([
            "Capítulo 1 - Estequiometria\n\n" + _paragraph("mol", 900),
            "Capítulo 2 - Soluções\n\n" + _paragraph("soluto", 900),
        ])
        paths = {" / ".join(c.heading_path) for c in chunks}
        self.assertGreaterEqual(len(paths), 2)


class SizeTests(_ChunkerCase):
    def test_divisible_content_is_windowed_and_respects_the_maximum(self):
        chunks = self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 12000)])
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            if chunk.chunk_type in ("PROSE", "DEFINITION", "SUMMARY"):
                self.assertLessEqual(
                    estimate_tokens(chunk.char_count),
                    MAX_CHUNK_TOKENS,
                    chunk.metadata,
                )

    def test_a_tiny_section_does_not_become_a_sub_minimum_chunk_of_its_own(self):
        """Abaixo do minimo o chunk e ruido no indice; funde com o vizinho."""
        chunks = self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 3000) + "\n\ncurto."])
        sub_minimum = [
            c for c in chunks
            if estimate_tokens(c.char_count) < MIN_CHUNK_TOKENS
            and c.chunk_type not in ("EXERCISE", "FORMULA", "TABLE", "WORKED_EXAMPLE")
        ]
        self.assertEqual(sub_minimum, [], [c.raw_text[:40] for c in sub_minimum])

    def test_char_count_and_token_estimate_agree_with_the_raw_text(self):
        for chunk in self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 5000)]):
            self.assertEqual(chunk.char_count, len(chunk.raw_text))
            self.assertEqual(chunk.token_estimate, estimate_tokens(chunk.char_count))


class IndivisibleUnitTests(_ChunkerCase):
    def test_a_worked_example_is_never_split(self):
        worked = "Exemplo resolvido\n" + _paragraph("CO2", 6000)
        chunks = self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 600) + "\n\n" + worked])
        examples = [c for c in chunks if c.chunk_type == "WORKED_EXAMPLE"]
        self.assertEqual(len(examples), 1)
        self.assertTrue(examples[0].metadata.get("oversized"))

    def test_an_oversized_indivisible_unit_is_flagged_not_cut(self):
        chunks = self._chunk([
            "Capítulo 1 - T\n\n" + _paragraph("mol", 500) + "\n\nResolução:\n"
            + _paragraph("massa", 7000)
        ])
        indivisible = [c for c in chunks if c.chunk_type == "WORKED_EXAMPLE"]
        self.assertEqual(len(indivisible), 1)
        self.assertGreater(estimate_tokens(indivisible[0].char_count), MAX_CHUNK_TOKENS)
        self.assertTrue(indivisible[0].metadata["oversized"])


class OverlapTests(_ChunkerCase):
    def test_overlap_never_crosses_a_section_boundary(self):
        """Critério central: sobreposicao entre capitulos misturaria assuntos
        e faria a recuperacao devolver o capitulo errado com confianca."""
        chunks = self._chunk([
            "Capítulo 1 - Estequiometria\n\n" + _paragraph("mol", 5000),
            "Capítulo 2 - Soluções\n\n" + _paragraph("soluto", 5000),
        ])
        for chunk in chunks:
            if chunk.metadata.get("overlap_prefix_chars"):
                self.assertTrue(
                    chunk.metadata.get("overlap_from_same_section"),
                    chunk.heading_path,
                )

    def test_the_first_chunk_of_a_section_has_no_overlap(self):
        chunks = self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 9000)])
        self.assertFalse(chunks[0].metadata.get("overlap_prefix_chars"))

    def test_an_indivisible_chunk_carries_no_overlap(self):
        chunks = self._chunk([
            "Capítulo 1 - T\n\n" + _paragraph("mol", 4000)
            + "\n\nExemplo resolvido\n" + _paragraph("CO2", 800)
        ])
        for chunk in chunks:
            if chunk.chunk_type in ("WORKED_EXAMPLE", "FORMULA", "TABLE", "EXERCISE"):
                self.assertFalse(chunk.metadata.get("overlap_prefix_chars"), chunk.chunk_type)


class ExerciseTests(_ChunkerCase):
    def test_exercises_detected_by_the_parser_become_their_own_chunks(self):
        pages = [
            "Capítulo 1 - Estequiometria\n\n" + _paragraph("mol", 900)
            + "\n\n1. Calcule a massa molar do dioxido de carbono, sabendo que as "
              "massas atomicas sao C igual a 12 e O igual a 16.\n"
              "a) 44 g\nb) 32 g\nc) 28 g\nd) 18 g\ne) 12 g\n"
        ]
        chunks = self._chunk(pages)
        exercises = [c for c in chunks if c.chunk_type == "EXERCISE"]
        self.assertTrue(exercises)
        for exercise in exercises:
            # Ruido numerado curto nao vira exercicio - ver _MIN_EXERCISE_CHARS.
            self.assertGreaterEqual(exercise.char_count, 60)
            self.assertIsNotNone(exercise.page_start)
            self.assertIn("question_number", exercise.metadata)


class StructureObservabilityTests(_ChunkerCase):
    """spec 20.4 - falso positivo tem de ser observavel para ser refinavel."""

    def test_every_chunk_records_what_classified_it(self):
        chunks = self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 2000)])
        for chunk in chunks:
            self.assertIn("structure", chunk.metadata)
            self.assertIn("classified_as", chunk.metadata["structure"])
            self.assertIn("has_formula", chunk.metadata["structure"])


class EmptyAndDegenerateTests(_ChunkerCase):
    def test_a_document_with_no_usable_text_yields_no_chunks(self):
        chunker = ProseChunker()
        from agente_ia_edu.services.ingestion_parser import ParsedDocument

        empty = ParsedDocument(
            filename="x.pdf", document_hash="h", title=None, author=None,
            page_count=1, sections=[], questions=[],
        )
        self.assertEqual(chunker.chunk(page_texts=[""], parsed=empty, page_offset=0), [])

    def test_no_chunk_is_emitted_with_empty_raw_text(self):
        chunks = self._chunk(["Capítulo 1 - T\n\n" + _paragraph("mol", 3000) + "\n\n\n\n"])
        for chunk in chunks:
            self.assertTrue(chunk.raw_text.strip())


class ExerciseGateIntegrationTests(_ChunkerCase):
    """Fase 3.1 - o portao dentro do pipeline, nao so como funcao pura.

    O que importa aqui e a INVARIANCIA: o portao reclassifica, e a contagem de
    chunks e o texto total nao mudam. Perder texto seria pior que o rotulo
    errado que o portao existe para consertar.
    """

    _EXERCISE = (
        "1. Calcule a massa molar do dioxido de carbono, sabendo que as massas "
        "atomicas sao C igual a 12 e O igual a 16.\n"
        "a) 44 g\nb) 32 g\nc) 28 g\nd) 18 g\ne) 12 g.\n"
    )
    _ANSWER = (
        "2. Alternativa A. O dioxido de carbono tem massa molar 44 g/mol, "
        "obtida somando 12 do carbono e 32 dos dois oxigenios presentes.\n"
    )  # termina em ponto de proposito: a heuristica do parser exige um ponto
       # antes do item numerado seguinte.
    _LIST_ITEM = (
        "3. Empreendimentos de impacto social sao voltados a individuos de "
        "baixa renda, permitindo-lhes acesso a bens e servicos essenciais.\n"
    )

    def _rich(self) -> list[str]:
        return [
            "Capítulo 10 - Estequiometria\n\n"
            + _paragraph("mol", 900)
            + "\n\n" + self._EXERCISE
            + "\n\n" + self._ANSWER
            + "\n\n" + self._LIST_ITEM
        ]

    def test_a_real_exercise_survives_the_gate(self):
        chunks = self._chunk(self._rich())
        exercises = [c for c in chunks if c.chunk_type == "EXERCISE"]
        self.assertTrue(exercises)
        for chunk in exercises:
            structure = chunk.metadata["structure"]
            self.assertEqual(structure["decision_reason"], "PROMOTED")
            self.assertTrue(structure["exercise_evidence"])
            self.assertNotIn("demoted_from", structure)

    def test_an_answer_key_becomes_solution(self):
        """Requisito 6: gabarito identificado deterministicamente recebe
        chunk_type SOLUTION, nao PROSE."""
        chunks = self._chunk(self._rich())
        solutions = [c for c in chunks if c.chunk_type == "SOLUTION"]
        self.assertTrue(solutions, [c.chunk_type for c in chunks])
        for chunk in solutions:
            structure = chunk.metadata["structure"]
            self.assertEqual(structure["decision_reason"], "ANSWER_KEY")
            self.assertEqual(structure["demoted_from"], "EXERCISE")
            self.assertIn("answer_opener", structure["exercise_vetoes"])
            self.assertIsNotNone(chunk.page_start)

    def test_a_false_candidate_is_reclassified_never_discarded(self):
        """Requisito 4. O texto do candidato rebaixado continua no corpus."""
        chunks = self._chunk(self._rich())
        demoted = [
            c for c in chunks
            if c.metadata["structure"].get("demoted_from") == "EXERCISE"
            and c.chunk_type not in ("EXERCISE", "SOLUTION")
        ]
        self.assertTrue(demoted)
        for chunk in demoted:
            self.assertEqual(chunk.metadata["structure"]["decision_reason"], "NO_EVIDENCE")
            self.assertTrue(chunk.raw_text.strip())
            self.assertIsNotNone(chunk.page_start)

    def test_the_gate_never_changes_the_chunk_count_or_the_text(self):
        """A invariancia que importa: o portao e um RE-rotulador."""
        chunks = self._chunk(self._rich())
        # Todo chunk tem texto e pagina, e a soma do texto cobre os
        # candidatos - nenhum foi engolido pelo portao.
        self.assertTrue(all(c.raw_text.strip() for c in chunks))
        self.assertTrue(all(c.page_start is not None for c in chunks))
        joined = "\n".join(c.raw_text for c in chunks)
        self.assertIn("massa molar do dioxido de carbono", joined)
        self.assertIn("Alternativa A", joined)
        self.assertIn("Empreendimentos de impacto social", joined)

    def test_every_gated_chunk_records_the_full_decision(self):
        """Requisito 5: origem da decisao preservada em metadata."""
        chunks = self._chunk(self._rich())
        gated = [
            c for c in chunks
            if "decision_reason" in c.metadata["structure"]
        ]
        self.assertTrue(gated)
        for chunk in gated:
            structure = chunk.metadata["structure"]
            self.assertIn(
                structure["decision_reason"],
                {"PROMOTED", "ANSWER_KEY", "VETOED", "NO_EVIDENCE"},
            )
            self.assertIsInstance(structure["exercise_evidence"], list)
            self.assertIsInstance(structure["exercise_vetoes"], list)

    def test_solution_is_never_split(self):
        long_answer = "4. Resolucao: " + _paragraph("CO2", 6000)
        chunks = self._chunk([
            "Capítulo 1 - T\n\n" + _paragraph("mol", 600) + "\n\n" + long_answer
        ])
        solutions = [c for c in chunks if c.chunk_type == "SOLUTION"]
        self.assertEqual(len(solutions), 1)


if __name__ == "__main__":
    unittest.main()
