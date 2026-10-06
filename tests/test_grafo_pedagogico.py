"""O GRAFO PEDAGÓGICO — e a prova de que ele não sabe Química.

O QUE ESTE ARQUIVO TRAVA
=========================
O motor precisa responder três perguntas, e nenhuma delas pode depender do
assunto:

    quais micro-habilidades compõem este conteúdo?
    o que precisa vir antes desta?
    por onde começar, quando várias estão fracas?

Metade dos testes usa um grafo SINTÉTICO de matemática. Se alguma regra
tropeçar nele, é porque ela aprendeu Química sem querer — e seria preciso
reescrever o motor para a segunda disciplina, que é exatamente o que uma
camada genérica existe para evitar.

O QUE O GRAFO NÃO É
====================
Não é tabela. Um grafo que muda por decisão pedagógica - e não por uso - é
artefato de código, como `itens_guiados.py` já é. Vira tabela no dia em que
alguém precisar editá-lo sem deploy, e não antes.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.grafo_pedagogico import (
    GrafoPedagogico,
    MicroHabilidade,
    CicloDePreRequisito,
)

# Um grafo de MATEMÁTICA, inventado só para este teste. Nenhuma regra do motor
# pode precisar saber o que "EQUALITY" significa.
MATH = GrafoPedagogico(
    conteudo="MATH-ALGEBRA-EQUATION-FIRST-DEGREE",
    habilidades=[
        MicroHabilidade(code="BASIC_OPERATIONS", label="Operações básicas",
                        objetivo="Somar, subtrair, multiplicar e dividir."),
        MicroHabilidade(code="EQUALITY", label="Ideia de igualdade",
                        objetivo="Entender que os dois lados valem o mesmo."),
        MicroHabilidade(code="INVERSE_OPERATIONS", label="Operações inversas",
                        objetivo="Desfazer uma operação para isolar.",
                        prerequisitos=("BASIC_OPERATIONS",)),
        MicroHabilidade(code="SOLVE_EQUATION", label="Resolver a equação",
                        objetivo="Isolar a incógnita.",
                        prerequisitos=("EQUALITY", "INVERSE_OPERATIONS")),
    ],
)


class OGrafoResponde(unittest.TestCase):

    def test_quais_habilidades_compoem_o_conteudo(self):
        self.assertEqual(
            {"BASIC_OPERATIONS", "EQUALITY", "INVERSE_OPERATIONS",
             "SOLVE_EQUATION"},
            set(MATH.codigos()))

    def test_o_rotulo_e_do_professor_e_o_codigo_e_do_sistema(self):
        self.assertEqual("Operações básicas", MATH.rotulo("BASIC_OPERATIONS"))

    def test_codigo_desconhecido_volta_ele_mesmo(self):
        """Nunca inventar nome: mostrar o código é honesto."""
        self.assertEqual("NAO-EXISTE", MATH.rotulo("NAO-EXISTE"))

    def test_pre_requisitos_diretos(self):
        self.assertEqual(("EQUALITY", "INVERSE_OPERATIONS"),
                         MATH.prerequisitos("SOLVE_EQUATION"))
        self.assertEqual((), MATH.prerequisitos("EQUALITY"))


class APROFUNDIDADEIMPORTA(unittest.TestCase):
    """Intervir na habilidade errada manda o aluno estudar o que ele já sabe."""

    def test_pre_requisitos_em_profundidade(self):
        """SOLVE_EQUATION depende de INVERSE_OPERATIONS, que depende de
        BASIC_OPERATIONS - e é em BASIC que se começa."""
        self.assertEqual(
            {"EQUALITY", "INVERSE_OPERATIONS", "BASIC_OPERATIONS"},
            set(MATH.prerequisitos_em_profundidade("SOLVE_EQUATION")))

    def test_a_ordem_vai_do_mais_basico_para_o_mais_dependente(self):
        ordem = MATH.prerequisitos_em_profundidade("SOLVE_EQUATION")
        self.assertLess(ordem.index("BASIC_OPERATIONS"),
                        ordem.index("INVERSE_OPERATIONS"))

    def test_habilidade_sem_pre_requisito_nao_tem_profundidade(self):
        self.assertEqual((), MATH.prerequisitos_em_profundidade("EQUALITY"))


class OPRIMEIROGARGALO(unittest.TestCase):
    """Com várias fracas, intervir onde o aluno está PRONTO para aprender.

    Ensinar `SOLVE_EQUATION` a quem não domina `BASIC_OPERATIONS` é falar
    sobre o telhado com quem ainda não tem parede.
    """

    def test_entre_duas_fracas_comeca_pela_que_sustenta_a_outra(self):
        self.assertEqual(
            "BASIC_OPERATIONS",
            MATH.primeiro_gargalo({"SOLVE_EQUATION", "BASIC_OPERATIONS"}))

    def test_uma_fraca_so_e_ela_mesma(self):
        self.assertEqual("EQUALITY", MATH.primeiro_gargalo({"EQUALITY"}))

    def test_a_cadeia_inteira_fraca_comeca_na_base(self):
        self.assertEqual(
            "BASIC_OPERATIONS",
            MATH.primeiro_gargalo({"SOLVE_EQUATION", "INVERSE_OPERATIONS",
                                   "BASIC_OPERATIONS", "EQUALITY"}))

    def test_sem_fraca_nenhuma_nao_ha_gargalo(self):
        self.assertIsNone(MATH.primeiro_gargalo(set()))

    def test_fraca_fora_do_grafo_e_ignorada(self):
        """Um subconteúdo antigo, sem nó, não pode derrubar a decisão."""
        self.assertEqual("EQUALITY",
                         MATH.primeiro_gargalo({"EQUALITY", "VINDA_DO_PASSADO"}))

    def test_so_fracas_fora_do_grafo_nao_produz_gargalo(self):
        self.assertIsNone(MATH.primeiro_gargalo({"VINDA_DO_PASSADO"}))

    def test_o_desempate_e_estavel(self):
        """Duas habilidades igualmente básicas: a resposta não pode variar
        entre execuções, ou o aluno veria a intervenção mudar sozinha."""
        primeiro = MATH.primeiro_gargalo({"EQUALITY", "BASIC_OPERATIONS"})
        for _ in range(5):
            self.assertEqual(primeiro,
                             MATH.primeiro_gargalo({"BASIC_OPERATIONS", "EQUALITY"}))


class OGrafoSeRecusaAEstarErrado(unittest.TestCase):

    def test_ciclo_e_recusado_na_construcao(self):
        """Um ciclo faria `primeiro_gargalo` nunca terminar - e a falha
        apareceria em produção, no meio da jornada de um aluno."""
        with self.assertRaises(CicloDePreRequisito):
            GrafoPedagogico(conteudo="X", habilidades=[
                MicroHabilidade(code="A", label="A", objetivo="",
                                prerequisitos=("B",)),
                MicroHabilidade(code="B", label="B", objetivo="",
                                prerequisitos=("A",)),
            ])

    def test_pre_requisito_para_habilidade_inexistente_e_recusado(self):
        with self.assertRaises(ValueError):
            GrafoPedagogico(conteudo="X", habilidades=[
                MicroHabilidade(code="A", label="A", objetivo="",
                                prerequisitos=("NAO_EXISTE",)),
            ])

    def test_codigo_repetido_e_recusado(self):
        with self.assertRaises(ValueError):
            GrafoPedagogico(conteudo="X", habilidades=[
                MicroHabilidade(code="A", label="A", objetivo=""),
                MicroHabilidade(code="A", label="A de novo", objetivo=""),
            ])


class ONUCLEOnaoSABEQUIMICA(unittest.TestCase):
    """O teste que impede a segunda disciplina de exigir um segundo motor."""

    def test_o_modulo_nao_cita_assunto_nenhum(self):
        import pathlib

        fonte = (pathlib.Path(__file__).resolve().parent.parent
                 / "src/agente_ia_edu/services/grafo_pedagogico.py"
                 ).read_text(encoding="utf-8")
        for assunto in ("CHEMISTRY", "STOICHIOMETRY", "MOLAR", "quimica",
                        "química", "mol ", "átomo"):
            with self.subTest(assunto=assunto):
                self.assertNotIn(assunto.lower(), fonte.lower())


if __name__ == "__main__":
    unittest.main()
