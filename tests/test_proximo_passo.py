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


def _conteudo(codigo, estado, prereqs=(), **evidencia):
    return {"content_code": codigo, "content_name": codigo.title(),
            "content_state": estado,
            "prerequisites": [dict(p) for p in prereqs],
            **evidencia}


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


class EvidenciaFracaNoProprioConteudoTests(unittest.TestCase):
    """O falso-pronto que o Piloto Zero produziu em 2026-10-04.

    O aluno errou as TRES perguntas de Estequiometria - acerto 0,0 - e a rota
    virou DIRECT. Causa: o planejador marca o conteudo como RECOMMENDED assim
    que ha qualquer evidencia, e RECOMMENDED estava na lista que libera. Mas
    RECOMMENDED quer dizer "pratique isto", nao "esta pronto".

    O estado do planejador diz se ha evidencia. Quem diz se ela e BOA e a
    politica - a mesma que ja decide o pre-requisito.
    """

    def test_errou_tudo_no_conteudo_da_atividade_NAO_libera(self):
        passo = passo_para([_conteudo("ESTEQ", "RECOMMENDED", **FRACO)])
        self.assertNotEqual(passo["kind"], PASSO_ATIVIDADE,
                            "errou tudo e foi liberado para a atividade")
        self.assertEqual(passo["kind"], PASSO_PRATICA)
        self.assertEqual(passo["readiness_route"], ROTA_PREPARACAO)

    def test_evidencia_boa_no_conteudo_libera(self):
        passo = passo_para([_conteudo("ESTEQ", "RECOMMENDED", **FORTE)])
        self.assertEqual(passo["kind"], PASSO_ATIVIDADE)
        self.assertEqual(passo["readiness_route"], ROTA_DIRETA)

    def test_evidencia_mediana_libera_a_atividade(self):
        """A pergunta e se ele pode COMECAR, nao se dominou."""
        passo = passo_para([_conteudo("ESTEQ", "RECOMMENDED", **MEDIANO)])
        self.assertEqual(passo["kind"], PASSO_ATIVIDADE)

    def test_READY_sem_evidencia_nenhuma_continua_liberando(self):
        """READY vem do planejador por outros caminhos (contexto da escola,
        pre-requisitos dominados). Sem evidencia propria E sem o planejador
        ter observado nada, nao inventamos bloqueio."""
        passo = passo_para([_conteudo("ESTEQ", "READY", **SEM_NADA)])
        self.assertEqual(passo["kind"], PASSO_ATIVIDADE)


class NaoSeRediagnosticaOqueJaFoiMedidoTests(unittest.TestCase):
    """Medido no navegador, no banco de desenvolvimento.

    O aluno tinha 8 respostas em Estequiometria (acerto 0,625) e 3 na base
    (0,667). O planejador marcava o conteúdo BLOCKED_BY_PREREQUISITE — a base
    não está MASTERED. Os dois acertos caem na faixa INTERMEDIÁRIA, que não é
    nem "fraca o bastante para praticar" nem "forte o bastante para liberar",
    e `_passo_para_um` devolve None para ambos.

    O passo caía então no ramo final, que respondia:

        kind: DIAGNOSTIC   reason: "falta evidencia sobre este conteudo"

    sobre um conteúdo com OITO respostas registradas. A frase era falsa, e a
    ação mandava o aluno responder de novo o que ele já tinha respondido.

    A REGRA
    ========
    Diagnosticar serve para medir quem não foi medido. Havendo amostra
    suficiente — o mesmo `min_sample_size` que a política já usa — o passo é
    PRATICAR. E, se quem trava é a base, pratica-se a BASE: é ela que precisa
    subir para destravar o resto.
    """

    def _travado(self):
        """Conteúdo medido, com estado travado pela base também medida."""
        return [_conteudo("esteq", "BLOCKED_BY_PREREQUISITE",
                          answered=8, accuracy=0.625,
                          prereqs=[_pre("balanc", answered=3, accuracy=0.667,
                                        content_state="READY")])]

    def test_nao_manda_diagnosticar_conteudo_com_amostra_suficiente(self):
        passo = passo_para(self._travado())
        self.assertNotEqual(passo["kind"], PASSO_DIAGNOSTICO,
                            "rediagnosticou um conteudo com 8 respostas")

    def test_pratica_a_BASE_quando_e_ela_que_trava(self):
        passo = passo_para(self._travado())
        self.assertEqual(passo["kind"], PASSO_PRATICA)
        self.assertEqual(passo["content_code"], "balanc",
                         "praticou o conteudo final em vez da base que trava")
        self.assertEqual(passo["readiness_route"], ROTA_PREPARACAO)

    def test_sem_prerequisito_travando_pratica_o_proprio_conteudo(self):
        passo = passo_para([_conteudo("esteq", "BLOCKED_BY_PREREQUISITE",
                                      answered=8, accuracy=0.625)])
        self.assertEqual(passo["kind"], PASSO_PRATICA)
        self.assertEqual(passo["content_code"], "esteq")

    def test_sem_amostra_suficiente_continua_diagnosticando(self):
        """A regra nao pode engolir o caso que o diagnostico existe para
        atender: quem nunca foi medido precisa ser medido."""
        passo = passo_para([_conteudo("esteq", "BLOCKED_BY_PREREQUISITE",
                                      answered=1, accuracy=1.0)])
        self.assertEqual(passo["kind"], PASSO_DIAGNOSTICO)

    def test_a_razao_nao_afirma_falta_de_evidencia_que_existe(self):
        passo = passo_para(self._travado())
        self.assertNotIn("nao ha evidencia", (passo.get("reason") or "").lower())
        self.assertNotIn("falta evidencia", (passo.get("reason") or "").lower())


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
