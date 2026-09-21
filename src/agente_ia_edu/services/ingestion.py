"""
Ingestion service orchestrates document parsing, storage, and tracking.

Handles the full pipeline: receive document → parse deterministically →
preserve original file → track extraction → maintain full traceability.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import IngestionAsset, IngestionDocument, IngestionQuestion, IngestionRun, IngestionSection
from .ingestion_parser import ParsedDocument, parse_document


class IngestionService:
    """Orchestrates document ingestion pipeline."""

    PARSER_VERSION = "1.0.0-deterministic"

    async def ingest_document(
        self,
        session: AsyncSession,
        filepath: Path,
        ingested_by: Optional[str] = None,
        answer_key: dict[int, str] | None = None,
        source_metadata: dict | None = None,
        parsed_override: ParsedDocument | None = None,
        document_type_override: str | None = None,
    ) -> tuple[IngestionDocument, IngestionRun]:
        """
        Ingest a document: parse it, preserve original, and track extraction.

        ``parsed_override`` (PHASE 10.7): a ``ParsedDocument`` already produced by
        the caller - used to supply a recovered text layer (PyMuPDF) for the four
        ENEM 2024/2025 booklets the PHASE 10.6 contract marked ``RECOVERED``.
        When ``None`` (every existing caller) the document is parsed here exactly
        as before. The idempotency short-circuit still runs first either way, so
        an already-ingested file is never re-parsed or re-imported.

        ``document_type_override`` (PHASE 26): the ``document_type`` column only
        accepts 'DOCX'/'PDF'/'OTHER'. Every existing caller ingests exactly those
        two suffixes and leaves this ``None``, so ``filepath.suffix`` is used
        exactly as before. Authorial ingestion (PHASE 26) also accepts TXT/MD,
        whose bare suffix would violate that CHECK constraint - it passes
        ``"OTHER"`` explicitly instead of widening the constraint.

        Returns (document, ingestion_run).
        """
        existing = await self.check_idempotency(session, filepath)
        if existing is not None:
            latest_run = await session.scalar(
                select(IngestionRun).where(IngestionRun.document_id == existing.id)
                .order_by(IngestionRun.created_at.desc()).limit(1)
            )
            if latest_run is not None:
                return existing, latest_run

        # Parse the document deterministically (or use the caller's parse)
        parsed = parsed_override if parsed_override is not None else parse_document(filepath)
        if answer_key is not None:
            from .ingestion_parser import PdfParser
            PdfParser.apply_answer_key(parsed, answer_key)

        # Create IngestionDocument record
        document = IngestionDocument(
            filename=parsed.filename,
            document_type=document_type_override or filepath.suffix.lstrip(".").upper(),
            document_hash=parsed.document_hash,
            storage_uri=str(filepath),
            file_size_bytes=filepath.stat().st_size,
            title=parsed.title,
            author=parsed.author,
            page_count=parsed.page_count,
            ingested_by_external_identity=ingested_by,
            status="processing",
            metadata_=source_metadata,
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

        # Save sections. Batched (one INSERT set, one flush) rather than one
        # flush() PER section - id is assigned client-side (uuid4) right here
        # rather than left to the mapped_column default (only applied AT
        # flush time), so section_map is fully known before any INSERT runs.
        # Measured live against Postgres before this fix: a 5-question
        # document issued 12 SQL statements, a 50-question one issued 61 -
        # real O(n) growth (one INSERT round-trip per section AND per
        # question); this brings both cases down to the same small constant.
        section_map = {}  # position -> IngestionSection.id
        section_rows = []
        for parsed_section in parsed.sections:
            section = IngestionSection(
                id=uuid4(),
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
            section_rows.append(section)
            section_map[parsed_section.position] = section.id
        if section_rows:
            session.add_all(section_rows)
            await session.flush()  # one flush for every section - not per-row

        # Save questions - same batching, one flush for the whole collection.
        question_map = {}
        question_rows = []
        for parsed_question in parsed.questions:
            section_id = None
            if parsed_question.section_index is not None and parsed_question.section_index in section_map:
                section_id = section_map[parsed_question.section_index]

            question = IngestionQuestion(
                id=uuid4(),
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
                metadata_={"requires_review": parsed_question.requires_review},
            )
            question_rows.append(question)
            question_map[parsed_question.question_number] = question.id
        if question_rows:
            session.add_all(question_rows)
            await session.flush()  # one flush for every question - not per-row

        for asset in parsed.assets:
            session.add(IngestionAsset(
                document_id=document.id,
                asset_type=asset.asset_type if asset.asset_type in {"IMAGE", "TABLE", "FORMULA", "DIAGRAM"} else "OTHER",
                asset_name=f"page-{asset.page}-{asset.asset_type.lower()}",
                storage_uri=str(filepath),
                question_id=question_map.get(asset.question_number),
                page=asset.page,
                position=asset.position,
                metadata_={
                    "evidence_only": True,
                    "requires_review": True,
                    "visual_asset_type": asset.asset_type,
                    "source_hash": asset.source_hash,
                    "mime_type": asset.mime_type,
                    "byte_size": asset.byte_size,
                    "association_confident": asset.association_confident,
                },
            ))

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

        from .ingestion_parser import DocxParser
        file_hash = DocxParser.file_hash(filepath)

        result = await session.execute(
            select(IngestionDocument).where(IngestionDocument.document_hash == file_hash)
        )
        return result.scalar_one_or_none()
