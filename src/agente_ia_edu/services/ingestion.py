"""
Ingestion service orchestrates document parsing, storage, and tracking.

Handles the full pipeline: receive document → parse deterministically →
preserve original file → track extraction → maintain full traceability.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import IngestionDocument, IngestionQuestion, IngestionRun, IngestionSection
from .ingestion_parser import ParsedDocument, parse_document


class IngestionService:
    """Orchestrates document ingestion pipeline."""

    PARSER_VERSION = "1.0.0-deterministic"

    async def ingest_document(
        self,
        session: AsyncSession,
        filepath: Path,
        ingested_by: Optional[str] = None,
    ) -> tuple[IngestionDocument, IngestionRun]:
        """
        Ingest a document: parse it, preserve original, and track extraction.

        Returns (document, ingestion_run).
        """
        # Parse the document deterministically
        parsed = parse_document(filepath)

        # Create IngestionDocument record
        document = IngestionDocument(
            filename=parsed.filename,
            document_type=filepath.suffix.lstrip(".").upper(),
            document_hash=parsed.document_hash,
            storage_uri=str(filepath),
            file_size_bytes=filepath.stat().st_size,
            title=parsed.title,
            author=parsed.author,
            page_count=parsed.page_count,
            ingested_by_external_identity=ingested_by,
            status="processing",
        )
        session.add(document)
        await session.flush()

        # Create IngestionRun record
        run = IngestionRun(
            document_id=document.id,
            parser_version=self.PARSER_VERSION,
            run_status="processing",
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.flush()

        # Save sections
        section_map = {}  # position -> IngestionSection.id
        for parsed_section in parsed.sections:
            section = IngestionSection(
                document_id=document.id,
                section_type=parsed_section.section_type,
                section_number=parsed_section.section_number,
                title=parsed_section.title,
                description=parsed_section.description,
                position=parsed_section.position,
                page_start=parsed_section.page_start,
                page_end=parsed_section.page_end,
                content_preview=" ".join(parsed_section.content_lines[:50])[:500] if parsed_section.content_lines else None,
            )
            session.add(section)
            await session.flush()
            section_map[parsed_section.position] = section.id

        # Save questions
        for parsed_question in parsed.questions:
            section_id = None
            if parsed_question.section_index is not None and parsed_question.section_index in section_map:
                section_id = section_map[parsed_question.section_index]

            question = IngestionQuestion(
                document_id=document.id,
                section_id=section_id,
                question_number=parsed_question.question_number,
                question_type="MULTIPLE_CHOICE" if parsed_question.alternatives_text else "OTHER",
                statement_text=parsed_question.statement_text,
                alternatives_text=parsed_question.alternatives_text,
                correct_answer=parsed_question.correct_answer,
                answer_explanation=parsed_question.answer_explanation,
                position=parsed_question.position,
                page_start=parsed_question.page_start,
                page_end=parsed_question.page_end,
                status="extracted",
            )
            session.add(question)

        # Update run statistics
        run.sections_found = len(parsed.sections)
        run.questions_found = len(parsed.questions)
        run.images_found = parsed.total_images
        run.tables_found = parsed.total_tables
        run.run_status = "completed"
        run.completed_at = datetime.now(timezone.utc)

        # Update document status
        document.status = "processed"

        await session.flush()
        await session.commit()

        return document, run

    async def check_idempotency(
        self,
        session: AsyncSession,
        filepath: Path,
    ) -> Optional[IngestionDocument]:
        """
        Check if this document was already ingested (by hash).

        Returns existing document if found, None otherwise.
        """
        from sqlalchemy import select

        file_hash = parse_document(filepath).document_hash  # Only compute hash, don't parse fully
        from .ingestion_parser import DocxParser
        file_hash = DocxParser.file_hash(filepath)

        result = await session.execute(
            select(IngestionDocument).where(IngestionDocument.document_hash == file_hash)
        )
        return result.scalar_one_or_none()
