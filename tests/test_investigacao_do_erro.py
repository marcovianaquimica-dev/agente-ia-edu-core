"""INVESTIGAR ANTES DE RESOLVER — e nunca afirmar o que não se mediu.

O QUE MUDA
===========
Até aqui, errar levava a uma explicação: o sistema despejava a resolução
inteira e esperava que a parte que faltava estivesse ali dentro. Para quem
errou só a última etapa, isso é ouvir de novo o que já sabia; para quem errou
a primeira, é ouvir três etapas construídas sobre a que falhou.

A investigação troca o despejo por uma pergunta curta de cada vez:

    ERRO -> hipótese -> micropergunta -> resposta -> gargalo localizado

A PARTE DELICADA É A HIPÓTESE
==============================
O distrator SUGERE um raciocínio. Ele não o prova. Um aluno que marcou
8,50 g pode ter pulado a proporção, pode ter errado a massa molar, pode ter
chutado. Dizer "você esqueceu de usar a proporção" afirma sobre a cabeça de
alguém a partir de uma letra marcada.

Metade deste arquivo trava a regra nova. A outra metade trava a linguagem —
e é a que importa mais, porque é a que um refactor futuro quebra sem perceber.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    LEITURA_FORMULA,
    MASSA_MOL,
    MASSA_MOLAR,
    PROPORCAO,
)
from agente_ia_edu.services.investigacao_do_erro import (
    INVESTIGACOES,
    conferir,
    gargalo,
    investigacao_para,
    para_o_aluno,
    proxima_etapa,
)


def _respostas_certas(inv, ate: int) -> dict[int, str]:
    return {e.ordem: e.correta for e in inv.etapas if e.ordem <= ate}


class AInvestigacaoEXISTEParaAsHabilidadesDoPiloto(unittest.TestCase):

    def test_massa_molar_tem_investigacao(self):
        self.assertIsNotNone(investigacao_para(CONTEUDO, MASSA_MOLAR))

    def test_leitura_de_formula_tem_investigacao(self):
        self.assertIsNotNone(investigacao_para(CONTEUDO, LEITURA_FORMULA))

    def test_proporcao_tem_investigacao(self):
        self.assertIsNotNone(investigacao_para(CONTEUDO, PROPORCAO))

    def test_massa_mol_tem_investigacao(self):
        self.assertIsNotNone(investigacao_para(CONTEUDO, MASSA_MOL))

    def test_habilidade_sem_investigacao_devolve_nada(self):
        """Inventar uma investigação genérica seria pior que não ter: o aluno
        responderia perguntas que não localizam coisa nenhuma."""
        self.assertIsNone(investigacao_para(CONTEUDO, "HABILIDADE_INEXISTENTE"))

    def test_conteudo_de_outra_disciplina_devolve_nada(self):
        self.assertIsNone(investigacao_para("MATH-ALGEBRA", MASSA_MOLAR))


class ACONTADECADAETAPAECONFERIDA(unittest.TestCase):
    """O mesmo fail-closed da sondagem: a suíte refaz, não revisa."""

    def test_nenhuma_etapa_afirma_conta_errada(self):
        self.assertEqual([], conferir())


class ACADEIADOMASSAMOLAR(unittest.TestCase):
    """§6 do bloco: NH₃, N = 14, H = 1 — e uma micropergunta de cada vez."""

    def setUp(self):
        self.inv = investigacao_para(CONTEUDO, MASSA_MOLAR)

    def test_comeca_pela_leitura_da_formula(self):
        """Antes de somar massas é preciso saber que há 3 H. Perguntar a
        contribuição dos hidrogênios a quem lê 1 H não localiza nada."""
        self.assertEqual(LEITURA_FORMULA, self.inv.etapas[0].habilidade)

    def test_a_primeira_pergunta_e_sobre_quantos_hidrogenios(self):
        self.assertIn("hidrog", self.inv.etapas[0].pergunta.lower())

    def test_as_etapas_sobem_da_leitura_para_a_soma(self):
        self.assertEqual([LEITURA_FORMULA, MASSA_MOLAR, MASSA_MOLAR],
                         [e.habilidade for e in self.inv.etapas])

    def test_a_ordem_e_sequencial_a_partir_de_um(self):
        self.assertEqual([1, 2, 3], [e.ordem for e in self.inv.etapas])


class ACADEIADO850g(unittest.TestCase):
    """§8/§30: N₂ + 3 H₂ → 2 NH₃, 14,0 g de N₂, o aluno marcou 8,50 g."""

    def setUp(self):
        self.inv = investigacao_para(CONTEUDO, PROPORCAO)

    def test_a_primeira_etapa_confere_massa_para_mol(self):
        self.assertEqual(MASSA_MOL, self.inv.etapas[0].habilidade)
        self.assertIn("14", self.inv.etapas[0].pergunta)

    def test_a_resposta_da_primeira_etapa_e_meio_mol(self):
        e = self.inv.etapas[0]
        self.assertIn("0,5", e.alternativas[e.correta])

    def test_a_segunda_etapa_confere_a_proporcao(self):
        self.assertEqual(PROPORCAO, self.inv.etapas[1].habilidade)

    def test_a_resposta_da_segunda_etapa_e_um_mol_de_amonia(self):
        e = self.inv.etapas[1]
        self.assertIn("1,0", e.alternativas[e.correta])

    def test_a_terceira_etapa_volta_de_mol_para_massa(self):
        self.assertEqual(MASSA_MOL, self.inv.etapas[2].habilidade)

    def test_a_resposta_da_terceira_etapa_e_17_g(self):
        e = self.inv.etapas[2]
        self.assertIn("17", e.alternativas[e.correta])

    def test_a_cadeia_inteira_fecha_na_resposta_da_questao(self):
        """17,0 g — a resposta correta da questão observada no navegador."""
        from agente_ia_edu.services.massa_molar import (
            massa_para_mol, mol_para_massa, por_proporcao,
        )
        mols_n2 = massa_para_mol(14.0, "N2")
        mols_nh3 = por_proporcao(mols_n2, 1, 2)
        self.assertAlmostEqual(17.0, mol_para_massa(mols_nh3, "NH3"))

    def test_8_50_e_exatamente_o_que_sai_de_pular_a_proporcao(self):
        """A hipótese não é um palpite solto: 0,5 x 17 = 8,5. É isso que a
        torna uma hipótese TESTÁVEL pela segunda micropergunta."""
        from agente_ia_edu.services.massa_molar import (
            massa_para_mol, mol_para_massa,
        )
        self.assertAlmostEqual(
            8.5, mol_para_massa(massa_para_mol(14.0, "N2"), "NH3"))


class AHIPOTESENAOVIRACERTEZA(unittest.TestCase):
    """§9. A metade que um refactor quebra sem perceber."""

    # Frases que AFIRMAM o raciocínio interno de alguém a partir de uma letra
    # marcada. Nenhuma delas pode aparecer em texto que chega ao aluno.
    ACUSATORIAS = (
        "você fez", "você calculou", "você esqueceu", "você errou",
        "você não", "você confundiu", "você pulou", "você trocou",
        "o que você fez foi", "seu erro foi", "você aplicou",
    )
    # E pelo menos uma destas precisa aparecer: é o que marca a frase como
    # hipótese em vez de diagnóstico.
    HEDGES = ("sugere", "pode ter", "pode estar", "talvez", "vamos conferir",
              "vamos olhar", "costuma", "parece")

    def test_toda_hipotese_e_formulada_como_hipotese(self):
        for inv in INVESTIGACOES:
            with self.subTest(inv=inv.key):
                baixo = inv.hipotese.lower()
                self.assertTrue(
                    any(h in baixo for h in self.HEDGES),
                    f"{inv.key}: a hipótese afirma em vez de supor: "
                    f"{inv.hipotese!r}")

    def test_nenhuma_hipotese_acusa_o_aluno(self):
        for inv in INVESTIGACOES:
            for frase in self.ACUSATORIAS:
                with self.subTest(inv=inv.key, frase=frase):
                    self.assertNotIn(frase, inv.hipotese.lower())

    def test_nenhum_texto_de_etapa_acusa_o_aluno(self):
        for inv in INVESTIGACOES:
            for e in inv.etapas:
                for texto in (e.pergunta, e.se_acertar, e.se_errar):
                    for frase in self.ACUSATORIAS:
                        with self.subTest(inv=inv.key, ordem=e.ordem,
                                          frase=frase):
                            self.assertNotIn(frase, texto.lower())

    def test_o_se_errar_ENSINA_em_vez_de_dar_a_letra(self):
        """"A resposta é C" encerra a etapa sem ensinar nada."""
        for inv in INVESTIGACOES:
            for e in inv.etapas:
                with self.subTest(inv=inv.key, ordem=e.ordem):
                    baixo = e.se_errar.lower()
                    self.assertNotIn("a resposta é", baixo)
                    self.assertNotIn("alternativa correta", baixo)
                    # E não pode ser só a letra, tampouco o texto dela.
                    self.assertNotEqual(
                        e.alternativas[e.correta].lower().strip(),
                        baixo.strip())

    def test_o_se_errar_tem_substancia(self):
        """Uma frase de cinco palavras não ensina a etapa."""
        for inv in INVESTIGACOES:
            for e in inv.etapas:
                with self.subTest(inv=inv.key, ordem=e.ordem):
                    self.assertGreaterEqual(len(e.se_errar.split()), 12)


class OGARGALOEONDEAPRIMEIRAETAPAFALHOU(unittest.TestCase):

    def setUp(self):
        self.inv = investigacao_para(CONTEUDO, PROPORCAO)

    def test_sem_resposta_nenhuma_nao_ha_gargalo(self):
        self.assertIsNone(gargalo(self.inv, {}))

    def test_tudo_certo_nao_ha_gargalo(self):
        self.assertIsNone(gargalo(self.inv, _respostas_certas(self.inv, 3)))

    def test_errar_a_primeira_localiza_a_habilidade_dela(self):
        e = self.inv.etapas[0]
        errada = next(k for k in e.alternativas if k != e.correta)
        self.assertEqual(MASSA_MOL, gargalo(self.inv, {1: errada}))

    def test_errar_a_segunda_depois_de_acertar_a_primeira_localiza_a_proporcao(self):
        e2 = self.inv.etapas[1]
        errada = next(k for k in e2.alternativas if k != e2.correta)
        respostas = {1: self.inv.etapas[0].correta, 2: errada}
        self.assertEqual(PROPORCAO, gargalo(self.inv, respostas))

    def test_a_primeira_falha_vence_a_segunda(self):
        """Errar duas etapas não quer dizer duas lacunas: a segunda se apoia
        na primeira, e começar pela mais alta é ensinar o telhado."""
        e1, e2 = self.inv.etapas[0], self.inv.etapas[1]
        respostas = {
            1: next(k for k in e1.alternativas if k != e1.correta),
            2: next(k for k in e2.alternativas if k != e2.correta),
        }
        self.assertEqual(MASSA_MOL, gargalo(self.inv, respostas))


class APROXIMAETAPAEAPRIMEIRASEMRESPOSTA(unittest.TestCase):

    def setUp(self):
        self.inv = investigacao_para(CONTEUDO, MASSA_MOLAR)

    def test_no_comeco_e_a_primeira(self):
        self.assertEqual(1, proxima_etapa(self.inv, {}).ordem)

    def test_depois_de_acertar_a_primeira_e_a_segunda(self):
        respostas = _respostas_certas(self.inv, 1)
        self.assertEqual(2, proxima_etapa(self.inv, respostas).ordem)

    def test_depois_de_todas_nao_ha_proxima(self):
        respostas = _respostas_certas(self.inv, len(self.inv.etapas))
        self.assertIsNone(proxima_etapa(self.inv, respostas))

    def test_errar_NAO_avanca_a_etapa(self):
        """Quem errou a etapa 1 precisa da etapa 1, não da 2."""
        e = self.inv.etapas[0]
        errada = next(k for k in e.alternativas if k != e.correta)
        self.assertEqual(1, proxima_etapa(self.inv, {1: errada}).ordem)


class OGABARITONAOVIAJAANTESDAHORA(unittest.TestCase):
    """A visão do aluno não pode carregar a resposta de uma etapa aberta."""

    def setUp(self):
        self.inv = investigacao_para(CONTEUDO, PROPORCAO)

    def test_a_etapa_aberta_chega_sem_a_letra_correta(self):
        visao = para_o_aluno(self.inv, {})
        self.assertNotIn("correta", visao["etapa"])

    def test_nem_o_texto_da_alternativa_certa_vem_marcado(self):
        visao = para_o_aluno(self.inv, {})
        for opcao in visao["etapa"]["options"]:
            with self.subTest(opcao=opcao["key"]):
                self.assertNotIn("correct", opcao)
                self.assertNotIn("is_correct", opcao)

    def test_a_serializacao_inteira_nao_contem_a_letra_de_uma_etapa_aberta(self):
        """Grep na saída: se a letra viajar em qualquer campo, isto pega."""
        import json
        bruto = json.dumps(para_o_aluno(self.inv, {}), ensure_ascii=False)
        self.assertNotIn('"correta"', bruto)

    def test_so_as_etapas_ja_respondidas_mostram_o_que_era(self):
        respostas = _respostas_certas(self.inv, 1)
        visao = para_o_aluno(self.inv, respostas)
        feitas = visao["concluidas"]
        self.assertEqual(1, len(feitas))
        self.assertEqual(self.inv.etapas[0].correta, feitas[0]["correct_option"])

    def test_no_fim_a_investigacao_se_declara_concluida(self):
        respostas = _respostas_certas(self.inv, len(self.inv.etapas))
        visao = para_o_aluno(self.inv, respostas)
        self.assertTrue(visao["completed"])
        self.assertIsNone(visao["etapa"])


class INVESTIGARNAOEDOMINIO(unittest.TestCase):
    """§13: a micropergunta serve ao diagnóstico, não ao mapa."""

    FONTE = (pathlib.Path(__file__).resolve().parent.parent
             / "src/agente_ia_edu/services/investigacao_do_erro.py")

    def test_o_modulo_nao_toca_banco_nem_provedor(self):
        baixo = self.FONTE.read_text(encoding="utf-8").lower()
        for proibido in ("asyncsession", "sqlalchemy", "db.models",
                         "textgenerationprovider", "build_text_provider"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, baixo)

    def test_o_modulo_nao_importa_a_politica_de_dominio(self):
        arvore = ast.parse(self.FONTE.read_text(encoding="utf-8"))
        nomes: list[str] = []
        for no in ast.walk(arvore):
            if isinstance(no, ast.ImportFrom):
                nomes.append(no.module or "")
                nomes += [a.name for a in no.names]
            elif isinstance(no, ast.Import):
                nomes += [a.name for a in no.names]
        for proibido in ("pedagogical_analysis", "PerformanceThresholdPolicy",
                         "curriculum_domain_map"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, nomes)

    def test_nenhuma_funcao_do_modulo_devolve_algo_chamado_mastery(self):
        bruto = self.FONTE.read_text(encoding="utf-8").lower()
        for proibido in ("mastery", "dominio_confirmado", "domina"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(f'"{proibido}"', bruto)


if __name__ == "__main__":
    unittest.main()
