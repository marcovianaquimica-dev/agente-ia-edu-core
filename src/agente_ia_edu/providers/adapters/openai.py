"""OpenAI implementation of the provider-neutral text-generation contract."""

import base64
import hashlib
import math
import os
import re
from datetime import datetime, timezone

from ..errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ..models import (
    EmbeddingArtifact,
    EmbeddingRequest,
    EmbeddingResult,
    EssayImageCorrectionRequest,
    EssayOcrToken,
    EssayPageTranscriptionRequest,
    EssayPageTranscriptionResult,
    TextGenerationRequest,
    TextGenerationResult,
)


# Confirmed live (2026-09-25): the model sometimes declines to transcribe a
# real handwritten essay page, returning a short apology as plain `content`
# instead of populating the SDK's `message.refusal` field - the pipeline then
# treated the refusal sentence itself as the transcription. This is a
# best-effort net for that failure mode (checked first: the proper
# `message.refusal` field, when the model does populate it).
_REFUSAL_PREFIXES = (
    "i'm sorry", "i am sorry", "sorry, but", "i can't", "i cannot",
    "desculpe", "sinto muito", "não posso", "nao posso",
    "i'm unable", "i am unable",
)

# Confirmed live (2026-09-25), a second failure mode: the model transcribes
# a real prefix of the page, then gives up partway through and appends its
# own refusal INSIDE the content instead of a clean, prefix-only refusal -
# e.g. "...(Continua com o resto do texto, mas não posso transcrever mais)."
# A prefix check never catches this; only a substring search does. This
# must reject the whole transcription (a silently truncated page is as
# unsafe to correct against as an outright refusal), not just strip the
# trailing note.
_REFUSAL_SUBSTRINGS = (
    "não posso transcrever", "nao posso transcrever",
    "não posso continuar", "nao posso continuar",
    "não posso ler o resto", "nao posso ler o resto",
    "continua com o resto do texto",
    "i cannot transcribe the rest", "i can't transcribe the rest",
    "i cannot continue", "i can't continue",
)


def _looks_like_a_refusal(content: str) -> bool:
    normalized = content.strip().lower()
    if normalized.startswith(_REFUSAL_PREFIXES):
        return True
    return any(phrase in normalized for phrase in _REFUSAL_SUBSTRINGS)


def _usage_tokens(response) -> tuple[int | None, int | None]:
    """The SDK's `response.usage` (prompt_tokens/completion_tokens) can be
    missing entirely on a hand-built test double, or `None` on a real
    response in rare cases - both must degrade to (None, None) rather than
    raising, since this is purely cost-reporting metadata, never something
    that should fail an otherwise-successful call."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None
    return getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)


def _embedding_usage_tokens(response) -> int | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    value = getattr(usage, "prompt_tokens", None)
    if value is None:
        value = getattr(usage, "total_tokens", None)
    return int(value) if value is not None else None


def _ordered_vectors(response, *, expected: int) -> list[list[float]]:
    """Vetores na ordem do PEDIDO, e nao na ordem em que vieram.

    A API documenta que a ordem da resposta pode diferir da do envio e que o
    alinhamento correto e pelo campo ``index``. Confiar na ordem de chegada
    trocaria enunciado por vetor errado - e silenciosamente, porque um vetor
    trocado continua sendo um vetor valido.
    """
    data = list(getattr(response, "data", None) or [])
    if len(data) != expected:
        raise ProviderInvalidResponseError(
            f"OpenAI returned {len(data)} embeddings for {expected} inputs"
        )
    ordenados: list[list[float]] = [None] * expected  # type: ignore[list-item]
    for posicao, item in enumerate(data):
        indice = getattr(item, "index", None)
        indice = posicao if indice is None else int(indice)
        if not 0 <= indice < expected or ordenados[indice] is not None:
            raise ProviderInvalidResponseError(
                f"OpenAI returned an out-of-range or duplicated index: {indice}"
            )
        vetor = getattr(item, "embedding", None)
        if not vetor:
            raise ProviderInvalidResponseError("OpenAI returned an empty embedding")
        ordenados[indice] = [float(component) for component in vetor]
    tamanhos = {len(vetor) for vetor in ordenados}
    if len(tamanhos) != 1:
        # A dimensao do ESPACO e validada pelo servico; o que se recusa aqui
        # e uma resposta internamente inconsistente.
        raise ProviderInvalidResponseError(
            f"OpenAI returned embeddings of differing lengths: {sorted(tamanhos)}"
        )
    return ordenados


class OpenAIProvider:
    provider = "openai"

    def __init__(self, *, api_key: str | None = None, model: str | None = None, vision_model: str | None = None, embedding_model: str | None = None, timeout_seconds: float | None = None, client=None):
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._model = model or os.getenv("OPENAI_MODEL")
        self._vision_model = vision_model or os.getenv("OPENAI_VISION_MODEL")
        # Default de ULTIMO recurso. O caminho normal e o chamador mandar o
        # modelo em ``EmbeddingRequest.model``, vindo da linha de
        # ``KnowledgeEmbeddingSpace`` - e assim que o Knowledge Engine usa um
        # modelo sem conhecer nome de modelo nenhum.
        self._embedding_model = embedding_model or os.getenv("OPENAI_EMBEDDING_MODEL")
        self._timeout_seconds = timeout_seconds or float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
        self._client = client

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Vetoriza textos, devolvendo o contrato provider-neutral.

        O texto vai VERBATIM. Nenhuma normalizacao aqui: ``text_hash``
        precisa continuar identificando exatamente o conteudo vetorizado, e
        qualquer transformacao - ate NFC - quebraria essa identidade.
        """
        if not self._api_key:
            raise ProviderConfigurationError("OpenAI is not configured")
        model = request.model or self._embedding_model
        if not model:
            raise ProviderConfigurationError("OpenAI embedding model is not configured")
        if not request.texts:
            # Nem chega a chamar: um lote vazio nao e erro, e gastar uma
            # requisicao para descobrir isso seria desperdicio.
            return EmbeddingResult(
                artifacts=(), provider=self.provider, model=model, dimensions=0
            )
        try:
            client = self._client or self._create_client()
            response = await client.embeddings.create(
                model=model,
                input=list(request.texts),
                timeout=self._timeout_seconds,
            )
            vectors = _ordered_vectors(response, expected=len(request.texts))
            generated_at = datetime.now(timezone.utc)
            dimensions = len(vectors[0])
            artifacts = tuple(
                EmbeddingArtifact(
                    canonical_text=text,
                    text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    vector=tuple(vector),
                    dimensions=dimensions,
                    provider=self.provider,
                    model=model,
                    generated_at=generated_at,
                )
                for text, vector in zip(request.texts, vectors)
            )
            norms = [math.sqrt(sum(c * c for c in vector)) for vector in vectors]
            media = sum(norms) / len(norms)
            return EmbeddingResult(
                artifacts=artifacts,
                provider=self.provider,
                model=model,
                dimensions=dimensions,
                vector_norm_mean=media,
                vectors_are_unit_norm=all(abs(n - 1.0) <= 1e-3 for n in norms),
                input_tokens=_embedding_usage_tokens(response),
            )
        except ProviderInvalidResponseError:
            raise
        except Exception as exc:
            raise self._map_error(exc) from exc

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if not self._api_key:
            raise ProviderConfigurationError("OpenAI is not configured")
        model = request.model or self._model
        if not model:
            raise ProviderConfigurationError("OpenAI model is not configured")
        try:
            client = self._client or self._create_client()
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Return only valid JSON. Follow the user prompt exactly."},
                    {"role": "user", "content": request.prompt},
                ],
                response_format={"type": "json_object"},
                timeout=self._timeout_seconds,
            )
            content = response.choices[0].message.content
            if not content:
                raise ProviderInvalidResponseError("OpenAI returned an empty response")
            input_tokens, output_tokens = _usage_tokens(response)
            return TextGenerationResult(
                text=content, provider=self.provider, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
        except ProviderInvalidResponseError:
            raise
        except Exception as exc:
            raise self._map_error(exc) from exc

    async def transcribe_page(
        self, request: EssayPageTranscriptionRequest
    ) -> EssayPageTranscriptionResult:
        if not self._api_key:
            raise ProviderConfigurationError("OpenAI is not configured")
        if not self._vision_model:
            raise ProviderConfigurationError("OpenAI vision model is not configured")
        try:
            client = self._client or self._create_client()
            image_b64 = base64.b64encode(request.image_path.read_bytes()).decode("ascii")
            response = await client.chat.completions.create(
                model=self._vision_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Transcreva literalmente o texto manuscrito ou impresso na "
                            "imagem, palavra por palavra, na ordem em que aparece. Nao "
                            "corrija ortografia, gramatica ou concordancia - reproduza "
                            "exatamente o que esta escrito, mesmo que contenha erros. "
                            "Nao adicione nenhum texto que nao esteja na imagem. Se uma "
                            "palavra ou trecho estiver genuinamente ilegivel, escreva "
                            "[ilegivel] no lugar dele em vez de adivinhar - nunca invente "
                            "uma palavra plausivel para preencher um trecho que voce nao "
                            "conseguiu ler. Transcreva a pagina inteira, do inicio ao fim; "
                            "nunca pare no meio e nunca escreva um pedido de desculpas ou "
                            "explicacao sobre nao conseguir continuar. Transcreva APENAS o "
                            "corpo do texto dissertativo-argumentativo escrito pelo "
                            "participante (o texto corrido nas linhas pautadas/numeradas). "
                            "NAO transcreva elementos padronizados de uma folha de "
                            "redacao oficial que nao fazem parte do texto do aluno: o "
                            "enunciado ou reafirmacao impressa do tema (ex.: linha "
                            "\"TEMA:\"), campos de identificacao (nome completo, "
                            "turma, turno, data, local de prova, assinatura do "
                            "participante), tabelas ou grades de correcao (ex.: "
                            "\"Aspectos Macroestruturais\", \"Comp. I\" a \"Comp. V\", "
                            "\"CORRETOR(A)\", \"NOTA\"), instrucoes de preenchimento "
                            "impressas na margem, ou qualquer outro elemento grafico do "
                            "formulario que nao seja prosa escrita pelo participante. "
                            "Ignore esses elementos completamente, mesmo que estejam "
                            "bem legiveis - nao os inclua na transcricao nem os "
                            "mencione."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{request.mime_type};base64,{image_b64}",
                                    "detail": "high",
                                },
                            }
                        ],
                    },
                ],
                logprobs=True,
                top_logprobs=1,
                timeout=self._timeout_seconds,
            )
            choice = response.choices[0]
            if getattr(choice.message, "refusal", None):
                raise ProviderInvalidResponseError(
                    f"OpenAI refused to transcribe the image: {choice.message.refusal}"
                )
            content = choice.message.content
            if not content:
                raise ProviderInvalidResponseError("OpenAI returned an empty transcription")
            if _looks_like_a_refusal(content):
                raise ProviderInvalidResponseError(
                    f"OpenAI refused to transcribe the image: {content}"
                )
            tokens = self._tokens_from_logprobs(content, choice.logprobs)
            input_tokens, output_tokens = _usage_tokens(response)
            return EssayPageTranscriptionResult(
                tokens=tokens, provider=self.provider, model=self._vision_model,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
        except ProviderInvalidResponseError:
            raise
        except Exception as exc:
            raise self._map_error(exc) from exc

    async def correct_from_images(
        self, request: EssayImageCorrectionRequest
    ) -> TextGenerationResult:
        if not self._api_key:
            raise ProviderConfigurationError("OpenAI is not configured")
        model = request.model or self._vision_model
        if not model:
            raise ProviderConfigurationError("OpenAI vision model is not configured")
        try:
            client = self._client or self._create_client()
            content: list[dict] = [{"type": "text", "text": request.prompt}]
            for image_path in request.image_paths:
                image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{request.mime_type};base64,{image_b64}"},
                    }
                )
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "Return only valid JSON. Follow the user prompt exactly.",
                    },
                    {"role": "user", "content": content},
                ],
                response_format={"type": "json_object"},
                timeout=self._timeout_seconds,
            )
            text = response.choices[0].message.content
            if not text:
                raise ProviderInvalidResponseError("OpenAI returned an empty response")
            input_tokens, output_tokens = _usage_tokens(response)
            return TextGenerationResult(
                text=text, provider=self.provider, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
        except ProviderInvalidResponseError:
            raise
        except Exception as exc:
            raise self._map_error(exc) from exc

    def _tokens_from_logprobs(self, content: str, logprobs) -> tuple[EssayOcrToken, ...]:
        """Approximates per-token confidence from the model's own generation
        logprobs (confidence = exp(logprob)). This is NOT a real OCR
        confidence score - it is a documented approximation, the best signal
        available without a dedicated vision/OCR confidence API. When the
        response carries no logprob data at all, every token gets full
        confidence rather than an invented number, so a missing signal never
        masquerades as a low-confidence flag the student has to review."""
        if logprobs is None or not getattr(logprobs, "content", None):
            return (EssayOcrToken(text=content, confidence=1.0, start=0, end=len(content)),)
        tokens: list[EssayOcrToken] = []
        cursor = 0
        for entry in logprobs.content:
            piece = entry.token
            idx = content.find(piece, cursor)
            if idx == -1:
                continue
            start, end = idx, idx + len(piece)
            confidence = math.exp(entry.logprob)
            tokens.append(EssayOcrToken(text=piece, confidence=confidence, start=start, end=end))
            cursor = end
        return tuple(tokens)

    def _create_client(self):
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=self._api_key,
            timeout=self._timeout_seconds,
            max_retries=0,
        )

    def _map_error(self, error: Exception):
        name = type(error).__name__
        if name in {"AuthenticationError", "PermissionDeniedError"}:
            return ProviderAuthenticationError("OpenAI authentication failed")
        if name == "RateLimitError":
            return ProviderRateLimitError("OpenAI rate limit reached")
        if name in {"APITimeoutError", "TimeoutError"}:
            return ProviderTimeoutError("OpenAI request timed out")
        low_level_error = error.__cause__ or error.__context__
        unavailable = ProviderUnavailableError("OpenAI request failed")
        unavailable.original_error_type = name
        unavailable.diagnostic_message = self._sanitize_error_message(str(error))
        unavailable.low_level_error_type = (
            type(low_level_error).__name__ if low_level_error is not None else None
        )
        unavailable.low_level_diagnostic_message = (
            self._sanitize_error_message(str(low_level_error))
            if low_level_error is not None
            else None
        )
        return unavailable

    def _sanitize_error_message(self, message: str) -> str:
        for secret in (self._api_key, os.getenv("DATABASE_URL")):
            if secret:
                message = message.replace(secret, "[REDACTED]")
            message = re.sub(
                r"(?:https?|postgres(?:ql)?)(?:\+[^:]+)?://[^\s/@:]+(?::[^\s/@]*)?@[^\s]+",
                "[REDACTED]",
                message,
                flags=re.I,
            )
        message = re.sub(r"postgres(?:ql)?(?:\+[^:]+)?://[^\s]+", "[REDACTED]", message, flags=re.I)
        message = re.sub(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+", "[REDACTED]", message)
        message = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+", r"\1[REDACTED]", message)
        message = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", message)
        return message