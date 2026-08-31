"""
Initial Diagnostic Service and Adaptive Engine (Phase 13).

Provides deterministic adaptive sondage for initial student mastery map estimation.
Supports both School-bound students and Independent/Autonomous students.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models import (
    CatalogNode,
    DiagnosticQuestionSelection,
    InitialDiagnostic,
    LearningHistory,
    QuestionOption,
    QuestionVersion,
    StudentContentMastery,
)
from agente_ia_edu.repositories.learning_path import QuestionSelectionRepository
from agente_ia_edu.services.answer_key import resolve_official_correct_option_id
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.learning_path import ContentMasteryService
from agente_ia_edu.services.learning_path_policies import DifficultyLevel

logger = logging.getLogger(__name__)


class DiagnosticStatus:
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CANCELLED = "CANCELLED"


@dataclass
class DiagnosticStoppingPolicy:
    """Configurable stopping criteria policy for adaptive diagnostic sondage."""

    min_questions: int = 3
    max_questions: int = 10
    target_confidence: float = 0.70

    def should_stop(
        self,
        questions_asked: int,
        current_confidence: float,
    ) -> tuple[bool, str]:
        if questions_asked >= self.max_questions:
            return True, "MAX_QUESTIONS_REACHED"
        if questions_asked >= self.min_questions and current_confidence >= self.target_confidence:
            return True, "TARGET_CONFIDENCE_REACHED"
        return False, "CONTINUE"


class InitialDiagnosticService:
    """Service managing Initial Diagnostic sessions, adaptive progression, and mastery map seeding."""

    def __init__(
        self,
        session: AsyncSession,
        knowledge_service: KnowledgeService,
        stopping_policy: DiagnosticStoppingPolicy | None = None,
    ):
        self.session = session
        self.knowledge_service = knowledge_service
        self.stopping_policy = stopping_policy or DiagnosticStoppingPolicy()
        self.mastery_service = ContentMasteryService()

    async def start_diagnostic(
        self,
        *,
        student_id: str,
        school_id: uuid.UUID | None = None,
        classroom_id: str | None = None,
        academic_year: str = "2026",
        grade_level: str | None = "3ª Série",
        discipline: str = "Química",
        diagnostic_version: str = "v1",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[InitialDiagnostic, DiagnosticQuestionSelection | None]:
        """Start a new Initial Diagnostic session (School-bound or Independent)."""
        # Check if an in-progress diagnostic exists
        stmt_existing = select(InitialDiagnostic).where(
            InitialDiagnostic.student_id == student_id,
            InitialDiagnostic.status == DiagnosticStatus.IN_PROGRESS,
        )
        res_existing = await self.session.execute(stmt_existing)
        existing = res_existing.scalar_one_or_none()

        if existing:
            # Return current diagnostic and next question
            next_q = await self._get_next_question_selection(existing)
            return existing, next_q

        diagnostic = InitialDiagnostic(
            student_id=student_id,
            school_id=school_id,
            classroom_id=classroom_id,
            academic_year=academic_year,
            grade_level=grade_level,
            discipline=discipline,
            diagnostic_version=diagnostic_version,
            status=DiagnosticStatus.IN_PROGRESS,
            total_questions_asked=0,
            total_correct=0,
            overall_confidence=0.0,
            started_at=datetime.now(timezone.utc),
            metadata_=metadata or {"school_bound": school_id is not None},
        )
        self.session.add(diagnostic)
        await self.session.flush()

        # Select first question adaptively
        first_q = await self._select_next_question_for_diagnostic(diagnostic, position=1)

        await self.session.commit()
        await self.session.refresh(diagnostic)
        return diagnostic, first_q

    async def answer_question(
        self,
        *,
        diagnostic_id: uuid.UUID,
        selection_id: uuid.UUID,
        selected_option_id: uuid.UUID | None = None,
        response_text: str | None = None,
    ) -> tuple[InitialDiagnostic, bool, bool, DiagnosticQuestionSelection | None]:
        """Submits an answer, updates adaptive state, checks stopping criteria, and selects next question if not finished."""
        diagnostic = await self.session.get(InitialDiagnostic, diagnostic_id)
        if not diagnostic or diagnostic.status != DiagnosticStatus.IN_PROGRESS:
            raise ValueError(f"Active InitialDiagnostic not found: {diagnostic_id}")

        selection = await self.session.get(DiagnosticQuestionSelection, selection_id)
        if not selection or selection.diagnostic_id != diagnostic_id:
            raise ValueError(f"DiagnosticQuestionSelection not found: {selection_id}")

        # Correct answer
        is_correct = False
        if selected_option_id is not None:
            correct_opt_id = await resolve_official_correct_option_id(
                self.session, selection.question_version_id
            )
            if correct_opt_id is not None:
                is_correct = (selected_option_id == correct_opt_id)
            else:
                # If no official key in DB, compare against option is_valid_option
                opt = await self.session.get(QuestionOption, selected_option_id)
                is_correct = bool(opt and opt.is_valid_option)

        selection.selected_option_id = selected_option_id
        selection.response_text = response_text
        selection.is_correct = is_correct
        selection.answered_at = datetime.now(timezone.utc)

        diagnostic.total_questions_asked += 1
        if is_correct:
            diagnostic.total_correct += 1

        # Calculate confidence adaptively based on number of questions answered
        questions_answered = diagnostic.total_questions_asked
        confidence = min(1.0, round(questions_answered / float(self.stopping_policy.max_questions), 4))
        diagnostic.overall_confidence = confidence

        # Check stopping policy
        stop_flag, stop_reason = self.stopping_policy.should_stop(questions_answered, confidence)

        if stop_flag:
            await self._finalize_diagnostic(diagnostic, stop_reason)
            await self.session.commit()
            await self.session.refresh(diagnostic)
            return diagnostic, is_correct, True, None

        # Select next question adaptively
        next_pos = questions_answered + 1
        next_q = await self._select_next_question_for_diagnostic(
            diagnostic,
            position=next_pos,
            last_is_correct=is_correct,
            last_difficulty=selection.difficulty_level,
        )

        if not next_q:
            # Insufficient candidate questions -> finalize
            await self._finalize_diagnostic(diagnostic, "NO_MORE_CANDIDATE_QUESTIONS")
            await self.session.commit()
            await self.session.refresh(diagnostic)
            return diagnostic, is_correct, True, None

        await self.session.commit()
        await self.session.refresh(diagnostic)
        return diagnostic, is_correct, False, next_q

    async def get_diagnostic_result(
        self,
        diagnostic_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Returns completed diagnostic results, mastery map, gaps, and evidence list."""
        stmt = (
            select(InitialDiagnostic)
            .where(InitialDiagnostic.id == diagnostic_id)
            .options(
                selectinload(InitialDiagnostic.question_selections).selectinload(DiagnosticQuestionSelection.question_version),
                selectinload(InitialDiagnostic.question_selections).selectinload(DiagnosticQuestionSelection.content_node),
            )
        )
        res = await self.session.execute(stmt)
        diagnostic = res.scalar_one_or_none()
        if not diagnostic:
            raise ValueError(f"InitialDiagnostic not found: {diagnostic_id}")

        selections = sorted(diagnostic.question_selections, key=lambda s: s.position)

        # Content node performance breakdown
        node_stats: dict[uuid.UUID, dict[str, Any]] = {}
        for sel in selections:
            n_id = sel.content_node_id
            if n_id not in node_stats:
                node_stats[n_id] = {
                    "node": sel.content_node,
                    "asked": 0,
                    "correct": 0,
                    "difficulties": [],
                }
            node_stats[n_id]["asked"] += 1
            if sel.is_correct:
                node_stats[n_id]["correct"] += 1
            node_stats[n_id]["difficulties"].append(sel.difficulty_level)

        mastery_map = []
        probable_gaps = []

        for n_id, stat in node_stats.items():
            node = stat["node"]
            asked = stat["asked"]
            correct = stat["correct"]
            score = (correct / asked * 100.0) if asked > 0 else 0.0

            if score < 50.0:
                level = DifficultyLevel.EASY.value
                probable_gaps.append({
                    "content_node_id": str(n_id),
                    "content_name": node.name if node else "Conteúdo",
                    "estimated_mastery": round(score, 1),
                    "prerequisite_check_required": node.parent_id is not None,
                })
            elif score < 70.0:
                level = DifficultyLevel.MEDIUM.value
            else:
                level = DifficultyLevel.HARD.value

            mastery_map.append({
                "content_node_id": str(n_id),
                "content_name": node.name if node else "Conteúdo",
                "estimated_mastery": round(score, 1),
                "confidence": float(diagnostic.overall_confidence),
                "recommended_difficulty": level,
                "evidence_origin": "INITIAL_DIAGNOSTIC",
            })

        return {
            "diagnostic_id": str(diagnostic.id),
            "student_id": diagnostic.student_id,
            "school_id": str(diagnostic.school_id) if diagnostic.school_id else None,
            "is_independent": diagnostic.school_id is None,
            "status": diagnostic.status,
            "diagnostic_version": diagnostic.diagnostic_version,
            "total_questions_asked": diagnostic.total_questions_asked,
            "total_correct": diagnostic.total_correct,
            "overall_confidence": float(diagnostic.overall_confidence),
            "started_at": diagnostic.started_at.isoformat(),
            "completed_at": diagnostic.completed_at.isoformat() if diagnostic.completed_at else None,
            "mastery_map": mastery_map,
            "probable_gaps": probable_gaps,
            "evidence_count": len(selections),
        }

    # -------------------------------------------------------------------------
    # PRIVATE ADAPTIVE ENGINE HELPER METHODS
    # -------------------------------------------------------------------------

    async def _select_next_question_for_diagnostic(
        self,
        diagnostic: InitialDiagnostic,
        position: int,
        last_is_correct: bool | None = None,
        last_difficulty: str | None = None,
    ) -> DiagnosticQuestionSelection | None:
        """Deterministically selects next question adaptively based on previous accuracy."""
        # Determine target difficulty adaptively
        if last_difficulty is None:
            target_difficulty = DifficultyLevel.EASY.value
        elif last_is_correct is True:
            if last_difficulty == DifficultyLevel.EASY.value:
                target_difficulty = DifficultyLevel.MEDIUM.value
            else:
                target_difficulty = DifficultyLevel.HARD.value
        else:
            if last_difficulty == DifficultyLevel.HARD.value:
                target_difficulty = DifficultyLevel.MEDIUM.value
            else:
                target_difficulty = DifficultyLevel.EASY.value

        # Fetch active content nodes from Catalog
        stmt_nodes = select(CatalogNode).where(
            CatalogNode.active.is_(True),
            CatalogNode.node_type != "DISCIPLINE",
            CatalogNode.parent_id.isnot(None),
        ).order_by(CatalogNode.position.asc())
        res_nodes = await self.session.execute(stmt_nodes)
        nodes = list(res_nodes.scalars().all())

        if not nodes:
            return None

        # Exclude already asked question_version_ids in this diagnostic
        already_asked_stmt = select(DiagnosticQuestionSelection.question_version_id).where(
            DiagnosticQuestionSelection.diagnostic_id == diagnostic.id
        )
        already_asked_res = await self.session.execute(already_asked_stmt)
        asked_qv_ids = set(already_asked_res.scalars().all())

        q_repo = QuestionSelectionRepository(self.session)

        # Iterate over nodes deterministically
        for node in nodes:
            candidates = await q_repo.list_candidate_versions(node.id, difficulty_level=target_difficulty)
            unasked = [qv for qv in candidates if qv.id not in asked_qv_ids]

            if not unasked:
                # Try fallback without difficulty filter for this node
                all_candidates = await q_repo.list_candidate_versions(node.id)
                unasked = [qv for qv in all_candidates if qv.id not in asked_qv_ids]

            if unasked:
                selected_qv = unasked[0]
                selection = DiagnosticQuestionSelection(
                    diagnostic_id=diagnostic.id,
                    question_version_id=selected_qv.id,
                    content_node_id=node.id,
                    difficulty_level=target_difficulty,
                    position=position,
                )
                self.session.add(selection)
                await self.session.flush()
                return selection

        return None

    async def _get_next_question_selection(self, diagnostic: InitialDiagnostic) -> DiagnosticQuestionSelection | None:
        stmt = (
            select(DiagnosticQuestionSelection)
            .where(
                DiagnosticQuestionSelection.diagnostic_id == diagnostic.id,
                DiagnosticQuestionSelection.answered_at.is_(None),
            )
            .order_by(DiagnosticQuestionSelection.position.asc())
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def _finalize_diagnostic(self, diagnostic: InitialDiagnostic, stop_reason: str) -> None:
        """Finalizes diagnostic, updates StudentContentMastery and LearningHistory audit records."""
        diagnostic.status = DiagnosticStatus.COMPLETED
        diagnostic.completed_at = datetime.now(timezone.utc)

        meta = diagnostic.metadata_ or {}
        meta["stop_reason"] = stop_reason
        diagnostic.metadata_ = dict(meta)

        # Group selections by content_node_id
        stmt = select(DiagnosticQuestionSelection).where(
            DiagnosticQuestionSelection.diagnostic_id == diagnostic.id,
            DiagnosticQuestionSelection.answered_at.isnot(None),
        )
        res = await self.session.execute(stmt)
        selections = list(res.scalars().all())

        node_groups: dict[uuid.UUID, list[DiagnosticQuestionSelection]] = {}
        for sel in selections:
            node_groups.setdefault(sel.content_node_id, []).append(sel)

        for n_id, sels in node_groups.items():
            asked = len(sels)
            correct = sum(1 for s in sels if s.is_correct is True)
            score = (correct / asked * 100.0) if asked > 0 else 0.0

            # Update StudentContentMastery
            mastery = await self.mastery_service.get_or_create_mastery(
                self.session,
                external_identity_id=diagnostic.student_id,
                content_node_id=n_id,
            )
            mastery.questions_answered += asked
            mastery.questions_correct += correct
            mastery.mastery_score = score
            mastery.confidence = float(diagnostic.overall_confidence)
            mastery.last_activity_at = datetime.now(timezone.utc)

            if score >= 70.0:
                mastery.current_level = DifficultyLevel.HARD.value
            elif score >= 50.0:
                mastery.current_level = DifficultyLevel.MEDIUM.value
            else:
                mastery.current_level = DifficultyLevel.EASY.value

            # Record in LearningHistory with INITIAL_DIAGNOSTIC activity_type
            for sel in sels:
                history = LearningHistory(
                    external_identity_id=diagnostic.student_id,
                    activity_type="INITIAL_DIAGNOSTIC",
                    question_version_id=sel.question_version_id,
                    selected_option_id=sel.selected_option_id,
                    response_text=sel.response_text,
                    difficulty_level=sel.difficulty_level,
                    is_correct=sel.is_correct,
                    points_awarded=1.0 if sel.is_correct else 0.0,
                    content_node_id=n_id,
                    created_at=datetime.now(timezone.utc),
                )
                self.session.add(history)

        await self.session.flush()
