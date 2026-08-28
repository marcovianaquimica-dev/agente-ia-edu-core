from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TextGenerationRequest:
    prompt: str
    model: str | None = None


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
