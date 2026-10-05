from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class TextGenerationRequest:
    prompt: str
    model: str | None = None
    # Optional determinism knob - None means "use the provider's own
    # default" (unchanged behavior for every caller that doesn't set this,
    # e.g. curriculum_classification.py/question_modification.py). Essay
    # correction sets a fixed seed (see essay_correction.py) - "mesma
    # redacao = mesma nota". A matching `temperature` knob was tried and
    # dropped: confirmed live (2026-09-28) that the model backing
    # OPENAI_MODEL rejects any value other than its default (1) with a 400
    # error, so there is no caller left that could ever set it.
    seed: int | None = None


@dataclass(frozen=True)
class TextGenerationResult:
    text: str
    provider: str
    model: str
    # Real per-call token usage from the underlying LLM SDK response, when the
    # provider reports it (e.g. OpenAI's `response.usage`). None whenever a
    # provider doesn't expose usage at all (e.g. FakeProvider, or a real SDK
    # response that legitimately came back with usage=None) - never a
    # fabricated 0, so a missing cost signal never masquerades as a real one.
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class EmbeddingRequest:
    texts: tuple[str, ...]
    model: str | None = None


@dataclass(frozen=True)
class EmbeddingArtifact:
    canonical_text: str
    text_hash: str
    vector: tuple[float, ...]
    dimensions: int
    provider: str
    model: str
    generated_at: datetime


@dataclass(frozen=True)
class EmbeddingResult:
    artifacts: tuple[EmbeddingArtifact, ...]
    provider: str
    model: str
    dimensions: int


@dataclass(frozen=True)
class EssayPageTranscriptionRequest:
    image_path: Path
    mime_type: str
    # Sobrescreve TRANSCRIPTION_SYSTEM_PROMPT (openai.py) quando preenchido.
    # Existe porque esse prompt padrao e escrito pro CORPO da redacao e
    # explicitamente instrui o modelo a NAO transcrever campos de
    # identificacao (nome, etc.) - o oposto do que services/essay_batch.py
    # precisa ao ler a regiao de CABECALHO de uma folha (nome/CPF). Usar o
    # prompt errado ali nao so devolve vazio, como confundia o modelo a
    # ponto de recusar a chamada inteira (confirmado ao vivo 2026-10-05).
    system_prompt: str | None = None


@dataclass(frozen=True)
class EssayOcrToken:
    text: str
    confidence: float
    start: int
    end: int


@dataclass(frozen=True)
class EssayPageTranscriptionResult:
    tokens: tuple[EssayOcrToken, ...]
    provider: str
    model: str
    # Same real-usage convention as TextGenerationResult - see its docstring.
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class EssayImageCorrectionRequest:
    """One or more ordered page images plus the fully-assembled correction
    prompt text. Unlike EssayPageTranscriptionRequest (always exactly one
    page), a correction call sees the whole essay - every page - at once,
    since annotations may reference structure spanning pages."""

    image_paths: tuple[Path, ...]
    mime_type: str
    prompt: str
    model: str | None = None
    # Same determinism knob as TextGenerationRequest, same reason - the
    # IMAGE_REGION correction path needs "mesma redacao = mesma nota" too.
    seed: int | None = None


@dataclass(frozen=True)
class DocumentPageTranscriptionRequest:
    """One page image of an arbitrary authorial document (e.g. a textbook
    chapter with no extractable PDF text layer), plus the FULL, caller-
    assembled prompt describing the target markdown convention.

    Deliberately distinct from EssayPageTranscriptionRequest: essay
    transcription has one fixed, universal system prompt ("copy literally,
    never correct spelling") baked into the provider, because every essay
    page is transcribed the same way. Document-page transcription instead
    targets whatever structural convention the caller's downstream parser
    expects (headings, numbered exercises, lettered alternatives - see
    ``authorial_material_parser.py``) - that convention is a caller concern,
    so the prompt travels with the request instead of living in the adapter.
    """

    image_path: Path
    mime_type: str
    prompt: str
    model: str | None = None


@dataclass(frozen=True)
class DocumentPageTranscriptionResult:
    text: str
    provider: str
    model: str
