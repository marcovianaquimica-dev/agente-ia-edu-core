from typing import Protocol, runtime_checkable

from .models import (
    EmbeddingRequest,
    EmbeddingResult,
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
