"""O Assessor Pedagógico decide ENSINAR antes de perguntar de novo.

O PROBLEMA QUE ISTO RESOLVE
============================
Reproduzido no navegador em 2026-10-05, com o Aluno Teste A:

    Atividade de Estequiometria
    → diagnóstico de Balanceamento
    → 1 de 3
    → "Continuar"
    → PRÁTICA: mais cinco questões de Balanceamento

Entre errar e responder de novo não havia nada. O sistema sabia que o aluno
não dominava conservação de átomos — a decisão trazia
`CONSERVACAO_DE_ATOMOS: 3 respondidas, 0,33` — e respondia oferecendo mais
perguntas. Isso é um encadeador adaptativo de exercícios, não um assessor.

O QUE ESTE MÓDULO DECIDE
=========================
Uma coisa só, e de forma determinística: dado o que já se sabe sobre a lacuna
e o que o aluno já fez a respeito dela, a próxima intervenção é ENSINAR,
PRATICAR, ou nenhuma.

Nenhuma decisão aqui consulta modelo de IA. O percurso pedagógico pertence ao
Núcleo; um modelo poderá, depois, adaptar a linguagem da explicação — não
escolher se o aluno precisa dela.

O QUE ELE NÃO DECIDE
=====================
Se o aluno aprendeu. Isso continua sendo da evidência, do mapa de domínio e
da política de cortes. Ter lido uma explicação não é ter aprendido, e há
teste para isso em `test_intervencao_nao_e_evidencia.py`.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.assessor_pedagogico import (
    ACAO_ENSINAR,
    ACAO_ESCALAR,
    ACAO_PRATICAR,
    LIMITE_DE_CICLOS,
    decidir_intervencao,
)
from agente_ia_edu.services.pedagogical_analysis import (
    BAND_IMPROVEMENT,
    BAND_INSUFFICIENT,
    BAND_INTERMEDIATE,
    BAND_STRONG,
)

LACUNA = {
    "por_habilidade": {
        "CONSERVACAO_DE_ATOMOS": {
            "answered": 3, "correct": 1, "accuracy": 1 / 3,
            "band": BAND_IMPROVEMENT, "name": "a conservação dos átomos",
        },
    },
    "suficiente": True,
}

SEM_LACUNA = {
    "por_habilidade": {
        "CONSERVACAO_DE_ATOMOS": {
            "answered": 3, "correct": 3, "accuracy": 1.0,
            "band": BAND_STRONG, "name": "a conservação dos átomos",
        },
    },
    "suficiente": True,
}


def _decidir(**kw):
    base = dict(habilidades=LACUNA, banda_do_conteudo=BAND_IMPROVEMENT,
                ja_ensinado=False, praticas_concluidas=0, ha_material=True)
    base.update(kw)
    return decidir_intervencao(**base)


class QuemPrecisaAprendeAntesTests(unittest.TestCase):

    def test_lacuna_recem_medida_manda_ENSINAR_nao_praticar(self):
        """A asserção central do macrobloco."""
        d = _decidir()
        self.assertEqual(d["action"], ACAO_ENSINAR,
                         "ofereceu mais questoes a quem acabou de errar")

    def test_depois_de_ensinado_manda_praticar(self):
        self.assertEqual(_decidir(ja_ensinado=True)["action"], ACAO_PRATICAR)

    def test_a_intervencao_nomeia_a_habilidade_que_falhou(self):
        d = _decidir()
        self.assertEqual(d["skill"], "CONSERVACAO_DE_ATOMOS")
        self.assertEqual(d["skill_name"], "a conservação dos átomos")


class NaoIntervirSemMotivoTests(unittest.TestCase):
    """Intervir em quem já sabe custa tempo do aluno e credibilidade."""

    def test_desempenho_forte_nao_recebe_intervencao(self):
        self.assertIsNone(_decidir(habilidades=SEM_LACUNA,
                                   banda_do_conteudo=BAND_STRONG)["action"])

    def test_desempenho_intermediario_nao_recebe_ENSINO(self):
        """Intermediário é "quase lá": pratica, não precisa reaprender."""
        d = _decidir(habilidades=SEM_LACUNA, banda_do_conteudo=BAND_INTERMEDIATE)
        self.assertNotEqual(d["action"], ACAO_ENSINAR)

    def test_sem_amostra_suficiente_nao_se_inventa_lacuna(self):
        """Sem medida não há diagnóstico de lacuna - quem decide o que fazer
        nesse caso é o diagnóstico, não o assessor."""
        d = _decidir(habilidades={"por_habilidade": {}, "suficiente": False},
                     banda_do_conteudo=BAND_INSUFFICIENT)
        self.assertIsNone(d["action"])


class SemMaterialNaoSePrometeAulaTests(unittest.TestCase):

    def test_sem_material_publicado_cai_na_pratica(self):
        """Prometer uma explicação que não existe seria pior que a prática."""
        d = _decidir(ha_material=False)
        self.assertEqual(d["action"], ACAO_PRATICAR)


class NaoEntrarEmLoopTests(unittest.TestCase):
    """DIAGNOSTIC → PRACTICE → DIAGNOSTIC → PRACTICE... sem mudar de
    estratégia é o que o aluno sente como "respondendo questões para sempre"."""

    def test_ensinar_e_praticar_alternam_em_vez_de_repetir(self):
        passos = []
        ja_ensinado, praticas = False, 0
        for _ in range(4):
            d = _decidir(ja_ensinado=ja_ensinado, praticas_concluidas=praticas)
            passos.append(d["action"])
            if d["action"] == ACAO_ENSINAR:
                ja_ensinado = True
            elif d["action"] == ACAO_PRATICAR:
                praticas += 1
                ja_ensinado = False      # novo ciclo: ensina de novo
        self.assertEqual(passos[:2], [ACAO_ENSINAR, ACAO_PRATICAR])
        self.assertNotEqual(passos[0], passos[1], "repetiu a mesma estrategia")

    def test_o_ciclo_e_contado_e_sobe(self):
        self.assertEqual(_decidir(praticas_concluidas=0)["cycle"], 1)
        self.assertEqual(_decidir(praticas_concluidas=2)["cycle"], 3)

    def test_passado_o_limite_o_sistema_sinaliza_mas_nao_abandona(self):
        """O teto nao e uma recusa a ajudar.

        Escrevi este teste exigindo PRATICAR e o codigo respondeu ENSINAR -
        e o codigo estava certo: no ciclo seguinte o aluno rever a explicacao
        e coerente. O que o teto precisa garantir e outra coisa, e e isto:
        continua havendo proximo passo, e alguem humano fica sabendo.

        O QUE MUDOU EM 2026-10-05, E POR QUE
        =====================================
        Este teste aceitava ENSINAR ou PRATICAR no teto, e era isso que o
        teste humano encontrou: depois de tres praticas sem destravar, o
        sistema oferecia a quarta. `escalate` ligava e ninguem o consumia.

        O teto agora TERMINA o ciclo: a acao e ESCALAR. A intencao do teste
        continua a mesma e e ela que esta verificada abaixo - ha proximo
        passo, e ele nao e silencio.
        """
        d = _decidir(praticas_concluidas=LIMITE_DE_CICLOS)
        self.assertEqual(d["action"], ACAO_ESCALAR,
                         "no teto, oferecer mais um lote repete o que falhou")
        self.assertIsNotNone(d["action"], "o teto virou uma recusa a ajudar")
        self.assertTrue((d["reason"] or "").strip(),
                        "escalar sem dizer nada ao aluno e abandonar")
        self.assertTrue(d["escalate"],
                        "depois de tantos ciclos alguem humano precisa saber")

    def test_antes_do_limite_nao_escala(self):
        self.assertFalse(_decidir(praticas_concluidas=0)["escalate"])


class AIntervencaoSabeParaOndeOAlunoVaiTests(unittest.TestCase):
    """O aluno não pode entrar em Balanceamento e esquecer por que está ali."""

    def test_o_objetivo_viaja_junto(self):
        d = decidir_intervencao(
            habilidades=LACUNA, banda_do_conteudo=BAND_IMPROVEMENT,
            ja_ensinado=False, praticas_concluidas=0, ha_material=True,
            objetivo_nome="Atividade de Estequiometria",
            conteudo_nome="Reações químicas e balanceamento")
        self.assertEqual(d["target_name"], "Atividade de Estequiometria")
        self.assertEqual(d["blocking_name"], "Reações químicas e balanceamento")

    def test_ha_um_motivo_em_linguagem_de_aluno(self):
        d = decidir_intervencao(
            habilidades=LACUNA, banda_do_conteudo=BAND_IMPROVEMENT,
            ja_ensinado=False, praticas_concluidas=0, ha_material=True,
            objetivo_nome="Atividade de Estequiometria",
            conteudo_nome="Reações químicas e balanceamento")
        for campo in ("reason", "learning_objective", "next_check"):
            with self.subTest(campo=campo):
                texto = d[campo]
                self.assertTrue((texto or "").strip())
                for interno in ("readiness", "DIRECT", "band", "origin_breakdown",
                                "PONTO_MELHORIA", "_"):
                    self.assertNotIn(interno, texto,
                                     f"vocabulario de sistema em {campo}")


class NenhumaDecisaoChamaIATests(unittest.TestCase):
    """O percurso pedagógico pertence ao Núcleo. Um modelo poderá adaptar a
    LINGUAGEM da explicação; não escolher se o aluno precisa dela."""

    def test_o_modulo_nao_importa_provider_de_ia(self):
        import ast
        import inspect

        from agente_ia_edu.services import assessor_pedagogico as mod

        arvore = ast.parse(inspect.getsource(mod))
        importados = []
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados += [a.name for a in no.names]
            elif isinstance(no, ast.ImportFrom):
                importados.append(no.module or "")
        proibidos = ("openai", "anthropic", "google.generativeai", "llm",
                     "ai_provider", "providers")
        for nome in importados:
            for p in proibidos:
                with self.subTest(importado=nome):
                    self.assertNotIn(p, (nome or "").lower())

    def test_o_modulo_nao_contem_corte_proprio(self):
        """As faixas são da política. Um número aqui seria uma segunda
        política, divergindo da primeira no primeiro ajuste."""
        import ast
        import inspect

        from agente_ia_edu.services import assessor_pedagogico as mod

        for no in ast.walk(ast.parse(inspect.getsource(mod))):
            if isinstance(no, ast.Constant) and isinstance(no.value, float):
                self.fail(f"corte numerico no assessor: {no.value}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
