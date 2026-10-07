"""O CASO CANÔNICO NH₃ — §5 do bloco, caminho por caminho.

    EDU:   "Qual é a massa molar do NH₃? N = 14 g/mol, H = 1 g/mol."
    ALUNO: 15

15 é compatível com 14 + 1 — a massa do N mais a de UM hidrogênio, com o
índice fora da conta. É a hipótese mais útil que o número permite.

E é só uma hipótese. Quem chutou também escreve 15.

O QUE ESTE ARQUIVO TRAVA
=========================
Que a cadeia inteira — abertura aberta, gatilho, hipótese hedgeada,
discriminante, bifurcação — exista no DOMÍNIO, e não no texto de uma tela
ou num prompt. Os quatro caminhos do §5 são testados aqui; o percurso no
navegador é o que prova que eles chegam ao aluno.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    LEITURA_FORMULA,
    MASSA_MOLAR,
)
from agente_ia_edu.services.hipotese_pedagogica import (
    ABERTA,
    APOIADA,
    ENFRAQUECIDA,
    aponta_para_prerequisito,
    atualizar,
)
from agente_ia_edu.services.investigacao_do_erro import (
    conferir,
    hipotese_para,
    investigacao_para,
)
from agente_ia_edu.services.massa_molar import contribuicoes, massa_molar
from agente_ia_edu.services.resposta_do_aluno import (
    ESPERA_NUMERO,
    OBS_AMBIGUA,
    OBS_CORRETA,
    OBS_INCORRETA,
    OBS_NAO_SEI,
    normalizar,
    observar,
)

INV = investigacao_para(CONTEUDO, MASSA_MOLAR)


def _responder_abertura(texto: str):
    """O que o sistema faz com o que o aluno escreveu na abertura."""
    r = normalizar(texto, espera=INV.abertura.espera)
    obs = observar(r, esperado_numero=INV.abertura.resposta)
    return r, obs, hipotese_para(INV, r.numero)


class AABERTURAEABERTA(unittest.TestCase):

    def test_a_pergunta_existe_e_pede_numero(self):
        self.assertIsNotNone(INV.abertura)
        self.assertEqual(ESPERA_NUMERO, INV.abertura.espera)

    def test_a_pergunta_da_as_massas_atomicas(self):
        """Sem elas, errar poderia significar "não sei calcular" ou "não
        lembro o valor do N", e o sistema não saberia qual."""
        self.assertIn("14", INV.abertura.pergunta)
        self.assertIn("1 g/mol", INV.abertura.pergunta)

    def test_a_resposta_certa_e_refeita_por_conta(self):
        self.assertAlmostEqual(massa_molar("NH3"), INV.abertura.resposta)

    def test_as_contas_do_conteudo_fecham(self):
        self.assertEqual([], conferir())


class QUINZEGERAHIPOTESEENAOCERTEZA(unittest.TestCase):
    """§4 — a invariante crítica."""

    def test_quinze_e_observado_como_incorreto(self):
        _, obs, _ = _responder_abertura("15")
        self.assertEqual(OBS_INCORRETA, obs)

    def test_quinze_produz_uma_hipotese(self):
        _, _, h = _responder_abertura("15")
        self.assertIsNotNone(h)
        self.assertEqual("INDEX_OMISSION", h.codigo)

    def test_o_valor_15_e_refeito_e_nao_escrito_a_mao(self):
        """14 + 1 — as massas atômicas, sem aplicar o índice."""
        por_elemento = {c[0]: c[2] for c in contribuicoes("NH3")}
        self.assertAlmostEqual(por_elemento["N"] + por_elemento["H"], 15.0)

    def test_a_hipotese_NASCE_ABERTA(self):
        self.assertEqual(ABERTA, atualizar(ABERTA, discriminante_correta=None))

    def test_a_frase_do_aluno_SUPOE_e_nao_afirma(self):
        _, _, h = _responder_abertura("15")
        baixo = h.como_dizer.lower()
        self.assertTrue(
            any(m in baixo for m in ("pode indicar", "pode ter", "sugere",
                                     "vamos conferir")),
            h.como_dizer)

    def test_a_frase_NAO_acusa(self):
        _, _, h = _responder_abertura("15")
        baixo = h.como_dizer.lower()
        for proibido in ("você esqueceu", "você não", "você multiplicou",
                         "você ignorou", "seu erro", "você fez"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, baixo)

    def test_um_numero_errado_SEM_gatilho_nao_inventa_hipotese(self):
        """O sistema só supõe onde alguém escreveu qual suposição aquele
        número sustenta."""
        _, obs, h = _responder_abertura("99")
        self.assertEqual(OBS_INCORRETA, obs)
        self.assertIsNone(h)

    def test_a_discriminante_e_a_pergunta_sobre_a_leitura(self):
        _, _, h = _responder_abertura("15")
        etapa = next(e for e in INV.etapas if e.ordem == h.discriminante)
        self.assertEqual(LEITURA_FORMULA, etapa.habilidade)
        self.assertIn("hidrog", etapa.pergunta.lower())


class CAMINHOA_H_IGUAL_3(unittest.TestCase):
    """Ele conta os três. A hipótese de falha básica enfraquece."""

    def test_acertar_a_discriminante_enfraquece_a_hipotese(self):
        _, _, h = _responder_abertura("15")
        self.assertEqual(ENFRAQUECIDA,
                         atualizar(ABERTA, discriminante_correta=True))

    def test_e_a_investigacao_continua_no_alvo_original(self):
        """Enfraquecida não é rejeitada, e o alvo segue sendo massa molar."""
        self.assertEqual(MASSA_MOLAR, INV.habilidade_alvo)

    def test_a_proxima_etapa_investiga_a_aplicacao_do_indice(self):
        from agente_ia_edu.services.grafo_estequiometria import MASSA_MOLAR as MM
        self.assertEqual(MM, INV.etapas[1].habilidade)
        self.assertIn("contribui", INV.etapas[1].pergunta.lower())


class CAMINHOB_H_IGUAL_1(unittest.TestCase):
    """Ele lê um hidrogênio. Agora há sinal de dificuldade na leitura."""

    def test_errar_a_discriminante_apoia_a_hipotese(self):
        self.assertEqual(APOIADA, atualizar(ABERTA,
                                            discriminante_correta=False))

    def test_a_hipotese_apoiada_aponta_para_o_PRE_REQUISITO(self):
        _, _, h = _responder_abertura("15")
        self.assertEqual(LEITURA_FORMULA, h.habilidade_suspeita)
        self.assertTrue(aponta_para_prerequisito(h, alvo=MASSA_MOLAR))

    def test_e_o_grafo_confirma_que_e_mesmo_um_degrau_abaixo(self):
        from agente_ia_edu.services.grafo_estequiometria import GRAFO
        self.assertIn(LEITURA_FORMULA, GRAFO.prerequisitos(MASSA_MOLAR))

    def test_o_objetivo_original_nao_e_abandonado(self):
        """Descer é para voltar. O alvo da investigação continua o mesmo."""
        self.assertEqual(MASSA_MOLAR, INV.habilidade_alvo)


class CAMINHOC_NAO_SEI(unittest.TestCase):

    def test_nao_sei_tem_observacao_propria(self):
        _, obs, _ = _responder_abertura("não sei")
        self.assertEqual(OBS_NAO_SEI, obs)

    def test_nao_sei_NAO_e_contado_como_erro(self):
        _, obs, _ = _responder_abertura("não sei")
        self.assertNotEqual(OBS_INCORRETA, obs)

    def test_nao_sei_NAO_e_contado_como_acerto(self):
        _, obs, _ = _responder_abertura("não sei")
        self.assertNotEqual(OBS_CORRETA, obs)

    def test_nao_sei_nao_produz_hipotese_sobre_o_raciocinio(self):
        """Ele não mostrou raciocínio nenhum. Supor um seria inventar."""
        _, _, h = _responder_abertura("não sei")
        self.assertIsNone(h)

    def test_me_ajuda_cai_no_mesmo_lugar(self):
        _, obs, _ = _responder_abertura("me ajuda")
        self.assertEqual(OBS_NAO_SEI, obs)

    def test_ha_uma_etapa_mais_simples_para_onde_ir(self):
        """Reduzir a complexidade exige ter para onde reduzir: a etapa 1 é
        mais simples que a abertura."""
        self.assertEqual(LEITURA_FORMULA, INV.etapas[0].habilidade)


class CAMINHOD_DEZESSETE(unittest.TestCase):
    """Resposta correta não é, sozinha, domínio."""

    def test_dezessete_e_observado_como_correto(self):
        _, obs, _ = _responder_abertura("17")
        self.assertEqual(OBS_CORRETA, obs)

    def test_dezessete_com_unidade_tambem(self):
        for texto in ("17 g/mol", "17g/mol", "17,0"):
            with self.subTest(texto=texto):
                _, obs, _ = _responder_abertura(texto)
                self.assertEqual(OBS_CORRETA, obs)

    def test_a_observacao_de_acerto_NAO_se_chama_dominio(self):
        _, obs, _ = _responder_abertura("17")
        for proibido in ("MASTER", "DOMIN", "LEARNED"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, obs.upper())

    def test_chutei_e_registrado_e_nao_julgado(self):
        from agente_ia_edu.services.resposta_do_aluno import (
            ESPERA_TEXTO, OBS_REGISTRADA,
        )
        r = normalizar("chutei", espera=ESPERA_TEXTO)
        self.assertEqual(OBS_REGISTRADA, observar(r))

    def test_uma_justificativa_tambem(self):
        from agente_ia_edu.services.resposta_do_aluno import (
            ESPERA_TEXTO, OBS_REGISTRADA,
        )
        r = normalizar("somei 14 com 3", espera=ESPERA_TEXTO)
        self.assertEqual(OBS_REGISTRADA, observar(r))


class AAMBIGUIDADENAOVIRARESPOSTA(unittest.TestCase):
    """§13: escutar não é adivinhar."""

    def test_quinze_ou_dezessete_nao_vira_nenhum_dos_dois(self):
        r, obs, h = _responder_abertura("15 ou 17")
        self.assertEqual(OBS_AMBIGUA, obs)
        self.assertIsNone(h)

    def test_e_nao_dispara_a_hipotese_do_15(self):
        _, _, h = _responder_abertura("15 ou 17")
        self.assertIsNone(h)

    def test_resposta_vazia_nao_vira_erro(self):
        from agente_ia_edu.services.resposta_do_aluno import OBS_SEM_RESPOSTA
        _, obs, _ = _responder_abertura("")
        self.assertEqual(OBS_SEM_RESPOSTA, obs)


class ATRANSFERENCIANAOREPETEOEXEMPLO(unittest.TestCase):
    """§6: o que se ensina e o que se verifica não podem ser o mesmo item."""

    def test_o_item_guiado_de_massa_molar_nao_e_o_NH3_da_abertura(self):
        from agente_ia_edu.services.itens_guiados import item_para
        item = item_para(CONTEUDO, MASSA_MOLAR)
        self.assertNotIn("NH₃", item["pergunta"])

    def test_e_ele_pergunta_outra_substancia(self):
        from agente_ia_edu.services.itens_guiados import item_para
        self.assertIn("CO₂", item_para(CONTEUDO, MASSA_MOLAR)["pergunta"])

    def test_a_resposta_da_transferencia_e_refeita_por_conta(self):
        from agente_ia_edu.services.itens_guiados import item_para
        item = item_para(CONTEUDO, MASSA_MOLAR)
        certa = item["alternativas"][item["correta"]]["texto"]
        self.assertTrue(certa.startswith(str(int(massa_molar("CO2")))))


if __name__ == "__main__":
    unittest.main()
