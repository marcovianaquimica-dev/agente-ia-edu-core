from __future__ import annotations

from uuid import UUID

from agente_ia_edu.db.models import (
    DifficultyEstimate,
    Question,
    QuestionClassification as QuestionClassificationModel,
    QuestionOption,
    QuestionVersion,
)
from agente_ia_edu.repositories.questions import QuestionRepository
from agente_ia_edu.api.schemas.questions import (
    AnswerKeyItem,
    Pagination,
    QuestionClassification as QuestionClassificationSchema,
    QuestionContent,
    QuestionDifficulty,
    QuestionListItem,
    QuestionListResponse,
    QuestionOption as QuestionOptionSchema,
    QuestionSource,
    QuestionDetail,
    SourceApplication,
    SourceBooklet,
    SourceExam,
    SourceInstitution,
    TaxonomyNodeReference,
    TaxonomyReference,
)


class QuestionService:
    def __init__(self, repository: QuestionRepository) -> None:
        self.repository = repository

    async def list_questions(self, *, page: int, limit: int, institution_code: str | None = None, exam_code: str | None = None, year: int | None = None, content: str | None = None) -> QuestionListResponse:
        filters = dict(institution_code=institution_code, exam_code=exam_code, year=year, content=content)
        total = await self.repository.count(**filters)
        questions = await self.repository.list_official(offset=(page - 1) * limit, limit=limit, **filters)
        versions = [self._official_version(question) for question in questions]
        version_ids = [version.id for version in versions]
        classifications = await self.repository.active_classifications(version_ids)
        difficulties = await self.repository.active_difficulties(version_ids)
        return QuestionListResponse(
            items=[
                self._to_list_item(
                    question,
                    classifications.get(version.id),
                    difficulties.get(version.id),
                )
                for question, version in zip(questions, versions)
            ],
            pagination=Pagination(page=page, limit=limit, total=total),
        )

    async def get_question(self, question_id: UUID, *, include_answer_key: bool = False) -> QuestionDetail | None:
        question = await self.repository.get_official(question_id)
        if question is None:
            return None
        version = self._official_version(question)
        classification = (await self.repository.active_classifications([version.id])).get(version.id)
        difficulty = (await self.repository.active_difficulties([version.id])).get(version.id)
        item = self._to_list_item(question, classification, difficulty)
        answer_key = None
        if include_answer_key:
            occurrence_ids = [occurrence.id for occurrence in version.booklet_questions]
            entries = await self.repository.answer_key_entries(occurrence_ids)
            seen_occurrences = set()
            answer_key = []
            for entry in entries:
                if entry.booklet_question_id in seen_occurrences:
                    continue
                seen_occurrences.add(entry.booklet_question_id)
                answer_key.append(
                    AnswerKeyItem(
                        booklet_code=entry.booklet_question.exam_booklet.code,
                        label=entry.official_answer_label,
                        option_id=entry.resolved_option_id,
                        revision=entry.revision.revision_number,
                        official=entry.revision.is_official,
                    )
                )
        return QuestionDetail(**item.model_dump(), answer_key=answer_key)

    @staticmethod
    def _official_version(question: Question) -> QuestionVersion:
        return next(version for version in question.versions if version.version_kind == "official_original")

    def _to_list_item(self, question: Question, classification, difficulty) -> QuestionListItem:
        version = self._official_version(question)
        content = QuestionContent(
            version_id=version.id,
            version_kind=version.version_kind,
            canonical_text=version.canonical_text,
            statement=version.statement,
            options=[self._option(option) for option in sorted(version.options, key=lambda value: value.position)],
        )
        return QuestionListItem(
            id=question.id,
            validation_status=question.validation_status,
            content=content,
            sources=[self._source(occurrence) for occurrence in version.booklet_questions],
            classification=self._classification(classification),
            difficulty=self._difficulty(difficulty),
        )

    @staticmethod
    def _classification(value: QuestionClassificationModel | None) -> QuestionClassificationSchema | None:
        if value is None or value.taxonomy.code != "bncc":
            return None
        if (
            value.competency_node is None
            or value.skill_node is None
            or value.competency_node.node_type != "competency"
            or value.skill_node.node_type != "skill"
            or value.skill_node.parent_id != value.competency_node.id
        ):
            return None
        return QuestionClassificationSchema(
            taxonomy=TaxonomyReference(code=value.taxonomy.code, version=value.taxonomy.version),
            competency=TaxonomyNodeReference(id=value.competency_node.id, code=value.competency_node.code, name=value.competency_node.name),
            skill=TaxonomyNodeReference(id=value.skill_node.id, code=value.skill_node.code, name=value.skill_node.name),
            confidence=value.confidence,
            source=value.source,
            classifier_version=value.classifier_version,
        )

    @staticmethod
    def _difficulty(value: DifficultyEstimate | None) -> QuestionDifficulty | None:
        if value is None:
            return None
        return QuestionDifficulty(
            score=value.score,
            band=value.band,
            confidence=value.confidence,
            source=value.source,
            method=value.method,
            method_version=value.method_version,
        )

    @staticmethod
    def _option(option: QuestionOption) -> QuestionOptionSchema:
        return QuestionOptionSchema(id=option.id, key=option.option_key, position=option.position, text=option.text)

    @staticmethod
    def _source(occurrence) -> QuestionSource:
        booklet = occurrence.exam_booklet
        application = booklet.exam_application
        exam = application.exam
        institution = exam.institution
        return QuestionSource(
            institution=SourceInstitution(code=institution.code, name=institution.name),
            exam=SourceExam(code=exam.code, name=exam.name),
            application=SourceApplication(year=application.year, type=application.application_type, day=application.day),
            booklets=[SourceBooklet(code=booklet.code, color=booklet.color, language=booklet.language, official_number=occurrence.official_number, position=occurrence.position)],
        )
