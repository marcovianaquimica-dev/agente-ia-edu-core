"""O CICLO ADAPTATIVO pela API real - os dois achados do teste humano.

Cada teste aqui nasceu de um comportamento medido em 2026-10-05 no banco de
desenvolvimento, pelo caminho que o aluno percorre de verdade:

  ACHADO 1  "desempenho ruim gera mais questoes". Medido:
            ENSINO -> guiada -> pratica 1/5 -> ENSINO -> pratica 1/5 ->
            ENSINO -> ... sem fim. `escalate` acendia no ciclo 4 e ninguem
            o consumia.

  ACHADO 2  "desempenho recente bom nao muda a rota". Medido:
            0/3, 1/5, 4/5, 5/5 -> acumulado 0,556 -> continuava ENSINO.
            Nove acertos nas ultimas dez, e a tela igual.

As letras correspondem a lista de testes obrigatorios do bloco.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.proximo_passo import (
    PASSO_ATIVIDADE,
    PASSO_DIAGNOSTICO,
    PASSO_ENSINO,
    PASSO_ESCALONAMENTO,
    PASSO_GUIADA,
    PASSO_PRATICA,
    PASSO_VERIFICACAO,
)

from test_intervencao_no_readiness import BALANC, IntervencaoNoReadinessTests

# Quantas questoes a base tem no cenario semeado. As tentativas abaixo usam 3
# para ficarem no minimo de amostra da politica - abaixo disso ela se recusa a
# concluir, e um teste que usa amostra insuficiente nao prova nada.
AMOSTRA = 3

INTERVENCOES = (PASSO_ENSINO, PASSO_GUIADA, PASSO_PRATICA, PASSO_VERIFICACAO,
                PASSO_ESCALONAMENTO)


class CicloAdaptativoE2E(IntervencaoNoReadinessTests):
    """Reusa o cenario de `test_intervencao_no_readiness` - mesmo seed, mesma
    atividade, mesmo material publicado, mesmo item guiado."""

    # Os testes herdados ja rodam no arquivo de origem; aqui so interessam os
    # novos. Sem isto a suite executaria o arquivo inteiro duas vezes.
    for _nome in list(vars(IntervencaoNoReadinessTests)):
        if _nome.startswith("test_"):
            locals()[_nome] = None
    del _nome

    # -- utilidades deste arquivo ------------------------------------------

    def _ciclo(self) -> int:
        return ((self._passo().get("intervention") or {}).get("cycle"))

    def _tendencia(self) -> str | None:
        return ((self._passo().get("intervention") or {}).get("trend"))

    def _ate_praticar(self):
        """Leva o aluno ate o ponto em que o passo e praticar sozinho."""
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        self._estudar()
        passo = self._passo()
        if passo["kind"] == PASSO_GUIADA:
            self._concluir_guiada(passo["item_key"])

    def _concluir_guiada(self, item_key: str):
        for _ in range(4):
            self.client.post(f"/api/v1/student/guided-practice/{item_key}/hint")
        for op in ("A", "B", "C", "D", "E"):
            r = self.client.post(
                f"/api/v1/student/guided-practice/{item_key}/answer",
                json={"selected_option": op})
            if r.status_code == 200 and r.json().get("completed"):
                return
        self.fail("a pratica guiada nao concluiu")

    # == A - diagnostico ruim nao libera ===================================

    def test_A_diagnostico_ruim_nao_libera_a_atividade(self):
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        self.assertNotEqual(self._passo()["kind"], PASSO_ATIVIDADE)

    # == B - nao ha loop infinito de PRATICA ===============================

    def test_B_praticar_mal_nao_devolve_sempre_mais_questoes(self):
        """O achado 1, em forma de teste: a estrategia precisa VARIAR."""
        self._ate_praticar()
        vistos = []
        for _ in range(5):
            passo = self._passo()
            vistos.append(passo["kind"])
            if passo["kind"] == PASSO_ESCALONAMENTO:
                break
            if passo["kind"] == PASSO_ENSINO:
                self._estudar()
                continue
            if passo["kind"] == PASSO_GUIADA:
                self._concluir_guiada(passo["item_key"])
                continue
            self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        self.assertIn(PASSO_ESCALONAMENTO, vistos,
                      f"errar sempre nunca terminou: {vistos}")
        self.assertLessEqual(
            vistos.count(PASSO_PRATICA), 4,
            f"so ofereceu mais questoes, uma atras da outra: {vistos}")

    def test_B_o_passo_nunca_fica_vazio(self):
        self._ate_praticar()
        for _ in range(6):
            passo = self._passo()
            self.assertIn(passo["kind"],
                          INTERVENCOES + (PASSO_DIAGNOSTICO, PASSO_ATIVIDADE))
            self.assertTrue((passo.get("cta") or "").strip(),
                            f"passo sem botao: {passo.get('kind')} / "
                            f"{passo.get('state')}")
            if passo["kind"] in (PASSO_ESCALONAMENTO, PASSO_ATIVIDADE):
                break
            if passo["kind"] == PASSO_ENSINO:
                self._estudar()
            elif passo["kind"] == PASSO_GUIADA:
                self._concluir_guiada(passo["item_key"])
            else:
                self._responder(BALANC, quantas=AMOSTRA, acertos=0)

    # == C - depois de pratica ruim ha intervencao coerente ================

    def test_C_depois_de_praticar_mal_a_intervencao_muda(self):
        self._ate_praticar()
        antes = self._passo()["kind"]
        self.assertEqual(antes, PASSO_PRATICA)
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        self.assertNotEqual(self._passo()["kind"], antes,
                            "repetiu exatamente a estrategia que falhou")

    def test_C_a_intervencao_nomeia_a_micro_habilidade(self):
        self._ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        inter = self._passo().get("intervention") or {}
        self.assertTrue(inter.get("skill"), "interveio sem dizer no que")

    # == D/E - a recuperacao e reconhecida e passa por verificacao =========

    def test_D_uma_pratica_forte_nao_e_ignorada(self):
        self._ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        self._avancar_ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self.assertEqual(self._tendencia(), "RECUPERANDO")

    def test_E_a_recuperacao_leva_a_VERIFICACAO(self):
        self._ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        self._avancar_ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self.assertEqual(self._passo()["kind"], PASSO_VERIFICACAO)

    def test_E_a_verificacao_e_curta(self):
        self._ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        passo = self._passo()
        self.assertEqual(passo["kind"], PASSO_VERIFICACAO)
        self.assertLessEqual(passo.get("question_count") or 99, AMOSTRA + 2,
                             "verificacao nao e outra pratica com outro nome")

    def test_F_verificacao_boa_libera(self):
        self._ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self.assertEqual(self._passo()["kind"], PASSO_VERIFICACAO)
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self.assertNotIn(self._passo()["kind"], INTERVENCOES,
                         "confirmou duas vezes e continuou na preparacao")

    def test_F_a_confirmacao_e_DITA_ao_aluno(self):
        """Medido no navegador: ao confirmar, a tela mostrava

            VERIFICAÇÃO CONCLUÍDA
            Você acertou 3 de 3.
            Suas respostas foram registradas.

        - o texto de reserva. Justamente no momento que o aluno esperou o
        bloco inteiro, ninguem disse que a base ficou firme. O `feedback` so
        era montado para passos de intervencao, e depois de confirmar o passo
        deixa de ser um.
        """
        # DUAS tentativas ruins antes, de proposito: com uma so, 6 de 9 ja
        # passa o corte acumulado e o aluno sai pela media - sem passar pela
        # confirmacao, que e o que este teste quer ver. Com duas, 6 de 12
        # continua abaixo do corte e e a TRAJETORIA que o libera.
        self._ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        self._avancar_ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        passo = self._passo()
        fb = passo.get("feedback") or {}
        self.assertTrue((fb.get("titulo") or "").strip(),
                        f"confirmou e a tela nao teve o que dizer: {passo}")
        self.assertEqual(fb.get("tom"), "BOM")

    def test_G_verificacao_ruim_volta_para_a_intervencao(self):
        self._ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self.assertEqual(self._passo()["kind"], PASSO_VERIFICACAO)
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        self.assertIn(self._passo()["kind"], INTERVENCOES)
        self.assertNotEqual(self._passo()["kind"], PASSO_VERIFICACAO)

    def test_P_uma_tentativa_boa_nao_apaga_o_historico(self):
        """Um 3/3 isolado nao pode liberar: ele leva a VERIFICAR, nao a passar."""
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        self._estudar()
        passo = self._passo()
        if passo["kind"] == PASSO_GUIADA:
            self._concluir_guiada(passo["item_key"])
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self.assertEqual(self._passo()["kind"], PASSO_VERIFICACAO,
                         "uma tentativa boa liberou a atividade sozinha")

    # == H/I/J - o que NAO e evidencia =====================================

    def test_H_ler_nao_muda_a_decisao_de_dominio(self):
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        antes = self._dominio(BALANC)
        self._estudar()
        self.assertEqual(self._dominio(BALANC), antes,
                         "ler mexeu no dominio")

    def test_J_a_guiada_nao_muda_a_decisao_de_dominio(self):
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        self._estudar()
        passo = self._passo()
        if passo["kind"] != PASSO_GUIADA:
            self.skipTest("o cenario nao ofereceu guiada neste ponto")
        antes = self._dominio(BALANC)
        self._concluir_guiada(passo["item_key"])
        self.assertEqual(self._dominio(BALANC), antes,
                         "conseguir com ajuda virou dominio")

    def test_K_a_pratica_autonoma_produz_evidencia(self):
        antes = self._dominio(BALANC)
        self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        self.assertNotEqual(self._dominio(BALANC), antes)

    def test_L_a_verificacao_respondida_produz_evidencia(self):
        self._ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self.assertEqual(self._passo()["kind"], PASSO_VERIFICACAO)
        antes = self._dominio(BALANC)
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self.assertNotEqual(self._dominio(BALANC), antes,
                            "verificar nao gravou nada: nao verificou")

    # == N/O/T - os extremos ===============================================

    def test_N_quem_ja_sabe_nao_recebe_intervencao(self):
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self.assertNotIn(self._passo()["kind"], (PASSO_ENSINO, PASSO_GUIADA),
                         "interveio em quem acertou tudo")

    def test_O_a_dificuldade_persistente_chega_a_escalate(self):
        self._ate_praticar()
        for _ in range(6):
            passo = self._passo()
            if passo["kind"] == PASSO_ESCALONAMENTO:
                break
            if passo["kind"] == PASSO_ENSINO:
                self._estudar()
            elif passo["kind"] == PASSO_GUIADA:
                self._concluir_guiada(passo["item_key"])
            else:
                self._responder(BALANC, quantas=AMOSTRA, acertos=0)
        passo = self._passo()
        self.assertEqual(passo["kind"], PASSO_ESCALONAMENTO)
        self.assertTrue((passo.get("intervention") or {}).get("escalate"))

    def test_T_o_caminho_de_quem_ja_sabe_nao_regrediu(self):
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        self.assertNotIn(self._passo()["kind"], INTERVENCOES)

    # == S - retomada ======================================================

    def test_S_reler_a_prontidao_devolve_o_mesmo_passo(self):
        self._ate_praticar()
        self._responder(BALANC, quantas=AMOSTRA, acertos=AMOSTRA)
        primeiro = self._passo()
        segundo = self._passo()
        self.assertEqual(primeiro["kind"], segundo["kind"])
        self.assertEqual(primeiro.get("content_code"),
                         segundo.get("content_code"))

    # -- utilidades extras -------------------------------------------------

    def _dominio(self, codigo: str):
        r = self.client.get(f"/api/v1/student/domain/content/{codigo}")
        if r.status_code != 200:
            return None
        c = r.json().get("content") or {}
        return (c.get("questions_answered"), c.get("questions_correct"))

    def _avancar_ate_praticar(self):
        """Consome ensino/guiada ate o passo voltar a ser praticar sozinho."""
        for _ in range(4):
            passo = self._passo()
            if passo["kind"] == PASSO_ENSINO:
                self._estudar()
            elif passo["kind"] == PASSO_GUIADA:
                self._concluir_guiada(passo["item_key"])
            else:
                return
        self.fail("o passo nunca voltou a ser pratica")


if __name__ == "__main__":
    unittest.main()
