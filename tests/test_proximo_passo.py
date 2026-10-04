"""O PROXIMO PASSO: o aluno nunca pode ficar sem para onde ir.

O teste humano de 2026-10-04 encontrou o aluno preso: errou as tres perguntas
de Balanceamento, o backend decidiu PREPARE_PREREQUISITE - corretamente - e a
prontidao da atividade continuou `DIAGNOSTIC` apontando para o MESMO conteudo.
Clicar em "Continuar" abria outro microdiagnostico de Balanceamento. E outro.

O microdiagnostico COLETA EVIDENCIA PARA DECIDIR. Ele nao e a trilha.
Depois que ele decidiu, repeti-lo nao acrescenta nada: a decisao ja foi
tomada, e o que falta e ESTUDAR.

A distincao que faltava:

    sem evidencia sobre a base        -> DIAGNOSTICAR (ainda nao sei)
    evidencia, e a base esta fraca    -> PRATICAR     (ja sei, e falta)
    evidencia, e a base esta boa      -> seguir

Nenhum corte novo e decidido aqui: as tres faixas vem de
PerformanceThresholdPolicy, a mesma que o microdiagnostico usa.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.proximo_passo import (
    PASSO_ATIVIDADE,
    PASSO_DIAGNOSTICO,
    PASSO_NENHUM,
    PASSO_PRATICA,
    passo_para,
)
from agente_ia_edu.services.readiness_route import (
    ROTA_DIAGNOSTICO, ROTA_DIRETA, ROTA_PREPARACAO,
)

SEM_NADA = {"answered": 0, "accuracy": None}
FRACO = {"answered": 3, "accuracy": 0.0}
MEDIANO = {"answered": 3, "accuracy": 0.67}
FORTE = {"answered": 3, "accuracy": 1.0}


def _conteudo(codigo, estado, prereqs=()):
    return {"content_code": codigo, "content_name": codigo.title(),
            "content_state": estado,
            "prerequisites": [dict(p) for p in prereqs]}


def _pre(codigo, **evidencia):
    return {"code": codigo, "name": codigo.title(), "mastered": False, **evidencia}


class SemEvidenciaDiagnosticaTests(unittest.TestCase):

    def test_base_desconhecida_manda_diagnosticar(self):
        passo = passo_para([_conteudo("ESTEQ", "INSUFFICIENT_EVIDENCE",
                                      [_pre("BALANC", **SEM_NADA)])])
        self.assertEqual(passo["kind"], PASSO_DIAGNOSTICO)
        self.assertEqual(passo["content_code"], "BALANC")

    def test_conteudo_sem_prerequisito_e_sem_evidencia_diagnostica_ele_mesmo(self):
        passo = passo_para([_conteudo("BALANC", "INSUFFICIENT_EVIDENCE")])
        self.assertEqual(passo["kind"], PASSO_DIAGNOSTICO)
        self.assertEqual(passo["content_code"], "BALANC")


class ComEvidenciaFracaPraticaTests(unittest.TestCase):
    """O caso que estava quebrado."""

    def test_base_ja_medida_e_fraca_manda_PRATICAR_nao_rediagnosticar(self):
        passo = passo_para([_conteudo("ESTEQ", "INSUFFICIENT_EVIDENCE",
                                      [_pre("BALANC", **FRACO)])])
        self.assertEqual(passo["kind"], PASSO_PRATICA,
                         "o aluno seria mandado a responder o mesmo "
                         "microdiagnostico outra vez")
        self.assertEqual(passo["content_code"], "BALANC")

    def test_a_rota_correspondente_e_PREREQUISITE_PREPARATION(self):
        passo = passo_para([_conteudo("ESTEQ", "INSUFFICIENT_EVIDENCE",
                                      [_pre("BALANC", **FRACO)])])
        self.assertEqual(passo["readiness_route"], ROTA_PREPARACAO)

    def test_praticar_NAO_libera_a_atividade(self):
        """Preparar-se nao e estar pronto."""
        passo = passo_para([_conteudo("ESTEQ", "INSUFFICIENT_EVIDENCE",
                                      [_pre("BALANC", **FRACO)])])
        self.assertNotEqual(passo["readiness_route"], ROTA_DIRETA)


class ComEvidenciaBoaSegueTests(unittest.TestCase):

    def test_base_forte_deixa_de_ser_obstaculo_e_diagnostica_o_conteudo(self):
        passo = passo_para([_conteudo("ESTEQ", "INSUFFICIENT_EVIDENCE",
                                      [_pre("BALANC", **FORTE)])])
        self.assertEqual(passo["content_code"], "ESTEQ",
                         "dominou a base e o sistema continuou preso nela")
        self.assertEqual(passo["kind"], PASSO_DIAGNOSTICO)

    def test_base_mediana_tambem_libera_a_base(self):
        """DESEMPENHO_INTERMEDIARIO e suficiente para COMECAR - a pergunta do
        microdiagnostico e se ele pode comecar, nao se dominou."""
        passo = passo_para([_conteudo("ESTEQ", "INSUFFICIENT_EVIDENCE",
                                      [_pre("BALANC", **MEDIANO)])])
        self.assertEqual(passo["content_code"], "ESTEQ")

    def test_conteudo_pronto_vai_para_a_atividade(self):
        passo = passo_para([_conteudo("ESTEQ", "READY")])
        self.assertEqual(passo["kind"], PASSO_ATIVIDADE)
        self.assertEqual(passo["readiness_route"], ROTA_DIRETA)


class FailClosedTests(unittest.TestCase):

    def test_atividade_sem_conteudo_conhecido_nao_libera(self):
        passo = passo_para([])
        self.assertNotEqual(passo["kind"], PASSO_ATIVIDADE)
        self.assertEqual(passo["readiness_route"], ROTA_DIAGNOSTICO)
        self.assertEqual(passo["kind"], PASSO_NENHUM,
                         "sem saber o que a atividade exige, nao ha o que "
                         "diagnosticar - e a tela precisa poder dizer isso")

    def test_estado_desconhecido_nao_libera(self):
        passo = passo_para([_conteudo("X", "COISA_NOVA")])
        self.assertNotEqual(passo["kind"], PASSO_ATIVIDADE)


class NaoHaCorteNovoAquiTests(unittest.TestCase):
    """A regra toda sai de PerformanceThresholdPolicy."""

    def test_o_modulo_nao_contem_numero_de_corte(self):
        """Mesma guarda que student_progress ja tinha: le a ARVORE do arquivo,
        nao o texto, para que uma explicacao em comentario nao se auto-acuse."""
        import ast
        import pathlib

        import agente_ia_edu.services.proximo_passo as mod

        arvore = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
        numeros = [n.value for n in ast.walk(arvore)
                   if isinstance(n, ast.Constant) and isinstance(n.value, float)]
        self.assertEqual(numeros, [],
                         f"corte pedagogico escrito a mao: {numeros}")

    def test_a_faixa_vem_da_politica(self):
        """Afrouxar a politica muda este modulo sem editar este modulo."""
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )

        frouxa = PerformanceThresholdPolicy(
            min_sample_size=3, strong_accuracy=0.99, improvement_accuracy=0.99)
        passo = passo_para([_conteudo("ESTEQ", "INSUFFICIENT_EVIDENCE",
                                      [_pre("BALANC", **FORTE)])],
                           thresholds=frouxa)
        # com corte em 0.99, acerto 1.0 continua forte
        self.assertEqual(passo["content_code"], "ESTEQ")

        passo2 = passo_para([_conteudo("ESTEQ", "INSUFFICIENT_EVIDENCE",
                                       [_pre("BALANC", **MEDIANO)])],
                            thresholds=frouxa)
        self.assertEqual(passo2["kind"], PASSO_PRATICA,
                         "a politica endureceu e o passo nao acompanhou")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
