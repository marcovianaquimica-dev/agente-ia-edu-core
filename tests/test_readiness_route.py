"""A rota de prontidao: como o aluno chega (ou nao) ate a tarefa da escola.

Esta decisao existia no JAVASCRIPT do prototipo. Era uma funcao de quatro
linhas lendo um MOCK, e nenhum teste a cobria - o que significa que a regra
pedagogica central do perfil Aluno morava no cliente, onde qualquer pessoa
com o console aberto pode mudar o proprio diagnostico.

Aqui ela e backend, e os tres valores sao os mesmos que
`study_sessions.readiness_route` aceita por CheckConstraint (migration 064).
Se divergirem, o banco recusa a escrita.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.readiness_route import (
    ROTA_DIAGNOSTICO,
    ROTA_DIRETA,
    ROTA_PREPARACAO,
    rota_de_estados,
)


class RotaDeEstadosTests(unittest.TestCase):
    """A funcao pura. Recebe o estado de cada conteudo exigido, devolve a rota."""

    def test_tudo_pronto_vai_direto(self):
        self.assertEqual(rota_de_estados(["READY"]), ROTA_DIRETA)

    def test_dominado_tambem_vai_direto(self):
        self.assertEqual(rota_de_estados(["MASTERED"]), ROTA_DIRETA)

    def test_recomendado_vai_direto(self):
        """RECOMMENDED nao e bloqueio: o planejador ja viu evidencia e sugere
        pratica. O aluno pode comecar a tarefa."""
        self.assertEqual(rota_de_estados(["RECOMMENDED"]), ROTA_DIRETA)

    def test_sem_evidencia_pede_diagnostico(self):
        self.assertEqual(rota_de_estados(["INSUFFICIENT_EVIDENCE"]), ROTA_DIAGNOSTICO)

    def test_bloqueado_por_prerequisito_pede_preparacao(self):
        self.assertEqual(rota_de_estados(["BLOCKED_BY_PREREQUISITE"]), ROTA_PREPARACAO)

    def test_bloqueio_vence_falta_de_evidencia(self):
        """Saber que falta o pre-requisito e mais informativo que nao saber
        nada: diagnosticar o conteudo final seria perguntar a coisa errada."""
        self.assertEqual(
            rota_de_estados(["INSUFFICIENT_EVIDENCE", "BLOCKED_BY_PREREQUISITE"]),
            ROTA_PREPARACAO)

    def test_falta_de_evidencia_vence_pronto(self):
        """Basta UM conteudo desconhecido para a atividade nao ser segura."""
        self.assertEqual(rota_de_estados(["READY", "INSUFFICIENT_EVIDENCE"]),
                         ROTA_DIAGNOSTICO)

    def test_atividade_sem_conteudo_conhecido_NAO_vai_direto(self):
        """Fail-closed. Lista vazia significa "as questoes ainda nao foram
        classificadas", nao "nao exige nada". Mandar o aluno direto seria
        tratar ignorancia como aprovacao.

        Este e o caso que mais me preocupa no Piloto Zero, porque e o estado
        NORMAL de uma atividade recem-criada."""
        self.assertEqual(rota_de_estados([]), ROTA_DIAGNOSTICO)

    def test_estado_desconhecido_NAO_vai_direto(self):
        """Um estado que esta funcao nao conhece pode ser novo, pode ser erro
        de digitacao. Em nenhum dos dois casos ele autoriza a atividade."""
        self.assertEqual(rota_de_estados(["COISA_NOVA"]), ROTA_DIAGNOSTICO)

    def test_as_tres_rotas_sao_as_do_banco(self):
        """Se alguem renomear uma rota aqui sem mexer na migration 064, o
        INSERT passa a ser recusado pelo CheckConstraint em producao. Melhor
        quebrar neste teste."""
        from agente_ia_edu.services.study_session import READINESS_ROUTES

        self.assertEqual(
            sorted([ROTA_DIRETA, ROTA_DIAGNOSTICO, ROTA_PREPARACAO]),
            sorted(READINESS_ROUTES))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
