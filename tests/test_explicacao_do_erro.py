"""O ERRO PRECISA ENSINAR ALGUMA COISA.

O QUE FOI MEDIDO, EM 2026-10-06
================================
No banco de desenvolvimento:

    SELECT count(*) FROM question_versions;                        595
    ... WHERE resolution_text IS NOT NULL AND btrim(...) <> '';       0

Nenhuma das 595 questões tem resolução curada. Então, hoje, TODO aluno que
erra e abre "Entenda a resposta" lê exatamente isto:

    "Não há resolução oficial passo a passo armazenada para esta questão.
     A geração de resolução por IA é uma fase futura e não é usada aqui."

Duas frases sobre a dívida técnica do produto, para um adolescente que acabou
de errar uma questão. Ele não aprende nada com elas, e fica sabendo de uma
coisa que não é problema dele.

A ORDEM DAS FONTES, E POR QUE ELA É ESSA
=========================================
    1. resolução curada        escrita e revisada por gente
    2. IA                      quando não há curada e há provedor
    3. fallback pedagógico     quando a IA não respondeu

Material curado nunca é trocado por IA: se alguém escreveu a resolução, ela
vale mais que qualquer geração. A IA entra onde hoje não há nada - que é,
medido, em 100% do acervo.

AS GARANTIAS ESTRUTURAIS
=========================
O serviço não recebe sessão de banco: ele não tem como escrever em lugar
nenhum, então ler uma explicação não pode virar evidência de domínio. Mesmo
mecanismo da `ConversaDoAssessor`, e pelo mesmo motivo.

A diferença entre os dois é deliberada: aqui o gabarito PODE entrar no
contexto, porque ensinar o exercício exige saber qual era a resposta. Na
conversa livre ele continua fora. São contextos diferentes, com listas
fechadas diferentes.
"""

from __future__ import annotations

import asyncio
import inspect
import unittest

from agente_ia_edu.providers.models import TextGenerationResult
from agente_ia_edu.services.explicacao_do_erro import (
    ESTRATEGIA_INICIAL,
    ESTRATEGIAS,
    FONTE_CURADA,
    FONTE_FALLBACK,
    FONTE_IA,
    TEXTO_DE_FALLBACK,
    ExplicacaoDoErro,
    proxima_estrategia,
)

CURADA = ("Pela equação, 2 mol de NH3 produzem 3 mol de H2. Como você achou "
          "0,5 mol de NH3, multiplique por 3/2.")

CONTEXTO = {
    "conteudo": "Estequiometria",
    "habilidade": "proporção estequiométrica",
    "enunciado": "Quantos mols de H2 são necessários para produzir 0,5 mol de NH3?",
    "alternativa_escolhida": "B) 0,5 mol",
    "alternativa_correta": "D) 0,75 mol",
}


class ProvedorFalso:
    def __init__(self, texto='{"explicacao": "Você parou no NH3."}'):
        self.texto = texto
        self.chamadas = []

    async def generate(self, request):
        self.chamadas.append(request.prompt)
        return TextGenerationResult(text=self.texto, provider="falso",
                                    model="falso-1")


class ProvedorQueFalha:
    def __init__(self):
        self.chamadas = []

    async def generate(self, request):
        self.chamadas.append(request.prompt)
        raise TimeoutError("provedor fora do ar")


def explicar(**kw):
    provider = kw.pop("provider", None)
    base = dict(resolucao_curada=None, contexto=CONTEXTO,
                estrategia=ESTRATEGIA_INICIAL)
    base.update(kw)
    return asyncio.run(ExplicacaoDoErro(provider=provider).explicar(**base))


class AResolucaoCuradaVemPrimeiro(unittest.TestCase):

    def test_curada_e_devolvida_como_esta(self):
        r = explicar(resolucao_curada=CURADA, provider=ProvedorFalso())
        self.assertEqual(FONTE_CURADA, r["fonte"])
        self.assertEqual(CURADA, r["texto"])

    def test_curada_nao_gasta_chamada_de_ia(self):
        p = ProvedorFalso()
        explicar(resolucao_curada=CURADA, provider=p)
        self.assertEqual([], p.chamadas,
                         "chamou a IA tendo resolução escrita por gente")

    def test_curada_em_branco_nao_conta_como_curada(self):
        r = explicar(resolucao_curada="   \n  ", provider=ProvedorFalso())
        self.assertEqual(FONTE_IA, r["fonte"])


class SemCuradaAIaExplica(unittest.TestCase):

    def test_usa_o_provedor_e_marca_a_fonte(self):
        r = explicar(provider=ProvedorFalso())
        self.assertEqual(FONTE_IA, r["fonte"])
        self.assertEqual("Você parou no NH3.", r["texto"])
        self.assertEqual("falso", r["provider"])
        self.assertFalse(r["fallback"])

    def test_o_envelope_json_nao_chega_ao_aluno(self):
        """O adaptador deste repositório fixa `response_format=json_object`.
        O aluno já viu `{"resposta":"..."}` na tela uma vez; não de novo."""
        r = explicar(provider=ProvedorFalso())
        self.assertNotIn("{", r["texto"])
        self.assertNotIn("explicacao", r["texto"])

    def test_o_erro_do_aluno_entra_no_prompt(self):
        """Explicar por que a correta é correta é menos útil que mostrar onde
        o raciocínio desviou - e para isso a escolha dele precisa chegar."""
        p = ProvedorFalso()
        explicar(provider=p)
        self.assertIn("0,5 mol", p.chamadas[0])
        self.assertIn("proporção estequiométrica", p.chamadas[0])

    def test_so_os_campos_da_lista_fechada_entram(self):
        p = ProvedorFalso()
        explicar(provider=p, contexto=dict(CONTEXTO, segredo="NAO_PODE_VAZAR"))
        self.assertNotIn("NAO_PODE_VAZAR", p.chamadas[0])


class QuandoAIaNaoResponde(unittest.TestCase):

    def test_cai_no_fallback_pedagogico(self):
        r = explicar(provider=ProvedorQueFalha())
        self.assertEqual(FONTE_FALLBACK, r["fonte"])
        self.assertTrue(r["fallback"])
        self.assertEqual(TEXTO_DE_FALLBACK, r["texto"])

    def test_sem_provedor_nenhum_tambem_cai_no_fallback(self):
        """Um ambiente sem chave não pode prender o aluno numa tela vazia."""
        from unittest import mock

        import agente_ia_edu.services.explicacao_do_erro as mod

        with mock.patch.object(mod, "build_text_provider",
                               side_effect=RuntimeError("sem chave")):
            r = explicar(provider=None)
        self.assertEqual(FONTE_FALLBACK, r["fonte"])

    def test_resposta_vazia_e_falha_e_nao_resposta(self):
        r = explicar(provider=ProvedorFalso(texto='{"explicacao": "   "}'))
        self.assertEqual(FONTE_FALLBACK, r["fonte"])

    def test_o_fallback_nao_mostra_nome_de_provedor_nem_erro(self):
        r = explicar(provider=ProvedorQueFalha())
        for proibido in ("Timeout", "provedor fora do ar", "openai", "falso",
                         "Traceback", "{"):
            self.assertNotIn(proibido, r["texto"])
        self.assertIsNone(r["provider"])


class NenhumaExplicacaoFalaDeDIVIDATECNICA(unittest.TestCase):
    """O aluno não precisa saber o que o produto ainda não construiu."""

    PROIBIDO = ("fase futura", "não é usada aqui", "não há resolução oficial",
                "armazenada para esta questão")

    def test_em_nenhuma_das_tres_fontes(self):
        for nome, r in (
                ("curada", explicar(resolucao_curada=CURADA)),
                ("ia", explicar(provider=ProvedorFalso())),
                ("fallback", explicar(provider=ProvedorQueFalha()))):
            with self.subTest(fonte=nome):
                baixo = r["texto"].lower()
                for frase in self.PROIBIDO:
                    self.assertNotIn(frase, baixo)


class AEstrategiaMUDA(unittest.TestCase):
    """"Explique de outro jeito" só significa alguma coisa se mudar."""

    def test_a_proxima_nunca_e_a_anterior(self):
        anterior = ESTRATEGIA_INICIAL
        for _ in range(len(ESTRATEGIAS) + 2):
            proxima = proxima_estrategia(anterior)
            self.assertNotEqual(anterior, proxima)
            self.assertIn(proxima, ESTRATEGIAS)
            anterior = proxima

    def test_sem_anterior_comeca_pela_inicial(self):
        self.assertEqual(ESTRATEGIA_INICIAL, proxima_estrategia(None))

    def test_estrategia_desconhecida_nao_quebra(self):
        self.assertIn(proxima_estrategia("INVENTADA"), ESTRATEGIAS)

    def test_a_estrategia_chega_ao_prompt(self):
        p = ProvedorFalso()
        outra = proxima_estrategia(ESTRATEGIA_INICIAL)
        explicar(provider=p, estrategia=outra)
        self.assertIn(outra, p.chamadas[0])

    def test_a_estrategia_volta_na_resposta(self):
        """A tela precisa saber qual foi, para pedir a seguinte."""
        r = explicar(provider=ProvedorFalso(), estrategia=ESTRATEGIAS[1])
        self.assertEqual(ESTRATEGIAS[1], r["estrategia"])

    def test_pedir_outro_jeito_ignora_a_curada_ja_mostrada(self):
        """Se a curada já foi lida e não bastou, relê-la não é outro jeito."""
        r = explicar(resolucao_curada=CURADA, provider=ProvedorFalso(),
                     estrategia=proxima_estrategia(ESTRATEGIA_INICIAL))
        self.assertEqual(FONTE_IA, r["fonte"])


class LERNAOESCREVE(unittest.TestCase):
    """A garantia é do código, não uma promessa no comentário."""

    def test_o_servico_nao_recebe_sessao(self):
        params = inspect.signature(ExplicacaoDoErro.__init__).parameters
        for proibido in ("session", "session_factory", "db", "engine"):
            self.assertNotIn(proibido, params,
                             "o serviço ganhou como escrever no banco")

    def test_o_modulo_nao_importa_modelo_nem_sessao(self):
        import pathlib

        fonte = (pathlib.Path(__file__).resolve().parent.parent
                 / "src/agente_ia_edu/services/explicacao_do_erro.py"
                 ).read_text(encoding="utf-8")
        for proibido in ("AsyncSession", "sqlalchemy", "db.models"):
            self.assertNotIn(proibido, fonte)


if __name__ == "__main__":
    unittest.main()
