"""PHASE 29 - Authorial Question Review, Approval & Publication: the
QUESTION BANK promotion step (spec s18/s19/s20).

This is a DIFFERENT concern from ``question_extraction_service.py`` (the
review workflow, staying entirely inside the staging tables): this module
is the ONLY place in PHASE 27/28/29 that ever creates rows in the OFFICIAL
``questions``/``question_versions``/``question_options`` tables, and it
NEVER modifies an existing official row - every publish is a fresh INSERT,
tagged ``origin_type='AUTHORIAL'`` with a traceable provenance
(``metadata_["provenance"]`` - the same convention already used by
``question_bank_importer.py``), exactly like every other writer in this
codebase (there is no ``UPDATE questions ...`` anywhere in this file).

Only ``APPROVED`` staging questions can be published (spec s18: "APPROVED
não significa necessariamente PUBLISHED"). Before creating a new official
question, the SAME content-hash duplicate check ``QuestionBankImporter``
already uses against ``QuestionVersion.content_hash`` is applied (spec
s19) - a collision routes the staging row to ``DUPLICATE_REVIEW`` instead
of silently creating a second question OR silently discarding it.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    ExtractedQuestion,
    ExtractedQuestionOption,
    IngestionDocument,
    Question,
    QuestionExtractionRun,
    QuestionOption,
    QuestionVersion,
)
from .question_extraction_service import (
    QuestionExtractionError,
    QuestionExtractionNotFound,
    record_review_event,
)


def _content_hash(canonical_text: str) -> str:
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def _canonical_text(question: ExtractedQuestion) -> str:
    """The professor's final word, falling back through the engine's own
    output - never raw_text directly (spec s7/s24: the human-reviewed
    layer, when it exists, is authoritative for publication)."""
    return question.reviewed_text or question.reconstructed_text or question.normalized_text


class QuestionPublicationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def publish_summary(self, run_id: UUID) -> dict:
        run = await self._session.get(QuestionExtractionRun, run_id)
        if run is None:
            raise QuestionExtractionNotFound("extraction run not found")
        doc = await self._session.get(IngestionDocument, run.ingestion_document_id)
        questions = list((await self._session.execute(
            select(ExtractedQuestion).where(ExtractedQuestion.run_id == run_id)
        )).scalars().all())
        return {
            "run_id": str(run_id), "document": doc.filename if doc else None,
            "total": len(questions),
            "approved": sum(1 for q in questions if q.review_status == "APPROVED"),
            "pending": sum(1 for q in questions if q.review_status not in
                          ("APPROVED", "PUBLISHED", "REJECTED", "DUPLICATE_REVIEW")),
            "rejected": sum(1 for q in questions if q.review_status == "REJECTED"),
            "duplicate_review": sum(1 for q in questions if q.review_status == "DUPLICATE_REVIEW"),
            "published": sum(1 for q in questions if q.review_status == "PUBLISHED"),
        }

    async def publish_run(self, run_id: UUID, *, published_by: str, school_id: UUID | None) -> dict:
        """Publishes every APPROVED question in this run. Never touches a
        question in any other status (spec s18: 'somente APPROVED pode ser
        publicado'). Returns a per-question outcome breakdown - nothing is
        silently skipped without being reported (spec s19)."""
        run = await self._session.get(QuestionExtractionRun, run_id)
        if run is None:
            raise QuestionExtractionNotFound("extraction run not found")
        doc = await self._session.get(IngestionDocument, run.ingestion_document_id)
        approved = list((await self._session.execute(
            select(ExtractedQuestion).where(
                ExtractedQuestion.run_id == run_id, ExtractedQuestion.review_status == "APPROVED",
            ).order_by(ExtractedQuestion.question_number)
        )).scalars().all())

        published: list[dict] = []
        duplicates: list[dict] = []
        errors: list[dict] = []
        for question in approved:
            canonical_text = _canonical_text(question)
            content_hash = _content_hash(canonical_text)
            existing_version = await self._session.scalar(
                select(QuestionVersion).where(QuestionVersion.content_hash == content_hash)
            )
            if existing_version is not None:
                # spec s19 - never create a second question for the same
                # content, and never silently discard the suspicion either.
                record_review_event(
                    question, event="STATUS_CHANGE", actor=published_by, to_status="DUPLICATE_REVIEW",
                    detail={"matched_question_version_id": str(existing_version.id)},
                )
                question.review_status = "DUPLICATE_REVIEW"
                duplicates.append({
                    "question_id": str(question.id), "question_number": question.question_number,
                    "matched_question_version_id": str(existing_version.id),
                })
                continue

            try:
                options = list((await self._session.execute(
                    select(ExtractedQuestionOption)
                    .where(ExtractedQuestionOption.question_id == question.id)
                    .order_by(ExtractedQuestionOption.position)
                )).scalars().all())
                provenance = {
                    "source_document_id": str(run.ingestion_document_id),
                    "source_material_id": str(run.ingestion_document_id),
                    "source_run_id": str(run.id),
                    "source_extraction_question_id": str(question.id),
                    "source_page": question.source_page_start,
                    "source_question_number": question.question_number,
                    "source_document_hash": doc.document_hash if doc else None,
                    "source_filename": doc.filename if doc else None,
                }
                transaction = self._session.begin_nested() if self._session.in_transaction() else self._session.begin()
                async with transaction:
                    official_question = Question(
                        question_type=(
                            "MULTIPLE_CHOICE" if question.question_type == "multiple_choice" else "OPEN_ENDED"
                        ),
                        school_id=school_id, origin_type="AUTHORIAL", status="PUBLISHED",
                        visibility_scope="SCHOOL", created_by_external_identity=published_by,
                        validation_status="validated", metadata_={"provenance": provenance},
                    )
                    self._session.add(official_question)
                    await self._session.flush()
                    version = QuestionVersion(
                        question_id=official_question.id, version_kind="official_original",
                        canonical_text=canonical_text, statement=canonical_text,
                        content_hash=content_hash, created_by_type="TEACHER",
                        created_by_id=published_by, metadata_={"provenance": provenance},
                    )
                    self._session.add(version)
                    await self._session.flush()
                    for opt in options:
                        self._session.add(QuestionOption(
                            question_version_id=version.id, option_key=opt.label,
                            position=opt.position, text=opt.text,
                        ))
                    await self._session.flush()
                question.published_question_id = official_question.id
                question.published_version_id = version.id
                record_review_event(
                    question, event="STATUS_CHANGE", actor=published_by, to_status="PUBLISHED",
                    detail={"question_id": str(official_question.id), "question_version_id": str(version.id)},
                )
                question.review_status = "PUBLISHED"
                published.append({
                    "question_id": str(question.id), "question_number": question.question_number,
                    "official_question_id": str(official_question.id),
                    "official_version_id": str(version.id),
                })
            except Exception as exc:  # noqa: BLE001
                errors.append({
                    "question_id": str(question.id), "question_number": question.question_number,
                    "error": str(exc),
                })
        await self._session.commit()
        return {
            "run_id": str(run_id), "attempted": len(approved),
            "published": published, "duplicates": duplicates, "errors": errors,
            "published_count": len(published), "duplicate_count": len(duplicates),
            "error_count": len(errors),
        }
