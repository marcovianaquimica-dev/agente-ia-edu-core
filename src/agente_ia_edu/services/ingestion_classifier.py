from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models import (
    IngestionDocument,
    IngestionQuestion,
    IngestionSection,
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
    Taxonomy,
    TaxonomyNode,
)
from agente_ia_edu.services.pedagogical_classifier import (
    PedagogicalClassificationService,
    PedagogicalClassifierProvider,
)

logger = logging.getLogger(__name__)

DEFAULT_CONFIDENCE_THRESHOLD = Decimal("0.70")


class DifficultyPolicy:
    """Normalizes AI-produced difficulty levels to learning engine difficulty levels.

    AI Difficulty: VERY_EASY, EASY, MEDIUM, HARD, VERY_HARD
    Learning Level: EASY, MEDIUM, HARD
    """

    AI_TO_LEARNING_LEVEL = {
        "VERY_EASY": "EASY",
        "EASY": "EASY",
        "MEDIUM": "MEDIUM",
        "HARD": "HARD",
        "VERY_HARD": "HARD",
    }

    @classmethod
    def to_learning_level(cls, ai_difficulty: str) -> str:
        normalized = (ai_difficulty or "").strip().upper()
        if normalized in cls.AI_TO_LEARNING_LEVEL:
            return cls.AI_TO_LEARNING_LEVEL[normalized]
        raise ValueError(f"Unknown AI difficulty level: {ai_difficulty}")


class IngestionClassificationService:
    """Orchestrates ingestion -> question versions -> AI pedagogical classification."""

    def __init__(
        self,
        session: AsyncSession,
        confidence_threshold: Decimal = DEFAULT_CONFIDENCE_THRESHOLD,
    ):
        self.session = session
        self.confidence_threshold = Decimal(str(confidence_threshold))

    async def classify_document_questions(
        self,
        document_id: uuid.UUID,
        provider: PedagogicalClassifierProvider,
        *,
        model_name: str = "mock-model",
        model_version: str = "v1",
        prompt_version: str = "prompt-v1",
        reprocess: bool = False,
        taxonomy_code: str | None = None,
    ) -> list[PedagogicalClassification]:
        """Classify all questions extracted from an ingested document.

        Guarantees:
        - Creates Question & QuestionVersion for IngestionQuestion if missing
        - Skips existing active classification if reprocess is False
        - Processes in batch while isolating question-level errors
        - Applies DifficultyPolicy to populate QuestionVersion.recommended_difficulty
        - Enforces confidence_threshold -> NEEDS_REVIEW
        - Validates taxonomy if taxonomy_code provided -> NEEDS_REVIEW if unmatched
        - Maintains full bidirectional traceability
        """
        stmt = (
            select(IngestionDocument)
            .where(IngestionDocument.id == document_id)
            .options(
                selectinload(IngestionDocument.questions),
                selectinload(IngestionDocument.sections),
            )
        )
        result = await self.session.execute(stmt)
        document = result.scalar_one_or_none()
        if document is None:
            raise ValueError(f"IngestionDocument not found: {document_id}")

        questions = sorted(document.questions, key=lambda q: q.position)
        if not questions:
            return []

        taxonomy_nodes_map: dict[str, TaxonomyNode] = {}
        if taxonomy_code:
            tax_stmt = select(Taxonomy).where(Taxonomy.code == taxonomy_code, Taxonomy.active.is_(True))
            tax_res = await self.session.execute(tax_stmt)
            taxonomy = tax_res.scalar_one_or_none()
            if taxonomy:
                node_stmt = select(TaxonomyNode).where(TaxonomyNode.taxonomy_id == taxonomy.id)
                node_res = await self.session.execute(node_stmt)
                for node in node_res.scalars().all():
                    taxonomy_nodes_map[node.code.upper()] = node

        classifications: list[PedagogicalClassification] = []

        for iq in questions:
            try:
                # 1. Ensure Question & QuestionVersion exist
                question_version = await self._ensure_question_version(iq, document)

                # 2. Check for duplicate classification unless reprocess=True
                if not reprocess:
                    existing_stmt = select(PedagogicalClassification).where(
                        PedagogicalClassification.question_version_id == question_version.id,
                        PedagogicalClassification.model_name == model_name,
                        PedagogicalClassification.model_version == model_version,
                        PedagogicalClassification.prompt_version == prompt_version,
                    ).order_by(PedagogicalClassification.created_at.desc())
                    existing_res = await self.session.execute(existing_stmt)
                    existing = existing_res.scalar_one_or_none()
                    if existing is not None:
                        classifications.append(existing)
                        continue

                # 3. Request classification from provider
                source_text = (iq.statement_text or "").strip()
                raw_response = await provider.classify(
                    question_text=source_text,
                    question_version_id=question_version.id,
                    question_number=iq.question_number,
                    model_name=model_name,
                    model_version=model_version,
                    prompt_version=prompt_version,
                )

                # 4. Coerce & validate payload
                normalized = PedagogicalClassificationService._coerce_payload(raw_response)
                validated = PedagogicalClassificationService._validate_payload(normalized)

                # 5. Difficulty Policy
                ai_difficulty = validated["difficulty"]
                learning_level = DifficultyPolicy.to_learning_level(ai_difficulty)
                question_version.recommended_difficulty = learning_level

                # 6. Check Confidence & Taxonomy Match
                status = validated["status"]
                class_conf = Decimal(str(validated["classification_confidence"]))
                diff_conf = Decimal(str(validated["difficulty_confidence"]))

                if class_conf < self.confidence_threshold or diff_conf < self.confidence_threshold:
                    status = "NEEDS_REVIEW"

                if taxonomy_code:
                    req_skills = validated.get("skills") or []
                    if not req_skills:
                        status = "NEEDS_REVIEW"
                    else:
                        for skill_code in req_skills:
                            if skill_code.upper() not in taxonomy_nodes_map:
                                status = "NEEDS_REVIEW"
                                break

                # 7. Persist classification with full traceability metadata
                metadata = {
                    "difficulty_learning_level": learning_level,
                    "ingestion_document_id": str(document.id),
                    "ingestion_question_id": str(iq.id),
                    "section_id": str(iq.section_id) if iq.section_id else None,
                    "page_start": iq.page_start,
                    "page_end": iq.page_end,
                    "question_number": iq.question_number,
                    "raw_response": validated,
                }

                classification = PedagogicalClassification(
                    question_version_id=question_version.id,
                    discipline=validated["discipline"],
                    content=validated["content"],
                    subcontent=validated["subcontent"],
                    difficulty=ai_difficulty,
                    classification_confidence=class_conf,
                    difficulty_confidence=diff_conf,
                    reasoning_type=validated["reasoning_type"],
                    prerequisites=validated.get("prerequisites") or [],
                    keywords=validated.get("keywords") or [],
                    competencies=validated.get("competencies") or [],
                    skills=validated.get("skills") or [],
                    model_name=model_name,
                    model_version=model_version,
                    prompt_version=prompt_version,
                    provider_name=str(validated.get("provider") or "unknown"),
                    input_tokens=int(validated.get("input_tokens", 0) or 0),
                    output_tokens=int(validated.get("output_tokens", 0) or 0),
                    total_tokens=int(validated.get("total_tokens", 0) or 0),
                    status=status,
                    source="ai",
                    metadata_=metadata,
                )
                self.session.add(classification)
                await self.session.flush()
                classifications.append(classification)

            except Exception as exc:
                logger.error("Error classifying IngestionQuestion %s: %s", iq.id, exc)
                continue

        await self.session.commit()
        return classifications

    async def _ensure_question_version(
        self,
        iq: IngestionQuestion,
        document: IngestionDocument,
    ) -> QuestionVersion:
        """Get existing QuestionVersion or create one from IngestionQuestion."""
        if iq.question_version_id is not None:
            qv = await self.session.get(QuestionVersion, iq.question_version_id)
            if qv is not None:
                return qv

        question = Question(validation_status="extracted")
        self.session.add(question)
        await self.session.flush()

        import hashlib
        content_hash = hashlib.sha256(iq.statement_text.encode("utf-8")).hexdigest()

        qv = QuestionVersion(
            question_id=question.id,
            version_kind="official_original",
            canonical_text=iq.statement_text,
            statement=iq.statement_text,
            content_hash=content_hash,
            metadata_={
                "source": "ingestion",
                "ingestion_document_id": str(document.id),
                "ingestion_question_id": str(iq.id),
            },
        )
        self.session.add(qv)
        await self.session.flush()

        if iq.alternatives_text:
            alt_lines = [line.strip() for line in iq.alternatives_text.split("\n") if line.strip()]
            for pos, line in enumerate(alt_lines, 1):
                match = re.match(r"^([a-eA-E])\)\s*(.+)$", line)
                if match:
                    key = match.group(1).upper()
                    text_val = match.group(2).strip()
                else:
                    key = str(pos)
                    text_val = line
                option = QuestionOption(
                    question_version_id=qv.id,
                    option_key=key,
                    position=pos,
                    text=text_val,
                )
                self.session.add(option)

        iq.question_version_id = qv.id
        await self.session.flush()
        return qv

    async def get_document_traceability(
        self,
        document_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """Return full traceability chain for a document."""
        stmt = (
            select(IngestionQuestion)
            .where(IngestionQuestion.document_id == document_id)
            .options(
                selectinload(IngestionQuestion.document),
                selectinload(IngestionQuestion.section),
                selectinload(IngestionQuestion.question_version),
            )
            .order_by(IngestionQuestion.position)
        )
        result = await self.session.execute(stmt)
        questions = result.scalars().all()

        trace_list = []
        for iq in questions:
            qv = iq.question_version
            classification = None
            if qv:
                class_stmt = (
                    select(PedagogicalClassification)
                    .where(PedagogicalClassification.question_version_id == qv.id)
                    .order_by(PedagogicalClassification.created_at.desc())
                )
                class_res = await self.session.execute(class_stmt)
                classification = class_res.scalar_one_or_none()

            trace_list.append({
                "document": {
                    "id": str(iq.document.id),
                    "filename": iq.document.filename,
                    "title": iq.document.title,
                },
                "section": {
                    "id": str(iq.section.id) if iq.section else None,
                    "type": iq.section.section_type if iq.section else None,
                    "title": iq.section.title if iq.section else None,
                },
                "ingestion_question": {
                    "id": str(iq.id),
                    "question_number": iq.question_number,
                    "statement_text": iq.statement_text,
                    "alternatives_text": iq.alternatives_text,
                    "correct_answer": iq.correct_answer,
                    "page_start": iq.page_start,
                },
                "question_version": {
                    "id": str(qv.id) if qv else None,
                    "recommended_difficulty": qv.recommended_difficulty if qv else None,
                },
                "classification": {
                    "id": str(classification.id) if classification else None,
                    "discipline": classification.discipline if classification else None,
                    "content": classification.content if classification else None,
                    "difficulty_ai": classification.difficulty if classification else None,
                    "difficulty_learning_level": classification.metadata_.get("difficulty_learning_level") if classification and classification.metadata_ else None,
                    "classification_confidence": float(classification.classification_confidence) if classification and classification.classification_confidence is not None else None,
                    "status": classification.status if classification else None,
                } if classification else None,
            })
        return trace_list

    async def get_classification_traceability(
        self,
        classification_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Return full traceability chain starting from a classification ID."""
        classification = await self.session.get(PedagogicalClassification, classification_id)
        if classification is None:
            return None

        qv = await self.session.get(QuestionVersion, classification.question_version_id)
        iq_id_str = classification.metadata_.get("ingestion_question_id") if classification.metadata_ else None

        iq = None
        if iq_id_str:
            iq = await self.session.get(IngestionQuestion, uuid.UUID(iq_id_str))
        elif qv:
            iq_stmt = select(IngestionQuestion).where(IngestionQuestion.question_version_id == qv.id)
            iq_res = await self.session.execute(iq_stmt)
            iq = iq_res.scalar_one_or_none()

        doc = None
        sec = None
        if iq:
            doc = await self.session.get(IngestionDocument, iq.document_id)
            if iq.section_id:
                sec = await self.session.get(IngestionSection, iq.section_id)

        return {
            "classification": {
                "id": str(classification.id),
                "discipline": classification.discipline,
                "content": classification.content,
                "difficulty_ai": classification.difficulty,
                "difficulty_learning_level": classification.metadata_.get("difficulty_learning_level") if classification.metadata_ else None,
                "status": classification.status,
            },
            "question_version": {
                "id": str(qv.id) if qv else None,
                "recommended_difficulty": qv.recommended_difficulty if qv else None,
            },
            "ingestion_question": {
                "id": str(iq.id) if iq else None,
                "question_number": iq.question_number if iq else None,
                "statement_text": iq.statement_text if iq else None,
                "alternatives_text": iq.alternatives_text if iq else None,
                "correct_answer": iq.correct_answer if iq else None,
                "page_start": iq.page_start if iq else None,
            },
            "section": {
                "id": str(sec.id) if sec else None,
                "type": sec.section_type if sec else None,
                "title": sec.title if sec else None,
            },
            "document": {
                "id": str(doc.id) if doc else None,
                "filename": doc.filename if doc else None,
            },
        }
