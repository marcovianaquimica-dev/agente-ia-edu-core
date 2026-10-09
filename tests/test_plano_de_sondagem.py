"""QUEM DECIDE O QUE SONDAR — o grafo, e não o acervo.

§8 do bloco. A sondagem não é "pegue cinco perguntas de Estequiometria":
é "o cérebro escolheu micro-habilidades relevantes; agora obtenha um
instrumento para cada uma".

Este arquivo trava a primeira metade. A segunda —
`test_instrumento_de_sondagem` — trava a escolha do instrumento.

O QUE PODE SER CONFUNDIDO, E NÃO PODE
======================================
O plano PULA habilidades sem instrumento. Isso parece o acervo decidindo a
pedagogia, e não é: a ORDEM continua sendo do grafo, da base ao topo, e o
acervo só responde onde existe com que medir. Há teste de que mudar o acervo
não reordena o plano — só encurta.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from agente_ia_edu.services.grafo_pedagogico import (
    GrafoPedagogico,
    MicroHabilidade,
)
from agente_ia_edu.services.plano_de_sondagem import ordem_de_base, planejar

# Grafo de brinquedo, sem química: o plano não é de Estequiometria.
#
#   BASE_A   BASE_B
#     |        |
#   MEIO_A   MEIO_B
#       \     /
#        TOPO
TOY = GrafoPedagogico(
    conteudo="TOY-1",
    habilidades=[
        MicroHabilidade(code="TOPO", label="Topo", objetivo="",
                        prerequisitos=("MEIO_A", "MEIO_B")),
        MicroHabilidade(code="MEIO_A", label="Meio A", objetivo="",
                        prerequisitos=("BASE_A",)),
        MicroHabilidade(code="BASE_A", label="Base A", objetivo=""),
        MicroHabilidade(code="MEIO_B", label="Meio B", objetivo="",
                        prerequisitos=("BASE_B",)),
        MicroHabilidade(code="BASE_B", label="Base B", objetivo=""),
    ],
)


class AORDEMEDABASEPARAOTOPO(unittest.TestCase):

    def test_as_bases_vem_antes_dos_meios_e_do_topo(self):
        ordem = ordem_de_base(TOY)
        for base in ("BASE_A", "BASE_B"):
            for acima in ("MEIO_A", "MEIO_B", "TOPO"):
                with self.subTest(base=base, acima=acima):
                    self.assertLess(ordem.index(base), ordem.index(acima))

    def test_o_topo_vem_por_ultimo(self):
        self.assertEqual("TOPO", ordem_de_base(TOY)[-1])

    def test_a_ordem_nao_depende_da_ordem_de_declaracao(self):
        """TOY declara TOPO primeiro, de propósito."""
        self.assertNotEqual(TOY.codigos()[0], ordem_de_base(TOY)[0])

    def test_a_ordem_e_estavel(self):
        self.assertEqual(ordem_de_base(TOY), ordem_de_base(TOY))

    def test_todas_as_habilidades_aparecem_uma_vez(self):
        ordem = ordem_de_base(TOY)
        self.assertEqual(sorted(TOY.codigos()), sorted(ordem))
        self.assertEqual(len(set(ordem)), len(ordem))


class OPLANOPEGAASPRIMEIRASMENSURAVEIS(unittest.TestCase):

    def test_sem_filtro_pega_as_primeiras_da_ordem(self):
        plano = planejar(TOY, quantas=2)
        self.assertEqual(("BASE_A", "BASE_B"), plano.habilidades)

    def test_habilidade_sem_instrumento_e_pulada(self):
        plano = planejar(TOY, quantas=2,
                         mensuravel=lambda c: c != "BASE_A")
        self.assertEqual(("BASE_B", "MEIO_A"), plano.habilidades)

    def test_e_ela_aparece_no_relatorio_do_que_faltou(self):
        """Falta de instrumento é informação, não detalhe silencioso."""
        plano = planejar(TOY, quantas=2, mensuravel=lambda c: c != "BASE_A")
        self.assertIn("BASE_A", plano.sem_instrumento)

    def test_pular_NAO_reordena_o_resto(self):
        """A regra central: o acervo encurta o plano, nunca o reordena."""
        completo = [c for c in ordem_de_base(TOY) if c != "BASE_A"]
        plano = planejar(TOY, quantas=len(completo),
                         mensuravel=lambda c: c != "BASE_A")
        self.assertEqual(tuple(completo), plano.habilidades)

    def test_nenhuma_mensuravel_devolve_plano_vazio_e_nao_um_chute(self):
        plano = planejar(TOY, quantas=3, mensuravel=lambda c: False)
        self.assertEqual((), plano.habilidades)
        self.assertEqual(len(TOY.codigos()), len(plano.sem_instrumento))

    def test_menos_mensuraveis_que_o_pedido_devolve_o_que_ha(self):
        plano = planejar(TOY, quantas=5,
                         mensuravel=lambda c: c in ("BASE_A", "TOPO"))
        self.assertEqual(("BASE_A", "TOPO"), plano.habilidades)
        self.assertEqual(5, plano.pedidas)

    def test_quem_chama_consegue_saber_que_o_plano_veio_curto(self):
        plano = planejar(TOY, quantas=5, mensuravel=lambda c: c == "BASE_A")
        self.assertLess(len(plano.habilidades), plano.pedidas)

    def test_pedir_zero_nao_estoura(self):
        self.assertEqual((), planejar(TOY, quantas=0).habilidades)

    def test_pedir_negativo_nao_estoura(self):
        self.assertEqual((), planejar(TOY, quantas=-3).habilidades)

    def test_o_plano_e_deterministico(self):
        planos = {planejar(TOY, quantas=3).habilidades for _ in range(30)}
        self.assertEqual(1, len(planos))


class JAMEDIDASSAEMDAFRENTE(unittest.TestCase):

    def test_habilidade_ja_medida_nao_e_sondada_de_novo(self):
        plano = planejar(TOY, quantas=2, ja_medidas=["BASE_A"])
        self.assertNotIn("BASE_A", plano.habilidades)

    def test_e_ela_nao_entra_no_relatorio_de_falta_de_instrumento(self):
        """Ter sido medida não é o mesmo que não ter instrumento."""
        plano = planejar(TOY, quantas=2, ja_medidas=["BASE_A"])
        self.assertNotIn("BASE_A", plano.sem_instrumento)


class OPLANONAOCONHECEQUIMICA(unittest.TestCase):

    FONTE = (pathlib.Path(__file__).resolve().parent.parent
             / "src/agente_ia_edu/services/plano_de_sondagem.py")

    def test_o_modulo_nao_importa_conteudo_de_disciplina_nenhuma(self):
        arvore = ast.parse(self.FONTE.read_text(encoding="utf-8"))
        nomes: list[str] = []
        for no in ast.walk(arvore):
            if isinstance(no, ast.ImportFrom):
                nomes.append(no.module or "")
                nomes += [a.name for a in no.names]
            elif isinstance(no, ast.Import):
                nomes += [a.name for a in no.names]
        for proibido in ("grafo_estequiometria", "sondagem_estequiometria",
                         "conteudo_estequiometria", "question_bank"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, nomes)

    def test_o_modulo_nao_toca_banco(self):
        baixo = self.FONTE.read_text(encoding="utf-8").lower()
        for proibido in ("asyncsession", "sqlalchemy", "db.models"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, baixo)

    def test_o_modulo_nao_importa_a_politica_de_dominio(self):
        """§7 — planejar o que perguntar não é medir o que ele sabe."""
        arvore = ast.parse(self.FONTE.read_text(encoding="utf-8"))
        nomes: list[str] = []
        for no in ast.walk(arvore):
            if isinstance(no, ast.ImportFrom):
                nomes.append(no.module or "")
        for proibido in ("pedagogical_analysis", "curriculum_domain_map"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, nomes)


class OGRAFODEESTEQUIOMETRIAPLANEJADODEVERDADE(unittest.TestCase):
    """O piloto, com o grafo real — mas sem exigir instrumento nenhum."""

    def setUp(self):
        from agente_ia_edu.services.grafo_estequiometria import GRAFO
        self.grafo = GRAFO

    def test_a_leitura_de_formula_vem_antes_de_massa_molar(self):
        """Massa molar declara leitura de fórmula como pré-requisito."""
        ordem = ordem_de_base(self.grafo)
        self.assertLess(ordem.index("LEITURA_DE_FORMULA"),
                        ordem.index("MASSA_MOLAR"))

    def test_o_problema_completo_vem_por_ultimo(self):
        self.assertEqual("ESTEQUIOMETRIA_INTEGRADA",
                         ordem_de_base(self.grafo)[-1])

    def test_massa_mol_vem_depois_de_massa_molar(self):
        ordem = ordem_de_base(self.grafo)
        self.assertLess(ordem.index("MASSA_MOLAR"),
                        ordem.index("RELACAO_MASSA_MOL"))

    def test_sem_as_duas_sem_acervo_o_plano_de_tres_e_o_esperado(self):
        """As duas que o acervo não cobre — conceito de mol e leitura de
        coeficiente — não têm questão classificada nenhuma, medido em
        2026-10-07.

        Eu escrevi `RELACAO_MASSA_MOL` como terceira e o teste reprovou: a
        profundidade de `PROPORCAO_ESTEQUIOMETRICA` é 1 (só leitura de
        coeficiente embaixo), e a de `RELACAO_MASSA_MOL` é 3 (massa molar,
        leitura de fórmula e conceito de mol). O código estava certo e a
        minha expectativa não — e a ordem que ele produz é melhor: ler a
        fórmula, massa molar e proporção são três degraus independentes, e
        as três têm item curado.
        """
        sem_acervo = {"CONCEITO_DE_MOL", "LEITURA_DE_COEFICIENTE"}
        plano = planejar(self.grafo, quantas=3,
                         mensuravel=lambda c: c not in sem_acervo)
        self.assertEqual(
            ("LEITURA_DE_FORMULA", "MASSA_MOLAR",
             "PROPORCAO_ESTEQUIOMETRICA"),
            plano.habilidades)
        self.assertEqual(sorted(sem_acervo), sorted(plano.sem_instrumento))

    def test_a_proporcao_e_sondada_sem_que_o_pre_requisito_dela_seja(self):
        """Dívida registrada, não escondida.

        `PROPORCAO_ESTEQUIOMETRICA` depende de `LEITURA_DE_COEFICIENTE`, que
        não tem instrumento nenhum. A sondagem pergunta o dependente sem
        poder conferir a base — e, se o aluno errar, o grafo apontará a
        proporção quando o problema pode estar um degrau abaixo.
        """
        sem_acervo = {"CONCEITO_DE_MOL", "LEITURA_DE_COEFICIENTE"}
        plano = planejar(self.grafo, quantas=3,
                         mensuravel=lambda c: c not in sem_acervo)
        self.assertIn("PROPORCAO_ESTEQUIOMETRICA", plano.habilidades)
        self.assertIn("LEITURA_DE_COEFICIENTE", plano.sem_instrumento)
        self.assertIn("LEITURA_DE_COEFICIENTE",
                      self.grafo.prerequisitos("PROPORCAO_ESTEQUIOMETRICA"))


if __name__ == "__main__":
    unittest.main()
