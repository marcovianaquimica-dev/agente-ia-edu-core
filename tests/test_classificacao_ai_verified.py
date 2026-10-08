"""AI_VERIFIED - quando o sistema pode aprovar sozinho.

A decisao de produto de 2026-10-04 inverteu a regra anterior: a IA classifica,
a IA verifica, e o sistema aprova automaticamente quando ha evidencia
suficiente. O humano passa a tratar excecoes.

Isso so e seguro se a palavra "suficiente" tiver um contrato, e nao for um
numero que o modelo diz sobre si mesmo.

POR QUE CONFIANCA NAO APROVA
=============================
`confidence` e o quanto o modelo ACHA que acertou. Nao e o quanto ele acerta.
Um modelo mal calibrado diz 0.97 e erra; um bem calibrado diz 0.7 e acerta.
Aprovar por `confidence >= 0.95` e terceirizar a decisao para a autoestima do
modelo - e ha teste abaixo que falha se alguem fizer isso.

O QUE APROVA
============
Concordancia entre DUAS etapas independentes, sobre uma taxonomia que existe,
numa questao integra, com evidencia curricular. Se qualquer um desses faltar:
REQUIRES_REVIEW. Fail-closed.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.classification_verification import (
    PROV_AI_SUGGESTED,
    PROV_AI_VERIFIED,
    PROV_HUMAN_VALIDATED,
    ClassificacaoCandidata,
    ContratoDeAprovacao,
    Veredito,
    decidir,
)

TAXONOMIA = {
    "CHEMISTRY-PHYSICAL-STOICHIOMETRY",
    "CHEMISTRY-GENERAL-BALANCING",
    "CHEMISTRY-PHYSICAL-ACID-BASE",
    "CHEMISTRY-SOLUTIONS",
}


def _candidata(**kw) -> ClassificacaoCandidata:
    base = dict(
        question_version_id="q1",
        primary="CHEMISTRY-PHYSICAL-STOICHIOMETRY",
        secondary=[],
        confidence=0.88,
        classifier_version="classificador@v1",
    )
    base.update(kw)
    return ClassificacaoCandidata(**base)


def _veredito(**kw) -> Veredito:
    base = dict(
        primary="CHEMISTRY-PHYSICAL-STOICHIOMETRY",
        secondary=[],
        evidencias=["a questao fornece a equacao e pede massa de reagente"],
        verifier_version="verificador@v1",
        integro=True,
    )
    base.update(kw)
    return Veredito(**base)


class ContratoDeAprovacaoTests(unittest.TestCase):

    def _decidir(self, candidata=None, veredito=None, taxonomia=None):
        # `taxonomia or TAXONOMIA` seria errado: set() vazio e falsy, e o caso
        # "taxonomia vazia" cairia no fallback sem testar coisa nenhuma.
        return decidir(candidata or _candidata(), veredito or _veredito(),
                       taxonomia=TAXONOMIA if taxonomia is None else taxonomia,
                       contrato=ContratoDeAprovacao.default())

    # -- A: AI_VERIFIED nunca vira HUMAN_VALIDATED -------------------------

    def test_a_decisao_automatica_nunca_produz_validacao_humana(self):
        for _ in range(3):
            r = self._decidir()
            self.assertNotEqual(r.provenance, PROV_HUMAN_VALIDATED)
        self.assertEqual(self._decidir().provenance, PROV_AI_VERIFIED)

    def test_o_decisor_nao_sabe_sequer_produzir_validacao_humana(self):
        """Se o caminho automatico nunca pode emitir HUMAN_VALIDATED, nao
        adianta alguem 'esquecer' de checar depois."""
        import inspect

        from agente_ia_edu.services import classification_verification

        fonte = inspect.getsource(classification_verification.decidir)
        self.assertNotIn(PROV_HUMAN_VALIDATED, fonte,
                         "o caminho automatico menciona validacao humana")

    # -- B: divergencia entre etapas ---------------------------------------

    def test_primary_divergente_vai_para_revisao(self):
        r = self._decidir(veredito=_veredito(primary="CHEMISTRY-SOLUTIONS"))
        self.assertEqual(r.provenance, PROV_AI_SUGGESTED)
        self.assertTrue(r.requires_review)
        self.assertIn("divergencia", " ".join(r.motivos).lower())

    def test_concordancia_no_primary_e_obrigatoria_mesmo_com_secundarios_iguais(self):
        r = self._decidir(
            candidata=_candidata(primary="CHEMISTRY-SOLUTIONS",
                                 secondary=["CHEMISTRY-PHYSICAL-ACID-BASE"]),
            veredito=_veredito(primary="CHEMISTRY-PHYSICAL-STOICHIOMETRY",
                               secondary=["CHEMISTRY-PHYSICAL-ACID-BASE"]))
        self.assertTrue(r.requires_review)

    # -- C: taxonomia inexistente ------------------------------------------

    def test_codigo_fora_da_taxonomia_vai_para_revisao(self):
        r = self._decidir(
            candidata=_candidata(primary="CHEMISTRY-INVENTADO"),
            veredito=_veredito(primary="CHEMISTRY-INVENTADO"))
        self.assertTrue(r.requires_review)
        self.assertIn("taxonomia", " ".join(r.motivos).lower())

    def test_secundario_fora_da_taxonomia_tambem_barra(self):
        r = self._decidir(
            candidata=_candidata(secondary=["NAO-EXISTE"]),
            veredito=_veredito(secondary=["NAO-EXISTE"]))
        self.assertTrue(r.requires_review)

    # -- D: questao corrompida ---------------------------------------------

    def test_questao_sem_integridade_nao_autoaprova(self):
        r = self._decidir(veredito=_veredito(integro=False,
                                             problemas=["alternativas nao batem"]))
        self.assertTrue(r.requires_review)
        self.assertIn("integridade", " ".join(r.motivos).lower())

    # -- E/F/G: multirrotulo ------------------------------------------------

    def test_multirrotulo_e_preservado_quando_as_duas_etapas_concordam(self):
        sec = ["CHEMISTRY-PHYSICAL-ACID-BASE"]
        r = self._decidir(candidata=_candidata(secondary=sec),
                          veredito=_veredito(secondary=sec))
        self.assertEqual(r.provenance, PROV_AI_VERIFIED)
        self.assertEqual(r.secondary, sec)

    def test_ha_exatamente_um_primary(self):
        r = self._decidir(candidata=_candidata(
            secondary=["CHEMISTRY-PHYSICAL-STOICHIOMETRY"]))
        self.assertTrue(r.requires_review,
                        "o primary repetido no secondary passou")

    def test_secundario_que_so_uma_etapa_viu_nao_entra_sozinho(self):
        """Mera mencao nao vira SECONDARY: se a verificacao independente nao
        confirmou, o rotulo nao e aprovado - mas o primary pode seguir."""
        r = self._decidir(candidata=_candidata(secondary=["CHEMISTRY-SOLUTIONS"]),
                          veredito=_veredito(secondary=[]))
        self.assertEqual(r.secondary, [],
                         "um rotulo que so o classificador viu foi aprovado")
        self.assertEqual(r.provenance, PROV_AI_VERIFIED,
                         "divergencia em SECONDARY nao deveria derrubar o PRIMARY")

    # -- H: confianca sozinha nao aprova -----------------------------------

    def test_confianca_altissima_nao_salva_uma_divergencia(self):
        r = self._decidir(candidata=_candidata(confidence=0.999),
                          veredito=_veredito(primary="CHEMISTRY-SOLUTIONS"))
        self.assertTrue(r.requires_review)

    def test_confianca_baixa_nao_derruba_um_caso_integro_e_concordante(self):
        """O contrato e estrutural. Confianca declarada nao e criterio em
        nenhuma das duas direcoes."""
        r = self._decidir(candidata=_candidata(confidence=0.10))
        self.assertEqual(r.provenance, PROV_AI_VERIFIED)

    def test_nao_existe_limiar_de_confianca_no_codigo(self):
        import ast
        import inspect

        from agente_ia_edu.services import classification_verification

        arvore = ast.parse(inspect.getsource(classification_verification))
        comparacoes = [
            n for n in ast.walk(arvore)
            if isinstance(n, ast.Compare)
            and isinstance(n.left, ast.Attribute)
            and n.left.attr == "confidence"
        ]
        self.assertEqual(comparacoes, [],
                         "ha comparacao de `confidence` com um limiar - "
                         "confianca declarada nao e precisao medida")

    # -- I: evidencia insuficiente -----------------------------------------

    def test_sem_evidencia_curricular_vai_para_revisao(self):
        r = self._decidir(veredito=_veredito(evidencias=[]))
        self.assertTrue(r.requires_review)
        self.assertIn("evidencia", " ".join(r.motivos).lower())

    # -- J: proveniencia e versoes persistidas ------------------------------

    def test_a_decisao_carrega_quem_classificou_e_quem_verificou(self):
        r = self._decidir()
        self.assertEqual(r.classifier_version, "classificador@v1")
        self.assertEqual(r.verifier_version, "verificador@v1")
        self.assertTrue(r.evidencias)
        self.assertTrue(r.motivos, "toda decisao precisa dizer por que")

    def test_a_decisao_de_revisao_tambem_diz_por_que(self):
        r = self._decidir(veredito=_veredito(primary="CHEMISTRY-SOLUTIONS"))
        self.assertTrue(r.motivos)

    # -- fail-closed --------------------------------------------------------

    def test_varios_problemas_ao_mesmo_tempo_sao_todos_reportados(self):
        r = self._decidir(
            candidata=_candidata(primary="NAO-EXISTE"),
            veredito=_veredito(primary="CHEMISTRY-SOLUTIONS", evidencias=[],
                               integro=False, problemas=["texto truncado"]))
        self.assertTrue(r.requires_review)
        self.assertGreaterEqual(len(r.motivos), 3,
                                f"so reportou {r.motivos}")

    def test_taxonomia_vazia_nao_aprova_nada(self):
        r = self._decidir(taxonomia=set())
        self.assertTrue(r.requires_review)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class BuracosEncontradosNaVarreduraTests(unittest.TestCase):
    """Tres defeitos que so apareceram ao rodar o contrato em 112 questoes
    reais. Nenhum deles aparecia nos testes sinteticos acima, porque eu havia
    imaginado as entradas em vez de observa-las.
    """

    def _decidir(self, candidata, veredito, taxonomia=None):
        return decidir(candidata, veredito,
                       taxonomia=TAXONOMIA if taxonomia is None else taxonomia,
                       contrato=ContratoDeAprovacao.default())

    # -- 1: concordancia forcada nao e evidencia ---------------------------

    def test_duas_etapas_sem_opcao_valida_nao_produzem_evidencia(self):
        """A varredura aprovou 'x2 + bx + 49 = 0' como conteudo de Quimica.

        As duas etapas receberam SO codigos de Quimica. Diante de uma questao
        de Matematica nao havia resposta certa possivel, as duas escolheram a
        menos ruim, escolheram a MESMA, e o contrato leu isso como
        concordancia. Convergencia entre duas escolhas impossiveis nao e
        evidencia de nada.
        """
        from agente_ia_edu.services.classification_verification import FORA_DO_ESCOPO

        r = self._decidir(
            _candidata(primary=FORA_DO_ESCOPO),
            _veredito(primary=FORA_DO_ESCOPO,
                      evidencias=["a questao e de matematica"]))
        self.assertTrue(r.requires_review)
        self.assertIn("escopo", " ".join(r.motivos).lower())

    def test_uma_etapa_so_dizendo_fora_do_escopo_ja_basta_para_barrar(self):
        from agente_ia_edu.services.classification_verification import FORA_DO_ESCOPO

        r = self._decidir(_candidata(primary="CHEMISTRY-PHYSICAL-STOICHIOMETRY"),
                          _veredito(primary=FORA_DO_ESCOPO))
        self.assertTrue(r.requires_review)

    # -- 2: primary vazio ---------------------------------------------------

    def test_primary_vazio_nao_passa(self):
        """A varredura aprovou DUAS questoes com primary vazio - uma de
        sociologia, uma em ingles. O filtro de taxonomia tinha um `if c` que
        pulava a string vazia, entao ela nunca era comparada com nada."""
        for vazio in ("", "   ", None):
            with self.subTest(valor=vazio):
                r = self._decidir(_candidata(primary=vazio or ""),
                                  _veredito(primary=vazio or ""))
                self.assertTrue(r.requires_review,
                                f"primary {vazio!r} foi aprovado")

    def test_secundario_vazio_tambem_nao_entra(self):
        r = self._decidir(_candidata(secondary=["", "CHEMISTRY-SOLUTIONS"]),
                          _veredito(secondary=["", "CHEMISTRY-SOLUTIONS"]))
        self.assertNotIn("", r.secondary)

    # -- 3: dependencia visual ---------------------------------------------

    def test_questao_que_depende_de_figura_nao_autoaprova(self):
        """A varredura aprovou 'o aparato ilustrado na figura'. O Player nao
        entrega figura: o aluno receberia uma questao impossivel."""
        r = self._decidir(_candidata(),
                          _veredito(depende_de_figura=True))
        self.assertTrue(r.requires_review)
        self.assertIn("figura", " ".join(r.motivos).lower())

    def test_sem_figura_segue_aprovando(self):
        r = self._decidir(_candidata(), _veredito(depende_de_figura=False))
        self.assertEqual(r.provenance, PROV_AI_VERIFIED)
