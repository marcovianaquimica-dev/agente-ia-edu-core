from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models import (
    CatalogNode,
    EducationalResource,
    PedagogicalContext,
    PedagogicalRecommendation,
    QuestionVersion,
    StudentContentMastery,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.learning_path_policies import DifficultyLevel

logger = logging.getLogger(__name__)


@dataclass
class RecommendationPriorityPolicy:
    """Configurable, deterministic policy for scoring and prioritizing recommendation candidates."""

    source_weights: dict[str, float] = field(
        default_factory=lambda: {
            "TEACHER": 100.0,
            "COORDINATION": 90.0,
            "SCHOOL_PLAN": 80.0,
            "AUTONOMOUS": 50.0,
        }
    )

    # Score boosts by mastery bracket
    low_mastery_boost: float = 40.0        # < 50%
    medium_mastery_boost: float = 30.0     # 50 - 69.9%
    consolidation_boost: float = 20.0      # 70 - 84.9%
    high_mastery_penalty: float = -30.0    # >= 85%

    alignment_bonus: float = 20.0          # Extra boost if multiple context sources align
    prerequisite_boost: float = 35.0       # Boost for review of missing prerequisites
    repetition_penalty: float = -50.0      # Penalty if recommended recently for same content/resource

    def get_source_weight(self, source: str) -> float:
        return self.source_weights.get(source.upper(), 50.0)

    def calculate_mastery_component(self, mastery_score: float) -> float:
        if mastery_score < 50.0:
            return self.low_mastery_boost
        elif mastery_score < 70.0:
            return self.medium_mastery_boost
        elif mastery_score < 85.0:
            return self.consolidation_boost
        else:
            return self.high_mastery_penalty

    def select_difficulty(self, mastery_score: float) -> str:
        if mastery_score < 50.0:
            return DifficultyLevel.EASY.value
        elif mastery_score < 70.0:
            return DifficultyLevel.MEDIUM.value
        else:
            return DifficultyLevel.HARD.value


class RecommendationEngine:
    """Engine that generates auditably explained, deterministic recommendations for students."""

    def __init__(
        self,
        session: AsyncSession,
        knowledge_service: KnowledgeService,
        policy: RecommendationPriorityPolicy | None = None,
    ):
        self.session = session
        self.knowledge_service = knowledge_service
        self.policy = policy or RecommendationPriorityPolicy()

    async def record_pedagogical_context(
        self,
        *,
        content_node_id: uuid.UUID,
        source: str,
        institution_id: str | None = None,
        classroom_id: str | None = None,
        author_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> PedagogicalContext:
        """Record a pedagogical context entry from Teacher, Coordination, or School Plan."""
        norm_source = source.upper()
        if norm_source not in ("TEACHER", "COORDINATION", "SCHOOL_PLAN"):
            raise ValueError(f"Invalid context source: {source}")

        entry = PedagogicalContext(
            content_node_id=content_node_id,
            source=norm_source,
            institution_id=institution_id,
            classroom_id=classroom_id,
            author_id=author_id,
            title=title,
            description=description,
            recorded_at=datetime.now(timezone.utc),
            active=True,
        )
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def generate_recommendations(
        self,
        *,
        student_id: str,
        institution_id: str | None = None,
        classroom_id: str | None = None,
        limit: int = 5,
    ) -> list[PedagogicalRecommendation]:
        """Crosses student mastery, pedagogical context, and Knowledge Layer to generate recommendations.

        Returns top `limit` recommendations ordered by priority score descending.
        """
        # 1. Fetch active pedagogical contexts for this classroom/institution
        contexts = await self._fetch_active_contexts(institution_id, classroom_id)

        # 2. Fetch student's mastery records
        masteries = await self._fetch_student_masteries(student_id)
        mastery_map: dict[uuid.UUID, StudentContentMastery] = {m.content_node_id: m for m in masteries}

        # 3. Fetch recent recommendations to prevent immediate repetition
        recent_recs = await self._fetch_recent_recommendations(student_id)
        recent_content_ids = {r.content_node_id for r in recent_recs}
        recent_resource_ids = {r.resource_id for r in recent_recs if r.resource_id}

        candidates: list[dict[str, Any]] = []

        # Context-Driven Candidates (Teacher / Coordination / School Plan)
        context_nodes_processed = set()
        if contexts:
            # Group contexts by content_node_id
            grouped_contexts: dict[uuid.UUID, list[PedagogicalContext]] = {}
            for ctx in contexts:
                grouped_contexts.setdefault(ctx.content_node_id, []).append(ctx)

            for node_id, ctx_list in grouped_contexts.items():
                context_nodes_processed.add(node_id)
                cand = await self._build_candidate_from_contexts(
                    student_id=student_id,
                    institution_id=institution_id,
                    classroom_id=classroom_id,
                    node_id=node_id,
                    ctx_list=ctx_list,
                    mastery=mastery_map.get(node_id),
                    mastery_map=mastery_map,
                    recent_content_ids=recent_content_ids,
                    recent_resource_ids=recent_resource_ids,
                )
                if cand:
                    candidates.append(cand)

        # Autonomous Flow Candidate Generation (when no context or to fill recommendations)
        # Scan catalog nodes for contents the student has low mastery in or hasn't practiced
        if len(candidates) < limit:
            auto_candidates = await self._build_autonomous_candidates(
                student_id=student_id,
                institution_id=institution_id,
                classroom_id=classroom_id,
                mastery_map=mastery_map,
                exclude_node_ids=context_nodes_processed,
                recent_content_ids=recent_content_ids,
                recent_resource_ids=recent_resource_ids,
            )
            candidates.extend(auto_candidates)

        # Sort candidates by priority score descending
        candidates.sort(key=lambda c: c["priority_score"], reverse=True)
        top_candidates = candidates[:limit]

        # Save to database
        saved_recs: list[PedagogicalRecommendation] = []
        for cand in top_candidates:
            rec = PedagogicalRecommendation(
                student_id=student_id,
                institution_id=institution_id,
                classroom_id=classroom_id,
                content_node_id=cand["content_node_id"],
                recommendation_type=cand["recommendation_type"],
                recommended_difficulty=cand["recommended_difficulty"],
                priority_score=cand["priority_score"],
                resource_id=cand.get("resource_id"),
                question_version_id=cand.get("question_version_id"),
                reason=cand["reason"],
                context_source=cand["context_source"],
                mastery_score_at_recommendation=cand.get("mastery_score"),
                status="ACTIVE",
                metadata_=cand["metadata"],
            )
            self.session.add(rec)
            saved_recs.append(rec)

        await self.session.commit()
        for rec in saved_recs:
            await self.session.refresh(rec)

        return saved_recs

    # -------------------------------------------------------------------------
    # PRIVATE HELPER METHODS
    # -------------------------------------------------------------------------

    async def _fetch_active_contexts(
        self,
        institution_id: str | None,
        classroom_id: str | None,
    ) -> list[PedagogicalContext]:
        conditions = [PedagogicalContext.active.is_(True)]
        if classroom_id:
            conditions.append(
                or_(
                    PedagogicalContext.classroom_id == classroom_id,
                    PedagogicalContext.classroom_id.is_(None),
                )
            )
        if institution_id:
            conditions.append(
                or_(
                    PedagogicalContext.institution_id == institution_id,
                    PedagogicalContext.institution_id.is_(None),
                )
            )

        stmt = (
            select(PedagogicalContext)
            .where(and_(*conditions))
            .order_by(PedagogicalContext.recorded_at.desc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def _fetch_student_masteries(self, student_id: str) -> list[StudentContentMastery]:
        stmt = select(StudentContentMastery).where(StudentContentMastery.external_identity_id == student_id)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def _fetch_recent_recommendations(self, student_id: str) -> list[PedagogicalRecommendation]:
        stmt = (
            select(PedagogicalRecommendation)
            .where(PedagogicalRecommendation.student_id == student_id)
            .order_by(PedagogicalRecommendation.created_at.desc())
            .limit(10)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def _build_candidate_from_contexts(
        self,
        *,
        student_id: str,
        institution_id: str | None,
        classroom_id: str | None,
        node_id: uuid.UUID,
        ctx_list: list[PedagogicalContext],
        mastery: StudentContentMastery | None,
        mastery_map: dict[uuid.UUID, StudentContentMastery],
        recent_content_ids: set[uuid.UUID],
        recent_resource_ids: set[uuid.UUID],
    ) -> dict[str, Any] | None:
        node = await self.session.get(CatalogNode, node_id)
        if not node:
            return None

        # Conflict resolution hierarchy: TEACHER > COORDINATION > SCHOOL_PLAN > AUTONOMOUS
        hierarchy = {"TEACHER": 1, "COORDINATION": 2, "SCHOOL_PLAN": 3, "AUTONOMOUS": 4}
        sorted_ctxs = sorted(ctx_list, key=lambda c: hierarchy.get(c.source.upper(), 99))
        primary_ctx = sorted_ctxs[0]
        primary_source = primary_ctx.source.upper()

        mastery_score = float(mastery.mastery_score) if mastery else 0.0

        # Check prerequisite: only if parent is a content node (not a DISCIPLINE) and has explicit low mastery
        if node.parent_id:
            parent_node = await self.session.get(CatalogNode, node.parent_id)
            if parent_node and parent_node.node_type != "DISCIPLINE":
                parent_mastery_record = mastery_map.get(node.parent_id)
                if parent_mastery_record and float(parent_mastery_record.mastery_score) < 50.0:
                    parent_mastery = float(parent_mastery_record.mastery_score)
                    parent_name = parent_node.name
                    reason = (
                        f"Antes de avançar em {node.name}, recomendamos revisar o pré-requisito "
                        f"'{parent_name}' em que seu domínio atual está em {parent_mastery:.1f}%."
                    )
                    score = (
                        self.policy.get_source_weight(primary_source)
                        + self.policy.prerequisite_boost
                        + self.policy.calculate_mastery_component(parent_mastery)
                    )
                    return {
                        "content_node_id": node.parent_id,
                        "recommendation_type": "REVIEW_PREREQUISITE",
                        "recommended_difficulty": DifficultyLevel.EASY.value,
                        "priority_score": score,
                        "reason": reason,
                        "context_source": primary_source,
                        "mastery_score": parent_mastery,
                        "metadata": {
                            "target_content_name": node.name,
                            "prerequisite_node_id": str(node.parent_id),
                            "prerequisite_name": parent_name,
                            "primary_context_source": primary_source,
                        },
                    }

        # Calculate base score
        source_weight = self.policy.get_source_weight(primary_source)
        mastery_component = self.policy.calculate_mastery_component(mastery_score)
        alignment = self.policy.alignment_bonus if len(ctx_list) > 1 else 0.0
        repetition = self.policy.repetition_penalty if node_id in recent_content_ids else 0.0

        priority_score = source_weight + mastery_component + alignment + repetition

        # Query resources and questions via Knowledge Layer
        resources = await self.knowledge_service.find_resources_by_content(
            node.name,
            requester_institution_id=institution_id,
        )
        rec_diff = self.policy.select_difficulty(mastery_score)

        rec_type = "PRACTICE"
        resource_id = None
        question_version_id = None

        if mastery_score < 50.0:
            if resources:
                rec_type = "STUDY_MATERIAL"
                # Filter out recently recommended resources if possible
                unseen = [r for r in resources if uuid.UUID(r["resource_id"]) not in recent_resource_ids]
                target_res = unseen[0] if unseen else resources[0]
                resource_id = uuid.UUID(target_res["resource_id"])
            else:
                rec_type = "REVIEW"

            reason = (
                f"Você estudou {node.name} recentemente na escola e seu domínio atual está em {mastery_score:.1f}%. "
                f"Recomendamos revisar o conteúdo e praticar questões de nível {rec_diff}."
            )
        elif mastery_score < 70.0:
            rec_type = "PRACTICE"
            reason = (
                f"Seu domínio em {node.name} está em {mastery_score:.1f}%. "
                f"Recomendamos praticar questões de nível {rec_diff} para consolidar o aprendizado."
            )
        elif mastery_score < 85.0:
            rec_type = "PRACTICE"
            reason = (
                f"Seu domínio em {node.name} está em nível intermediário ({mastery_score:.1f}%). "
                f"Recomendamos resolver questões de nível {rec_diff}."
            )
        else:
            rec_type = "REVIEW"
            reason = (
                f"Você possui alto domínio ({mastery_score:.1f}%) em {node.name}. "
                f"Manteremos revisões periódicas de nível {rec_diff}."
            )

        # Get question if PRACTICE
        if rec_type in ("PRACTICE", "REVIEW"):
            questions = await self.knowledge_service.find_questions_by_content(node.name, difficulty=rec_diff)
            if questions:
                question_version_id = uuid.UUID(questions[0]["question_version_id"])

        return {
            "content_node_id": node.id,
            "recommendation_type": rec_type,
            "recommended_difficulty": rec_diff,
            "priority_score": priority_score,
            "resource_id": resource_id,
            "question_version_id": question_version_id,
            "reason": reason,
            "context_source": primary_source,
            "mastery_score": mastery_score,
            "metadata": {
                "content_name": node.name,
                "context_sources": [c.source for c in ctx_list],
                "source_weight": source_weight,
                "mastery_component": mastery_component,
                "alignment_bonus": alignment,
                "repetition_penalty": repetition,
            },
        }

    async def _build_autonomous_candidates(
        self,
        *,
        student_id: str,
        institution_id: str | None,
        classroom_id: str | None,
        mastery_map: dict[uuid.UUID, StudentContentMastery],
        exclude_node_ids: set[uuid.UUID],
        recent_content_ids: set[uuid.UUID],
        recent_resource_ids: set[uuid.UUID],
    ) -> list[dict[str, Any]]:
        # Fetch active catalog nodes excluding top-level DISCIPLINE nodes
        stmt = select(CatalogNode).where(
            CatalogNode.active.is_(True),
            CatalogNode.node_type != "DISCIPLINE",
            CatalogNode.parent_id.isnot(None),
        )
        res = await self.session.execute(stmt)
        nodes = res.scalars().all()

        auto_candidates = []
        for node in nodes:
            if node.id in exclude_node_ids:
                continue

            mastery = mastery_map.get(node.id)
            mastery_score = float(mastery.mastery_score) if mastery else 0.0

            source_weight = self.policy.get_source_weight("AUTONOMOUS")
            mastery_component = self.policy.calculate_mastery_component(mastery_score)
            repetition = self.policy.repetition_penalty if node.id in recent_content_ids else 0.0

            priority_score = source_weight + mastery_component + repetition

            rec_diff = self.policy.select_difficulty(mastery_score)
            resources = await self.knowledge_service.find_resources_by_content(
                node.name,
                requester_institution_id=institution_id,
            )

            rec_type = "PRACTICE"
            resource_id = None
            question_version_id = None

            if mastery_score < 50.0:
                if resources:
                    rec_type = "STUDY_MATERIAL"
                    target_res = resources[0]
                    resource_id = uuid.UUID(target_res["resource_id"])
                reason = (
                    f"Analisando seu desempenho autônomo, identificamos um domínio de {mastery_score:.1f}% em {node.name}. "
                    f"Recomendamos revisar o material e praticar questões de nível {rec_diff}."
                )
            elif mastery_score < 70.0:
                rec_type = "PRACTICE"
                reason = (
                    f"Analisando seu histórico autônomo, seu domínio em {node.name} está em {mastery_score:.1f}%. "
                    f"Recomendamos praticar questões de nível {rec_diff}."
                )
            else:
                rec_type = "PRACTICE"
                reason = (
                    f"Seu desempenho em {node.name} é de {mastery_score:.1f}%. "
                    f"Recomendamos desafios de nível {rec_diff}."
                )

            if rec_type in ("PRACTICE", "REVIEW"):
                questions = await self.knowledge_service.find_questions_by_content(node.name, difficulty=rec_diff)
                if questions:
                    question_version_id = uuid.UUID(questions[0]["question_version_id"])

            auto_candidates.append({
                "content_node_id": node.id,
                "recommendation_type": rec_type,
                "recommended_difficulty": rec_diff,
                "priority_score": priority_score,
                "resource_id": resource_id,
                "question_version_id": question_version_id,
                "reason": reason,
                "context_source": "AUTONOMOUS",
                "mastery_score": mastery_score,
                "metadata": {
                    "content_name": node.name,
                    "source_weight": source_weight,
                    "mastery_component": mastery_component,
                    "repetition_penalty": repetition,
                },
            })

        return auto_candidates

    async def _get_mastery_score(self, student_id: str, content_node_id: uuid.UUID) -> float:
        stmt = select(StudentContentMastery).where(
            StudentContentMastery.external_identity_id == student_id,
            StudentContentMastery.content_node_id == content_node_id,
        )
        res = await self.session.execute(stmt)
        m = res.scalar_one_or_none()
        return float(m.mastery_score) if m else 0.0
