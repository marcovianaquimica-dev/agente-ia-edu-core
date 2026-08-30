"""
Repositories for ingestion domain entities.

Provides query layer for IngestionDocument, IngestionRun, etc.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ..db.models import (
    IngestionAsset,
    IngestionDocument,
    IngestionQuestion,
    IngestionRun,
    IngestionSection,
)


class IngestionDocumentRepository:
    """Repository for IngestionDocument queries."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, document_id: UUID) -> Optional[IngestionDocument]:
        """Fetch document by ID."""
        result = await self.session.execute(
            select(IngestionDocument).where(IngestionDocument.id == document_id)
        )
        return result.scalar_one_or_none()

    async def get_by_hash(self, document_hash: str) -> Optional[IngestionDocument]:
        """Fetch document by content hash (for idempotency)."""
        result = await self.session.execute(
            select(IngestionDocument).where(IngestionDocument.document_hash == document_hash)
        )
        return result.scalar_one_or_none()

    async def list_by_status(self, status: str) -> list[IngestionDocument]:
        """List documents by status (pending, processing, processed, failed, archived)."""
        result = await self.session.execute(
            select(IngestionDocument).where(IngestionDocument.status == status)
        )
        return result.scalars().all()

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[IngestionDocument]:
        """List all documents with pagination."""
        result = await self.session.execute(
            select(IngestionDocument).limit(limit).offset(offset)
        )
        return result.scalars().all()


class IngestionRunRepository:
    """Repository for IngestionRun queries."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, run_id: UUID) -> Optional[IngestionRun]:
        """Fetch ingestion run by ID."""
        result = await self.session.execute(
            select(IngestionRun).where(IngestionRun.id == run_id)
        )
        return result.scalar_one_or_none()

    async def list_by_document(self, document_id: UUID) -> list[IngestionRun]:
        """List all runs for a document."""
        result = await self.session.execute(
            select(IngestionRun).where(IngestionRun.document_id == document_id)
        )
        return result.scalars().all()


class IngestionSectionRepository:
    """Repository for IngestionSection queries."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, section_id: UUID) -> Optional[IngestionSection]:
        """Fetch section by ID."""
        result = await self.session.execute(
            select(IngestionSection).where(IngestionSection.id == section_id)
        )
        return result.scalar_one_or_none()

    async def list_by_document(self, document_id: UUID) -> list[IngestionSection]:
        """List all sections in a document, ordered by position."""
        result = await self.session.execute(
            select(IngestionSection)
            .where(IngestionSection.document_id == document_id)
            .order_by(IngestionSection.position)
        )
        return result.scalars().all()

    async def list_by_type(self, document_id: UUID, section_type: str) -> list[IngestionSection]:
        """List sections of a specific type in a document."""
        result = await self.session.execute(
            select(IngestionSection).where(
                and_(
                    IngestionSection.document_id == document_id,
                    IngestionSection.section_type == section_type,
                )
            )
        )
        return result.scalars().all()


class IngestionQuestionRepository:
    """Repository for IngestionQuestion queries."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, question_id: UUID) -> Optional[IngestionQuestion]:
        """Fetch question by ID."""
        result = await self.session.execute(
            select(IngestionQuestion).where(IngestionQuestion.id == question_id)
        )
        return result.scalar_one_or_none()

    async def list_by_document(self, document_id: UUID) -> list[IngestionQuestion]:
        """List all questions in a document, ordered by position."""
        result = await self.session.execute(
            select(IngestionQuestion)
            .where(IngestionQuestion.document_id == document_id)
            .order_by(IngestionQuestion.position)
        )
        return result.scalars().all()

    async def list_by_section(self, section_id: UUID) -> list[IngestionQuestion]:
        """List all questions in a section, ordered by position."""
        result = await self.session.execute(
            select(IngestionQuestion)
            .where(IngestionQuestion.section_id == section_id)
            .order_by(IngestionQuestion.position)
        )
        return result.scalars().all()

    async def count_by_document(self, document_id: UUID) -> int:
        """Count total questions in a document."""
        result = await self.session.execute(
            select(IngestionQuestion).where(IngestionQuestion.document_id == document_id)
        )
        return len(result.scalars().all())


class IngestionAssetRepository:
    """Repository for IngestionAsset queries."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_document(self, document_id: UUID) -> list[IngestionAsset]:
        """List all assets in a document."""
        result = await self.session.execute(
            select(IngestionAsset).where(IngestionAsset.document_id == document_id)
        )
        return result.scalars().all()

    async def list_by_question(self, question_id: UUID) -> list[IngestionAsset]:
        """List assets associated with a question."""
        result = await self.session.execute(
            select(IngestionAsset).where(IngestionAsset.question_id == question_id)
        )
        return result.scalars().all()

    async def list_by_section(self, section_id: UUID) -> list[IngestionAsset]:
        """List assets associated with a section."""
        result = await self.session.execute(
            select(IngestionAsset).where(IngestionAsset.section_id == section_id)
        )
        return result.scalars().all()
