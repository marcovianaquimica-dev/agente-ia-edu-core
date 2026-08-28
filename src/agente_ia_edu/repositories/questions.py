from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    DifficultyEstimate,
    ExamApplication,
    Exam,
    ExamBooklet,
    Institution,
    Question,
    QuestionClassification,
    QuestionVersion,
    Taxonomy,
    TaxonomyNode,
)


class QuestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _official_version_filter(self):
        return QuestionVersion.version_kind == "official_original"

    def _list_query(self, query):
        return query.join(Question.versions).where(self._official_version_filter())

    async def count(self, *, institution_code: str | None = None, exam_code: str | None = None, year: int | None = None, content: str | None = None) -> int:
        query = select(func.count(func.distinct(Question.id)))
        query = self._list_query(query)
        query = self._apply_filters(query, institution_code, exam_code, year, content)
        return int((await self.session.scalar(query)) or 0)

    async def list_official(self, *, offset: int, limit: int, institution_code: str | None = None, exam_code: str | None = None, year: int | None = None, content: str | None = None) -> list[Question]:
        query = select(Question).options(
            selectinload(Question.versions).selectinload(QuestionVersion.options),
            selectinload(Question.versions)
            .selectinload(QuestionVersion.booklet_questions)
            .joinedload(BookletQuestion.exam_booklet)
            .joinedload(ExamBooklet.exam_application)
            .joinedload(ExamApplication.exam)
            .joinedload(Exam.institution),
        )
        query = self._list_query(query)
        query = self._apply_filters(query, institution_code, exam_code, year, content)
        query = query.distinct().offset(offset).limit(limit)
        result = await self.session.scalars(query)
        return list(result.unique().all())

    async def get_official(self, question_id: UUID) -> Question | None:
        query = (
            select(Question)
            .where(Question.id == question_id)
            .options(
                selectinload(Question.versions).selectinload(QuestionVersion.options),
                selectinload(Question.versions)
                .selectinload(QuestionVersion.booklet_questions)
                .joinedload(BookletQuestion.exam_booklet)
                .joinedload(ExamBooklet.exam_application)
                .joinedload(ExamApplication.exam)
                .joinedload(Exam.institution),
            )
        )
        question = await self.session.scalar(query)
        if question is None:
            return None
        if not any(version.version_kind == "official_original" for version in question.versions):
            return None
        return question

    async def active_classifications(self, version_ids: list[UUID]) -> dict[UUID, QuestionClassification]:
        if not version_ids:
            return {}
        query = (
            select(QuestionClassification)
            .join(QuestionClassification.taxonomy)
            .options(
                joinedload(QuestionClassification.taxonomy),
                joinedload(QuestionClassification.competency_node),
                joinedload(QuestionClassification.skill_node),
            )
            .where(
                QuestionClassification.question_version_id.in_(version_ids),
                QuestionClassification.is_primary.is_(True),
                QuestionClassification.status == "active",
                Taxonomy.code == "bncc",
            )
        )
        result = await self.session.scalars(query)
        return {value.question_version_id: value for value in result.unique().all()}

    async def active_difficulties(self, version_ids: list[UUID]) -> dict[UUID, DifficultyEstimate]:
        if not version_ids:
            return {}
        query = select(DifficultyEstimate).where(
            DifficultyEstimate.question_version_id.in_(version_ids),
            DifficultyEstimate.status == "active",
        )
        result = await self.session.scalars(query)
        return {value.question_version_id: value for value in result.all()}

    async def answer_key_entries(self, occurrence_ids: list[UUID]) -> list[AnswerKeyEntry]:
        if not occurrence_ids:
            return []
        query = (
            select(AnswerKeyEntry)
            .join(AnswerKeyEntry.revision)
            .options(
                joinedload(AnswerKeyEntry.revision),
                joinedload(AnswerKeyEntry.booklet_question).joinedload(
                    BookletQuestion.exam_booklet
                ),
            )
            .where(
                AnswerKeyEntry.booklet_question_id.in_(occurrence_ids),
                AnswerKeyRevision.is_official.is_(True),
            )
            .order_by(
                AnswerKeyEntry.booklet_question_id,
                AnswerKeyRevision.revision_number.desc(),
            )
        )
        result = await self.session.scalars(query)
        return list(result.unique().all())

    @staticmethod
    def _apply_filters(query, institution_code, exam_code, year, content):
        if institution_code or exam_code or year:
            query = query.join(QuestionVersion.booklet_questions).join(ExamBooklet).join(ExamApplication).join(Exam).join(Institution)
        if institution_code:
            query = query.where(Institution.code == institution_code)
        if exam_code:
            query = query.where(Exam.code == exam_code)
        if year:
            query = query.where(ExamApplication.year == year)
        if content:
            query = query.where(QuestionVersion.canonical_text.ilike(f"%{content}%"))
        return query
