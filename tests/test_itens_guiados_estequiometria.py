"""A PRÁTICA GUIADA DE ESTEQUIOMETRIA — e a ajuda que não entrega a resposta.

POR QUE ESTES ITENS PRECISARAM EXISTIR
=======================================
Medido em 2026-10-06: `item_para("CHEMISTRY-PHYSICAL-STOICHIOMETRY", ...)`
devolvia `None`. Havia prática guiada escrita só para balanceamento, então o
aluno de Estequiometria saía da sondagem e caía direto em cinco questões
sozinho — o degrau de baixo da escada, sem ter passado pelos de cima.

O QUE MUDA NO CONTRATO
=======================
Os itens de balanceamento declaram `{equacao, balanceada}`, porque lá a
verdade é contagem de átomos. Aqui a alternativa é um número com unidade, e
a verdade é aritmética. O formato passou a aceitar `{texto}` — e os testes
que conferiam balanceamento foram ESCOPADOS ao conteúdo de balanceamento,
não afrouxados.

O QUE ESTE ARQUIVO TRAVA
=========================
1. cada gabarito é refeito por `massa_molar`;
2. nenhum nível de ajuda cita a letra correta nem o texto dela;
3. há item para cada uma das quatro habilidades prioritárias;
4. a unidade aparece na alternativa — "2" e "2 mol" não são a mesma coisa.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    LEITURA_FORMULA,
    MASSA_MOL,
    MASSA_MOLAR,
    PROPORCAO,
)
from agente_ia_edu.services.itens_guiados import (
    NIVEIS_DE_AJUDA,
    item_para,
    para_o_aluno,
)
from agente_ia_edu.services.itens_guiados_estequiometria import (
    ITENS,
    conferir,
)

PRIORITARIAS = (LEITURA_FORMULA, MASSA_MOLAR, MASSA_MOL, PROPORCAO)


class OGABARITOEREFEITO(unittest.TestCase):

    def test_nenhum_item_afirma_conta_errada(self):
        self.assertEqual([], conferir())


class HAITEMPARACADAHABILIDADEPRIORITARIA(unittest.TestCase):

    def test_as_quatro_tem_item_proprio(self):
        for s in PRIORITARIAS:
            with self.subTest(s=s):
                item = item_para(CONTEUDO, s)
                self.assertIsNotNone(item, f"{s} ficou sem pratica guiada")
                self.assertEqual(s, item["skill"])

    def test_o_conteudo_deixou_de_devolver_nada(self):
        """O bug medido em 2026-10-06, travado por teste."""
        self.assertIsNotNone(item_para(CONTEUDO, None))

    def test_habilidade_sem_item_proprio_cai_no_conteudo_e_nao_noutro(self):
        item = item_para(CONTEUDO, "HABILIDADE_INEXISTENTE")
        self.assertIsNotNone(item)
        self.assertEqual(CONTEUDO, item["content_code"])

    def test_nenhum_item_de_estequiometria_vaza_para_balanceamento(self):
        item = item_para("CHEMISTRY-GENERAL-BALANCING", None)
        self.assertEqual("CHEMISTRY-GENERAL-BALANCING", item["content_code"])


class AAJUDANAOENTREGAARESPOSTA(unittest.TestCase):

    def test_nenhum_nivel_cita_a_letra_correta(self):
        for item in ITENS:
            correta = item["correta"]
            for ajuda in item["ajudas"]:
                with self.subTest(item=item["key"], nivel=ajuda["nivel"]):
                    self.assertNotIn(f"alternativa {correta}",
                                     ajuda["texto"].lower())
                    self.assertNotIn(f"letra {correta}", ajuda["texto"].lower())

    def test_nenhum_nivel_reproduz_o_texto_da_alternativa_certa(self):
        for item in ITENS:
            certa = item["alternativas"][item["correta"]]["texto"]
            for ajuda in item["ajudas"]:
                with self.subTest(item=item["key"], nivel=ajuda["nivel"]):
                    self.assertNotIn(certa.lower(), ajuda["texto"].lower())

    def test_os_niveis_sao_crescentes_e_sem_buraco(self):
        for item in ITENS:
            with self.subTest(item=item["key"]):
                self.assertEqual(list(range(1, len(item["ajudas"]) + 1)),
                                 [a["nivel"] for a in item["ajudas"]])

    def test_cada_nivel_tem_um_tipo_conhecido(self):
        for item in ITENS:
            for ajuda in item["ajudas"]:
                with self.subTest(item=item["key"], nivel=ajuda["nivel"]):
                    self.assertIn(ajuda["tipo"], NIVEIS_DE_AJUDA)

    def test_cada_nivel_diz_algo_diferente_do_anterior(self):
        """Repetir a dica anterior com outras palavras não é um segundo
        nível de ajuda — é o primeiro, de novo."""
        for item in ITENS:
            textos = [a["texto"] for a in item["ajudas"]]
            with self.subTest(item=item["key"]):
                self.assertEqual(len(set(textos)), len(textos))

    def test_ha_quatro_niveis_em_cada_item(self):
        for item in ITENS:
            with self.subTest(item=item["key"]):
                self.assertEqual(len(NIVEIS_DE_AJUDA), len(item["ajudas"]))


class AUNIDADEAPARECE(unittest.TestCase):
    """"2" e "2 mol" não são a mesma coisa, e a alternativa é onde isso
    aparece — um aluno que responde certo com a unidade errada não aprendeu
    a conta inteira."""

    def test_toda_alternativa_tem_texto(self):
        for item in ITENS:
            for letra, alt in item["alternativas"].items():
                with self.subTest(item=item["key"], letra=letra):
                    self.assertTrue(alt["texto"].strip())

    def test_as_alternativas_numericas_trazem_unidade(self):
        unidades = ("g/mol", "mol", "g", "átomo")
        for item in ITENS:
            if item["skill"] == LEITURA_FORMULA:
                continue  # contagem de átomos: a unidade está no enunciado
            for letra, alt in item["alternativas"].items():
                with self.subTest(item=item["key"], letra=letra):
                    self.assertTrue(
                        any(u in alt["texto"] for u in unidades),
                        f"{alt['texto']!r} sem unidade")

    def test_as_alternativas_sao_distintas(self):
        for item in ITENS:
            textos = [a["texto"] for a in item["alternativas"].values()]
            with self.subTest(item=item["key"]):
                self.assertEqual(len(set(textos)), len(textos))

    def test_a_correta_e_uma_das_alternativas(self):
        for item in ITENS:
            with self.subTest(item=item["key"]):
                self.assertIn(item["correta"], item["alternativas"])


class OQUECHEGAAOALUNO(unittest.TestCase):

    def test_a_resposta_correta_nao_viaja(self):
        for item in ITENS:
            visao = para_o_aluno(item, ajudas_liberadas=0)
            with self.subTest(item=item["key"]):
                self.assertNotIn("correta", visao)
                self.assertNotIn("correta", repr(visao))

    def test_as_alternativas_chegam_legiveis(self):
        for item in ITENS:
            for alt in para_o_aluno(item, ajudas_liberadas=0)["options"]:
                with self.subTest(item=item["key"], letra=alt["key"]):
                    self.assertTrue(alt["text"].strip())

    def test_o_fecho_so_aparece_depois_e_diz_o_que_ficou(self):
        for item in ITENS:
            with self.subTest(item=item["key"]):
                self.assertTrue(item["fecho"].strip())
                self.assertNotIn("fecho", para_o_aluno(item, ajudas_liberadas=4))


class OSSUBSCRITOSSOBREVIVEM(unittest.TestCase):

    def test_nenhuma_formula_com_digito_ascii_colado(self):
        import re
        texto = []
        for item in ITENS:
            texto.append(item["pergunta"])
            texto += [a["texto"] for a in item["alternativas"].values()]
            texto += [a["texto"] for a in item["ajudas"]]
            texto.append(item["fecho"])
        achados = re.findall(r"\b(?:NH|CO|SO|OH|H|N|O|Al|Mg|Ca)\d",
                             "\n".join(texto))
        self.assertEqual([], achados, f"fórmulas sem subscrito: {achados}")


if __name__ == "__main__":
    unittest.main()
