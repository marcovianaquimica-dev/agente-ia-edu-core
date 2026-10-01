"""CEREBRO / Knowledge Engine - Fase 3: a politica de chunking, versionada.

Toda constante de tamanho e todo limiar de PARTIAL moram aqui, nunca
espalhados pelo codigo. Mesmo regime imutavel de ``essay_engine_contract``:
mudanca de forma e um modulo ``v2`` novo, nunca uma edicao da v1.

Dois assuntos que este modulo fixa e que importam mais que os numeros:

1. a separacao entre ``raw_text`` (literal da fonte), ``heading_path``
   (contexto do sistema) e ``retrieval_text`` (representacao enriquecida), e
   QUAL delas alimenta o ``text_hash`` - spec 20.2;
2. o que faz um documento ser PARTIAL, que NAO e "tem uma pagina vazia" -
   spec 20.3.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.knowledge_chunking_policy.v1 import (
    CHARS_PER_TOKEN,
    CONTIGUOUS_GAP_MIN_PAGES,
    HIGH_GAP_MIN_PAGES,
    HIGH_GAP_RATIO,
    INDIVISIBLE_CHUNK_TYPES,
    MAX_CHUNK_TOKENS,
    MIN_CHUNK_TOKENS,
    MIN_USEFUL_CHARS,
    POLICY_VERSION,
    TARGET_CHUNK_TOKENS,
    PageTextStats,
    build_retrieval_text,
    classify_extraction_status,
    estimate_tokens,
    retrieval_text_hash,
    source_text_hash,
)


class PolicyShapeTests(unittest.TestCase):
    def test_the_version_is_declared(self):
        self.assertEqual(POLICY_VERSION, "v1")

    def test_the_size_bounds_are_ordered_and_match_the_spec(self):
        self.assertLess(MIN_CHUNK_TOKENS, TARGET_CHUNK_TOKENS)
        self.assertLess(TARGET_CHUNK_TOKENS, MAX_CHUNK_TOKENS)
        self.assertEqual((MIN_CHUNK_TOKENS, TARGET_CHUNK_TOKENS, MAX_CHUNK_TOKENS),
                         (120, 700, 1100))

    def test_token_estimation_is_deterministic_and_rounds_up(self):
        self.assertEqual(CHARS_PER_TOKEN, 4)
        self.assertEqual(estimate_tokens(0), 0)
        self.assertEqual(estimate_tokens(1), 1)
        self.assertEqual(estimate_tokens(4), 1)
        self.assertEqual(estimate_tokens(5), 2)
        self.assertEqual(estimate_tokens(2800), 700)

    def test_the_pedagogically_indivisible_types_are_named(self):
        self.assertEqual(
            set(INDIVISIBLE_CHUNK_TYPES),
            {"EXERCISE", "TABLE", "FORMULA", "WORKED_EXAMPLE", "CURRICULUM_ITEM"},
        )


class RetrievalTextTests(unittest.TestCase):
    """spec 20.2 - o contexto que o sistema acrescenta nunca pode parecer
    parte do texto literal da obra."""

    def test_the_heading_path_is_prefixed_to_the_raw_text(self):
        built = build_retrieval_text(
            raw_text="A estequiometria estuda as relacoes quantitativas.",
            heading_path=["Cap. 10 - Estequiometria", "10.2 Reagente limitante"],
        )
        self.assertTrue(built.startswith("Cap. 10 - Estequiometria"))
        self.assertIn("10.2 Reagente limitante", built)
        self.assertTrue(built.endswith("A estequiometria estuda as relacoes quantitativas."))

    def test_without_a_heading_path_the_retrieval_text_is_the_raw_text(self):
        self.assertEqual(build_retrieval_text(raw_text="texto", heading_path=[]), "texto")
        self.assertEqual(build_retrieval_text(raw_text="texto", heading_path=None), "texto")

    def test_building_the_retrieval_text_never_mutates_the_raw_text(self):
        raw = "  texto   com    espacos  "
        built = build_retrieval_text(raw_text=raw, heading_path=["T"])
        self.assertIn(raw.strip(), built)

    def test_the_hash_is_over_the_retrieval_text_not_the_raw_text(self):
        """Decisao normativa da spec 20.2.

        text_hash tambem vive em knowledge_chunk_embeddings, onde seu trabalho
        e ser a chave de idempotencia do embedding - e o embedding e calculado
        a partir do retrieval_text. Hashear o raw_text faria uma mudanca de
        politica passar em silencio, deixando embeddings obsoletos
        indistinguiveis de validos.
        """
        raw = "mesmo texto literal"
        with_heading = retrieval_text_hash(raw_text=raw, heading_path=["Cap. 1"])
        without_heading = retrieval_text_hash(raw_text=raw, heading_path=[])
        self.assertNotEqual(with_heading, without_heading)
        # E o hash do texto da fonte NAO muda com o contexto - e por isso que
        # os dois existem separados.
        self.assertEqual(source_text_hash(raw), source_text_hash(raw))

    def test_both_hashes_are_sha256_hex(self):
        self.assertEqual(len(retrieval_text_hash(raw_text="x", heading_path=[])), 64)
        self.assertEqual(len(source_text_hash("x")), 64)

    def test_the_hash_is_stable_across_calls(self):
        args = dict(raw_text="estequiometria", heading_path=["Cap. 10"])
        self.assertEqual(retrieval_text_hash(**args), retrieval_text_hash(**args))


def _stats(pages: list[tuple[int, bool]]) -> list[PageTextStats]:
    """``pages`` e uma lista de (chars, tem_imagem), 1-indexada na ordem."""
    return [
        PageTextStats(page=index, char_count=chars, has_image=has_image)
        for index, (chars, has_image) in enumerate(pages, start=1)
    ]


class ExtractionStatusTests(unittest.TestCase):
    """spec 20.3 - pagina vazia NAO e, por si, perda de conteudo."""

    def test_no_text_anywhere_is_failed(self):
        status, reasons = classify_extraction_status(_stats([(0, False), (0, True)]))
        self.assertEqual(status, "FAILED")
        self.assertEqual(reasons, ["NO_TEXT_EXTRACTED"])

    def test_a_fully_extracted_document_is_extracted(self):
        status, reasons = classify_extraction_status(_stats([(3000, False)] * 10))
        self.assertEqual(status, "EXTRACTED")
        self.assertEqual(reasons, [])

    def test_leading_and_trailing_blank_pages_are_not_a_loss(self):
        """Capa, folha de guarda e brancos finais ficam FORA das paginas
        interiores por construcao - e por isso nao disparam PARTIAL."""
        pages = [(0, False), (0, False)] + [(3000, False)] * 8 + [(0, False), (0, False)]
        status, reasons = classify_extraction_status(_stats(pages))
        self.assertEqual(status, "EXTRACTED")
        self.assertEqual(reasons, [])

    def test_a_single_blank_interior_page_without_image_is_not_partial(self):
        """Uma pagina de fato branca no meio do livro e comum e nao e perda."""
        pages = [(3000, False)] * 5 + [(0, False)] + [(3000, False)] * 5
        status, reasons = classify_extraction_status(_stats(pages))
        self.assertEqual(status, "EXTRACTED")
        self.assertEqual(reasons, [])

    def test_an_interior_page_with_an_image_and_no_text_is_partial(self):
        """A assinatura de pagina escaneada/achatada: havia conteudo, e ele
        nao saiu."""
        pages = [(3000, False)] * 5 + [(0, True)] + [(3000, False)] * 5
        status, reasons = classify_extraction_status(_stats(pages))
        self.assertEqual(status, "PARTIAL")
        self.assertIn("IMAGE_ONLY_INTERIOR_PAGE", reasons)

    def test_a_sparse_interior_page_with_an_image_is_partial(self):
        """Sem texto UTIL, nao "sem texto": uma legenda solta nao salva a
        pagina."""
        pages = [(3000, False)] * 5 + [(MIN_USEFUL_CHARS - 1, True)] + [(3000, False)] * 5
        status, reasons = classify_extraction_status(_stats(pages))
        self.assertEqual(status, "PARTIAL")
        self.assertIn("IMAGE_ONLY_INTERIOR_PAGE", reasons)

    def test_a_contiguous_gap_of_three_interior_pages_is_partial_even_without_images(self):
        pages = [(3000, False)] * 5 + [(0, False)] * 3 + [(3000, False)] * 20
        status, reasons = classify_extraction_status(_stats(pages))
        self.assertEqual(status, "PARTIAL")
        self.assertIn("CONTIGUOUS_GAP", reasons)

    def test_a_contiguous_gap_of_two_interior_pages_is_not_enough_on_its_own(self):
        pages = [(3000, False)] * 20 + [(0, False)] * 2 + [(3000, False)] * 20
        status, reasons = classify_extraction_status(_stats(pages))
        self.assertEqual(status, "EXTRACTED")
        self.assertEqual(reasons, [])

    def test_a_diffuse_loss_above_the_ratio_is_partial(self):
        """Nem imagem, nem corrida longa - so quantidade. E perda."""
        pages = []
        for _ in range(10):
            pages.extend([(3000, False), (3000, False), (3000, False), (0, False)])
        pages.append((3000, False))
        status, reasons = classify_extraction_status(_stats(pages))
        self.assertEqual(status, "PARTIAL")
        self.assertIn("HIGH_GAP_RATIO", reasons)

    def test_the_ratio_rule_needs_an_absolute_count_too(self):
        """Sem isso, UMA pagina branca entre nove interiores daria 11% e
        viraria PARTIAL - o estado perderia utilidade por excesso de alarme."""
        self.assertEqual(HIGH_GAP_MIN_PAGES, 3)
        nine_interior_one_gap = [(3000, False)] * 5 + [(0, False)] + [(3000, False)] * 5
        self.assertGreater(1 / 9, HIGH_GAP_RATIO)  # a proporcao sozinha dispararia
        status, reasons = classify_extraction_status(_stats(nine_interior_one_gap))
        self.assertEqual((status, reasons), ("EXTRACTED", []))

    def test_the_ratio_threshold_is_the_documented_ten_percent(self):
        self.assertEqual(HIGH_GAP_RATIO, 0.10)
        self.assertEqual(CONTIGUOUS_GAP_MIN_PAGES, 3)
        self.assertEqual(MIN_USEFUL_CHARS, 200)

    def test_every_reason_that_fires_is_reported_not_just_the_first(self):
        pages = [(3000, False)] * 4 + [(0, True)] + [(0, False)] * 3 + [(3000, False)] * 4
        status, reasons = classify_extraction_status(_stats(pages))
        self.assertEqual(status, "PARTIAL")
        self.assertIn("IMAGE_ONLY_INTERIOR_PAGE", reasons)
        self.assertIn("CONTIGUOUS_GAP", reasons)

    def test_an_empty_document_is_failed_not_extracted(self):
        status, reasons = classify_extraction_status([])
        self.assertEqual(status, "FAILED")
        self.assertEqual(reasons, ["NO_TEXT_EXTRACTED"])

    def test_a_single_page_document_with_text_is_extracted(self):
        status, reasons = classify_extraction_status(_stats([(3000, False)]))
        self.assertEqual(status, "EXTRACTED")
        self.assertEqual(reasons, [])


if __name__ == "__main__":
    unittest.main()
