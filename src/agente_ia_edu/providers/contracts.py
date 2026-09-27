from typing import Protocol, runtime_checkable

from .models import (
    DocumentPageTranscriptionRequest,
    DocumentPageTranscriptionResult,
    EmbeddingRequest,
    EmbeddingResult,
    EssayImageCorrectionRequest,
    EssayPageTranscriptionRequest,
    EssayPageTranscriptionResult,
    TextGenerationRequest,
    TextGenerationResult,
)


@runtime_checkable
class TextGenerationProvider(Protocol):
    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """Generate text from a provider-neutral request."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Generate embeddings from a provider-neutral request."""


@runtime_checkable
class EssayTranscriptionProvider(Protocol):
    async def transcribe_page(
        self, request: EssayPageTranscriptionRequest
    ) -> EssayPageTranscriptionResult:
        """Transcribe one essay page image into raw, unedited text tokens -
        never suggesting spelling/grammar correction."""


@runtime_checkable
class EssayImageCorrectionProvider(Protocol):
    async def correct_from_images(
        self, request: EssayImageCorrectionRequest
    ) -> TextGenerationResult:
        """Produce a correction JSON response (matching essay_engine_contract.v1's
        RESPONSE_SCHEMA, sans identification) from one or more ordered essay
        page images, for a submission with no canonical text."""


@runtime_checkable
class DocumentPageTranscriptionProvider(Protocol):
    async def transcribe_document_page(
        self, request: DocumentPageTranscriptionRequest
    ) -> DocumentPageTranscriptionResult:
        """Transcribe one authorial-document page image (e.g. a textbook page
        with no extractable text layer) into structured markdown text,
        following the caller-supplied prompt's convention. Never fabricates
        content it cannot read with confidence - an illegible or purely
        visual element becomes an honest textual note, not an invented
        value/formula."""
