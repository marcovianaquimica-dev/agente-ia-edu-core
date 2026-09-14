"""
Initial Diagnostic Service and Adaptive Engine (Phase 13).

Provides deterministic adaptive sondage for initial student mastery map estimation.
Supports both School-bound students and Independent/Autonomous students.
"""

from __future__ import annotations

import logging
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

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
    TeachingLesson,
)
from agente_ia_edu.repositories.learning_path import QuestionSelectionRepository
from agente_ia_edu.services.answer_key import resolve_official_correct_option_id
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.learning_path import ContentMasteryService
from agente_ia_edu.services.learning_path_policies import DifficultyLevel
from agente_ia_edu.services.proficiency import DiagnosticConcentrationPolicy, DiagnosticCoveragePolicy, DiagnosticDecisionPolicy, GlobalDiagnosticCoveragePolicy, PedagogicalEvidence, PrerequisiteHypothesisPolicy, ProficiencyEstimator, SimpleProficiencyEstimator
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService

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
        coverage_sufficient: bool = False,
    ) -> tuple[bool, str]:
        if questions_asked >= self.max_questions:
            return True, "MAX_QUESTIONS_REACHED"
        if (
            questions_asked >= self.min_questions
            and current_confidence >= self.target_confidence
            and coverage_sufficient
        ):
            return True, "TARGET_CONFIDENCE_REACHED"
        return False, "CONTINUE"


class InitialDiagnosticService:
    """Service managing Initial Diagnostic sessions, adaptive progression, and mastery map seeding."""

    def __init__(
        self,
        session: AsyncSession,
        knowledge_service: KnowledgeService,
        stopping_policy: DiagnosticStoppingPolicy | None = None,
        proficiency_estimator: ProficiencyEstimator | None = None,
        coverage_policy: DiagnosticCoveragePolicy | None = None,
        decision_policy: DiagnosticDecisionPolicy | None = None,
        global_coverage_policy: GlobalDiagnosticCoveragePolicy | None = None,
        concentration_policy: DiagnosticConcentrationPolicy | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.session = session
        self.knowledge_service = knowledge_service
        self.stopping_policy = stopping_policy or DiagnosticStoppingPolicy()
        self.mastery_service = ContentMasteryService()
        self.proficiency_estimator = proficiency_estimator or SimpleProficiencyEstimator()
        self.coverage_policy = coverage_policy or DiagnosticCoveragePolicy()
        self.decision_policy = decision_policy or DiagnosticDecisionPolicy()
        self.global_coverage_policy = global_coverage_policy or GlobalDiagnosticCoveragePolicy()
        self.concentration_policy = concentration_policy or DiagnosticConcentrationPolicy()
        self.prerequisite_hypothesis_policy = PrerequisiteHypothesisPolicy()
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    async def start_diagnostic(
        self,
        *,
        student_id: str,
        school_id: uuid.UUID | None = None,
        classroom_id: str | None = None,
        academic_year: str = "2026",
        grade_level: str | None = "3ª Série",
        discipline: str = "Quimica",
        diagnostic_version: str = "v1",
        metadata: dict[str, Any] | None = None,
        defer_questions: bool = False,
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
            started_at=self.now_provider(),
            metadata_=metadata or {"school_bound": school_id is not None},
        )
        self.session.add(diagnostic)
        await self.session.flush()

        first_q = None
        if defer_questions:
            meta = dict(diagnostic.metadata_ or {})
            meta["entry_status"] = "IN_PROGRESS"
            meta["entry_step"] = "WELCOME"
            diagnostic.metadata_ = meta
        else:
            first_q = await self._select_next_question_for_diagnostic(diagnostic, position=1)

        await self.session.commit()
        await self.session.refresh(diagnostic)
        return diagnostic, first_q

    async def save_entry_profile(
        self,
        *,
        diagnostic_id: uuid.UUID,
        authorized_student_id: str,
        authorized_school_id: uuid.UUID | None,
        profile: dict[str, Any],
        complete: bool = False,
    ) -> tuple[InitialDiagnostic, DiagnosticQuestionSelection | None]:
        """Persist learner-provided pedagogical context without changing identity or access."""
        diagnostic = await self.session.get(InitialDiagnostic, diagnostic_id)
        if not diagnostic:
            raise ValueError(f"InitialDiagnostic not found: {diagnostic_id}")
        if diagnostic.student_id != authorized_student_id or diagnostic.school_id != authorized_school_id:
            raise PermissionError("Diagnostic does not belong to the authenticated context")
        if diagnostic.total_questions_asked:
            raise ValueError("Diagnostic entry is no longer editable after questions begin")

        allowed = {
            "preferred_name", "age_range", "study_objectives", "interest_areas",
            "perceived_difficulties", "free_text", "discipline", "content",
            "needs_guidance", "diagnostic_mode", "priority_disciplines", "step",
        }
        entry_profile = {key: value for key, value in profile.items() if key in allowed and value not in (None, [], "")}
        metadata = dict(diagnostic.metadata_ or {})
        metadata["entry_profile"] = {**metadata.get("entry_profile", {}), **entry_profile}
        metadata["entry_step"] = entry_profile.get("step", metadata.get("entry_step", "WELCOME"))
        metadata["entry_status"] = "COMPLETED" if complete else "IN_PROGRESS"

        # Resolve preferred content text to canonical CatalogNode.id
        content_text = entry_profile.get("content")
        preferred_content = {"status": "NOT_PROVIDED", "node_id": None, "name": None, "content_text": None}
        if content_text:
            universe_snapshot = metadata.get("context_snapshot", {}).get("pedagogical_universe")
            if universe_snapshot and universe_snapshot.get("id"):
                universe_id = uuid.UUID(universe_snapshot["id"])
                from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService
                universe_service = PedagogicalUniverseService(self.session)
                preferred_content = await universe_service.resolve_preferred_content_node(
                    universe_id, content_text
                )
        metadata["preferred_content_resolution"] = preferred_content

        diagnostic.metadata_ = metadata

        if entry_profile.get("discipline"):
            diagnostic.discipline = entry_profile["discipline"]

        next_question = None
        if complete:
            await self.session.flush()
            next_question = await self._select_next_question_for_diagnostic(diagnostic, position=1)
        await self.session.commit()
        await self.session.refresh(diagnostic)
        return diagnostic, next_question

    async def answer_question(
        self,
        *,
        diagnostic_id: uuid.UUID,
        selection_id: uuid.UUID,
        selected_option_id: uuid.UUID | None = None,
        response_text: str | None = None,
        is_unknown: bool = False,
        authorized_student_id: str | None = None,
        authorized_school_id: uuid.UUID | None = None,
    ) -> tuple[InitialDiagnostic, bool, bool, DiagnosticQuestionSelection | None]:
        """Submits an answer, updates adaptive state, checks stopping criteria, and selects next question if not finished."""
        diagnostic = await self.session.get(InitialDiagnostic, diagnostic_id)
        if not diagnostic or diagnostic.status != DiagnosticStatus.IN_PROGRESS:
            raise ValueError(f"Active InitialDiagnostic not found: {diagnostic_id}")

        if authorized_student_id is not None:
            if diagnostic.student_id != authorized_student_id:
                raise PermissionError("Diagnostic does not belong to the authenticated student")
            if authorized_school_id is None:
                if diagnostic.school_id is not None:
                    raise PermissionError("Independent student cannot answer a school diagnostic")
            elif diagnostic.school_id != authorized_school_id:
                raise PermissionError("Diagnostic school does not match the authenticated context")

        selection = await self.session.get(DiagnosticQuestionSelection, selection_id)
        if not selection or selection.diagnostic_id != diagnostic_id:
            raise ValueError(f"DiagnosticQuestionSelection not found: {selection_id}")
        if selection.answered_at is not None:
            raise ValueError("DiagnosticQuestionSelection has already been answered")

        # An explicit unknown response remains pedagogical evidence, not an invalid answer.
        is_correct = False
        if selected_option_id is not None and not is_unknown:
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
        selection.response_text = "UNKNOWN" if is_unknown else response_text
        selection.is_correct = is_correct
        selection.answered_at = self.now_provider()

        answered = list((await self.session.execute(
            select(DiagnosticQuestionSelection).where(
                DiagnosticQuestionSelection.diagnostic_id == diagnostic.id,
                DiagnosticQuestionSelection.content_node_id == selection.content_node_id,
                DiagnosticQuestionSelection.answered_at.isnot(None),
            ).order_by(DiagnosticQuestionSelection.position)
        )).scalars().all())
        estimate = self.proficiency_estimator.estimate([
            PedagogicalEvidence(is_correct=bool(item.is_correct), difficulty_level=item.difficulty_level)
            for item in answered
        ])
        coverage = self.coverage_policy.assess([
            PedagogicalEvidence(is_correct=bool(item.is_correct), difficulty_level=item.difficulty_level)
            for item in answered
        ], self.proficiency_estimator)
        metadata = dict(diagnostic.metadata_ or {})
        metadata.setdefault("pedagogical_evidence", []).append({
            "selection_id": str(selection.id),
            "question_version_id": str(selection.question_version_id),
            "content_node_id": str(selection.content_node_id),
            "difficulty_level": selection.difficulty_level,
            "is_correct": is_correct,
            "response_kind": "UNKNOWN" if is_unknown else "ANSWERED",
            "estimate_after": estimate.mastery_score,
            "confidence": estimate.confidence,
            "band": estimate.band,
            "coverage_status": coverage.status,
            "is_inconsistent": coverage.is_inconsistent,
            "needs_prerequisite_investigation": coverage.needs_prerequisite_investigation,
        })
        hypotheses = list(metadata.get("prerequisite_hypotheses", []))
        timestamp = selection.answered_at.isoformat()
        evidence_id = str(selection.id)
        target_id = str(selection.content_node_id)
        matching_indexes = [index for index, item in enumerate(hypotheses) if item["prerequisite_content_id"] == target_id]
        for index in matching_indexes:
            hypotheses[index] = self.prerequisite_hypothesis_policy.update(
                hypotheses[index], is_correct=is_correct, is_unknown=is_unknown, timestamp=timestamp, evidence_id=evidence_id
            )
        node = await self.session.get(CatalogNode, selection.content_node_id)
        if coverage.needs_prerequisite_investigation and node and node.parent_id:
            prerequisite_id = str(node.parent_id)
            if not any(item["target_content_id"] == target_id and item["prerequisite_content_id"] == prerequisite_id for item in hypotheses):
                hypotheses.append(self.prerequisite_hypothesis_policy.create(
                    target_content_id=target_id, prerequisite_content_id=prerequisite_id, timestamp=timestamp, evidence_id=evidence_id
                ))
        metadata["prerequisite_hypotheses"] = hypotheses

        diagnostic.total_questions_asked += 1
        if is_correct:
            diagnostic.total_correct += 1

        # Calculate confidence adaptively based on number of questions answered
        questions_asked = diagnostic.total_questions_asked
        questions_answered = diagnostic.total_questions_asked
        confidence = min(1.0, round(questions_answered / float(self.stopping_policy.max_questions), 4))
        diagnostic.overall_confidence = confidence

        mode = metadata.get("entry_profile", {}).get("diagnostic_mode", "DISCIPLINE").upper()
        started_at = diagnostic.started_at
        answered_at = selection.answered_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if answered_at.tzinfo is None:
            answered_at = answered_at.replace(tzinfo=timezone.utc)
        elapsed_seconds = max(0, int((answered_at - started_at).total_seconds()))
        global_mode = mode in {"GLOBAL", "ENEM", "UNSPECIFIED"}
        global_stop = await self._global_sufficiency(diagnostic) if global_mode else False
        if global_mode:
            metadata["global_coverage"] = (diagnostic.metadata_ or {}).get("global_coverage")
        decision = self.decision_policy.decide(
            mode=mode,
            elapsed_seconds=elapsed_seconds,
            questions_asked=questions_answered,
            coverage=coverage,
            global_stop=global_stop,
            global_mode=global_mode,
        )
        metadata["latest_decision"] = {
            "action": decision.action, "reason": decision.reason,
            "elapsed_seconds": elapsed_seconds, "high_information_only": decision.high_information_only,
        }
        diagnostic.metadata_ = metadata
        stop_flag = decision.action in {"FINISH_SUFFICIENT", "FINISH_TIME"}
        stop_reason = decision.reason
        if questions_asked >= self.stopping_policy.max_questions:
            stop_flag, stop_reason = True, "MAX_QUESTIONS_REACHED"

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

    async def _global_sufficiency(self, diagnostic: InitialDiagnostic) -> bool:
        selections = list((await self.session.execute(
            select(DiagnosticQuestionSelection).where(
                DiagnosticQuestionSelection.diagnostic_id == diagnostic.id,
                DiagnosticQuestionSelection.answered_at.isnot(None),
            )
        )).scalars().all())
        nodes = {item.content_node_id: await self.session.get(CatalogNode, item.content_node_id) for item in selections}
        states: dict[str, list[Any]] = {}
        for node_id, node in nodes.items():
            if not node:
                continue
            root = await self.session.get(CatalogNode, node.root_id)
            evidence = [PedagogicalEvidence(bool(item.is_correct), item.difficulty_level) for item in selections if item.content_node_id == node_id]
            states.setdefault(root.name if root else "General", []).append(self.coverage_policy.assess(evidence, self.proficiency_estimator))
        roots = list((await self.session.execute(
            select(CatalogNode).where(CatalogNode.node_type == "DISCIPLINE", CatalogNode.active.is_(True))
        )).scalars().all())
        for root in roots:
            states.setdefault(root.name, [self.coverage_policy.assess([], self.proficiency_estimator)])
        summary = self.global_coverage_policy.summarize(states)
        stop, _ = self.global_coverage_policy.should_stop_global_diagnostic(summary, diagnostic.total_questions_asked)
        metadata = dict(diagnostic.metadata_ or {})
        metadata["global_coverage"] = {"overall_coverage": summary.overall_coverage, "overall_confidence": summary.overall_confidence, "residual_uncertainty": list(summary.residual_uncertainty)}
        diagnostic.metadata_ = metadata
        return stop

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
        raw_result = {"questions_answered": 0, "correct": 0, "incorrect": 0, "unknown": 0}
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
            if sel.answered_at:
                raw_result["questions_answered"] += 1
                if sel.response_text == "UNKNOWN":
                    raw_result["unknown"] += 1
                elif sel.is_correct:
                    raw_result["correct"] += 1
                else:
                    raw_result["incorrect"] += 1

        mastery_map = []
        probable_gaps = []

        for n_id, stat in node_stats.items():
            node = stat["node"]
            asked = stat["asked"]
            correct = stat["correct"]
            evidence = [
                PedagogicalEvidence(is_correct=bool(item.is_correct), difficulty_level=item.difficulty_level)
                for item in selections if item.content_node_id == n_id and item.answered_at
            ]
            estimate = self.proficiency_estimator.estimate(evidence)
            coverage = self.coverage_policy.assess(evidence, self.proficiency_estimator)
            score = estimate.mastery_score

            if score < 50.0:
                level = DifficultyLevel.EASY.value
                prerequisite = await self.session.get(CatalogNode, node.parent_id) if node and node.parent_id else None
                probable_gaps.append({
                    "content_node_id": str(n_id),
                    "content_name": node.name if node else "Conteúdo",
                    "estimated_mastery": round(score, 1),
                    "prerequisite_check_required": node.parent_id is not None,
                    "possible_prerequisite_gap": {
                        "content_node_id": str(prerequisite.id),
                        "content_name": prerequisite.name,
                        "confidence": estimate.confidence,
                        "evidence_origin": "INITIAL_DIAGNOSTIC",
                    } if prerequisite else None,
                })
            elif score < 70.0:
                level = DifficultyLevel.MEDIUM.value
            else:
                level = DifficultyLevel.HARD.value

            mastery_map.append({
                "content_node_id": str(n_id),
                "content_name": node.name if node else "Conteúdo",
                "estimated_mastery": round(score, 1),
                "confidence": estimate.confidence,
                "coverage_status": coverage.status,
                "is_inconsistent": coverage.is_inconsistent,
                "recommended_difficulty": level,
                "evidence_origin": "INITIAL_DIAGNOSTIC",
            })

        completed_at = diagnostic.completed_at or self.now_provider()
        started_at = diagnostic.started_at
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
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
            "raw_result": raw_result,
            "duration_seconds": max(0, int((completed_at - started_at).total_seconds())),
            "completion_reason": (diagnostic.metadata_ or {}).get("stop_reason"),
            "latest_decision": (diagnostic.metadata_ or {}).get("latest_decision"),
            "pedagogical_states": [
                {"content_node_id": item["content_node_id"], "coverage_status": item["coverage_status"], "confidence": item["confidence"]}
                for item in mastery_map
            ],
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

        entry_profile = (diagnostic.metadata_ or {}).get("entry_profile", {})
        mode = entry_profile.get("diagnostic_mode", "DISCIPLINE").upper()
        root_query = select(CatalogNode).where(
            CatalogNode.node_type == "DISCIPLINE",
            CatalogNode.active.is_(True),
        )
        if mode == "DISCIPLINE":
            root_query = root_query.where(CatalogNode.name == diagnostic.discipline)
        elif mode == "OBJECTIVE" and entry_profile.get("priority_disciplines"):
            root_query = root_query.where(CatalogNode.name.in_(entry_profile["priority_disciplines"]))
        # GLOBAL, ENEM, and UNSPECIFIED use all eligible catalog disciplines.
        discipline_nodes = list((await self.session.execute(root_query.order_by(CatalogNode.position, CatalogNode.name))).scalars().all())
        if not discipline_nodes:
            return None
        root_ids = [node.id for node in discipline_nodes]
        curricular_root_ids = {node.id for node in discipline_nodes}
        curricular_root_ids.update(node.root_id for node in discipline_nodes if node.root_id)

        stmt_nodes = select(CatalogNode).where(
            CatalogNode.active.is_(True),
            CatalogNode.node_type != "DISCIPLINE",
            CatalogNode.parent_id.isnot(None),
            CatalogNode.root_id.in_(curricular_root_ids),
        ).order_by(CatalogNode.position.asc())
        res_nodes = await self.session.execute(stmt_nodes)
        nodes = list(res_nodes.scalars().all())

        if not nodes:
            return None

        universe_snapshot = (diagnostic.metadata_ or {}).get("context_snapshot", {}).get("pedagogical_universe")
        universe_id = uuid.UUID(universe_snapshot["id"]) if universe_snapshot else None
        if universe_snapshot:
            universe_service = PedagogicalUniverseService(self.session)
            nodes = [node for node in nodes if await universe_service.contains_catalog_node(universe_id, node.id)]
            if not nodes:
                return None

        # Exclude already asked question_version_ids in this diagnostic
        already_asked_stmt = select(DiagnosticQuestionSelection.question_version_id).where(
            DiagnosticQuestionSelection.diagnostic_id == diagnostic.id
        )
        already_asked_res = await self.session.execute(already_asked_stmt)
        asked_qv_ids = set(already_asked_res.scalars().all())

        asked_nodes = list((await self.session.execute(
            select(DiagnosticQuestionSelection.content_node_id).where(
                DiagnosticQuestionSelection.diagnostic_id == diagnostic.id,
            )
        )).scalars().all())
        root_counts: dict[uuid.UUID, int] = {root_id: 0 for root_id in root_ids}
        if asked_nodes:
            asked_roots = await self.session.execute(
                select(CatalogNode.root_id).where(CatalogNode.id.in_(asked_nodes))
            )
            for root_id in asked_roots.scalars().all():
                if root_id in root_counts:
                    root_counts[root_id] += 1
        if mode != "DISCIPLINE":
            nodes.sort(key=lambda node: (root_counts.get(node.root_id, 0), node.position, node.name, str(node.id)))

        parent_ids = {node.parent_id for node in nodes if node.parent_id is not None}
        nodes.sort(key=lambda node: (
            node.id in parent_ids,
            root_counts.get(node.root_id, 0) if mode != "DISCIPLINE" else 0,
            node.position,
            node.name,
            str(node.id),
        ))

        recent_node_ids = [str(item) for item in asked_nodes[-self.concentration_policy.max_consecutive_same_content:]]
        recent_root_ids = []
        for node_id in asked_nodes[-self.concentration_policy.max_consecutive_same_discipline:]:
            node = await self.session.get(CatalogNode, node_id)
            if node:
                recent_root_ids.append(str(node.root_id))
        constrained_nodes = []
        concentration_decision = "CONTINUE_SAME_CONTENT"
        filtered_for_concentration = False
        for node in nodes:
            decision = self.concentration_policy.decision(
                recent_content_ids=recent_node_ids,
                recent_discipline_ids=recent_root_ids,
                candidate_content_id=str(node.id),
                candidate_discipline_id=str(node.root_id),
                has_alternative_content=any(other.id != node.id for other in nodes),
                has_alternative_discipline=any(other.root_id != node.root_id for other in nodes),
            )
            if decision == "CONTINUE_SAME_CONTENT":
                constrained_nodes.append(node)
            else:
                filtered_for_concentration = True
                concentration_decision = decision
        if constrained_nodes:
            nodes = constrained_nodes

        q_repo = QuestionSelectionRepository(self.session)
        context_snapshot = (diagnostic.metadata_ or {}).get("context_snapshot", {})

        taught_node_ids: set[uuid.UUID] = set()
        if diagnostic.school_id is not None:
            lessons = select(TeachingLesson.content_node_id).where(
                TeachingLesson.school_id == diagnostic.school_id,
                TeachingLesson.academic_year == diagnostic.academic_year,
            )
            if diagnostic.classroom_id:
                lessons = lessons.where(TeachingLesson.classroom_id == diagnostic.classroom_id)
            taught_node_ids = set((await self.session.execute(lessons)).scalars().all())
            if taught_node_ids:
                taught_nodes = [node for node in nodes if node.id in taught_node_ids]
                if taught_nodes:
                    nodes = taught_nodes

        suspected_prerequisites = {
            item["prerequisite_content_id"]
            for item in (diagnostic.metadata_ or {}).get("prerequisite_hypotheses", [])
            if item["status"] in {"SUSPECTED", "UNDER_INVESTIGATION", "SUPPORTED"}
        }
        if last_is_correct is False:
            previous = await self.session.execute(
                select(DiagnosticQuestionSelection.content_node_id).where(
                    DiagnosticQuestionSelection.diagnostic_id == diagnostic.id,
                    DiagnosticQuestionSelection.position == position - 1,
                )
            )
            previous_node = await self.session.get(CatalogNode, previous.scalar_one_or_none())
            if previous_node and previous_node.parent_id and str(previous_node.parent_id) in suspected_prerequisites:
                prerequisite = next((node for node in nodes if node.id == previous_node.parent_id), None)
                if prerequisite:
                    nodes = [prerequisite] + [node for node in nodes if node.id != prerequisite.id]

        # Iterate over nodes deterministically
        for node in nodes:
            candidates = await q_repo.list_diagnostic_candidate_versions(
                node.id,
                difficulty_level=target_difficulty,
                school_id=diagnostic.school_id,
                classroom_id=diagnostic.classroom_id,
                unit_id=context_snapshot.get("unit_id"),
                segment=context_snapshot.get("segment"),
                grade_level=diagnostic.grade_level,
                universe_id=universe_id,
            )
            unasked = [qv for qv in candidates if qv.id not in asked_qv_ids]

            if not unasked:
                # Try fallback without difficulty filter for this node
                all_candidates = await q_repo.list_diagnostic_candidate_versions(
                    node.id,
                    school_id=diagnostic.school_id,
                    classroom_id=diagnostic.classroom_id,
                    unit_id=context_snapshot.get("unit_id"),
                    segment=context_snapshot.get("segment"),
                    grade_level=diagnostic.grade_level,
                    universe_id=universe_id,
                )
                unasked = [qv for qv in all_candidates if qv.id not in asked_qv_ids]

            if unasked:
                selected_qv = unasked[0]
                selected_concentration_decision = concentration_decision
                if recent_node_ids and str(node.id) != recent_node_ids[-1]:
                    selected_concentration_decision = "SWITCH_CONTENT"
                if recent_root_ids and str(node.root_id) != recent_root_ids[-1]:
                    selected_concentration_decision = "SWITCH_DISCIPLINE"
                selection = DiagnosticQuestionSelection(
                    diagnostic_id=diagnostic.id,
                    question_version_id=selected_qv.id,
                    content_node_id=node.id,
                    difficulty_level=target_difficulty,
                    position=position,
                )
                self.session.add(selection)
                metadata = deepcopy(diagnostic.metadata_ or {})
                metadata.setdefault("selection_trace", []).append({
                    "position": position,
                    "content_node_id": str(node.id),
                    "discipline_root_id": str(node.root_id),
                    "diagnostic_mode": mode,
                    "difficulty": target_difficulty,
                    "reason": "PREREQUISITE_CHECK" if last_is_correct is False and node.parent_id else "CONTEXTUAL_CANDIDATE",
                    "concentration_decision": selected_concentration_decision,
                    "concentration_filter_applied": filtered_for_concentration,
                    "taught_content": node.id in taught_node_ids,
                })
                diagnostic.metadata_ = metadata
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
            estimate = self.proficiency_estimator.estimate([
                PedagogicalEvidence(is_correct=bool(item.is_correct), difficulty_level=item.difficulty_level)
                for item in sels
            ])
            score = estimate.mastery_score

            # Update StudentContentMastery
            mastery = await self.mastery_service.get_or_create_mastery(
                self.session,
                external_identity_id=diagnostic.student_id,
                content_node_id=n_id,
            )
            mastery.questions_answered += asked
            mastery.questions_correct += correct
            mastery.mastery_score = score
            mastery.confidence = estimate.confidence
            mastery.last_activity_at = datetime.now(timezone.utc)

            if score >= 75.0:
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
