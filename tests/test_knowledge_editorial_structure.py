"""CEREBRO - Fase 5.1b: `editorial_role` - deteccao em tres camadas.

ARQUITETURA, E A MEDICAO QUE A IMPOE
====================================

**Camada 1, REGIAO.** O manual do professor e uma regiao CONTIGUA, e isso foi
medido nas tres obras: p477-543 em Investigar (pureza 99%), p467-543 em
Cotidiano (97%), p465-541 em SuperAcao (90%). A regiao e derivada de DENSIDADE
DE MARCADORES, nunca de posicao - a posicao e so onde ela calhou de cair.

E e a regiao que resolve o problema central: medido, **34% a 66% dos chunks do
ultimo 15% de cada livro nao disparam sinal algum** - "Sim, pois faz parte da
ideia...", "Resposta pessoal.", "Possiveis respostas:". Nenhum detector por
chunk isolado os pegaria. Dentro da regiao, eles sao gabarito.

**Camada 2, PERFIL DA OBRA.** Um detector unico seria errado, e a medicao e
categorica:

    marcador            Investigar   Cotidiano   SuperAcao
    MPxxx                        0         188         161
    romano isolado             95p          10          14
    pontilhado >=3               2          91          32
    instrucao ao professor     129          49         113

``MPxxx`` nao existe em Investigar; o pontilhado quase nao existe la. O unico
sinal presente nas tres obras e a instrucao ao professor. Perfis sao ativados
por EVIDENCIA OBSERVADA no documento, nunca por nome de editora.

**Camada 3, SINAIS UNIVERSAIS por chunk.** Refinam dentro da regiao e pegam
ocorrencias fora dela.

POR QUE A INCERTEZA FALHA PARA O LADO DE INCLUIR
================================================

A assimetria e o argumento: um falso positivo ESCONDE conteudo legitimo e
ninguem percebe; um falso negativo apenas mantem o estado atual. Entao
``UNKNOWN`` e ELEGIVEL, e evidencia insuficiente nunca vira exclusao.

Medicao que justifica o rigor: o detector ingenuo da sondagem acertou **5 de
14** (~36%) numa amostra manual de ANSWER_KEY. Um detector assim pioraria o
sistema.

NAO CLASSIFICADO x UNKNOWN
==========================

``editorial_detector_version IS NULL`` significa **nao processado por esta
versao**. ``role = 'UNKNOWN'`` com versao preenchida significa **classificado,
e a evidencia nao bastou**. Sao estados diferentes e nao devem se confundir -
``UNKNOWN`` nao pode esconder ausencia de processamento.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.knowledge_retrieval_policy.v1 import (
    EDITORIAL_ROLES,
    POLICY,
    RETRIEVAL_PURPOSES,
    editorial_role_is_eligible,
    statistical_corpus_roles,
)
from agente_ia_edu.services.knowledge_engine.editorial_structure import (
    DETECTOR_VERSION,
    EditorialVerdict,
    classify_editorial,
    detect_profiles,
    detect_regions,
    page_signals,
)


class VocabularyTests(unittest.TestCase):
    def test_the_roles_are_the_approved_ones(self):
        self.assertEqual(
            set(EDITORIAL_ROLES),
            {
                "CONTENT",
                "TABLE_OF_CONTENTS",
                "INDEX",
                "ANSWER_KEY",
                "TEACHER_GUIDE",
                "REFERENCES",
                "FRONT_MATTER",
                "BACK_MATTER",
                "UNKNOWN",
            },
        )

    def test_teacher_guide_is_a_role_of_its_own(self):
        """A regiao contem orientacao didatica que NAO e resposta."""
        self.assertIn("TEACHER_GUIDE", EDITORIAL_ROLES)
        self.assertIn("ANSWER_KEY", EDITORIAL_ROLES)

    def test_the_detector_is_versioned(self):
        self.assertEqual(DETECTOR_VERSION, "v1")


class PageSignalTests(unittest.TestCase):
    def test_the_moderna_marker_is_detected(self):
        self.assertIn("mp_marker", page_signals("MP049 Capítulo 3 ....... MP051"))

    def test_an_isolated_roman_numeral_is_detected(self):
        """O marcador de "Investigar e Conhecer", medido em 95 paginas."""
        self.assertIn("roman_folio", page_signals("LXXIX\npropriedades antissépticas"))

    def test_a_roman_numeral_inside_a_sentence_is_not_a_folio(self):
        self.assertNotIn(
            "roman_folio", page_signals("O capítulo XIV trata de cinética.")
        )

    def test_the_alternative_marker_is_case_insensitive(self):
        """Investigar escreve ``Alternativa d.`` minusculo - foi por exigir
        maiuscula que a primeira sondagem deu ZERO naquela obra."""
        for texto in ("Alternativa D. O iodo sofre redução.", "Alternativa d. O iodo."):
            self.assertIn("alternative_answer", page_signals(texto))

    def test_teacher_instruction_is_detected(self):
        for texto in (
            "Ajude os estudantes a sistematizar esse tipo de atividade.",
            "Espera-se que os estudantes reconheçam as geometrias.",
            "Oriente a turma a registrar as observações.",
        ):
            self.assertIn("teacher_instruction", page_signals(texto))

    def test_answer_openers_are_detected(self):
        for texto in (
            "Resposta pessoal. Procure destacar o papel das políticas.",
            "Possíveis respostas: roupas de algodão orgânico.",
            "Resoluções e comentários Aplicando conhecimentos",
        ):
            self.assertIn("answer_opener", page_signals(texto))

    def test_dot_leaders_are_detected(self):
        self.assertIn("dot_leaders", page_signals("Capítulo 5 ........... 120"))

    def test_reference_signals_are_detected(self):
        self.assertIn(
            "reference_apparatus",
            page_signals(
                "EMSLEY, J. Nature's Building Blocks. São Paulo: Moderna, 2011. "
                "ISBN 978-85. Disponível em: http://x. Acesso em: 3 mar. 2024."
            ),
        )

    def test_clean_content_has_no_signal(self):
        self.assertEqual(
            page_signals(
                "A estequiometria relaciona as quantidades de reagentes e "
                "produtos de uma reação química, segundo a equação balanceada."
            ),
            frozenset(),
        )


class ProfileTests(unittest.TestCase):
    """Perfil ativado por EVIDENCIA, nunca por nome de editora."""

    def test_the_moderna_profile_activates_on_observed_markers(self):
        pages = [f"MP{i:03d} Texto do manual docente." for i in range(10)]
        self.assertIn("moderna_mp", detect_profiles(pages))

    def test_the_moderna_profile_stays_off_without_evidence(self):
        pages = ["Texto comum de química geral." for _ in range(400)]
        self.assertNotIn("moderna_mp", detect_profiles(pages))

    def test_one_isolated_marker_does_not_activate_a_profile(self):
        pages = ["MP001 só isso"] + ["Texto comum." for _ in range(300)]
        self.assertNotIn("moderna_mp", detect_profiles(pages))

    def test_the_roman_folio_profile_activates_on_observed_evidence(self):
        pages = [f"LXX{'I' * (i % 4)}\nTexto do manual." for i in range(10)]
        self.assertIn("roman_folio", detect_profiles(pages))

    def test_profiles_are_deterministic(self):
        pages = [f"MP{i:03d} manual" for i in range(10)]
        self.assertEqual(detect_profiles(pages), detect_profiles(pages))


class RegionTests(unittest.TestCase):
    def test_a_contiguous_run_of_marked_pages_becomes_a_region(self):
        pages = ["Química geral e suas aplicações." for _ in range(20)]
        for index in range(12, 20):
            pages[index] = (
                "MP050 Ajude os estudantes a resolver. Alternativa B. correta."
            )
        regions = detect_regions(pages)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].page_start, 13)
        self.assertEqual(regions[0].page_end, 20)

    def test_a_small_gap_does_not_break_a_region(self):
        pages = ["Conteúdo de química." for _ in range(20)]
        marcada = "MP050 Ajude os estudantes. Alternativa B."
        for index in (12, 13, 15, 16, 17):
            pages[index] = marcada
        regions = detect_regions(pages)
        self.assertEqual(len(regions), 1)
        self.assertEqual((regions[0].page_start, regions[0].page_end), (13, 18))

    def test_two_separate_runs_become_two_regions(self):
        """Regioes, no PLURAL: em Cotidiano o sumario do manual (p452-463) e
        uma regiao separada do corpo do manual (p467-543)."""
        pages = ["Conteúdo de química." for _ in range(40)]
        marcada = "MP050 Ajude os estudantes. Alternativa B."
        for index in list(range(5, 9)) + list(range(25, 32)):
            pages[index] = marcada
        regions = detect_regions(pages)
        self.assertEqual(len(regions), 2)

    def test_a_single_marked_page_is_not_a_region(self):
        """Uma pagina solta nao faz regiao - falha para o lado de incluir."""
        pages = ["Conteúdo." for _ in range(20)]
        pages[7] = "MP050 Ajude os estudantes. Alternativa B."
        self.assertEqual(detect_regions(pages), ())

    def test_a_region_carries_its_dominant_signals(self):
        pages = ["Conteúdo de química." for _ in range(20)]
        for index in range(12, 18):
            pages[index] = "Capítulo 5 ......... MP050 ......... MP051"
        regions = detect_regions(pages)
        self.assertIn("dot_leaders", regions[0].signals)

    def test_position_is_never_the_decision(self):
        """O ultimo 15% de um livro SEM marcador nenhum nao vira regiao."""
        pages = [f"Conteúdo legítimo de química, página {i}." for i in range(100)]
        self.assertEqual(detect_regions(pages), ())


class ClassificationTests(unittest.TestCase):
    def _verdict(self, text, **kwargs) -> EditorialVerdict:
        base = {
            "heading_path": (),
            "page": 10,
            "page_count": 100,
            "regions": (),
            "profiles": frozenset(),
            "chunk_type": "PROSE",
        }
        base.update(kwargs)
        return classify_editorial(text, **base)

    def test_plain_content_is_content(self):
        verdict = self._verdict(
            "A estequiometria relaciona as quantidades de reagentes e produtos "
            "de uma reação química a partir da equação balanceada."
        )
        self.assertEqual(verdict.role, "CONTENT")
        self.assertEqual(verdict.signals, ())

    def test_dot_leader_lines_pointing_at_a_folio_make_a_table_of_contents(self):
        verdict = self._verdict(
            "Capítulo 1 Conceitos introdutórios ......... MP017\n"
            "Capítulo 2 Elementos químicos .............. MP021\n"
            "Capítulo 3 Modelos atômicos ................ MP024"
        )
        self.assertEqual(verdict.role, "TABLE_OF_CONTENTS")
        self.assertIn("dot_leaders", verdict.signals)

    def test_an_answer_opener_inside_a_region_is_an_answer_key(self):
        regions = detect_regions(
            ["Conteúdo." for _ in range(10)]
            + ["MP050 Ajude os estudantes. Alternativa B." for _ in range(8)]
        )
        verdict = self._verdict(
            "Resposta pessoal. Procure destacar o papel das políticas públicas.",
            page=14,
            page_count=18,
            regions=regions,
        )
        self.assertEqual(verdict.role, "ANSWER_KEY")

    def test_a_bare_answer_inside_a_region_is_still_an_answer_key(self):
        """O caso que nenhum sinal por chunk pegaria - 34% a 66% dos chunks do
        fim de cada livro. Dentro da regiao, e gabarito."""
        regions = detect_regions(
            ["Conteúdo." for _ in range(10)]
            + ["MP050 Ajude os estudantes. Alternativa B." for _ in range(8)]
        )
        verdict = self._verdict(
            "Sim, pois quanto mais concentrada é a solução, maior é a "
            "quantidade de colisões entre as partículas.",
            page=14,
            page_count=18,
            regions=regions,
        )
        self.assertIn(verdict.role, ("ANSWER_KEY", "TEACHER_GUIDE", "BACK_MATTER"))
        self.assertNotEqual(verdict.role, "CONTENT")
        self.assertIn("region", verdict.decision_reason)

    def test_teacher_guidance_is_not_an_answer_key(self):
        regions = detect_regions(
            ["Conteúdo." for _ in range(10)]
            + ["MP050 Ajude os estudantes. Alternativa B." for _ in range(8)]
        )
        verdict = self._verdict(
            "Incentive os estudantes a identificar informações essenciais "
            "entre diferentes grupos e a negociar divergências de leitura.",
            page=14,
            page_count=18,
            regions=regions,
        )
        self.assertEqual(verdict.role, "TEACHER_GUIDE")

    def test_references_need_their_own_heading(self):
        verdict = self._verdict(
            "REFERÊNCIAS BIBLIOGRÁFICAS\n"
            "EMSLEY, J. Nature's Building Blocks. São Paulo: Moderna, 2011. "
            "ISBN 978-85-16. BURROWS, A. et al. Química."
        )
        self.assertEqual(verdict.role, "REFERENCES")
        self.assertEqual(verdict.decision_reason, "REFERENCE_HEADING")

    def test_citations_inside_content_are_not_references(self):
        """REGRESSAO da amostra real: 185 falsos positivos numa obra so.
        Conteudo legitimo cita fonte em legenda de figura e em atividade -
        "Disponivel em:", "Acesso em:", ISBN. Dois ou tres indicios NAO
        bastam para excluir, porque REFERENCES e fechado em LEARN, PRACTICE
        e ASSESS, e excluir conteudo e o erro caro."""
        verdict = self._verdict(
            "Quantas cores uma caneta tem? Muitos corantes apresentam "
            "compostos classificados como aminas. Disponível em: "
            "http://exemplo.org. Acesso em: 3 mar. 2024. ISBN 978-85."
        )
        self.assertEqual(verdict.role, "CONTENT")

    def test_dot_leaders_in_running_text_are_not_a_table_of_contents(self):
        """REGRESSAO da amostra real: p422 e p427 de "Investigar e Conhecer"
        sao texto corrido sobre polimeros, tinham 3+ corridas de pontos e
        viravam TABLE_OF_CONTENTS - que e fechado em TODO proposito. Uma
        linha de sumario APONTA para um folio; texto corrido nao."""
        verdict = self._verdict(
            "Em função das condições reacionais aplicadas na síntese ........ "
            "pode-se obter polietilenos de diferentes densidades ........ "
            "Os mais comuns são o polietileno de baixa densidade ........"
        )
        self.assertNotEqual(verdict.role, "TABLE_OF_CONTENTS")

    def test_an_imperative_verb_alone_is_not_teacher_guidance(self):
        """REGRESSAO: "Enfatize", "Proponha" e "Convide" soltos pegavam
        atividade DO ALUNO - p45 "Observe a tirinha e faca o que se pede".
        Verbo no imperativo nao identifica destinatario."""
        for texto in (
            "Observe a tirinha e faça o que se pede no caderno.",
            "Proponha uma hipótese para explicar o fenômeno observado.",
            "Enfatize os aspectos quantitativos ao resolver o exercício.",
        ):
            self.assertEqual(self._verdict(texto).role, "CONTENT", texto)

    def test_teacher_guidance_outside_a_region_does_not_exclude(self):
        """Fora de regiao o indicio e fraco, entao o chunk fica UNKNOWN - que
        e ELEGIVEL. A evidencia e registrada, mas nao esconde nada."""
        verdict = self._verdict(
            "Ajude os estudantes a sistematizar esse tipo de atividade "
            "proposta ao longo do capítulo sobre ligações químicas."
        )
        self.assertEqual(verdict.role, "UNKNOWN")
        self.assertEqual(verdict.decision_reason, "TEACHER_SIGNAL_OUTSIDE_REGION")
        self.assertIn("teacher_instruction", verdict.signals)
        from agente_ia_edu.knowledge_retrieval_policy.v1 import (
            editorial_role_is_eligible,
        )

        self.assertTrue(editorial_role_is_eligible(verdict.role, "PRACTICE"))

    def test_an_index_is_detected_by_its_own_heading(self):
        """Declarado no vocabulario e NAO observado nas tres obras - zero
        ocorrencias de "Indice remissivo". Entra como regra, nao como numero."""
        verdict = self._verdict(
            "Índice remissivo\nácido, 45, 88\nbase, 92\ncatalisador, 268"
        )
        self.assertEqual(verdict.role, "INDEX")

    def test_front_matter_needs_both_signal_and_being_at_the_front(self):
        catalogo = (
            "Dados Internacionais de Catalogação na Publicação (CIP). "
            "Todos os direitos reservados. Diretoria editorial."
        )
        inicio = self._verdict(catalogo, page=2, page_count=500)
        self.assertEqual(inicio.role, "FRONT_MATTER")
        # o MESMO texto no meio do livro nao e front matter: posicao e sinal
        # AUXILIAR, e sozinha ela nao decide - mas sem ela este papel nao se
        # sustenta.
        meio = self._verdict(catalogo, page=250, page_count=500)
        self.assertNotEqual(meio.role, "FRONT_MATTER")

    def test_a_solution_chunk_can_also_be_an_answer_key(self):
        """ORTOGONALIDADE: chunk_type e forma/funcao pedagogica local;
        editorial_role e funcao editorial na obra. SOLUTION + ANSWER_KEY e
        combinacao legitima e esperada."""
        regions = detect_regions(
            ["Conteúdo." for _ in range(10)]
            + ["MP050 Ajude os estudantes. Alternativa B." for _ in range(8)]
        )
        verdict = self._verdict(
            "Alternativa B. O cálculo considera a proporção molar correta.",
            page=14,
            page_count=18,
            regions=regions,
            chunk_type="SOLUTION",
        )
        self.assertEqual(verdict.role, "ANSWER_KEY")

    def test_the_verdict_carries_confidence_signals_and_reason(self):
        verdict = self._verdict("Capítulo 1 ....... 10\nCapítulo 2 ....... 20\nCapítulo 3 ....... 30")
        self.assertGreater(verdict.confidence, 0.0)
        self.assertLessEqual(verdict.confidence, 1.0)
        self.assertTrue(verdict.signals)
        self.assertTrue(verdict.decision_reason)
        self.assertEqual(verdict.detector_version, DETECTOR_VERSION)

    def test_conflicting_evidence_yields_unknown_and_not_an_exclusion(self):
        """Evidencia conflitante nao exclui: UNKNOWN e ELEGIVEL."""
        verdict = self._verdict(
            "Índice remissivo ......... 500\n"
            "EMSLEY, J. São Paulo: Moderna. ISBN 978. Disponível em: http://x. "
            "Acesso em: 2024. Ajude os estudantes. Resposta pessoal."
        )
        self.assertIn(verdict.role, ("UNKNOWN", "INDEX", "REFERENCES", "TABLE_OF_CONTENTS"))
        if verdict.role == "UNKNOWN":
            self.assertTrue(editorial_role_is_eligible("UNKNOWN", "PRACTICE"))

    def test_classification_is_idempotent(self):
        texto = "Resposta pessoal. Procure destacar as políticas públicas."
        first = self._verdict(texto)
        second = self._verdict(texto)
        self.assertEqual(first.role, second.role)
        self.assertEqual(first.confidence, second.confidence)
        self.assertEqual(first.signals, second.signals)

    def test_an_empty_chunk_is_unknown_and_not_content(self):
        verdict = self._verdict("")
        self.assertEqual(verdict.role, "UNKNOWN")


class EligibilityTests(unittest.TestCase):
    def test_content_is_eligible_everywhere(self):
        for purpose in RETRIEVAL_PURPOSES:
            self.assertTrue(editorial_role_is_eligible("CONTENT", purpose))

    def test_unknown_is_eligible_everywhere(self):
        """A assimetria: falso positivo esconde conteudo; falso negativo
        apenas mantem o estado atual."""
        for purpose in RETRIEVAL_PURPOSES:
            self.assertTrue(editorial_role_is_eligible("UNKNOWN", purpose))

    def test_an_answer_key_follows_the_solution_rule(self):
        self.assertFalse(editorial_role_is_eligible("ANSWER_KEY", "PRACTICE"))
        self.assertFalse(editorial_role_is_eligible("ANSWER_KEY", "ASSESS"))
        self.assertTrue(editorial_role_is_eligible("ANSWER_KEY", "LEARN"))
        self.assertTrue(editorial_role_is_eligible("ANSWER_KEY", "AUTHOR"))

    def test_an_absent_purpose_closes_the_answer_key(self):
        self.assertFalse(editorial_role_is_eligible("ANSWER_KEY", None))

    def test_a_teacher_guide_is_for_authoring_only(self):
        self.assertTrue(editorial_role_is_eligible("TEACHER_GUIDE", "AUTHOR"))
        self.assertFalse(editorial_role_is_eligible("TEACHER_GUIDE", "LEARN"))
        self.assertFalse(editorial_role_is_eligible("TEACHER_GUIDE", "PRACTICE"))

    def test_navigation_apparatus_is_closed_everywhere(self):
        for role in ("TABLE_OF_CONTENTS", "INDEX", "FRONT_MATTER", "BACK_MATTER"):
            for purpose in RETRIEVAL_PURPOSES:
                self.assertFalse(editorial_role_is_eligible(role, purpose), role)

    def test_every_role_has_a_declared_eligibility(self):
        for role in EDITORIAL_ROLES:
            self.assertIn(role, POLICY.eligibility_by_role)

    def test_an_unknown_role_fails_open_and_that_is_declared(self):
        """Papel que a politica nao conhece e ELEGIVEL - porque esconder
        conteudo e o erro caro. A decisao e explicita, nao acidental."""
        self.assertTrue(editorial_role_is_eligible("PAPEL_NOVO", "PRACTICE"))


class StatisticalCorpusTests(unittest.TestCase):
    """``df``, ``N`` e ``avgdl`` sobre o corpus ELEGIVEL da politica."""

    def test_it_is_derived_from_the_eligibility_table(self):
        """Nao e uma segunda lista para dessincronizar: e o conjunto de papeis
        com pelo menos um proposito aberto."""
        esperado = {
            role
            for role in EDITORIAL_ROLES
            if any(
                editorial_role_is_eligible(role, purpose)
                for purpose in RETRIEVAL_PURPOSES
            )
        }
        self.assertEqual(set(statistical_corpus_roles()), esperado)

    def test_navigation_apparatus_is_out_of_the_statistics(self):
        roles = statistical_corpus_roles()
        for role in ("TABLE_OF_CONTENTS", "INDEX", "FRONT_MATTER", "BACK_MATTER"):
            self.assertNotIn(role, roles)

    def test_content_and_unknown_are_in(self):
        self.assertIn("CONTENT", statistical_corpus_roles())
        self.assertIn("UNKNOWN", statistical_corpus_roles())

    def test_the_answer_key_stays_in_because_learn_can_retrieve_it(self):
        self.assertIn("ANSWER_KEY", statistical_corpus_roles())

    def test_the_definition_is_deterministic_and_ordered(self):
        self.assertEqual(statistical_corpus_roles(), statistical_corpus_roles())
        self.assertEqual(
            list(statistical_corpus_roles()), sorted(statistical_corpus_roles())
        )

    def test_it_is_part_of_the_policy_snapshot(self):
        snapshot = POLICY.snapshot()
        self.assertIn("eligibility_by_role", snapshot)
        self.assertIn("editorial_detector_version", snapshot)


if __name__ == "__main__":
    unittest.main()
