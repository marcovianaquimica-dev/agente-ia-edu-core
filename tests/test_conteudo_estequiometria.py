"""O CONTEÚDO CURADO DE ESTEQUIOMETRIA — conferido antes de chegar ao aluno.

POR QUE ISTO É CÓDIGO E NÃO UMA LINHA NO BANCO
===============================================
O mesmo motivo de `conteudo_balanceamento`: sendo dado estruturado no
repositório, cada número entra na suíte. A diferença é o verificador — lá é
contagem de átomos (`chemistry_balance`), aqui é aritmética de massas
(`massa_molar`). Um erro de digitação em "17 g/mol" derruba o teste, não a
aprendizagem de alguém.

O QUE O MATERIAL PRECISA TER, POR MICRO-HABILIDADE (§5 do bloco)
=================================================================
objetivo, pré-requisitos, erro esperado, explicação, exemplo resolvido,
verificação. As perguntas guiadas e a prática com apoio NÃO estão aqui: elas
são `investigacao_do_erro` e `itens_guiados`, e duplicá-las no material
criaria duas versões da mesma coisa divergindo na primeira correção.

O QUE ESTE ARQUIVO TRAVA
=========================
1. toda conta afirmada pelo conteúdo é refeita;
2. os pré-requisitos declarados são os do grafo, não outros;
3. subscritos químicos preservados (teste O do bloco);
4. nenhum passo depende de imagem para o raciocínio matemático;
5. a linguagem não acusa o aluno.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.conteudo_estequiometria import (
    CONTENT_CODE,
    MATERIAL,
    MICRO_HABILIDADES,
    SECOES,
    conferir,
    passos_do_exemplo,
)
from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    GRAFO,
    LEITURA_FORMULA,
    MASSA_MOL,
    MASSA_MOLAR,
    PROPORCAO,
)

PRIORITARIAS = (LEITURA_FORMULA, MASSA_MOLAR, MASSA_MOL, PROPORCAO)


class ACONTAEREFEITA(unittest.TestCase):
    """O fail-closed. Sem isto, nada mais neste arquivo importa."""

    def test_nenhuma_conta_do_conteudo_esta_errada(self):
        self.assertEqual([], conferir())


class OCONTEUDOCOBREASHABILIDADESDOPILOTO(unittest.TestCase):

    def test_o_codigo_e_o_do_catalogo(self):
        self.assertEqual(CONTEUDO, CONTENT_CODE)

    def test_as_quatro_habilidades_prioritarias_tem_conteudo(self):
        for s in PRIORITARIAS:
            with self.subTest(s=s):
                self.assertIn(s, MICRO_HABILIDADES)

    def test_nenhuma_habilidade_fora_do_grafo(self):
        """Conteúdo sobre uma habilidade que o grafo não conhece nunca seria
        escolhido como alvo — ficaria escrito e nunca seria servido."""
        for s in MICRO_HABILIDADES:
            with self.subTest(s=s):
                self.assertIn(s, GRAFO.codigos())

    def test_cada_uma_declara_tudo_o_que_o_bloco_exige(self):
        exigidos = ("objetivo", "erro_esperado", "explicacao", "exemplo",
                    "verificacao")
        for s, dados in MICRO_HABILIDADES.items():
            for campo in exigidos:
                with self.subTest(s=s, campo=campo):
                    self.assertTrue(str(dados.get(campo) or "").strip(),
                                    f"{s} sem {campo}")


class OSPREREQUISITOSSAOOSDOGRAFO(unittest.TestCase):
    """Duas listas de pré-requisitos divergiriam na primeira correção."""

    def test_cada_habilidade_repete_o_que_o_grafo_diz(self):
        for s, dados in MICRO_HABILIDADES.items():
            with self.subTest(s=s):
                self.assertEqual(tuple(GRAFO.prerequisitos(s)),
                                 tuple(dados["prerequisitos"]))


class OEXEMPLOESEQUENCIALEVISIVEL(unittest.TestCase):
    """§17: passo a passo, e não um parágrafo com a conta no meio."""

    def test_cada_exemplo_tem_pelo_menos_dois_passos(self):
        for s, dados in MICRO_HABILIDADES.items():
            with self.subTest(s=s):
                self.assertGreaterEqual(len(dados["exemplo"]["passos"]), 2)

    def test_cada_passo_tem_rotulo_conta_e_resultado(self):
        for s, dados in MICRO_HABILIDADES.items():
            for p in dados["exemplo"]["passos"]:
                with self.subTest(s=s, rotulo=p.get("rotulo")):
                    for campo in ("rotulo", "conta", "resultado", "fala"):
                        self.assertTrue(str(p.get(campo) or "").strip())

    def test_nenhum_passo_depende_de_imagem(self):
        """O raciocínio matemático essencial não pode viver num <img>."""
        for s, dados in MICRO_HABILIDADES.items():
            for p in dados["exemplo"]["passos"]:
                junto = f"{p['conta']} {p['resultado']} {p['fala']}".lower()
                with self.subTest(s=s, rotulo=p["rotulo"]):
                    for proibido in ("<img", "src=", ".png", ".jpg", ".svg"):
                        self.assertNotIn(proibido, junto)

    def test_passos_do_exemplo_devolve_um_bloco_por_habilidade(self):
        for s in MICRO_HABILIDADES:
            with self.subTest(s=s):
                blocos = passos_do_exemplo(s)
                self.assertEqual(1, len(blocos))
                self.assertEqual("STEP_SEQUENCE", blocos[0]["block_type"])
                self.assertTrue(blocos[0]["metadata"]["passos"])


class OSSUBSCRITOSSOBREVIVEM(unittest.TestCase):
    """Teste O do bloco. NH3 escrito assim ensina a fórmula errada."""

    SUBSCRITOS = "₀₁₂₃₄₅₆₇₈₉"

    def _todo_o_texto(self) -> str:
        pedacos = [MATERIAL["title"], MATERIAL["description"],
                   MATERIAL["introduction"], MATERIAL["summary"]]
        for dados in MICRO_HABILIDADES.values():
            pedacos += [dados["objetivo"], dados["erro_esperado"],
                        dados["explicacao"], dados["verificacao"]]
            pedacos.append(dados["exemplo"]["titulo"])
            for p in dados["exemplo"]["passos"]:
                pedacos += [p["rotulo"], p["conta"], p["resultado"], p["fala"]]
        return "\n".join(pedacos)

    def test_ha_subscritos_de_verdade_no_conteudo(self):
        self.assertTrue(any(c in self._todo_o_texto() for c in self.SUBSCRITOS))

    def test_nenhuma_formula_aparece_com_digito_ascii_colado(self):
        """"NH3" escrito com 3 comum, e não ₃, ensina a notação errada."""
        import re
        achados = re.findall(r"\b(?:NH|H|N|O|CO|Ca)\d", self._todo_o_texto())
        self.assertEqual([], achados, f"fórmulas sem subscrito: {achados}")


class ALINGUAGEMNAOACUSA(unittest.TestCase):
    """§18 — o tom do Assessor, varrido no conteúdo inteiro."""

    PROIBIDAS = ("você falhou", "você não domina", "resposta insuficiente",
                 "você errou", "deficiência", "você é fraco",
                 "não conseguiu", "incapaz")

    def test_nenhuma_frase_punitiva(self):
        texto = "\n".join(
            [MATERIAL["introduction"], MATERIAL["summary"]]
            + [d["explicacao"] for d in MICRO_HABILIDADES.values()]
            + [d["erro_esperado"] for d in MICRO_HABILIDADES.values()]
        ).lower()
        for frase in self.PROIBIDAS:
            with self.subTest(frase=frase):
                self.assertNotIn(frase, texto)

    def test_o_erro_esperado_descreve_o_ERRO_e_nao_o_aluno(self):
        """"Confunde índice com coeficiente" fala do erro; "o aluno é
        confuso" fala da pessoa."""
        for s, dados in MICRO_HABILIDADES.items():
            with self.subTest(s=s):
                self.assertNotIn("o aluno é", dados["erro_esperado"].lower())


class ASSECOESESTAOPRONTASPARAPUBLICACAO(unittest.TestCase):
    """O formato que `publicar_material_*` já sabe escrever."""

    def test_ha_uma_secao_por_micro_habilidade(self):
        self.assertEqual(len(MICRO_HABILIDADES), len(SECOES))

    def test_as_secoes_seguem_a_ordem_do_grafo(self):
        """Massa molar antes de leitura de fórmula seria o telhado antes da
        parede, e é a ordem em que o material é lido."""
        ordem = [s["skill"] for s in SECOES]
        for i, skill in enumerate(ordem):
            for pre in GRAFO.prerequisitos(skill):
                if pre in ordem:
                    with self.subTest(skill=skill, pre=pre):
                        self.assertLess(ordem.index(pre), i)

    def test_cada_secao_tem_posicao_titulo_e_blocos(self):
        for s in SECOES:
            with self.subTest(skill=s["skill"]):
                self.assertTrue(s["title"].strip())
                self.assertTrue(s["blocks"])
                self.assertEqual(CONTENT_CODE, s["content_code"])

    def test_as_posicoes_sao_unicas_e_sequenciais(self):
        self.assertEqual(list(range(1, len(SECOES) + 1)),
                         [s["position"] for s in SECOES])

    def test_cada_secao_carrega_um_exemplo_passo_a_passo(self):
        for s in SECOES:
            tipos = [b["block_type"] for b in s["blocks"]]
            with self.subTest(skill=s["skill"]):
                self.assertIn("STEP_SEQUENCE", tipos)


if __name__ == "__main__":
    unittest.main()
