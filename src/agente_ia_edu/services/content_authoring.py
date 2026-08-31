from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import (
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
    TheoryMaterial,
    TheoryMaterialVersion,
)


class QuestionWorkflowStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class MaterialWorkflowStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True, slots=True)
class QuestionAuthoringResult:
    question_id: UUID
    version_id: UUID
    status: str


class QuestionAuthoringService:
    """Authoring workflow for the pedagogy bank without rewriting the core engine.

    The existing Question/QuestionVersion tables already model versioned content.
    This service adds a deterministic workflow on top of those tables while leaving
    official and historical data intact.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_question(
        self,
        *,
        created_by_external_identity: str,
        statement: str,
        options: Iterable[str],
        correct_option: str,
        author_type: str = "TEACHER",
        school_id: UUID | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> QuestionAuthoringResult:
        if not statement or not statement.strip():
            raise ValueError("Question statement is required.")

        option_list = list(options)
        if len(option_list) < 2:
            raise ValueError("At least two answer options are required.")
        if correct_option not in option_list:
            raise ValueError("The correct option must exist in the provided options.")

        question = Question(validation_status=QuestionWorkflowStatus.DRAFT.value)
        self.session.add(question)
        await self.session.flush()

        content_hash = hashlib.sha256(statement.strip().encode("utf-8")).hexdigest()
        version = QuestionVersion(
            question_id=question.id,
            version_kind="author_draft",
            canonical_text=statement.strip(),
            statement=statement.strip(),
            content_hash=content_hash,
            recommended_difficulty="EASY",
            created_by_type=author_type,
            created_by_id=created_by_external_identity,
            metadata_={
                "status": QuestionWorkflowStatus.DRAFT.value,
                "school_id": str(school_id) if school_id is not None else None,
                "origin_type": "AUTHOR",
                "visibility_scope": "PRIVATE",
                **(metadata or {}),
            },
        )
        self.session.add(version)
        await self.session.flush()

        for index, option_text in enumerate(option_list, start=1):
            self.session.add(
                QuestionOption(
                    question_version_id=version.id,
                    option_key=chr(65 + index - 1),
                    position=index,
                    text=option_text.strip(),
                    is_valid_option=(option_text.strip() == correct_option.strip()),
                )
            )

        await self.session.flush()
        return QuestionAuthoringResult(
            question_id=question.id,
            version_id=version.id,
            status=question.validation_status,
        )

    async def get_current_version(self, question_id: UUID) -> QuestionVersion:
        stmt = (
            select(QuestionVersion)
            .where(QuestionVersion.question_id == question_id)
            .order_by(QuestionVersion.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        version = result.scalar_one_or_none()
        if version is None:
            raise ValueError("Question does not have a version.")
        return version

    async def create_new_version(
        self,
        *,
        question_id: UUID,
        statement: str,
        options: Iterable[str],
        correct_option: str,
        created_by_external_identity: str,
        reason: str | None = None,
    ) -> QuestionAuthoringResult:
        question = await self.session.get(Question, question_id)
        if question is None:
            raise ValueError("Question not found.")
        if question.validation_status == QuestionWorkflowStatus.PUBLISHED.value:
            raise ValueError("Published questions are immutable; create a new question instead.")

        latest_version = await self.get_current_version(question_id)
        option_list = list(options)
        if not option_list or correct_option not in option_list:
            raise ValueError("The corrected answer must exist in the provided option list.")

        new_version = QuestionVersion(
            question_id=question_id,
            version_kind="author_revision",
            parent_version_id=latest_version.id,
            canonical_text=statement.strip(),
            statement=statement.strip(),
            content_hash=hashlib.sha256(statement.strip().encode("utf-8")).hexdigest(),
            recommended_difficulty=latest_version.recommended_difficulty or "EASY",
            created_by_type="TEACHER",
            created_by_id=created_by_external_identity,
            change_reason=reason,
            metadata_={
                "status": QuestionWorkflowStatus.DRAFT.value,
                "parent_version_id": str(latest_version.id),
                "origin_type": "AUTHOR",
            },
        )
        self.session.add(new_version)
        await self.session.flush()

        for index, option_text in enumerate(option_list, start=1):
            self.session.add(
                QuestionOption(
                    question_version_id=new_version.id,
                    option_key=chr(65 + index - 1),
                    position=index,
                    text=option_text.strip(),
                    is_valid_option=(option_text.strip() == correct_option.strip()),
                )
            )

        question.validation_status = QuestionWorkflowStatus.DRAFT.value
        await self.session.flush()
        return QuestionAuthoringResult(
            question_id=question.id,
            version_id=new_version.id,
            status=question.validation_status,
        )

    async def submit_for_review(self, question_id: UUID) -> Question:
        question = await self.session.get(Question, question_id)
        if question is None:
            raise ValueError("Question not found.")
        if question.validation_status not in {QuestionWorkflowStatus.DRAFT.value, QuestionWorkflowStatus.REJECTED.value}:
            raise ValueError("Only draft or rejected questions can be submitted for review.")
        question.validation_status = QuestionWorkflowStatus.PENDING_REVIEW.value
        version = await self.get_current_version(question_id)
        metadata = dict(version.metadata_ or {})
        metadata["status"] = QuestionWorkflowStatus.PENDING_REVIEW.value
        version.metadata_ = metadata
        await self.session.flush()
        return question

    async def approve(self, question_id: UUID) -> Question:
        question = await self.session.get(Question, question_id)
        if question is None:
            raise ValueError("Question not found.")
        if question.validation_status != QuestionWorkflowStatus.PENDING_REVIEW.value:
            raise ValueError("Only questions under review can be approved.")
        question.validation_status = QuestionWorkflowStatus.APPROVED.value
        version = await self.get_current_version(question_id)
        metadata = dict(version.metadata_ or {})
        metadata["status"] = QuestionWorkflowStatus.APPROVED.value
        version.metadata_ = metadata
        await self.session.flush()
        return question

    async def reject(self, question_id: UUID, reason: str | None = None) -> Question:
        question = await self.session.get(Question, question_id)
        if question is None:
            raise ValueError("Question not found.")
        if question.validation_status != QuestionWorkflowStatus.PENDING_REVIEW.value:
            raise ValueError("Only questions under review can be rejected.")
        question.validation_status = QuestionWorkflowStatus.REJECTED.value
        version = await self.get_current_version(question_id)
        metadata = dict(version.metadata_ or {})
        metadata["status"] = QuestionWorkflowStatus.REJECTED.value
        if reason:
            metadata["rejection_reason"] = reason
        version.metadata_ = metadata
        await self.session.flush()
        return question

    async def suggest_classification(
        self,
        *,
        question_id: UUID,
        discipline: str,
        content: str,
        subcontent: str,
        difficulty: str,
        reasoning_type: str = "application",
        confidence: float = 0.75,
        model_name: str = "baseline_rule_classifier",
        provider_name: str = "local",
    ) -> PedagogicalClassification:
        question = await self.session.get(Question, question_id)
        if question is None:
            raise ValueError("Question not found.")

        version = await self.get_current_version(question_id)
        classification = PedagogicalClassification(
            question_version_id=version.id,
            discipline=discipline,
            content=content,
            subcontent=subcontent,
            difficulty=difficulty.upper(),
            classification_confidence=confidence,
            difficulty_confidence=confidence,
            reasoning_type=reasoning_type,
            prerequisites=[],
            keywords=[discipline, content, subcontent],
            competencies=[],
            skills=[],
            model_name=model_name,
            model_version="1.0",
            prompt_version="phase15-local",
            provider_name=provider_name,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            status="CLASSIFIED" if confidence >= 0.7 else "NEEDS_REVIEW",
            source="ai",
            metadata_={"suggested_for_question": str(question.id)},
        )
        self.session.add(classification)
        await self.session.flush()
        return classification

    async def publish_question(self, question_id: UUID) -> Question:
        question = await self.session.get(Question, question_id)
        if question is None:
            raise ValueError("Question not found.")
        if question.validation_status != QuestionWorkflowStatus.APPROVED.value:
            raise ValueError("Only approved questions can be published.")

        version = await self.get_current_version(question_id)
        result = await self.session.execute(
            select(PedagogicalClassification).where(
                PedagogicalClassification.question_version_id == version.id,
                PedagogicalClassification.status == "CLASSIFIED",
            )
        )
        classification = result.scalar_one_or_none()
        if classification is None or (classification.classification_confidence is None or float(classification.classification_confidence) < 0.7):
            raise ValueError("Question needs a reviewed classification with sufficient confidence before publication.")

        question.validation_status = QuestionWorkflowStatus.PUBLISHED.value
        metadata = dict(version.metadata_ or {})
        metadata["status"] = QuestionWorkflowStatus.PUBLISHED.value
        version.metadata_ = metadata
        await self.session.flush()
        return question

    async def archive_question(self, question_id: UUID) -> Question:
        question = await self.session.get(Question, question_id)
        if question is None:
            raise ValueError("Question not found.")
        question.validation_status = QuestionWorkflowStatus.ARCHIVED.value
        version = await self.get_current_version(question_id)
        metadata = dict(version.metadata_ or {})
        metadata["status"] = QuestionWorkflowStatus.ARCHIVED.value
        version.metadata_ = metadata
        await self.session.flush()
        return question


class MaterialAuthoringService:
    """Thin wrapper over the existing material/version lifecycle.

    This intentionally reuses TheoryMaterial + TheoryMaterialVersion rather than
    creating a second material model.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _normalize_status(value: str | None) -> str:
        return (value or "").upper()

    async def get_current_version(self, material_id: UUID) -> TheoryMaterialVersion:
        latest = await self.session.execute(
            select(TheoryMaterialVersion)
            .where(TheoryMaterialVersion.material_id == material_id)
            .order_by(TheoryMaterialVersion.version_number.desc())
            .limit(1)
        )
        version = latest.scalar_one_or_none()
        if version is None:
            raise ValueError("Material does not have a version.")
        return version

    async def create_material(
        self,
        *,
        title: str,
        created_by_external_identity: str,
        visibility_scope: str = "PRIVATE",
        origin_type: str = "AUTHOR",
        primary_content_node_id: UUID | None = None,
    ) -> TheoryMaterial:
        material = TheoryMaterial(
            title=title,
            created_by_external_identity=created_by_external_identity,
            primary_content_node_id=primary_content_node_id,
        )
        self.session.add(material)
        await self.session.flush()

        await self.create_version(
            material_id=material.id,
            created_by_external_identity=created_by_external_identity,
            introduction=None,
            summary=None,
        )
        return material

    async def create_version(
        self,
        *,
        material_id: UUID,
        created_by_external_identity: str,
        introduction: str | None = None,
        summary: str | None = None,
    ) -> TheoryMaterialVersion:
        latest = await self.session.execute(
            select(TheoryMaterialVersion)
            .where(TheoryMaterialVersion.material_id == material_id)
            .order_by(TheoryMaterialVersion.version_number.desc())
            .limit(1)
        )
        latest_version = latest.scalar_one_or_none()
        next_number = (latest_version.version_number + 1) if latest_version else 1

        version = TheoryMaterialVersion(
            material_id=material_id,
            version_number=next_number,
            status=MaterialWorkflowStatus.DRAFT.value,
            introduction=introduction,
            summary=summary,
            created_by_external_identity=created_by_external_identity,
        )
        self.session.add(version)
        await self.session.flush()
        return version

    async def submit_for_review(self, material_version_id: UUID) -> TheoryMaterialVersion:
        version = await self.session.get(TheoryMaterialVersion, material_version_id)
        if version is None:
            raise ValueError("Material version not found.")

        status = self._normalize_status(version.status)
        if status not in {MaterialWorkflowStatus.DRAFT.value, MaterialWorkflowStatus.REJECTED.value}:
            raise ValueError("Only draft or rejected materials can be submitted for review.")

        version.status = MaterialWorkflowStatus.PENDING_REVIEW.value
        await self.session.flush()
        return version

    async def approve_material(self, material_version_id: UUID) -> TheoryMaterialVersion:
        version = await self.session.get(TheoryMaterialVersion, material_version_id)
        if version is None:
            raise ValueError("Material version not found.")
        if self._normalize_status(version.status) != MaterialWorkflowStatus.PENDING_REVIEW.value:
            raise ValueError("Only materials under review can be approved.")

        version.status = MaterialWorkflowStatus.APPROVED.value
        await self.session.flush()
        return version

    async def reject_material(self, material_version_id: UUID, reason: str | None = None) -> TheoryMaterialVersion:
        version = await self.session.get(TheoryMaterialVersion, material_version_id)
        if version is None:
            raise ValueError("Material version not found.")
        if self._normalize_status(version.status) != MaterialWorkflowStatus.PENDING_REVIEW.value:
            raise ValueError("Only materials under review can be rejected.")

        version.status = MaterialWorkflowStatus.REJECTED.value
        if reason:
            version.summary = (version.summary or "") + (f"\nRejeição: {reason}" if version.summary else f"Rejeição: {reason}")
        await self.session.flush()
        return version

    async def publish_material(self, material_version_id: UUID) -> TheoryMaterialVersion:
        version = await self.session.get(TheoryMaterialVersion, material_version_id)
        if version is None:
            raise ValueError("Material version not found.")

        status = self._normalize_status(version.status)
        if status == MaterialWorkflowStatus.PUBLISHED.value:
            raise ValueError("Material version is already published.")
        if status != MaterialWorkflowStatus.APPROVED.value:
            raise ValueError("Only approved materials can be published.")

        version.status = MaterialWorkflowStatus.PUBLISHED.value
        version.published_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        await self.session.flush()
        return version

    async def archive_material(self, material_version_id: UUID) -> TheoryMaterialVersion:
        version = await self.session.get(TheoryMaterialVersion, material_version_id)
        if version is None:
            raise ValueError("Material version not found.")

        status = self._normalize_status(version.status)
        if status == MaterialWorkflowStatus.ARCHIVED.value:
            raise ValueError("Material version is already archived.")
        if status == MaterialWorkflowStatus.PUBLISHED.value:
            version.status = MaterialWorkflowStatus.ARCHIVED.value
            await self.session.flush()
            return version
        if status in {MaterialWorkflowStatus.DRAFT.value, MaterialWorkflowStatus.PENDING_REVIEW.value, MaterialWorkflowStatus.APPROVED.value, MaterialWorkflowStatus.REJECTED.value}:
            version.status = MaterialWorkflowStatus.ARCHIVED.value
            await self.session.flush()
            return version
        raise ValueError("This material version cannot be archived in its current state.")


__all__ = [
    "MaterialAuthoringService",
    "QuestionAuthoringResult",
    "QuestionAuthoringService",
    "QuestionWorkflowStatus",
]
