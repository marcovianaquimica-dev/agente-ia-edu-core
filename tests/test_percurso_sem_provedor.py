"""SEM IA, O PERCURSO PEDAGÓGICO CONTINUA DE PÉ.

§27 do bloco. A pergunta não é "o sistema degrada com elegância?" — é "o
aluno fica sem orientação?". Um timeout do provedor no meio de uma
apresentação não pode fechar a porta do produto.

O QUE ESTE ARQUIVO MEDE
========================
Cada peça do percurso deste bloco, SEM PROVEDOR NENHUM:

    sondagem          itens curados, no repositório
    gargalo           grafo + política, determinístico
    investigação      cadeia curada, com gabarito conferido por conta
    material          conteúdo curado, publicado no banco
    prática guiada    itens curados, com quatro níveis de ajuda
    decisão seguinte  determinística

A IA aparece em UM lugar do percurso: a explicação de um erro. E lá há
fallback honesto — que não finge ser explicação e não conta ao aluno a
dívida técnica do produto.

COMO A AUSÊNCIA É SIMULADA
===========================
Nenhum módulo do percurso importa provedor. Isso não é afirmado aqui: é
verificado pela AST de cada um deles, o que torna a propriedade estrutural
em vez de uma promessa que envelhece.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent / "src/agente_ia_edu"

# Os modulos que sustentam o percurso. Nenhum deles pode depender de IA.
DO_PERCURSO = (
    "services/sondagem_estequiometria.py",
    "services/grafo_estequiometria.py",
    "services/grafo_pedagogico.py",
    "services/sinal_diagnostico.py",
    "services/investigacao_do_erro.py",
    "services/servico_de_investigacao.py",
    "services/escada_de_apoio.py",
    "services/assessor_pedagogico.py",
    "services/conteudo_estequiometria.py",
    "services/itens_guiados.py",
    "services/itens_guiados_estequiometria.py",
    "services/pratica_guiada.py",
    "services/massa_molar.py",
    "services/feedback_pedagogico.py",
    # A selecao do instrumento de sondagem entrou em 2026-10-07. Ela e
    # conhecimento pedagogico do Nucleo - qual item mede qual
    # micro-habilidade - e nao pode passar a depender de modelo nenhum.
    "services/instrumento_de_sondagem.py",
    "services/plano_de_sondagem.py",
    "services/seletor_de_sondagem.py",
)

PROIBIDOS = ("providers.factory", "build_text_provider",
             "TextGenerationProvider", "ProviderRouter", "openai")


class NENHUMAPECADOPERCURSODEPENDEDEIA(unittest.TestCase):

    def _importados(self, caminho: str) -> list[str]:
        arvore = ast.parse((RAIZ / caminho).read_text(encoding="utf-8"))
        nomes: list[str] = []
        for no in ast.walk(arvore):
            if isinstance(no, ast.ImportFrom):
                nomes.append(no.module or "")
                nomes += [a.name for a in no.names]
            elif isinstance(no, ast.Import):
                nomes += [a.name for a in no.names]
        return nomes

    def test_nenhum_importa_provedor(self):
        for caminho in DO_PERCURSO:
            nomes = self._importados(caminho)
            for proibido in PROIBIDOS:
                with self.subTest(modulo=caminho, proibido=proibido):
                    self.assertFalse(
                        any(proibido in n for n in nomes),
                        f"{caminho} depende de {proibido}")

    def test_e_nenhum_menciona_provedor_no_corpo(self):
        """Import é o caminho normal, mas não é o único."""
        for caminho in DO_PERCURSO:
            baixo = (RAIZ / caminho).read_text(encoding="utf-8").lower()
            for proibido in ("build_text_provider(", "providerrouter("):
                with self.subTest(modulo=caminho, proibido=proibido):
                    self.assertNotIn(proibido, baixo)


class OPERCURSOINTEIRORODASEMIA(unittest.TestCase):
    """Não é só a ausência de import: as peças rodam e devolvem conteúdo."""

    def test_a_sondagem_tem_itens_e_o_gabarito_fecha(self):
        from agente_ia_edu.services.sondagem_estequiometria import ITENS, conferir
        self.assertTrue(ITENS)
        self.assertEqual([], conferir())

    def test_a_investigacao_tem_cadeia_e_o_gabarito_fecha(self):
        from agente_ia_edu.services.investigacao_do_erro import (
            INVESTIGACOES, conferir,
        )
        self.assertTrue(INVESTIGACOES)
        self.assertEqual([], conferir())

    def test_o_conteudo_curado_existe_e_as_contas_fecham(self):
        from agente_ia_edu.services.conteudo_estequiometria import (
            MICRO_HABILIDADES, conferir,
        )
        self.assertTrue(MICRO_HABILIDADES)
        self.assertEqual([], conferir())

    def test_a_pratica_guiada_tem_item_para_a_lacuna(self):
        from agente_ia_edu.services.grafo_estequiometria import (
            CONTEUDO, MASSA_MOLAR,
        )
        from agente_ia_edu.services.itens_guiados import item_para
        item = item_para(CONTEUDO, MASSA_MOLAR)
        self.assertIsNotNone(item)
        self.assertTrue(item["ajudas"])

    def test_a_decisao_de_intervencao_sai_sem_ia(self):
        from agente_ia_edu.services.assessor_pedagogico import (
            ACAO_INVESTIGAR, decidir_intervencao,
        )
        from agente_ia_edu.services.pedagogical_analysis import BAND_IMPROVEMENT
        d = decidir_intervencao(
            habilidades={"por_habilidade": {
                "MASSA_MOLAR": {"band": BAND_IMPROVEMENT, "answered": 4,
                                "correct": 1, "accuracy": 0.25,
                                "name": "massa molar"}}},
            banda_do_conteudo=BAND_IMPROVEMENT, ja_ensinado=False,
            praticas_concluidas=0, ha_material=True,
            ha_investigacao_pendente=True,
            tentativas=[{"answered": 4, "correct": 1}])
        self.assertEqual(ACAO_INVESTIGAR, d["action"])
        self.assertTrue(d["reason"].strip())


class AEXPLICACAOCAIEMFALLBACKHONESTO(unittest.TestCase):
    """O único ponto do percurso em que a IA entra."""

    def _explicar(self, provider):
        from agente_ia_edu.services.explicacao_do_erro import ExplicacaoDoErro

        async def run():
            return await ExplicacaoDoErro(provider=provider).explicar(
                resolucao_curada=None,
                contexto={"conteudo": "Estequiometria"})

        return asyncio.run(run())

    class _ProvedorQueFalha:
        async def generate(self, _):
            raise RuntimeError("provedor fora do ar")

    def test_provedor_fora_do_ar_nao_deixa_o_aluno_sem_texto(self):
        r = self._explicar(self._ProvedorQueFalha())
        self.assertTrue(r["fallback"])
        self.assertGreater(len(r["texto"].split()), 20)

    def test_o_fallback_nao_fala_da_divida_tecnica_do_produto(self):
        """O aluno acabou de errar. O que o produto ainda não construiu não
        é problema dele, e não ensina nada.

        A comparação é por PALAVRA INTEIRA. Escrevi `assertNotIn("ia", ...)`
        primeiro e o teste reprovou um texto correto: "ia" está dentro de
        "enunc-IA-do". Substring é a armadilha óbvia destas varreduras, e
        esta é a segunda vez que ela me pega neste bloco.
        """
        import re
        baixo = self._explicar(self._ProvedorQueFalha())["texto"].lower()
        for proibido in ("fase futura", "não é usada aqui", "não implementado",
                         "indisponível", "ia", "modelo", "provedor", "api"):
            with self.subTest(proibido=proibido):
                self.assertIsNone(
                    re.search(rf"\b{re.escape(proibido)}\b", baixo),
                    f"o fallback menciona {proibido!r}")

    def test_o_fallback_devolve_o_aluno_ao_que_funciona_sem_ia(self):
        texto = self._explicar(self._ProvedorQueFalha())["texto"].lower()
        self.assertIn("releia o enunciado", texto)

    def test_a_resolucao_CURADA_nao_depende_de_provedor_nenhum(self):
        from agente_ia_edu.services.explicacao_do_erro import FONTE_CURADA

        async def run():
            from agente_ia_edu.services.explicacao_do_erro import ExplicacaoDoErro
            return await ExplicacaoDoErro(
                provider=self._ProvedorQueFalha()).explicar(
                resolucao_curada="A conta é 14 + 3 = 17 g/mol.",
                contexto={"conteudo": "X"})

        r = asyncio.run(run())
        self.assertEqual(FONTE_CURADA, r["fonte"])
        self.assertIn("17 g/mol", r["texto"])


if __name__ == "__main__":
    unittest.main()
