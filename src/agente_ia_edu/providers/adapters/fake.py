import hashlib
from datetime import datetime, timezone

from ..models import (
    EmbeddingArtifact,
    EmbeddingRequest,
    EmbeddingResult,
    TextGenerationRequest,
    TextGenerationResult,
)


class FakeProvider:
    provider = "fake"
    text_model = "fake-text-v1"
    embedding_model = "fake-embedding-v1"
    embedding_dimensions = 8
    generated_at = datetime(2000, 1, 1, tzinfo=timezone.utc)

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        model = request.model or self.text_model
        return TextGenerationResult(
            text=f"FAKE RESPONSE: {request.prompt}",
            provider=self.provider,
            model=model,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        model = request.model or self.embedding_model
        artifacts = tuple(
            self._build_artifact(canonical_text=text, model=model)
            for text in request.texts
        )
        return EmbeddingResult(
            artifacts=artifacts,
            provider=self.provider,
            model=model,
            dimensions=self.embedding_dimensions,
        )

    def _build_artifact(self, canonical_text: str, model: str) -> EmbeddingArtifact:
        text_hash = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
        digest = hashlib.sha256(canonical_text.encode("utf-8")).digest()
        vector = tuple((byte / 255.0) * 2.0 - 1.0 for byte in digest[: self.embedding_dimensions])
        return EmbeddingArtifact(
            canonical_text=canonical_text,
            text_hash=text_hash,
            vector=vector,
            dimensions=self.embedding_dimensions,
            provider=self.provider,
            model=model,
            generated_at=self.generated_at,
        )
