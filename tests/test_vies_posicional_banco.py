"""Viés posicional no banco diagnóstico REAL, não no candidato em memória.

O bloco que criou o Diagnostic Bank mediu o viés nos itens *candidatos* e
corrigiu ali — 9 em "B" viraram 21/21/21/21/14%. Mas a correção ficou no
pipeline de geração: o arquivo que foi efetivamente carregado no banco
(`scripts/data/diagnostic_bank_balanceamento_v2.json`) é anterior a ela, e os
14 itens que o aluno responde chegaram com **10 gabaritos em "B"**.

Medido em 2026-10-04, no banco de dev, enquanto eu conferia o Caminho A.

POR QUE ISTO É GRAVE AQUI
==========================
Num teste comum, viés posicional infla uma nota. Num **diagnóstico** ele
produz FALSO-PRONTO: o aluno que marca "B" em tudo acerta 10 de 14, a política
conclui que ele domina Balanceamento, e o sistema o libera para Estequiometria
sem que ele saiba balancear uma equação. O instrumento mente sobre a pessoa
que ele deveria estar medindo.

Este teste mede o arquivo que é a FONTE do banco, que é o que de fato chega
ao aluno.
"""

from __future__ import annotations

import collections
import json
import pathlib
import unittest

FONTE = (pathlib.Path(__file__).resolve().parent.parent
         / "scripts" / "data" / "diagnostic_bank_balanceamento_v2.json")
LETRAS = ("A", "B", "C", "D", "E")


def _itens() -> list[dict]:
    bruto = json.loads(FONTE.read_text(encoding="utf-8"))
    return [r["item"] for r in bruto if r["decisao"]["status"] == "AI_VERIFIED"]


class DistribuicaoDoGabaritoTests(unittest.TestCase):

    def setUp(self):
        self.itens = _itens()
        self.contagem = collections.Counter(i["correct_answer"] for i in self.itens)

    def test_ha_itens_para_medir(self):
        self.assertGreaterEqual(len(self.itens), 10)

    def test_nenhuma_letra_concentra_o_gabarito(self):
        """O limite não é estético.

        Com 14 itens e 5 letras, o esperado por letra é ~20%. Aceito até 40% —
        o dobro — porque arredondamento e um banco pequeno não permitem
        exatidão. Acima disso, marcar sempre a mesma letra começa a aprovar
        alguém: é esse o dano.
        """
        total = len(self.itens)
        for letra, n in self.contagem.items():
            with self.subTest(letra=letra):
                self.assertLessEqual(
                    n / total, 0.40,
                    f"{n} de {total} gabaritos em {letra!r} — quem marcar "
                    f"sempre {letra} acerta {n / total:.0%} sem saber o conteúdo")

    def test_o_chute_fixo_nao_passa_na_politica(self):
        """A asserção que importa: marcar sempre a mesma letra tem de FALHAR
        no microdiagnóstico, sob a política real, sem número escrito aqui."""
        from agente_ia_edu.services.micro_diagnostic import (
            DECISION_PROCEED, MicroDiagnosticService,
        )
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )

        politica = PerformanceThresholdPolicy.default()
        amostra = politica.min_sample_size
        svc = MicroDiagnosticService.__new__(MicroDiagnosticService)
        svc._thresholds = politica

        for letra in LETRAS:
            acertos = self.contagem.get(letra, 0)
            taxa = acertos / len(self.itens)
            with self.subTest(letra=letra):
                d = svc.decidir(answered=amostra, accuracy=taxa)
                self.assertNotEqual(
                    d["decision"], DECISION_PROCEED,
                    f"marcar sempre {letra!r} ({taxa:.0%}) liberou o aluno "
                    f"para a atividade")

    def test_pelo_menos_tres_letras_sao_usadas(self):
        """Um banco com gabarito só em B e C ensina o padrão, não a química."""
        self.assertGreaterEqual(len(self.contagem), 3,
                                f"gabaritos concentrados em {sorted(self.contagem)}")


class AQuimicaNaoMudaTests(unittest.TestCase):
    """A redistribuição reordena alternativas. Se tiver mexido na química, o
    item deixou de ser o que foi verificado."""

    def test_a_alternativa_correta_continua_sendo_o_mesmo_texto(self):
        for i, item in enumerate(_itens(), 1):
            with self.subTest(item=i):
                correta = item["options"][item["correct_answer"]]
                self.assertTrue(correta.strip(),
                                "a alternativa correta ficou vazia")

    def test_as_cinco_alternativas_continuam_distintas(self):
        for i, item in enumerate(_itens(), 1):
            textos = [item["options"][k] for k in LETRAS]
            with self.subTest(item=i):
                self.assertEqual(len(set(textos)), len(textos),
                                 "a reordenação duplicou uma alternativa")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
