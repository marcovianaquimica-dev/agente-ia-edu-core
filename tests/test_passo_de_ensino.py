"""O ENSINO entra na matriz de CTA e na jornada, sem desarrumar o resto.

A etapa de ensino é uma quarta linha da mesma matriz (tipo × estado), não um
caso especial costurado na tela. Se fosse um caso especial, voltaria a ser um
`if` no JavaScript — que é de onde este projeto já teve de tirar o rótulo
"Responder" uma vez.

DENTRO DA PREPARAÇÃO, NÃO AO LADO DELA
=======================================
A jornada principal continua com quatro etapas no máximo:

    Diagnóstico → Preparação → Atividade → Resultado

Ensinar é uma coisa que acontece DENTRO de "Preparação", junto com praticar.
Uma etapa "Ensino" ao lado de "Preparação" dobraria o tamanho da barra e
contaria ao aluno um detalhe de implementação.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.proximo_passo import (
    ESTADO_CONCLUIDO,
    ESTADO_EM_ANDAMENTO,
    ESTADO_NAO_INICIADO,
    ETAPA_ATIVIDADE,
    ETAPA_DIAGNOSTICO,
    ETAPA_PREPARACAO,
    ETAPA_RESULTADO,
    PASSO_ATIVIDADE,
    PASSO_DIAGNOSTICO,
    PASSO_ENSINO,
    PASSO_PRATICA,
    cta_para,
    jornada_de,
)

ESTADOS = (ESTADO_NAO_INICIADO, ESTADO_EM_ANDAMENTO, ESTADO_CONCLUIDO)


class OEnsinoTemCTAProprioTests(unittest.TestCase):

    def test_os_tres_estados_tem_rotulo(self):
        for estado in ESTADOS:
            with self.subTest(estado=estado):
                self.assertTrue((cta_para(PASSO_ENSINO, estado) or "").strip())

    def test_os_tres_rotulos_sao_diferentes(self):
        """Se dois coincidissem, o aluno não saberia se está começando ou
        retomando — a mesma regra que vale para os outros tipos."""
        rotulos = [cta_para(PASSO_ENSINO, e) for e in ESTADOS]
        self.assertEqual(len(set(rotulos)), 3, rotulos)

    def test_quem_nao_comecou_nao_le_continuar(self):
        self.assertNotIn("Continuar", cta_para(PASSO_ENSINO, ESTADO_NAO_INICIADO))

    def test_terminado_o_ensino_o_rotulo_leva_a_pratica(self):
        """Depois de entender, o passo é tentar — e o botão diz isso, em vez
        de um "Continuar" que não conta para onde."""
        self.assertIn("ratic", cta_para(PASSO_ENSINO, ESTADO_CONCLUIDO))

    def test_um_tipo_desconhecido_continua_sem_botao(self):
        self.assertIsNone(cta_para("QUALQUER_COISA", ESTADO_NAO_INICIADO))


class OEnsinoNaoCriaEtapaNovaNaJornadaTests(unittest.TestCase):

    def test_ensinar_acende_PREPARACAO(self):
        j = jornada_de(origens={"MICRO_DIAGNOSTIC": 3},
                       estado_atividade=ESTADO_NAO_INICIADO,
                       rota="PREREQUISITE_PREPARATION", kind=PASSO_ENSINO)
        atuais = [e["id"] for e in j if e["state"] == ESTADO_EM_ANDAMENTO]
        self.assertEqual(atuais, [ETAPA_PREPARACAO])

    def test_a_jornada_continua_com_no_maximo_quatro_etapas(self):
        j = jornada_de(origens={"MICRO_DIAGNOSTIC": 3, "PRACTICE": 2},
                       estado_atividade=ESTADO_NAO_INICIADO,
                       rota="PREREQUISITE_PREPARATION", kind=PASSO_ENSINO)
        self.assertEqual([e["id"] for e in j],
                         [ETAPA_DIAGNOSTICO, ETAPA_PREPARACAO,
                          ETAPA_ATIVIDADE, ETAPA_RESULTADO])

    def test_nenhum_rotulo_da_jornada_virou_codigo_interno(self):
        j = jornada_de(origens={}, estado_atividade=ESTADO_NAO_INICIADO,
                       rota="PREREQUISITE_PREPARATION", kind=PASSO_ENSINO)
        for e in j:
            with self.subTest(etapa=e["id"]):
                self.assertNotIn("_", e["label"])


class AMatrizAntigaNaoMudouTests(unittest.TestCase):
    """Regressão: a etapa nova não pode alterar nenhuma das nove células que
    já estavam validadas em produção."""

    ESPERADO = {
        (PASSO_DIAGNOSTICO, ESTADO_NAO_INICIADO): "Responder diagnóstico",
        (PASSO_DIAGNOSTICO, ESTADO_EM_ANDAMENTO): "Continuar diagnóstico",
        (PASSO_DIAGNOSTICO, ESTADO_CONCLUIDO): "Continuar",
        (PASSO_PRATICA, ESTADO_NAO_INICIADO): "Praticar agora",
        (PASSO_PRATICA, ESTADO_EM_ANDAMENTO): "Continuar prática",
        (PASSO_PRATICA, ESTADO_CONCLUIDO): "Continuar",
        (PASSO_ATIVIDADE, ESTADO_NAO_INICIADO): "Começar atividade",
        (PASSO_ATIVIDADE, ESTADO_EM_ANDAMENTO): "Continuar atividade",
        (PASSO_ATIVIDADE, ESTADO_CONCLUIDO): "Ver resultado",
    }

    def test_as_nove_celulas_continuam_identicas(self):
        for (kind, estado), rotulo in self.ESPERADO.items():
            with self.subTest(kind=kind, estado=estado):
                self.assertEqual(cta_para(kind, estado), rotulo)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
