"""O MOTOR PEDAGÓGICO — os cenários de aceitação, um a um.

Este arquivo é o teste central do bloco. Ele não mede se o código roda: mede
se o percurso de um aluno que não sabe calcular massa molar é o percurso que
um professor faria.

Os cenários A–G são os do enunciado do bloco, e cada classe abaixo é um
deles. Metade das regras é testada também com um grafo sintético de
matemática — se alguma tropeçar nele, ela aprendeu Química sem querer.

A REGRA QUE SUSTENTA TODAS
===========================
O motor DECIDE; ele não escreve texto, não mede aluno e não fala com IA. Ele
recebe o que foi medido e devolve qual é a próxima intervenção. Por isso ele
é testável sem banco, sem HTTP e sem provedor.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.estrategia_de_ensino import (
    DECOMPOSICAO,
    ESTRATEGIAS,
    L0_AUTONOMO,
    L1_DICA,
    L2_PERGUNTAS_GUIADAS,
    L3_EXEMPLO_RESOLVIDO,
)
from agente_ia_edu.services.grafo_estequiometria import (
    GRAFO,
    INTEGRADO,
    LEITURA_FORMULA,
    MASSA_MOL,
    MASSA_MOLAR,
    PROPORCAO,
)
from agente_ia_edu.services.motor_pedagogico import (
    PASSO_AVANCAR,
    PASSO_ENSINAR,
    PASSO_ESCALAR,
    PASSO_PRATICAR,
    PASSO_PRATICAR_COM_APOIO,
    PASSO_SONDAR,
    PASSO_VERIFICAR,
    EstadoDaHabilidade,
    decidir,
)
from agente_ia_edu.services.sondagem import (
    ESTADO_CONFIRMADO,
    ESTADO_NAO_MEDIDO,
    ESTADO_PRECISA_APOIO,
)


def _mapa(**estados) -> dict[str, str]:
    """Mapa completo do grafo, com os estados informados sobrescrevendo."""
    base = {c: ESTADO_NAO_MEDIDO for c in GRAFO.codigos()}
    base.update(estados)
    return base


class CenarioA_OGargaloEIdentificado(unittest.TestCase):
    """Acerta leitura de fórmula, erra massa molar.

    Esperado: o sistema aponta MASSA_MOLAR, e não manda uma questão
    integrada.
    """

    def setUp(self):
        self.decisao = decidir(
            grafo=GRAFO,
            mapa=_mapa(**{LEITURA_FORMULA: ESTADO_CONFIRMADO,
                          MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
            historico={})

    def test_a_habilidade_escolhida_e_a_que_falhou(self):
        self.assertEqual(MASSA_MOLAR, self.decisao.habilidade)

    def test_o_passo_e_ensinar_e_nao_mais_uma_questao(self):
        self.assertEqual(PASSO_ENSINAR, self.decisao.passo)

    def test_nao_manda_o_problema_completo(self):
        self.assertNotEqual(INTEGRADO, self.decisao.habilidade)

    def test_a_primeira_estrategia_e_a_primeira_da_escada(self):
        self.assertEqual(ESTRATEGIAS[0], self.decisao.estrategia)

    def test_o_aluno_le_o_nome_da_habilidade_e_nao_o_codigo(self):
        self.assertEqual("Massa molar", self.decisao.rotulo)


class CenarioB_OPreRequisitoVemPrimeiro(unittest.TestCase):
    """Erra massa molar E leitura de fórmula.

    Esperado: trabalha o pré-requisito antes — ensinar massa molar a quem não
    lê o 3 do NH₃ é falar sobre o telhado com quem não tem parede.
    """

    def test_comeca_pela_leitura_da_formula(self):
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{LEITURA_FORMULA: ESTADO_PRECISA_APOIO,
                                  MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                    historico={})
        self.assertEqual(LEITURA_FORMULA, d.habilidade)

    def test_e_quando_o_prerequisito_ficar_firme_volta_para_a_de_cima(self):
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{LEITURA_FORMULA: ESTADO_CONFIRMADO,
                                  MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                    historico={})
        self.assertEqual(MASSA_MOLAR, d.habilidade)


class CenarioC_NaoEntendiMudaAEstrategia(unittest.TestCase):
    """Recebeu DECOMPOSIÇÃO e disse que não entendeu.

    Esperado: a próxima não é DECOMPOSIÇÃO. Repetir com outras palavras é
    repetir.
    """

    def test_a_segunda_estrategia_e_outra(self):
        d = decidir(
            grafo=GRAFO,
            mapa=_mapa(**{MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
            historico={MASSA_MOLAR: EstadoDaHabilidade(
                estrategias_usadas=(DECOMPOSICAO,))})
        self.assertEqual(PASSO_ENSINAR, d.passo)
        self.assertNotEqual(DECOMPOSICAO, d.estrategia)

    def test_nunca_repete_nenhuma_das_ja_usadas(self):
        usadas = (ESTRATEGIAS[0], ESTRATEGIAS[1], ESTRATEGIAS[2])
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                    historico={MASSA_MOLAR: EstadoDaHabilidade(
                        estrategias_usadas=usadas)})
        self.assertNotIn(d.estrategia, usadas)

    def test_dizer_que_nao_entendeu_nao_e_tentativa(self):
        """"Não entendi" não é uma resposta errada: é um pedido."""
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                    historico={MASSA_MOLAR: EstadoDaHabilidade(
                        estrategias_usadas=(DECOMPOSICAO,), tentativas=0)})
        self.assertEqual(PASSO_ENSINAR, d.passo)


class CenarioD_AcertoComAjudaNaoConfirma(unittest.TestCase):
    """Recebeu exemplo resolvido e acertou com ajuda.

    Esperado: não marcar como dominado. Retirar apoio e dar item novo.
    """

    def test_depois_de_ensinar_vem_pratica_com_apoio(self):
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                    historico={MASSA_MOLAR: EstadoDaHabilidade(
                        estrategias_usadas=(DECOMPOSICAO,), ensinou_agora=True)})
        self.assertEqual(PASSO_PRATICAR_COM_APOIO, d.passo)
        self.assertEqual(L3_EXEMPLO_RESOLVIDO, d.nivel_de_apoio)

    def test_acertar_com_apoio_retira_um_degrau_e_nao_confirma(self):
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                    historico={MASSA_MOLAR: EstadoDaHabilidade(
                        estrategias_usadas=(DECOMPOSICAO,),
                        nivel_de_apoio=L3_EXEMPLO_RESOLVIDO,
                        ultimo_acerto=True)})
        self.assertEqual(L2_PERGUNTAS_GUIADAS, d.nivel_de_apoio)
        self.assertNotEqual(PASSO_AVANCAR, d.passo)

    def test_a_escada_chega_ao_autonomo(self):
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                    historico={MASSA_MOLAR: EstadoDaHabilidade(
                        estrategias_usadas=(DECOMPOSICAO,),
                        nivel_de_apoio=L1_DICA, ultimo_acerto=True)})
        self.assertEqual(L0_AUTONOMO, d.nivel_de_apoio)
        self.assertEqual(PASSO_PRATICAR, d.passo)


class CenarioE_EvidenciaAutonomaFazAvancar(unittest.TestCase):
    """Acertou sozinho, em L0.

    Esperado: registrar evidência e seguir o contrato — não insistir na
    habilidade que ele acabou de demonstrar.
    """

    def test_acerto_autonomo_leva_a_verificar(self):
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                    historico={MASSA_MOLAR: EstadoDaHabilidade(
                        estrategias_usadas=(DECOMPOSICAO,),
                        nivel_de_apoio=L0_AUTONOMO, ultimo_acerto=True)})
        self.assertEqual(PASSO_VERIFICAR, d.passo)

    def test_a_verificacao_e_sem_apoio(self):
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                    historico={MASSA_MOLAR: EstadoDaHabilidade(
                        estrategias_usadas=(DECOMPOSICAO,),
                        nivel_de_apoio=L0_AUTONOMO, ultimo_acerto=True)})
        self.assertEqual(L0_AUTONOMO, d.nivel_de_apoio)

    def test_habilidade_confirmada_sai_do_caminho(self):
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{MASSA_MOLAR: ESTADO_CONFIRMADO,
                                  MASSA_MOL: ESTADO_PRECISA_APOIO}),
                    historico={})
        self.assertEqual(MASSA_MOL, d.habilidade)


class CenarioF_OLoopTemFim(unittest.TestCase):
    """Falhou depois de todas as estratégias.

    Esperado: não gerar questões para sempre. Escalar — sem apagar progresso.
    """

    def test_esgotadas_as_estrategias_escala(self):
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                    historico={MASSA_MOLAR: EstadoDaHabilidade(
                        estrategias_usadas=ESTRATEGIAS)})
        self.assertEqual(PASSO_ESCALAR, d.passo)

    def test_escalar_continua_apontando_a_habilidade(self):
        """Escalar sem dizer sobre o quê não ajuda ninguém."""
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                    historico={MASSA_MOLAR: EstadoDaHabilidade(
                        estrategias_usadas=ESTRATEGIAS)})
        self.assertEqual(MASSA_MOLAR, d.habilidade)
        self.assertEqual("Massa molar", d.rotulo)

    def test_nunca_devolve_a_mesma_estrategia_duas_vezes_num_percurso(self):
        """O percurso inteiro, simulado: a lista de estratégias não repete."""
        usadas: list[str] = []
        for _ in range(len(ESTRATEGIAS) + 2):
            d = decidir(grafo=GRAFO,
                        mapa=_mapa(**{MASSA_MOLAR: ESTADO_PRECISA_APOIO}),
                        historico={MASSA_MOLAR: EstadoDaHabilidade(
                            estrategias_usadas=tuple(usadas))})
            if d.passo != PASSO_ENSINAR:
                break
            self.assertNotIn(d.estrategia, usadas)
            usadas.append(d.estrategia)
        self.assertEqual(len(usadas), len(set(usadas)))


class CenarioG_QuemJaSabeNaoEInterrompido(unittest.TestCase):
    """Acertou as sondagens relevantes.

    Esperado: não obrigá-lo a explicações desnecessárias.
    """

    def test_tudo_confirmado_leva_a_avancar(self):
        d = decidir(grafo=GRAFO,
                    mapa={c: ESTADO_CONFIRMADO for c in GRAFO.codigos()},
                    historico={})
        self.assertEqual(PASSO_AVANCAR, d.passo)
        self.assertIsNone(d.habilidade)

    def test_nada_medido_ainda_leva_a_sondar(self):
        d = decidir(grafo=GRAFO, mapa=_mapa(), historico={})
        self.assertEqual(PASSO_SONDAR, d.passo)

    def test_nao_medido_nunca_vira_intervencao(self):
        """Intervir sobre o que ninguém mediu é inventar sobre a pessoa."""
        d = decidir(grafo=GRAFO,
                    mapa=_mapa(**{LEITURA_FORMULA: ESTADO_CONFIRMADO}),
                    historico={})
        self.assertEqual(PASSO_AVANCAR, d.passo)


class OMotorNaoSabeQuimica(unittest.TestCase):

    def test_o_modulo_nao_cita_assunto_nem_provedor(self):
        import pathlib

        fonte = (pathlib.Path(__file__).resolve().parent.parent
                 / "src/agente_ia_edu/services/motor_pedagogico.py"
                 ).read_text(encoding="utf-8").lower()
        for proibido in ("chemistry", "quimica", "química", "estequiometria",
                         "molar", "provider", "openai", "asyncsession"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, fonte)

    def test_funciona_com_um_grafo_de_outra_disciplina(self):
        from agente_ia_edu.services.grafo_pedagogico import (
            GrafoPedagogico,
            MicroHabilidade,
        )

        math = GrafoPedagogico(conteudo="MATH-EQ1", habilidades=[
            MicroHabilidade(code="OPS", label="Operações", objetivo=""),
            MicroHabilidade(code="SOLVE", label="Resolver", objetivo="",
                            prerequisitos=("OPS",)),
        ])
        d = decidir(grafo=math,
                    mapa={"OPS": ESTADO_PRECISA_APOIO,
                          "SOLVE": ESTADO_PRECISA_APOIO},
                    historico={})
        self.assertEqual("OPS", d.habilidade)
        self.assertEqual("Operações", d.rotulo)
        self.assertEqual(PASSO_ENSINAR, d.passo)


if __name__ == "__main__":
    unittest.main()
