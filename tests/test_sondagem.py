"""A SONDAGEM — primeiro a habilidade, depois o item.

O QUE MUDA EM RELAÇÃO AO QUE EXISTIA
=====================================
O diagnóstico anterior escolhia QUESTÕES: pegava as do conteúdo e, desde o
bloco passado, preferia as marcadas como fáceis. Isso é melhor que sortear
por número oficial, mas ainda responde à pergunta errada.

A pergunta certa vem antes: **qual micro-habilidade estou medindo agora?**
Só depois: qual item mede essa habilidade sem exigir as outras.

O QUE ESTE ARQUIVO TRAVA
=========================
A ordem (do mais básico para o mais dependente), a cobertura (uma habilidade
não medida não pode ser dada por sabida), e a leitura do resultado por
habilidade — nunca uma porcentagem só.

Metade usa um grafo sintético: a sondagem não pode saber de que disciplina
se trata.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.grafo_pedagogico import (
    GrafoPedagogico,
    MicroHabilidade,
)
from agente_ia_edu.services.sondagem import (
    ESTADO_CONFIRMADO,
    ESTADO_NAO_MEDIDO,
    ESTADO_PRECISA_APOIO,
    ItemDeSondagem,
    mapa_por_habilidade,
    roteiro_de_sondagem,
)

MATH = GrafoPedagogico(
    conteudo="MATH-EQ1",
    habilidades=[
        MicroHabilidade(code="OPS", label="Operações", objetivo=""),
        MicroHabilidade(code="EQUAL", label="Igualdade", objetivo=""),
        MicroHabilidade(code="INV", label="Inversas", objetivo="",
                        prerequisitos=("OPS",)),
        MicroHabilidade(code="SOLVE", label="Resolver", objetivo="",
                        prerequisitos=("EQUAL", "INV")),
    ],
)

ITENS = [
    ItemDeSondagem(key="i-solve", habilidade="SOLVE", enunciado="…",
                   alternativas={"A": "1", "B": "2"}, correta="A"),
    ItemDeSondagem(key="i-ops", habilidade="OPS", enunciado="…",
                   alternativas={"A": "1", "B": "2"}, correta="B"),
    ItemDeSondagem(key="i-inv", habilidade="INV", enunciado="…",
                   alternativas={"A": "1", "B": "2"}, correta="A"),
]


class OROTEIROVAIDOBASICOPARAOCOMPLEXO(unittest.TestCase):

    def test_a_ordem_segue_a_profundidade_no_grafo(self):
        roteiro = roteiro_de_sondagem(MATH, ITENS)
        self.assertEqual(["OPS", "INV", "SOLVE"],
                         [i.habilidade for i in roteiro])

    def test_habilidade_sem_item_simplesmente_nao_e_sondada(self):
        """Não inventar item: EQUAL não tem um, então não entra."""
        self.assertNotIn("EQUAL",
                         [i.habilidade for i in roteiro_de_sondagem(MATH, ITENS)])

    def test_item_de_habilidade_fora_do_grafo_fica_de_fora(self):
        intruso = ItemDeSondagem(key="x", habilidade="NAO_EXISTE",
                                 enunciado="…", alternativas={"A": "1"},
                                 correta="A")
        roteiro = roteiro_de_sondagem(MATH, ITENS + [intruso])
        self.assertNotIn("NAO_EXISTE", [i.habilidade for i in roteiro])

    def test_um_item_por_habilidade(self):
        duplicado = ItemDeSondagem(key="i-ops-2", habilidade="OPS",
                                   enunciado="…", alternativas={"A": "1"},
                                   correta="A")
        roteiro = roteiro_de_sondagem(MATH, ITENS + [duplicado])
        habilidades = [i.habilidade for i in roteiro]
        self.assertEqual(len(habilidades), len(set(habilidades)))

    def test_o_roteiro_e_estavel(self):
        self.assertEqual([i.key for i in roteiro_de_sondagem(MATH, ITENS)],
                         [i.key for i in roteiro_de_sondagem(MATH, ITENS)])

    def test_tamanho_limita_sem_quebrar_a_ordem(self):
        """Sondagem curta começa pelo mais básico, não por um sorteio."""
        roteiro = roteiro_de_sondagem(MATH, ITENS, tamanho=2)
        self.assertEqual(["OPS", "INV"], [i.habilidade for i in roteiro])


class ODIAGNOSTICONAOENOTA(unittest.TestCase):
    """2 de 5 = 40% não diz onde intervir. O mapa por habilidade diz."""

    def test_cada_habilidade_recebe_o_proprio_estado(self):
        mapa = mapa_por_habilidade(MATH, {"OPS": True, "INV": False})
        self.assertEqual(ESTADO_CONFIRMADO, mapa["OPS"])
        self.assertEqual(ESTADO_PRECISA_APOIO, mapa["INV"])

    def test_habilidade_nao_sondada_fica_nao_medida(self):
        """Não medir não é saber, e também não é não saber."""
        mapa = mapa_por_habilidade(MATH, {"OPS": True})
        self.assertEqual(ESTADO_NAO_MEDIDO, mapa["SOLVE"])
        self.assertEqual(ESTADO_NAO_MEDIDO, mapa["EQUAL"])

    def test_o_mapa_cobre_o_grafo_inteiro(self):
        mapa = mapa_por_habilidade(MATH, {})
        self.assertEqual(set(MATH.codigos()), set(mapa))

    def test_resposta_de_habilidade_fora_do_grafo_e_ignorada(self):
        mapa = mapa_por_habilidade(MATH, {"OPS": True, "FANTASMA": False})
        self.assertNotIn("FANTASMA", mapa)

    def test_as_fracas_sao_so_as_medidas_e_fracas(self):
        from agente_ia_edu.services.sondagem import fracas_do_mapa

        mapa = mapa_por_habilidade(MATH, {"OPS": True, "INV": False})
        self.assertEqual({"INV"}, set(fracas_do_mapa(mapa)))

    def test_nao_medida_nunca_vira_fraca(self):
        """Senão o aluno receberia intervenção sobre o que ninguém mediu."""
        from agente_ia_edu.services.sondagem import fracas_do_mapa

        mapa = mapa_por_habilidade(MATH, {})
        self.assertEqual(set(), set(fracas_do_mapa(mapa)))


class OITEMSECONFEREOUSERECUSA(unittest.TestCase):

    def test_correta_fora_das_alternativas_e_recusada(self):
        with self.assertRaises(ValueError):
            ItemDeSondagem(key="x", habilidade="OPS", enunciado="…",
                           alternativas={"A": "1", "B": "2"}, correta="Z")

    def test_item_sem_alternativa_e_recusado(self):
        with self.assertRaises(ValueError):
            ItemDeSondagem(key="x", habilidade="OPS", enunciado="…",
                           alternativas={}, correta="A")

    def test_enunciado_vazio_e_recusado(self):
        with self.assertRaises(ValueError):
            ItemDeSondagem(key="x", habilidade="OPS", enunciado="   ",
                           alternativas={"A": "1"}, correta="A")


class ASONDAGEMNAOSABEDEQUEDISCIPLINASETRATA(unittest.TestCase):

    def test_o_modulo_nao_cita_assunto(self):
        import pathlib

        fonte = (pathlib.Path(__file__).resolve().parent.parent
                 / "src/agente_ia_edu/services/sondagem.py"
                 ).read_text(encoding="utf-8")
        for assunto in ("chemistry", "quimica", "química", "estequiometria",
                        "molar", "átomo"):
            with self.subTest(assunto=assunto):
                self.assertNotIn(assunto, fonte.lower())

    def test_o_modulo_nao_chama_provider_de_ia(self):
        """A sondagem tem de funcionar com o provedor fora do ar."""
        import pathlib

        fonte = (pathlib.Path(__file__).resolve().parent.parent
                 / "src/agente_ia_edu/services/sondagem.py"
                 ).read_text(encoding="utf-8")
        for proibido in ("provider", "TextGeneration", "openai"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido.lower(), fonte.lower())


if __name__ == "__main__":
    unittest.main()
