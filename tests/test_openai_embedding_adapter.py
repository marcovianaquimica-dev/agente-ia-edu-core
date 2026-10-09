"""CEREBRO - Fase 6: o adapter REAL de embeddings, dentro de ``providers/``.

ISOLAMENTO, QUE E O PONTO
=========================

Tudo que sabe o nome "OpenAI" e o nome do modelo vive aqui. O Knowledge
Engine recebe ``EmbeddingResult`` e nunca pergunta de onde veio - provider,
modelo e dimensao chegam ate ele pela linha de ``KnowledgeEmbeddingSpace``,
nao por import.

Os testes usam um cliente DUBLE, nunca a rede: o contrato que importa e o
que o adapter faz com a resposta, e isso se verifica sem gastar token.

NORMA UNITARIA E OBSERVACAO, NAO PRESSUPOSTO
============================================

``text-embedding-3`` devolve vetores de norma ~1, e isso torna cosseno,
produto interno e L2 monotonicamente equivalentes. Mas a arquitetura **nao
depende disso**: a norma e MEDIDA e registrada em ``EmbeddingResult``, para
que um provider futuro que nao normalize seja um fato observavel em vez de
um erro silencioso de ranking.
"""

from __future__ import annotations

import math
import unittest

from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.providers.errors import (
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from agente_ia_edu.providers.models import EmbeddingRequest


class _Item:
    def __init__(self, embedding, index=0):
        self.embedding = embedding
        self.index = index


class _Response:
    def __init__(self, items, usage=None, model="text-embedding-3-small"):
        self.data = items
        self.usage = usage
        self.model = model


class _Embeddings:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class _Client:
    def __init__(self, response=None, error=None):
        self.embeddings = _Embeddings(response=response, error=error)


def _provider(client, **kwargs) -> OpenAIProvider:
    """Adapter de teste. ``embedding_model`` sempre explicito: o adapter
    EXIGE modelo, e no caminho real ele vem da linha do espaco - nunca de um
    default escondido."""
    kwargs.setdefault("api_key", "sk-test")
    kwargs.setdefault("embedding_model", "modelo-de-teste")
    return OpenAIProvider(client=client, **kwargs)


def _unit(seed: int, dimensions: int) -> list[float]:
    raw = [math.sin(seed + i) for i in range(dimensions)]
    norm = math.sqrt(sum(c * c for c in raw))
    return [c / norm for c in raw]


class ConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_unconfigured_provider_refuses_before_calling(self):
        provider = OpenAIProvider(api_key=None, client=_Client())
        with self.assertRaises(ProviderConfigurationError):
            await provider.embed(EmbeddingRequest(texts=("texto",)))

    async def test_an_unset_model_refuses_before_calling(self):
        """Sem modelo nao ha chamada. O modelo nao tem default escondido: ele
        vem do espaco, e um default silencioso embeddaria o corpus com o
        modelo errado sem ninguem perceber."""
        client = _Client()
        provider = OpenAIProvider(api_key="sk-test", embedding_model=None, client=client)
        with self.assertRaises(ProviderConfigurationError):
            await provider.embed(EmbeddingRequest(texts=("texto",)))
        self.assertEqual(client.embeddings.calls, [])

    async def test_the_request_model_wins_over_the_configured_default(self):
        """O MODELO VEM DE FORA. E assim que o Knowledge Engine manda o modelo
        da linha do espaco sem conhecer nenhum nome de modelo."""
        client = _Client(_Response([_Item(_unit(1, 4))]))
        provider = OpenAIProvider(
            api_key="sk-test", embedding_model="modelo-default", client=client
        )
        result = await provider.embed(
            EmbeddingRequest(texts=("texto",), model="modelo-do-espaco")
        )
        self.assertEqual(client.embeddings.calls[0]["model"], "modelo-do-espaco")
        self.assertEqual(result.model, "modelo-do-espaco")


class VerbatimTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_text_is_sent_verbatim(self):
        """``canonical_text`` vai como esta. Nenhuma normalizacao: o
        ``text_hash`` precisa continuar identificando exatamente o conteudo
        que foi vetorizado."""
        texto = "  Diluição das SOLUÇÕES  \n\n com acento e CAIXA mista  "
        client = _Client(_Response([_Item(_unit(1, 4))]))
        provider = _provider(client)
        await provider.embed(EmbeddingRequest(texts=(texto,)))
        self.assertEqual(client.embeddings.calls[0]["input"], [texto])

    async def test_the_hash_identifies_exactly_what_was_sent(self):
        import hashlib

        texto = "Diluição das soluções"
        client = _Client(_Response([_Item(_unit(1, 4))]))
        provider = _provider(client)
        result = await provider.embed(EmbeddingRequest(texts=(texto,)))
        artifact = result.artifacts[0]
        self.assertEqual(artifact.canonical_text, texto)
        self.assertEqual(
            artifact.text_hash, hashlib.sha256(texto.encode("utf-8")).hexdigest()
        )

    async def test_several_texts_keep_their_order(self):
        textos = ("primeiro", "segundo", "terceiro")
        client = _Client(
            _Response([_Item(_unit(i, 4), index=i) for i in range(3)])
        )
        provider = _provider(client)
        result = await provider.embed(EmbeddingRequest(texts=textos))
        self.assertEqual(
            tuple(a.canonical_text for a in result.artifacts), textos
        )

    async def test_an_out_of_order_response_is_reordered_by_index(self):
        """A API documenta que a ordem pode nao ser a de envio; o ``index`` e
        quem manda. Trocar enunciado por vetor errado seria silencioso."""
        textos = ("primeiro", "segundo")
        client = _Client(
            _Response([_Item(_unit(9, 4), index=1), _Item(_unit(1, 4), index=0)])
        )
        provider = _provider(client)
        result = await provider.embed(EmbeddingRequest(texts=textos))
        self.assertEqual(result.artifacts[0].canonical_text, "primeiro")
        self.assertEqual(list(result.artifacts[0].vector), _unit(1, 4))
        self.assertEqual(result.artifacts[1].canonical_text, "segundo")
        self.assertEqual(list(result.artifacts[1].vector), _unit(9, 4))


class ResponseShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_missing_vector_is_an_invalid_response(self):
        client = _Client(_Response([]))
        provider = _provider(client)
        with self.assertRaises(ProviderInvalidResponseError):
            await provider.embed(EmbeddingRequest(texts=("a", "b")))

    async def test_a_count_mismatch_is_an_invalid_response(self):
        client = _Client(_Response([_Item(_unit(1, 4))]))
        provider = _provider(client)
        with self.assertRaises(ProviderInvalidResponseError):
            await provider.embed(EmbeddingRequest(texts=("a", "b")))

    async def test_vectors_of_differing_length_are_an_invalid_response(self):
        """O adapter nao sabe qual dimensao o ESPACO espera - quem valida
        isso e o servico. O que ele sabe e que uma resposta internamente
        inconsistente nao pode seguir adiante."""
        client = _Client(
            _Response([_Item(_unit(1, 4), 0), _Item(_unit(2, 8), 1)])
        )
        provider = _provider(client)
        with self.assertRaises(ProviderInvalidResponseError):
            await provider.embed(EmbeddingRequest(texts=("a", "b")))

    async def test_an_empty_request_never_calls_the_provider(self):
        client = _Client(_Response([]))
        provider = _provider(client)
        result = await provider.embed(EmbeddingRequest(texts=()))
        self.assertEqual(result.artifacts, ())
        self.assertEqual(client.embeddings.calls, [])

    async def test_the_reported_dimension_comes_from_the_vector(self):
        client = _Client(_Response([_Item(_unit(1, 16))]))
        provider = _provider(client)
        result = await provider.embed(EmbeddingRequest(texts=("a",)))
        self.assertEqual(result.dimensions, 16)
        self.assertEqual(result.artifacts[0].dimensions, 16)


class NormObservabilityTests(unittest.IsolatedAsyncioTestCase):
    """A norma e MEDIDA, nunca pressuposta."""

    async def test_the_norm_is_measured_and_reported(self):
        client = _Client(_Response([_Item(_unit(3, 32))]))
        provider = _provider(client)
        result = await provider.embed(EmbeddingRequest(texts=("a",)))
        self.assertAlmostEqual(result.vector_norm_mean, 1.0, places=6)
        self.assertTrue(result.vectors_are_unit_norm)

    async def test_a_provider_that_does_not_normalise_is_observable(self):
        """Nao e erro - e fato registrado. A arquitetura nao depende de norma
        unitaria, e o cosseno e calculado de verdade em vez de assumido."""
        client = _Client(_Response([_Item([3.0, 4.0, 0.0, 0.0])]))
        provider = _provider(client)
        result = await provider.embed(EmbeddingRequest(texts=("a",)))
        self.assertAlmostEqual(result.vector_norm_mean, 5.0, places=6)
        self.assertFalse(result.vectors_are_unit_norm)
        self.assertEqual(len(result.artifacts), 1)

    async def test_a_zero_vector_does_not_crash_the_norm_report(self):
        client = _Client(_Response([_Item([0.0, 0.0, 0.0, 0.0])]))
        provider = _provider(client)
        result = await provider.embed(EmbeddingRequest(texts=("a",)))
        self.assertEqual(result.vector_norm_mean, 0.0)
        self.assertFalse(result.vectors_are_unit_norm)


class ErrorMappingTests(unittest.IsolatedAsyncioTestCase):
    """Erros do SDK viram erros do PROJETO, para que o ``ProviderRouter``
    possa fazer fallback sem conhecer o SDK."""

    async def test_a_rate_limit_becomes_a_project_error(self):
        error = type("RateLimitError", (Exception,), {})("429")
        provider = _provider(_Client(error=error))
        with self.assertRaises(ProviderRateLimitError):
            await provider.embed(EmbeddingRequest(texts=("a",)))

    async def test_a_timeout_becomes_a_project_error(self):
        error = type("APITimeoutError", (Exception,), {})("timeout")
        provider = _provider(_Client(error=error))
        with self.assertRaises(ProviderTimeoutError):
            await provider.embed(EmbeddingRequest(texts=("a",)))

    async def test_the_api_key_never_leaks_into_an_error_message(self):
        chave = "sk-proj-super-secreta-123"
        error = Exception(f"falhou usando {chave} no header")
        provider = _provider(_Client(error=error), api_key=chave)
        with self.assertRaises(Exception) as caught:
            await provider.embed(EmbeddingRequest(texts=("a",)))
        self.assertNotIn(chave, str(getattr(caught.exception, "diagnostic_message", "")))


class ProviderIsolationTests(unittest.TestCase):
    """O Knowledge Engine nao pode conhecer o fornecedor nem o modelo."""

    def test_no_knowledge_engine_module_imports_a_provider_adapter(self):
        import ast
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu"
        proibidos = {"openai", "anthropic"}
        culpados = []
        for path in sorted((raiz / "services" / "knowledge_engine").rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                nomes = []
                if isinstance(node, ast.Import):
                    nomes = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    nomes = [node.module or ""]
                for nome in nomes:
                    if any(p in nome.lower() for p in proibidos):
                        culpados.append(f"{path.name}: {nome}")
        self.assertEqual(culpados, [])

    def test_no_knowledge_engine_module_names_an_embedding_model(self):
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu"
        culpados = [
            path.name
            for path in sorted((raiz / "services" / "knowledge_engine").rglob("*.py"))
            if "text-embedding" in path.read_text()
        ]
        self.assertEqual(culpados, [])

    def test_the_policy_does_not_name_a_model_either(self):
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu"
        for path in sorted((raiz / "knowledge_retrieval_policy").rglob("*.py")):
            self.assertNotIn("text-embedding", path.read_text(), path.name)
            self.assertNotIn("openai", path.read_text().lower(), path.name)


class FactoryWiringTests(unittest.TestCase):
    """A fabrica e o UNICO lugar onde "openai" e um nome.

    E ela recebe esse nome e o modelo como ARGUMENTO, nunca do ambiente: os
    dois sao campos da linha de ``knowledge_embedding_spaces``. E isso que
    permite ao Knowledge Engine alcancar um fornecedor real sem nomear
    nenhum.
    """

    def test_an_embedding_provider_is_built_for_a_space(self):
        import os
        from unittest import mock

        from agente_ia_edu.providers.factory import build_embedding_provider

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            router = build_embedding_provider("openai", model="modelo-do-espaco")
        self.assertTrue(router._embedding_providers)
        self.assertEqual(
            router._embedding_providers[0]._embedding_model, "modelo-do-espaco"
        )

    def test_a_space_without_a_model_is_refused(self):
        import os
        from unittest import mock

        from agente_ia_edu.providers.errors import ProviderConfigurationError
        from agente_ia_edu.providers.factory import build_embedding_provider

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with self.assertRaises(ProviderConfigurationError):
                build_embedding_provider("openai", model="")

    def test_an_unknown_provider_name_is_refused_without_leaking_a_key(self):
        from agente_ia_edu.providers.errors import ProviderConfigurationError
        from agente_ia_edu.providers.factory import build_embedding_provider

        with self.assertRaises(ProviderConfigurationError) as caught:
            build_embedding_provider("fornecedor-inexistente", model="m")
        self.assertIn("fornecedor-inexistente", str(caught.exception))
        self.assertNotIn("sk-", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
