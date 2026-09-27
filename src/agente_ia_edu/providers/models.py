from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


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
