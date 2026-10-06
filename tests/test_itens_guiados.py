"""Os itens da prática guiada, e a ajuda que não entrega a resposta.

A DIFERENÇA ENTRE AJUDA E RESPOSTA
===================================
Uma "dica" que diz qual alternativa marcar não ensina — encerra. O aluno sai
com a questão certa e a dúvida intacta.

Então cada nível aqui tem de reduzir uma dificuldade DIFERENTE:

    1  lembrar o conceito que vale
    2  dizer onde olhar
    3  dizer que operação fazer
    4  descrever o alvo, ainda sem apontá-lo

Até o último nível o aluno continua tendo de identificar a alternativa. Há
teste exigindo que nenhum nível cite a letra correta nem o texto dela.

A QUÍMICA CONTINUA SENDO CONFERIDA POR CONTAGEM
================================================
Cada alternativa declara se está balanceada, e a declaração passa pelo mesmo
verificador que já aprova os itens do banco diagnóstico. Exatamente uma
alternativa fecha — se duas fechassem, o item teria duas respostas certas.

DOIS CONJUNTOS, DOIS VERIFICADORES
===================================
Desde que Estequiometria ganhou itens próprios, `ITENS` tem dois conteúdos.
As asserções sobre EQUAÇÕES — contagem de átomos, `balanceada`, não repetir
a equação certa na dica — só fazem sentido para balanceamento, e por isso
iteram `ITENS_DE_BALANCEAMENTO`. Isso é um escopo, não um afrouxamento: as
garantias genéricas (nenhum nível entrega a letra, níveis sem buraco, o
gabarito não viaja) continuam varrendo `ITENS` inteiro, e a aritmética de
Estequiometria tem o verificador dela em
`test_itens_guiados_estequiometria.py`.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.chemistry_balance import equacao_balanceada
from agente_ia_edu.services.conteudo_balanceamento import para_exibicao
from agente_ia_edu.services.itens_guiados import (
    CONTENT_CODE as BALANCEAMENTO,
    NIVEIS_DE_AJUDA,
    ITENS,
    item_para,
    para_o_aluno,
)

# Só os que declaram equação. Ver o cabeçalho.
ITENS_DE_BALANCEAMENTO = [i for i in ITENS if i["content_code"] == BALANCEAMENTO]


class AQuimicaDeCadaItemEhConferidaTests(unittest.TestCase):
    """Balanceamento. A aritmética de Estequiometria tem arquivo próprio."""

    def test_cada_alternativa_esta_no_estado_declarado(self):
        for item in ITENS_DE_BALANCEAMENTO:
            for letra, alt in item["alternativas"].items():
                with self.subTest(item=item["key"], letra=letra):
                    self.assertEqual(
                        equacao_balanceada(alt["equacao"]), alt["balanceada"],
                        f"{alt['equacao']!r} declarada "
                        f"balanceada={alt['balanceada']}")

    def test_exatamente_uma_alternativa_fecha(self):
        """Duas que fechassem dariam duas respostas certas; nenhuma, nenhuma."""
        for item in ITENS_DE_BALANCEAMENTO:
            fecham = [l for l, a in item["alternativas"].items() if a["balanceada"]]
            with self.subTest(item=item["key"]):
                self.assertEqual(len(fecham), 1, f"fecham: {fecham}")

    def test_a_correta_e_a_que_fecha(self):
        for item in ITENS_DE_BALANCEAMENTO:
            fecha = next(l for l, a in item["alternativas"].items() if a["balanceada"])
            with self.subTest(item=item["key"]):
                self.assertEqual(item["correta"], fecha)


class AAjudaNaoEntregaARespostaTests(unittest.TestCase):

    def test_nenhum_nivel_cita_a_letra_correta(self):
        """"Marque a B" não é dica, é gabarito."""
        for item in ITENS:
            letra = item["correta"]
            for ajuda in item["ajudas"]:
                with self.subTest(item=item["key"], nivel=ajuda["nivel"]):
                    for forma in (f" {letra} ", f"({letra})", f"alternativa {letra}",
                                  f"letra {letra}"):
                        self.assertNotIn(forma, ajuda["texto"],
                                         f"a ajuda cita {forma!r}")

    def test_nenhum_nivel_reproduz_a_equacao_correta_inteira(self):
        """Escrever a equação certa por extenso é apontar a alternativa."""
        for item in ITENS_DE_BALANCEAMENTO:
            crua = item["alternativas"][item["correta"]]["equacao"]
            for ajuda in item["ajudas"]:
                with self.subTest(item=item["key"], nivel=ajuda["nivel"]):
                    for forma in (crua, para_exibicao(crua)):
                        self.assertNotIn(forma, ajuda["texto"])

    def test_os_niveis_sao_crescentes_e_sem_buraco(self):
        for item in ITENS:
            niveis = [a["nivel"] for a in item["ajudas"]]
            with self.subTest(item=item["key"]):
                self.assertEqual(niveis, list(range(1, len(niveis) + 1)))

    def test_cada_nivel_tem_um_tipo_conhecido(self):
        for item in ITENS:
            for ajuda in item["ajudas"]:
                with self.subTest(item=item["key"], nivel=ajuda["nivel"]):
                    self.assertIn(ajuda["tipo"], NIVEIS_DE_AJUDA)

    def test_cada_nivel_diz_algo_diferente_do_anterior(self):
        """Três reformulações da mesma frase não são ajuda progressiva."""
        for item in ITENS:
            textos = [a["texto"] for a in item["ajudas"]]
            with self.subTest(item=item["key"]):
                self.assertEqual(len(set(textos)), len(textos))

    def test_ha_mais_de_um_nivel(self):
        for item in ITENS:
            with self.subTest(item=item["key"]):
                self.assertGreater(len(item["ajudas"]), 1)


class OQueChegaAoAlunoTests(unittest.TestCase):
    """`para_o_aluno` é o que a API devolve. O que não estiver aqui não vaza."""

    def test_a_resposta_correta_nao_viaja(self):
        for item in ITENS:
            visao = para_o_aluno(item, ajudas_liberadas=0)
            texto = repr(visao)
            with self.subTest(item=item["key"]):
                self.assertNotIn("correta", visao)
                self.assertNotIn("balanceada", texto,
                                 "o estado de cada equacao entrega a resposta")

    def test_nenhuma_ajuda_vem_antes_de_ser_liberada(self):
        for item in ITENS:
            visao = para_o_aluno(item, ajudas_liberadas=0)
            with self.subTest(item=item["key"]):
                self.assertEqual(visao["ajudas"], [])
                self.assertEqual(visao["ajudas_disponiveis"], len(item["ajudas"]))

    def test_so_as_ajudas_liberadas_viajam(self):
        item = ITENS[0]
        visao = para_o_aluno(item, ajudas_liberadas=2)
        self.assertEqual([a["nivel"] for a in visao["ajudas"]], [1, 2])

    def test_pedir_mais_ajudas_do_que_existem_nao_estoura(self):
        item = ITENS[0]
        visao = para_o_aluno(item, ajudas_liberadas=99)
        self.assertEqual(len(visao["ajudas"]), len(item["ajudas"]))

    def test_as_alternativas_chegam_com_texto_legivel(self):
        """`options`, com o mesmo formato que o player ja usa - a tela nao
        precisa aprender uma segunda forma de desenhar alternativa."""
        for item in ITENS:
            for alt in para_o_aluno(item, ajudas_liberadas=0)["options"]:
                with self.subTest(item=item["key"], letra=alt["key"]):
                    self.assertTrue(alt["text"].strip())
                    self.assertNotIn("->", alt["text"], "equacao nao formatada")


class OItemSEGUEAMicroHabilidadeTests(unittest.TestCase):

    def test_ha_item_para_a_habilidade_medida_no_piloto(self):
        item = item_para("CHEMISTRY-GENERAL-BALANCING", "CONSERVACAO_DE_ATOMOS")
        self.assertIsNotNone(item)
        self.assertEqual(item["skill"], "CONSERVACAO_DE_ATOMOS")

    def test_habilidade_sem_item_proprio_cai_no_conteudo_nao_em_outra(self):
        """Dar a intervenção de uma habilidade a quem falhou noutra manda o
        aluno estudar o que ele já sabe — e ele percebe."""
        item = item_para("CHEMISTRY-GENERAL-BALANCING", "HABILIDADE_INEXISTENTE")
        self.assertIsNotNone(item, "ficou sem pratica guiada nenhuma")
        self.assertNotEqual(item["skill"], "HABILIDADE_INEXISTENTE")

    def test_conteudo_sem_itens_nao_inventa_pratica_guiada(self):
        self.assertIsNone(item_para("CONTEUDO-QUE-NAO-EXISTE", None))

    def test_sem_habilidade_ainda_ha_um_item_do_conteudo(self):
        self.assertIsNotNone(item_para("CHEMISTRY-GENERAL-BALANCING", None))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
