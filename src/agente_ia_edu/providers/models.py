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
