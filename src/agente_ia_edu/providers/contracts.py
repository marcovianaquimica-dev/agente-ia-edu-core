from typing import Protocol, runtime_checkable

from .models import (
    EmbeddingRequest,
    EmbeddingResult,
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
