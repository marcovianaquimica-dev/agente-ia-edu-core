"""Controlled import from intermediate ingestion records into Question Bank."""

import hashlib
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, Exam, ExamApplication, ExamBooklet, IngestionDocument, IngestionQuestion, Institution, Question, QuestionOption, QuestionVersion, SourceDocument


@dataclass(frozen=True)
class QuestionImportResult:
    created: bool
    question_id: UUID | None
    question_version_id: UUID | None
    imported_options: int
    validation_status: str
    review_required: bool
    reason: str | None
    provenance: dict


class QuestionBankImporter:
    def __init__(self, session: AsyncSession, *, fail_after_question: bool = False):
        self.session = session
        self.fail_after_question = fail_after_question

    async def import_question(self, ingestion_question_id: UUID) -> QuestionImportResult:
        item = await self.session.get(IngestionQuestion, ingestion_question_id)
        if item is None:
            raise ValueError("Ingestion question not found")
        document = await self.session.get(IngestionDocument, item.document_id)
        reason, options = self._validate(item, document)
        provenance = self._provenance(item, document)
        if reason:
            item.metadata_ = {**(item.metadata_ or {}), "review_required": True, "review_reason": reason}
            await self.session.commit()
            return QuestionImportResult(False, None, None, 0, "REVIEW_REQUIRED", True, reason, provenance)
        if item.question_version_id:
            return QuestionImportResult(False, None, item.question_version_id, 5, "IMPORTED", False, None, provenance)

        content_hash = hashlib.sha256(item.statement_text.encode("utf-8")).hexdigest()
        existing = await self.session.scalar(select(QuestionVersion).where(QuestionVersion.content_hash == content_hash))
        if existing:
            item.question_version_id = existing.id
            item.status = "imported"
            await self.session.commit()
            return QuestionImportResult(False, existing.question_id, existing.id, 5, "IMPORTED", False, None, provenance)

        try:
            transaction = self.session.begin_nested() if self.session.in_transaction() else self.session.begin()
            async with transaction:
                question = Question(
                    question_type="MULTIPLE_CHOICE", origin_type="IMPORTED", status="DRAFT",
                    visibility_scope="PRIVATE", validation_status="validated",
                    metadata_={"provenance": provenance},
                )
                self.session.add(question)
                await self.session.flush()
                if self.fail_after_question:
                    raise RuntimeError("Simulated import failure")
                version = QuestionVersion(
                    question_id=question.id, version_kind="official_original",
                    canonical_text=item.statement_text, statement=item.statement_text,
                    content_hash=content_hash, metadata_={"source": "ingestion", **provenance},
                )
                self.session.add(version)
                await self.session.flush()
                for position, (key, text) in enumerate(options, start=1):
                    self.session.add(QuestionOption(
                        question_version_id=version.id, option_key=key, position=position,
                        text=text, is_valid_option=key == item.correct_answer,
                    ))
                await self.session.flush()
                booklet, key_revision = await self._official_context(document)
                booklet_question = BookletQuestion(
                    exam_booklet_id=booklet.id, question_version_id=version.id,
                    position=item.position + 1, official_number=item.question_number,
                    page_number=item.page_start, extraction_method="pypdf-text-layer",
                    extractor_version="1.0.0-deterministic", evidence_uri=document.storage_uri,
                    metadata_={"ingestion_question_id": str(item.id), "page_end": item.page_end},
                )
                self.session.add(booklet_question)
                await self.session.flush()
                correct_option = await self.session.scalar(select(QuestionOption).where(
                    QuestionOption.question_version_id == version.id,
                    QuestionOption.option_key == item.correct_answer,
                ))
                if correct_option is None:
                    raise ValueError("Official answer does not resolve to an imported option")
                self.session.add(AnswerKeyEntry(
                    answer_key_revision_id=key_revision.id, booklet_question_id=booklet_question.id,
                    official_answer_label=item.correct_answer, resolved_option_id=correct_option.id,
                    page_number=item.page_start,
                ))
                item.question_version_id = version.id
                item.status = "imported"
            await self.session.commit()
            return QuestionImportResult(True, question.id, version.id, 5, "VALIDATED", False, None, provenance)
        except Exception:
            await self.session.rollback()
            raise

    @staticmethod
    def _validate(item: IngestionQuestion, document: IngestionDocument | None):
        if document is None or not document.document_hash or not document.storage_uri:
            return "Missing source document provenance", []
        if not item.question_number or not item.statement_text.strip():
            return "Missing question number or statement", []
        if (item.metadata_ or {}).get("annulled"):
            return "Question is annulled", []
        if (item.metadata_ or {}).get("requires_review"):
            return "Extraction requires review", []
        if not item.correct_answer or item.correct_answer not in "ABCDE":
            return "Missing or invalid official answer key", []
        lines = [line.strip() for line in (item.alternatives_text or "").splitlines() if line.strip()]
        parsed = []
        for line in lines:
            match = re.match(r"^([A-E])\)\s+(.+)$", line)
            if not match or not match.group(2).strip():
                return "Alternatives are incomplete", []
            parsed.append((match.group(1), match.group(2).strip()))
        if [key for key, _ in parsed] != list("ABCDE") or len({text for _, text in parsed}) != 5:
            return "Alternatives must be unique A-E", []
        return None, parsed

    @staticmethod
    def _provenance(item: IngestionQuestion, document: IngestionDocument) -> dict:
        return {
            "ingestion_question_id": str(item.id), "ingestion_document_id": str(document.id),
            "document_hash": document.document_hash, "storage_uri": document.storage_uri,
            "question_number": item.question_number, **(document.metadata_ or {}),
        }

    async def _official_context(self, document: IngestionDocument) -> tuple[ExamBooklet, AnswerKeyRevision]:
        metadata = document.metadata_ or {}
        year = int(metadata["exam_year"])
        day = int(metadata["exam_day"])
        booklet_code = str(metadata["booklet"])
        institution = await self.session.scalar(select(Institution).where(Institution.code == "INEP"))
        if institution is None:
            institution = Institution(code="INEP", name="Instituto Nacional de Estudos e Pesquisas Educacionais Anisio Teixeira")
            self.session.add(institution)
            await self.session.flush()
        exam = await self.session.scalar(select(Exam).where(Exam.institution_id == institution.id, Exam.code == "ENEM"))
        if exam is None:
            exam = Exam(institution_id=institution.id, code="ENEM", name="Exame Nacional do Ensino Medio")
            self.session.add(exam)
            await self.session.flush()
        application = await self.session.scalar(select(ExamApplication).where(ExamApplication.exam_id == exam.id, ExamApplication.year == year, ExamApplication.application_type == "regular", ExamApplication.day == day))
        if application is None:
            application = ExamApplication(exam_id=exam.id, year=year, application_type="regular", day=day, official_identifier=f"ENEM-{year}-D{day}")
            self.session.add(application)
            await self.session.flush()
        booklet = await self.session.scalar(select(ExamBooklet).where(ExamBooklet.exam_application_id == application.id, ExamBooklet.code == booklet_code))
        if booklet is None:
            booklet = ExamBooklet(exam_application_id=application.id, code=booklet_code, color=metadata.get("booklet_color"), official_identifier=f"ENEM-{year}-{booklet_code}")
            self.session.add(booklet)
            await self.session.flush()
        proof = await self.session.scalar(select(SourceDocument).where(SourceDocument.content_hash == document.document_hash))
        if proof is None:
            proof = SourceDocument(exam_application_id=application.id, exam_booklet_id=booklet.id, document_type="proof", title=document.filename, source_url=metadata["source_url"], acquired_at=document.created_at, content_hash=document.document_hash, storage_uri=document.storage_uri, page_count=document.page_count, extraction_method="pypdf-text-layer", extractor_version="1.0.0-deterministic")
            self.session.add(proof)
            await self.session.flush()
        key_hash = metadata.get("answer_key_document_hash", f"answer-key:{metadata.get('answer_key_source_url', booklet_code)}")
        key_document = await self.session.scalar(select(SourceDocument).where(SourceDocument.content_hash == key_hash))
        if key_document is None:
            key_document = SourceDocument(exam_application_id=application.id, exam_booklet_id=booklet.id, document_type="answer_key", title=f"Answer key {booklet_code}", source_url=metadata["answer_key_source_url"], acquired_at=document.created_at, content_hash=key_hash, storage_uri=None)
            self.session.add(key_document)
            await self.session.flush()
        revision = await self.session.scalar(select(AnswerKeyRevision).where(AnswerKeyRevision.source_document_id == key_document.id, AnswerKeyRevision.revision_number == 1))
        if revision is None:
            revision = AnswerKeyRevision(source_document_id=key_document.id, revision_number=1, is_official=True)
            self.session.add(revision)
            await self.session.flush()
        return booklet, revision