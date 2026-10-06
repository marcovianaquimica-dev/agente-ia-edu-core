"""AS INVARIANTES DO MOTOR — o que ele não pode fazer, por construção.

"A inteligência pedagógica pertence ao Núcleo Edu 360. A IA é um componente
substituível." Isso só é verdade se for verificável, e estas são as
verificações.

O que este arquivo trava não é comportamento: é AUSÊNCIA de caminho. Um
módulo que não importa sessão não tem como escrever; um que não importa
provider não tem como pedir a decisão a um modelo. Teste de comportamento
prova o que o código faz hoje; teste de ausência de caminho prova o que ele
não pode passar a fazer sem alguém perceber.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import unittest

SERVICES = (pathlib.Path(__file__).resolve().parent.parent
            / "src/agente_ia_edu/services")

# As camadas do motor pedagógico. Nenhuma delas decide com IA, e nenhuma
# escreve no banco.
CAMADAS_DO_MOTOR = (
    "grafo_pedagogico.py",
    "grafo_estequiometria.py",
    "grafos_pedagogicos.py",
    "sondagem.py",
    "sondagem_estequiometria.py",
    "estrategia_de_ensino.py",
    "motor_pedagogico.py",
    "modelo_do_aluno.py",
)


def _fonte(nome: str) -> str:
    return (SERVICES / nome).read_text(encoding="utf-8")


class AIANAODECIDEOPERCURSO(unittest.TestCase):
    """Um modelo pode adaptar a LINGUAGEM de uma explicação. Nunca escolher
    se o aluno precisa dela."""

    def test_nenhuma_camada_do_motor_importa_provedor(self):
        for nome in CAMADAS_DO_MOTOR:
            with self.subTest(modulo=nome):
                arvore = ast.parse(_fonte(nome))
                for no in ast.walk(arvore):
                    alvos = []
                    if isinstance(no, ast.Import):
                        alvos = [a.name for a in no.names]
                    elif isinstance(no, ast.ImportFrom):
                        alvos = [no.module or ""]
                    for alvo in alvos:
                        self.assertNotIn("provider", alvo.lower(),
                                         f"{nome} importa provedor de IA")

    def test_nenhuma_camada_cita_fornecedor(self):
        for nome in CAMADAS_DO_MOTOR:
            with self.subTest(modulo=nome):
                baixo = _fonte(nome).lower()
                for vendor in ("openai", "anthropic", "gemini", "gpt-"):
                    self.assertNotIn(vendor, baixo)


class OMOTORNAOESCREVE(unittest.TestCase):
    """Decisão não é escrita. Um motor que pudesse gravar mastery seria um
    motor que pode dar por aprendido quem ele próprio decidiu ensinar."""

    def test_nenhuma_camada_importa_sessao_de_banco(self):
        for nome in CAMADAS_DO_MOTOR:
            with self.subTest(modulo=nome):
                baixo = _fonte(nome).lower()
                for proibido in ("asyncsession", "sqlalchemy", "db.models"):
                    self.assertNotIn(proibido, baixo,
                                     f"{nome} alcança o banco")

    def test_decidir_nao_recebe_sessao(self):
        from agente_ia_edu.services.motor_pedagogico import decidir

        params = inspect.signature(decidir).parameters
        for proibido in ("session", "session_factory", "db", "engine"):
            self.assertNotIn(proibido, params)


class ACERTOCOMAJUDANAOEEVIDENCIA(unittest.TestCase):
    """A linha que impede o sistema de dar por aprendido quem foi conduzido
    até a resposta."""

    def test_so_o_nivel_autonomo_produz_evidencia(self):
        from agente_ia_edu.services.estrategia_de_ensino import (
            L0_AUTONOMO,
            NIVEIS,
            produz_evidencia_autonoma,
        )

        produzem = [n for n in NIVEIS if produz_evidencia_autonoma(n)]
        self.assertEqual([L0_AUTONOMO], produzem)

    def test_o_motor_nunca_manda_verificar_a_partir_de_acerto_assistido(self):
        """Verificar vem depois de acerto SOZINHO. Verificar quem acabou de
        acertar com exemplo resolvido confirmaria a ajuda, não a aprendizagem."""
        from agente_ia_edu.services.estrategia_de_ensino import (
            DECOMPOSICAO,
            L1_DICA,
            L2_PERGUNTAS_GUIADAS,
            L3_EXEMPLO_RESOLVIDO,
        )
        from agente_ia_edu.services.grafo_estequiometria import GRAFO, MASSA_MOLAR
        from agente_ia_edu.services.motor_pedagogico import (
            PASSO_VERIFICAR,
            EstadoDaHabilidade,
            decidir,
        )
        from agente_ia_edu.services.sondagem import ESTADO_PRECISA_APOIO

        for nivel in (L3_EXEMPLO_RESOLVIDO, L2_PERGUNTAS_GUIADAS, L1_DICA):
            with self.subTest(nivel=nivel):
                d = decidir(grafo=GRAFO,
                            mapa={MASSA_MOLAR: ESTADO_PRECISA_APOIO},
                            historico={MASSA_MOLAR: EstadoDaHabilidade(
                                estrategias_usadas=(DECOMPOSICAO,),
                                nivel_de_apoio=nivel, ultimo_acerto=True)})
                self.assertNotEqual(PASSO_VERIFICAR, d.passo)


class NENHUMCORTENOVO(unittest.TestCase):
    """As faixas chegam prontas da política. Um número numa destas camadas
    seria uma segunda política, divergindo no primeiro ajuste."""

    SEM_NUMERO_DE_CORTE = ("motor_pedagogico.py", "modelo_do_aluno.py",
                           "estrategia_de_ensino.py", "grafo_pedagogico.py")

    def test_nenhum_literal_float_nas_camadas_de_decisao(self):
        for nome in self.SEM_NUMERO_DE_CORTE:
            with self.subTest(modulo=nome):
                for no in ast.walk(ast.parse(_fonte(nome))):
                    if isinstance(no, ast.Constant) and isinstance(no.value, float):
                        self.fail(f"{nome}: literal float {no.value}")


class OLOOPNAOERATERNO(unittest.TestCase):
    """Qualquer percurso termina — em avanço, ou em escalada."""

    def test_um_aluno_que_erra_sempre_chega_a_escalar(self):
        from agente_ia_edu.services.estrategia_de_ensino import L0_AUTONOMO
        from agente_ia_edu.services.grafo_estequiometria import GRAFO, MASSA_MOLAR
        from agente_ia_edu.services.modelo_do_aluno import historico_por_habilidade
        from agente_ia_edu.services.motor_pedagogico import PASSO_ESCALAR, decidir
        from agente_ia_edu.services.sondagem import ESTADO_PRECISA_APOIO

        passos = []
        for ciclo in range(1, 12):
            d = decidir(
                grafo=GRAFO,
                mapa={MASSA_MOLAR: ESTADO_PRECISA_APOIO},
                historico={MASSA_MOLAR: historico_por_habilidade(
                    MASSA_MOLAR, ciclo=ciclo, nivel=L0_AUTONOMO,
                    ultimo_acerto=False)})
            passos.append(d.passo)
            if d.passo == PASSO_ESCALAR:
                break
        self.assertIn(PASSO_ESCALAR, passos,
                      "errando sempre, o percurso nunca termina")


if __name__ == "__main__":
    unittest.main()
