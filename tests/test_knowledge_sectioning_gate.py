"""CEREBRO - Fase 5.1a: secoes fantasmas de sumario e chunks gigantes.

DUAS INVARIANTES ESTRUTURAIS, e a causa de cada uma
===================================================

**1. Linha de sumario nao e cabecalho de secao.**

Medido no livro real (Cotidiano v1, pagina 452 - o sumario do manual do
professor): o parser detectou 25 secoes cuja faixa cobre aquela pagina, e 24
delas tem como TITULO uma linha do sumario, pontilhados inclusive:

    pos=197  p452-452  'Conceitos introdutorios a Quimica ....................'
    pos=198  p452-452  'Elementos quimicos e substancias quimicas ............'
    pos=220  p452-463  'Nanotecnologia .......................................'

Para cada uma, o chunker abria uma janela sobre a faixa e cortava a partir do
titulo - produzindo SUFIXOS ANINHADOS da mesma pagina, com tamanhos
decrescentes (2.723, 2.714, 2.686, 2.061, 1.889...). Resultado: **40 chunks
numa pagina de 8.614 caracteres**, e 17,4% do acervo com texto contido em
outro chunk.

A correcao ataca a CAUSA - a linha de sumario deixa de originar secao - e nao
deduplica texto depois de criado. O ``authorial_material_parser`` NAO e
alterado: PHASE 26 depende dele, e o filtro vive no Knowledge Engine, como o
portao de EXERCISE da Fase 3.1.

**2. Nada janelavel escapa do limite.**

``_window`` testava ``if buffer and candidate > _MAX_CHARS`` - com o buffer
VAZIO, um bloco unico nunca era testado. Um bloco de 45.040 caracteres (a
secao pos=220, 12 paginas de sumario sem fronteira de sentenca) virava um
chunk so. Havia 145 chunks acima de 6.000 caracteres no acervo.

Excecao preservada e OBSERVAVEL: unidade comprovadamente indivisivel
(``INDIVISIBLE_CHUNK_TYPES``) continua inteira, marcada com ``oversized``.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.knowledge_chunking_policy.v1 import (
    INDIVISIBLE_CHUNK_TYPES,
    MAX_CHUNK_TOKENS,
    CHARS_PER_TOKEN,
)
from agente_ia_edu.services.knowledge_engine.chunking import (
    ProseChunker,
    is_table_of_contents_title,
)
from agente_ia_edu.services.ingestion_parser import ParsedDocument, ParsedSection

MAX_CHARS = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN


def _section(position: int, title: str, start: int, end: int) -> ParsedSection:
    return ParsedSection(
        section_type="CHAPTER",
        title=title,
        description=None,
        section_number=str(position),
        page_start=start,
        page_end=end,
        content_lines=[],
        position=position,
    )


def _document(sections) -> ParsedDocument:
    return ParsedDocument(
        filename="livro.pdf",
        document_hash="h" * 64,
        title=None,
        author=None,
        page_count=len(sections),
        sections=sections,
        questions=[],
    )


class TableOfContentsTitleTests(unittest.TestCase):
    """Deteccao PURA, sobre o titulo cru do parser."""

    def test_a_dot_leader_run_marks_a_table_of_contents_line(self):
        self.assertTrue(
            is_table_of_contents_title(
                "Conceitos introdutórios à Química ........................"
            )
        )

    def test_a_short_dot_run_is_not_enough(self):
        """Reticencias nao sao pontilhado de sumario."""
        self.assertFalse(is_table_of_contents_title("E assim por diante..."))

    def test_a_real_heading_is_never_a_table_of_contents_line(self):
        for title in (
            "Proporção nas reações químicas",
            "Capítulo 10 - Quantidade de matéria e mol",
            "Nanotecnologia Objetivos do capítulo • Compreender",
            "GOMES, A",
        ):
            self.assertFalse(is_table_of_contents_title(title), title)

    def test_an_empty_title_is_not_a_table_of_contents_line(self):
        self.assertFalse(is_table_of_contents_title(""))
        self.assertFalse(is_table_of_contents_title(None))


class PhantomSectionTests(unittest.TestCase):
    """O caso da pagina 452, reduzido ao essencial e fixado como regressao."""

    def setUp(self):
        # Uma pagina de sumario, com as mesmas marcas do caso real.
        self.toc_page = (
            "MP002\n"
            "ORIENTAÇÕES GERAIS DA COLEÇÃO ....................................\n"
            "Capítulo 1 Conceitos introdutórios à Química .............. MP017\n"
            "Capítulo 2 Elementos químicos e substâncias químicas ...... MP021\n"
            "Capítulo 3 Modelos atômicos e tabela periódica ............ MP024\n"
        )
        self.content_page = (
            "A estequiometria relaciona as quantidades de reagentes e produtos "
            "em uma reação química. O reagente limitante determina o rendimento "
            "do processo, e o excesso permanece sem reagir ao final.\n\n"
            "Para calcular, converte-se a massa em quantidade de matéria."
        )

    def _chunk(self, sections):
        parsed = _document(sections)
        return ProseChunker().chunk(
            page_texts=[self.content_page, self.toc_page], parsed=parsed
        )

    def test_toc_lines_do_not_become_sections(self):
        """Uma linha de sumario por capitulo - o caso da pagina 452."""
        sections = [
            _section(1, "Estequiometria", 1, 1),
            _section(2, "Conceitos introdutórios à Química ..............", 2, 2),
            _section(3, "Elementos químicos e substâncias químicas ......", 2, 2),
            _section(4, "Modelos atômicos e tabela periódica ............", 2, 2),
        ]
        drafts = self._chunk(sections)
        headings = {" > ".join(d.heading_path) for d in drafts}
        for phantom in ("Conceitos", "Elementos", "Modelos atômicos"):
            self.assertFalse(
                any(phantom in h for h in headings),
                f"secao fantasma {phantom!r} virou chunk: {headings}",
            )

    def test_no_chunk_text_is_contained_in_another(self):
        """A duplicacao medida (17,4% do acervo) vinha dos sufixos aninhados."""
        sections = [
            _section(1, "Estequiometria", 1, 1),
            _section(2, "Conceitos introdutórios à Química ..............", 2, 2),
            _section(3, "Elementos químicos e substâncias químicas ......", 2, 2),
        ]
        bodies = [" ".join(d.raw_text.split()) for d in self._chunk(sections)]
        for index, body in enumerate(bodies):
            for other in bodies[:index] + bodies[index + 1 :]:
                self.assertNotIn(body, other, f"texto duplicado: {body[:60]!r}")

    def test_the_toc_page_is_still_represented_once(self):
        """Suprimir a secao fantasma NAO pode apagar a pagina: o conteudo
        editorial e preservado, so deixa de ser multiplicado."""
        sections = [
            _section(1, "Estequiometria", 1, 1),
            _section(2, "Sumário do manual", 2, 2),
            _section(3, "Conceitos introdutórios à Química ..............", 2, 2),
        ]
        drafts = self._chunk(sections)
        paginas = {d.page_start for d in drafts}
        self.assertIn(2, paginas)

    def test_a_document_of_only_toc_lines_still_yields_something(self):
        """Falha segura: se TODAS as secoes forem suprimidas, o documento nao
        pode sumir."""
        sections = [
            _section(1, "Conceitos introdutórios à Química ..............", 2, 2),
            _section(2, "Elementos químicos ............................", 2, 2),
        ]
        drafts = self._chunk(sections)
        self.assertGreater(len(drafts), 0)
        self.assertTrue(
            any(d.metadata.get("section_suppressed") for d in drafts),
            "a supressao tem de ser observavel no chunk de recuperacao",
        )

    def test_the_suppression_is_recorded_as_a_countable_signal(self):
        sections = [
            _section(1, "Estequiometria", 1, 1),
            _section(2, "Conceitos introdutórios à Química ..............", 2, 2),
        ]
        drafts = self._chunk(sections)
        self.assertTrue(all("chunking_policy_version" in d.metadata for d in drafts))


class SharedPageRangeTests(unittest.TestCase):
    """A causa GERAL dos sufixos aninhados, maior que o sumario.

    Medido no Cotidiano v1: 19 faixas de paginas repetidas envolvendo 107 das
    264 secoes. Na pagina 9 sao 12 secoes cujo titulo e nome de autor de
    bibliografia - 'MEIS, L', 'FILGUEIRAS, C', 'ABDALLA, M'. Cada uma abria
    janela sobre a MESMA pagina e levava o sufixo inteiro a partir do seu
    proprio titulo.

    A correcao e estrutural e vale para qualquer secao: o texto de uma secao
    termina onde comeca a PROXIMA, nao no fim da faixa de paginas.
    """

    def setUp(self):
        self.page = (
            "MEIS, L. Ciencia e educacao. O primeiro verbete fala sobre o "
            "ensino de bioquimica no Brasil contemporaneo.\n\n"
            "FILGUEIRAS, C. Historia da quimica. O segundo verbete trata da "
            "origem das sociedades cientificas brasileiras.\n\n"
            "ABDALLA, M. O terceiro verbete discute o metodo cientifico e a "
            "sua aplicacao no ensino medio brasileiro."
        )

    def _chunk(self):
        sections = [
            _section(1, "MEIS, L", 1, 1),
            _section(2, "FILGUEIRAS, C", 1, 1),
            _section(3, "ABDALLA, M", 1, 1),
        ]
        return ProseChunker().chunk(page_texts=[self.page], parsed=_document(sections))

    def test_a_section_stops_where_the_next_one_begins(self):
        drafts = self._chunk()
        primeiro = [d for d in drafts if "MEIS" in " > ".join(d.heading_path)]
        self.assertTrue(primeiro)
        for draft in primeiro:
            self.assertNotIn("ABDALLA", draft.raw_text)

    def test_sections_sharing_a_page_range_do_not_nest(self):
        bodies = [" ".join(d.raw_text.split()) for d in self._chunk()]
        for index, body in enumerate(bodies):
            for other in bodies[:index] + bodies[index + 1 :]:
                self.assertNotIn(body, other, f"sufixo aninhado: {body[:50]!r}")

    def test_no_text_of_the_page_is_lost(self):
        drafts = self._chunk()
        juntos = " ".join(" ".join(d.raw_text.split()) for d in drafts)
        for marca in ("bioquimica", "sociedades cientificas", "metodo cientifico"):
            self.assertIn(marca, juntos, marca)


class PageCoverageTests(unittest.TestCase):
    """Nenhum conteudo legitimo pode simplesmente desaparecer.

    O corte na proxima secao e o cursor sequencial reduziram a duplicacao de
    17,4% para 8,9% - mas tambem derrubaram a cobertura de paginas, porque
    texto que nenhuma secao reivindica deixava de ser emitido. Conteudo
    editorial nao e apagado: ele e representado e depois classificado.
    """

    def test_a_page_no_section_claims_is_still_represented(self):
        paginas = [
            "Capítulo 1 - Estequiometria\n\n"
            + "A estequiometria relaciona quantidades de reagentes e produtos "
            * 6,
            "Esta página inteira não pertence a seção alguma, mas tem texto "
            "útil que precisa continuar representado no corpus do sistema. "
            * 4,
        ]
        sections = [_section(1, "Capítulo 1 - Estequiometria", 1, 1)]
        drafts = ProseChunker().chunk(page_texts=paginas, parsed=_document(sections))
        paginas_cobertas = {
            p for d in drafts for p in range(d.page_start, d.page_end + 1)
        }
        self.assertIn(2, paginas_cobertas)

    def test_the_residual_chunk_is_observable(self):
        paginas = [
            "Capítulo 1\n\n" + "Texto do capítulo um sobre química geral. " * 8,
            "Página órfã com conteúdo editorial relevante preservado aqui. " * 5,
        ]
        sections = [_section(1, "Capítulo 1", 1, 1)]
        drafts = ProseChunker().chunk(page_texts=paginas, parsed=_document(sections))
        residuais = [d for d in drafts if d.metadata.get("residual_page")]
        self.assertTrue(residuais, "a pagina resgatada tem de ser identificavel")
        self.assertEqual({d.page_start for d in residuais}, {2})

    def test_a_blank_page_does_not_become_a_chunk(self):
        paginas = ["Capítulo 1\n\n" + "Texto do capítulo. " * 20, "   \n  "]
        sections = [_section(1, "Capítulo 1", 1, 1)]
        drafts = ProseChunker().chunk(page_texts=paginas, parsed=_document(sections))
        self.assertFalse([d for d in drafts if d.metadata.get("residual_page")])


class ExerciseBoundaryTests(unittest.TestCase):
    """A causa estrutural dos chunks gigantes, e ela NAO e o janelamento.

    Medido nos tres livros: **100% dos chunks acima de 6.000 caracteres vem
    do caminho de exercicio** e nenhum do janelamento. O ``statement_text``
    que o parser entrega vai direto para ``_draft`` - por desenho, porque um
    enunciado e uma unidade - e portanto nenhum limite se aplica.

    Por que ele fica gigante: o enunciado nao tem fronteira de FIM. Quando
    nenhuma questao seguinte e detectada, ele engole o resto da secao. Os
    maiores casos medidos nao tem um unico item numerado interno e comecam
    com ``EMSLEY, J.`` (bibliografia), com o texto de competencias da BNCC ou
    com ``Espera-se que os estudantes...`` (gabarito) - e um deles tem
    ``question_number = 472``.

    A correcao e de FRONTEIRA, nao de fatiamento: o enunciado termina na
    primeira fronteira estrutural, e o que vem depois volta ao fluxo de prosa
    para ser classificado como qualquer outro texto. Nada e truncado, nada e
    descartado, e a correcao e contavel na metadata.
    """

    def _chunk(self, statement: str, *, page: str | None = None):
        from agente_ia_edu.services.ingestion_parser import ParsedQuestion

        question = ParsedQuestion(
            question_number=10,
            statement_text=statement,
            alternatives_text=None,
            correct_answer=None,
            answer_explanation=None,
            page_start=1,
            page_end=1,
            position=1,
            section_index=1,
            requires_review=False,
        )
        document = ParsedDocument(
            filename="livro.pdf",
            document_hash="h" * 64,
            title=None,
            author=None,
            page_count=1,
            sections=[_section(1, "Capítulo 1 - Exercícios", 1, 1)],
            questions=[question],
        )
        body = page or ("Capítulo 1 - Exercícios\n\n" + statement)
        return ProseChunker().chunk(page_texts=[body], parsed=document)

    def test_a_swallowed_statement_is_cut_at_a_structural_boundary(self):
        """O enunciado real, seguido de material que ele engoliu."""
        enunciado = (
            "(Enem) Calcule a massa de gás carbônico liberada na combustão "
            "completa de um mol de etanol, considerando a estequiometria da "
            "reação envolvida."
        )
        engolido = "\n\n".join(
            f"EMSLEY, J. Obra de referência número {i} sobre química geral, "
            f"publicada em São Paulo pela editora acadêmica."
            for i in range(60)
        )
        drafts = self._chunk(f"{enunciado}\n\n{engolido}")
        self.assertGreater(len(drafts), 1)
        exercicios = [d for d in drafts if d.chunk_type in ("EXERCISE", "SOLUTION")]
        self.assertTrue(exercicios)
        self.assertLess(exercicios[0].char_count, 1000)
        self.assertIn("Calcule a massa", exercicios[0].raw_text)
        self.assertNotIn("EMSLEY", exercicios[0].raw_text)

    def test_nothing_is_truncated_or_discarded(self):
        enunciado = "(Enem) Calcule a massa de gás carbônico liberada na reação."
        engolido = "\n\n".join(
            f"Parágrafo engolido número {i} com texto suficiente para formar "
            f"um bloco de prosa legítimo no corpus do sistema."
            for i in range(60)
        )
        drafts = self._chunk(f"{enunciado}\n\n{engolido}")
        juntos = " ".join(" ".join(d.raw_text.split()) for d in drafts)
        self.assertIn("Calcule a massa", juntos)
        for i in (0, 17, 42, 59):
            self.assertIn(f"Parágrafo engolido número {i} ", juntos + " ")

    def test_the_boundary_correction_is_observable(self):
        enunciado = "(Enem) Calcule a massa de gás carbônico liberada."
        engolido = "\n\n".join(
            f"Texto engolido {i} com tamanho bastante para virar bloco próprio."
            for i in range(80)
        )
        drafts = self._chunk(f"{enunciado}\n\n{engolido}")
        self.assertTrue(
            any(d.metadata.get("exercise_boundary_corrected") for d in drafts),
            "a correcao de fronteira tem de aparecer na metadata",
        )

    def test_the_page_range_is_preserved_in_every_segment(self):
        enunciado = "(Enem) Calcule a massa liberada na combustão do etanol."
        engolido = "\n\n".join(
            f"Texto engolido {i} com tamanho bastante para virar bloco próprio."
            for i in range(80)
        )
        for draft in self._chunk(f"{enunciado}\n\n{engolido}"):
            self.assertEqual(draft.page_start, 1)
            self.assertEqual(draft.page_end, 1)

    def test_a_statement_within_the_limit_is_never_touched(self):
        """Fase 3.1 intacta: enunciado de tamanho normal segue inteiro, e o
        portao continua decidindo sozinho."""
        enunciado = (
            "(Enem) Considere a reação de combustão do metano e calcule a "
            "massa de água formada. a) 18 g b) 36 g c) 54 g d) 72 g"
        )
        drafts = self._chunk(enunciado)
        exercicios = [d for d in drafts if d.chunk_type == "EXERCISE"]
        self.assertEqual(len(exercicios), 1)
        self.assertFalse(exercicios[0].metadata.get("exercise_boundary_corrected"))
        self.assertIn("18 g", exercicios[0].raw_text)

    def test_a_solution_statement_keeps_its_type_after_correction(self):
        """Regra da Fase 3.1 preservada: gabarito continua SOLUTION."""
        gabarito = "Alternativa B. O cálculo correto considera a proporção molar."
        engolido = "\n\n".join(
            f"Resolução comentada número {i} com texto suficiente para bloco."
            for i in range(80)
        )
        drafts = self._chunk(f"{gabarito}\n\n{engolido}")
        primeiro = [d for d in drafts if "Alternativa B" in d.raw_text]
        self.assertTrue(primeiro)
        self.assertEqual(primeiro[0].chunk_type, "SOLUTION")

    def test_a_legitimately_large_head_passes_through_an_explicit_policy(self):
        """Unidade grande de verdade - um paragrafo unico sem fronteira - nao
        e dividida em silencio: passa pelo teto e a divisao fica marcada."""
        from agente_ia_edu.services.knowledge_engine.chunking import (
            INDIVISIBLE_CEILING_CHARS,
        )

        unico = "(Enem) " + ("palavra " * 20000)
        drafts = self._chunk(unico)
        for draft in drafts:
            self.assertLessEqual(draft.char_count, INDIVISIBLE_CEILING_CHARS)
        self.assertTrue(
            any(d.metadata.get("indivisible_overflow") for d in drafts)
        )


class OversizedChunkTests(unittest.TestCase):
    """Invariante: nada janelavel escapa do limite."""

    def _one_section(self, page: str):
        parsed = _document([_section(1, "Capítulo único", 1, 1)])
        return ProseChunker().chunk(page_texts=[page], parsed=parsed)

    def test_a_single_huge_block_without_sentence_breaks_is_split(self):
        """O caso real: 12 paginas de sumario viraram UM bloco de 45.040
        chars, porque nao havia fronteira de sentenca e porque ``_window`` so
        testava o limite quando o buffer ja tinha algo."""
        page = "Capítulo único\n\n" + ("palavra " * 12000)
        drafts = self._one_section(page)
        self.assertGreater(len(drafts), 1)
        for draft in drafts:
            self.assertLessEqual(draft.char_count, MAX_CHARS, draft.char_count)

    def test_the_hard_split_is_observable(self):
        page = "Capítulo único\n\n" + ("palavra " * 12000)
        drafts = self._one_section(page)
        self.assertTrue(
            any(d.metadata.get("hard_split") for d in drafts),
            "um corte por tamanho tem de aparecer na metadata",
        )

    def test_no_text_is_lost_in_the_hard_split(self):
        filler = "palavra " * 12000
        page = "Capítulo único\n\n" + filler
        drafts = self._one_section(page)
        recovered = "".join(d.raw_text for d in drafts).replace(" ", "")
        self.assertEqual(recovered.count("palavra"), 12000)

    def test_a_divisible_chunk_never_exceeds_the_maximum(self):
        page = "Capítulo único\n\n" + " ".join(
            f"Sentenca numero {i} sobre quimica geral." for i in range(4000)
        )
        for draft in self._one_section(page):
            if draft.chunk_type not in INDIVISIBLE_CHUNK_TYPES:
                self.assertLessEqual(draft.char_count, MAX_CHARS)

    def test_merging_small_fragments_never_grows_past_the_maximum(self):
        """Segundo vazamento de tamanho: ``flush`` funde fragmento abaixo do
        minimo no chunk anterior, e fundir repetidamente crescia sem limite -
        era de la que vinham os chunks PROSE de 15 mil caracteres."""
        fragmentos = "\n\n".join(f"Frase curta numero {i}." for i in range(400))
        page = "Capítulo único\n\n" + fragmentos
        for draft in self._one_section(page):
            if draft.chunk_type not in INDIVISIBLE_CHUNK_TYPES:
                self.assertLessEqual(draft.char_count, MAX_CHARS, draft.char_count)

    def test_an_absurdly_large_indivisible_unit_is_still_bounded(self):
        """A excecao e para unidade COMPROVADAMENTE indivisivel. Um exercicio
        de 70.990 caracteres - medido no SuperAcao - nao e um exercicio: e
        artefato da heuristica do parser. Alem do teto, a alegacao de
        indivisibilidade deixa de ser cridivel."""
        from agente_ia_edu.services.knowledge_engine.chunking import (
            INDIVISIBLE_CEILING_CHARS,
            bound_indivisible,
        )

        pieces = bound_indivisible("x " * 60000)
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLessEqual(len(piece), INDIVISIBLE_CEILING_CHARS)

    def test_an_indivisible_unit_under_the_ceiling_stays_whole(self):
        from agente_ia_edu.services.knowledge_engine.chunking import bound_indivisible

        body = "x" * (MAX_CHARS + 500)
        self.assertEqual(bound_indivisible(body), [body])

    def test_the_ceiling_is_applied_in_the_real_emission_path(self):
        """``bound_indivisible`` existir nao basta - tem de estar LIGADO. O
        maior chunk medido no acervo era de 82.022 caracteres."""
        from agente_ia_edu.knowledge_chunking_policy.v1 import INDIVISIBLE_CHUNK_TYPES
        from agente_ia_edu.services.knowledge_engine.chunking import (
            INDIVISIBLE_CEILING_CHARS,
        )

        gigante = "Resolva o exercício a seguir. " * 4000
        page = "Capítulo único\n\n" + gigante
        drafts = self._one_section(page)
        for draft in drafts:
            self.assertLessEqual(
                draft.char_count, INDIVISIBLE_CEILING_CHARS, draft.chunk_type
            )

    def test_an_indivisible_unit_may_stay_whole_but_is_marked(self):
        """A excecao existe e e OBSERVAVEL - cortar um exemplo resolvido ao
        meio produz dois chunks que nao sustentam afirmacao nenhuma."""
        from agente_ia_edu.services.knowledge_engine.chunking import _draft

        draft = _draft(
            ordinal=1,
            chunk_type="WORKED_EXAMPLE",
            heading_path=("Capítulo 1",),
            page_start=1,
            page_end=1,
            raw_text="x" * (MAX_CHARS + 500),
            extra={"boundary_approximate": False},
        )
        self.assertTrue(draft.metadata["oversized"])
        self.assertGreater(draft.char_count, MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
