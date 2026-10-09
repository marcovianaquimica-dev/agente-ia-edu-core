"""OS SETE ESTADOS DO §6 — e o único que comprova domínio.

O QUE A AUDITORIA DE 2026-10-08 ENCONTROU
==========================================
As garantias existiam, espalhadas e provadas uma a uma: `solved_unaided`
separa resolver de resolver sozinho, `hints_used` e `help_requests` registram
a ajuda, `escada_de_apoio.produz_evidencia` libera só o degrau autônomo, e a
conversa não recebe sessão de banco.

O que não existia era a lista. O §6 pede que o sistema DISTINGA sete
estados, e distinguir exige nomeá-los num lugar só — senão a invariante
("entendi" não é domínio) é a soma de quatro garantias que ninguém lê junto,
e o oitavo caminho que alguém escrever amanhã não encontra onde se encaixar.

O QUE ESTE ARQUIVO TRAVA
=========================
Que EXATAMENTE UM dos sete produza evidência de domínio. Não é uma
conveniência de teste: é a única regra que, se cair, transforma "o aluno
disse que entendeu" em "o aluno domina".

E que a ordem dos estados seja a da progressão real — conteúdo apresentado
vem antes de compreensão manifestada, que vem antes de aplicação com ajuda.
Um estado fora de ordem significaria que alguém o entendeu errado.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.estados_de_aprendizagem import (
    APLICACAO_ASSISTIDA,
    APLICACAO_INDEPENDENTE,
    COMPREENSAO_MANIFESTADA,
    CONSOLIDACAO,
    CONTEUDO_APRESENTADO,
    DOMINIO_DEMONSTRADO,
    ESTADOS,
    RETENCAO,
    anterior_a,
    produz_evidencia_de_dominio,
    rotulo_do_estado,
)


class OSSETEESTADOS(unittest.TestCase):

    def test_sao_sete(self):
        self.assertEqual(7, len(ESTADOS))

    def test_e_estao_na_ordem_da_progressao(self):
        self.assertEqual(
            (CONTEUDO_APRESENTADO, COMPREENSAO_MANIFESTADA,
             APLICACAO_ASSISTIDA, APLICACAO_INDEPENDENTE,
             DOMINIO_DEMONSTRADO, CONSOLIDACAO, RETENCAO),
            ESTADOS)

    def test_nenhum_nome_se_repete(self):
        self.assertEqual(len(ESTADOS), len(set(ESTADOS)))

    def test_todos_tem_rotulo_de_aluno(self):
        """Nada de "evidência", "banda" ou "mastery" na tela."""
        for e in ESTADOS:
            with self.subTest(e):
                r = rotulo_do_estado(e)
                self.assertTrue(r)
                for jargao in ("evidência", "banda", "mastery", "readiness"):
                    self.assertNotIn(jargao, r.lower())


class SOUMPRODUZEVIDENCIADEDOMINIO(unittest.TestCase):
    """A invariante inteira do módulo."""

    def test_exatamente_um(self):
        quantos = sum(1 for e in ESTADOS if produz_evidencia_de_dominio(e))
        self.assertEqual(1, quantos)

    def test_e_ele_e_a_aplicacao_INDEPENDENTE(self):
        self.assertTrue(produz_evidencia_de_dominio(APLICACAO_INDEPENDENTE))

    def test_ter_visto_o_conteudo_NAO_comprova(self):
        self.assertFalse(produz_evidencia_de_dominio(CONTEUDO_APRESENTADO))

    def test_dizer_que_entendeu_NAO_comprova(self):
        """O §6, textualmente: "entendi" não comprova domínio."""
        self.assertFalse(produz_evidencia_de_dominio(COMPREENSAO_MANIFESTADA))

    def test_acertar_COM_AJUDA_nao_comprova(self):
        """O §6: resposta correta após dica não é independente."""
        self.assertFalse(produz_evidencia_de_dominio(APLICACAO_ASSISTIDA))

    def test_e_os_tres_posteriores_tambem_nao_PRODUZEM(self):
        """Domínio, consolidação e retenção são CONCLUSÕES sobre evidência.

        Eles não são um acontecimento no qual o aluno responde algo — são o
        que a política conclui depois. Tratá-los como produtores faria a
        conclusão virar sua própria prova.
        """
        for e in (DOMINIO_DEMONSTRADO, CONSOLIDACAO, RETENCAO):
            with self.subTest(e):
                self.assertFalse(produz_evidencia_de_dominio(e))

    def test_estado_desconhecido_nao_produz(self):
        """Fail-closed: o que o módulo não conhece não comprova nada."""
        self.assertFalse(produz_evidencia_de_dominio("ALGO_NOVO"))
        self.assertFalse(produz_evidencia_de_dominio(None))


class AORDEMEUTIL(unittest.TestCase):

    def test_conteudo_vem_antes_de_compreensao(self):
        self.assertTrue(anterior_a(CONTEUDO_APRESENTADO,
                                   COMPREENSAO_MANIFESTADA))

    def test_assistida_vem_antes_de_independente(self):
        self.assertTrue(anterior_a(APLICACAO_ASSISTIDA,
                                   APLICACAO_INDEPENDENTE))

    def test_retencao_nao_vem_antes_de_nada(self):
        for e in ESTADOS:
            with self.subTest(e):
                self.assertFalse(anterior_a(RETENCAO, e))

    def test_desconhecido_nao_ordena(self):
        self.assertFalse(anterior_a("X", APLICACAO_INDEPENDENTE))
        self.assertFalse(anterior_a(APLICACAO_INDEPENDENTE, "X"))


class OMODULONAODECIDEPEDAGOGIA(unittest.TestCase):
    """Ele NOMEIA estados. Não lê banco, não decide passo, não mede nada."""

    def _fonte(self) -> str:
        import pathlib

        from _fonte import codigo

        raiz = pathlib.Path(__file__).resolve().parent.parent
        return codigo(raiz
                      / "src/agente_ia_edu/services/estados_de_aprendizagem.py")

    def test_nao_importa_banco(self):
        for proibido in ("sqlalchemy", "AsyncSession", "select("):
            with self.subTest(proibido):
                self.assertNotIn(proibido, self._fonte())

    def test_e_nao_decide_passo(self):
        self.assertNotIn("PASSO_", self._fonte())


class ELESCOMBINAMCOMOQUEJAEXISTE(unittest.TestCase):
    """O módulo nomeia o que o produto já garantia — não uma segunda regra.

    Se estas duas asserções falharem, há DUAS verdades sobre o que comprova
    domínio, e elas vão divergir no primeiro ajuste.
    """

    def test_a_aplicacao_independente_e_o_degrau_AUTONOMO_da_escada(self):
        from agente_ia_edu.services.escada_de_apoio import (
            NIVEIS,
            produz_evidencia,
        )

        autonomos = [n for n in NIVEIS if produz_evidencia(n)]
        self.assertEqual(1, len(autonomos),
                         "a escada mudou de ideia sobre quem comprova")

    def test_e_a_guiada_continua_fora_da_evidencia(self):
        from agente_ia_edu.services.escada_de_apoio import (
            NIVEL_GUIADA,
            produz_evidencia,
        )

        self.assertFalse(produz_evidencia(NIVEL_GUIADA))
        self.assertFalse(produz_evidencia_de_dominio(APLICACAO_ASSISTIDA))


if __name__ == "__main__":
    unittest.main()
