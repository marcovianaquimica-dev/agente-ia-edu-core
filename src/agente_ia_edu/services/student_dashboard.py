from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import (
    CatalogNode,
    EducationalResource,
    LearningHistory,
    PedagogicalContext,
    PedagogicalRecommendation,
    StudentContentMastery,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.recommendation import RecommendationEngine
from agente_ia_edu.services.video_engine import VideoRecommendationEngine

logger = logging.getLogger(__name__)


class StudentDashboardService:
    """Service orchestrating student dashboard, evolution, and learning path views."""

    def __init__(
        self,
        session: AsyncSession,
        knowledge_service: KnowledgeService,
        recommendation_engine: RecommendationEngine,
        video_engine: VideoRecommendationEngine,
    ):
        self.session = session
        self.knowledge_service = knowledge_service
        self.recommendation_engine = recommendation_engine
        self.video_engine = video_engine

    async def get_dashboard(
        self,
        *,
        student_id: str,
        institution_id: str | None = None,
        classroom_id: str | None = None,
        time_period: str = "academic_year",
    ) -> dict[str, Any]:
        """Builds student dashboard payload without duplicating underlying domain engines."""
        # 1. Filter timestamps according to time_period
        now = datetime.now(timezone.utc)
        since_date = self._get_period_start_date(time_period, now)

        # 2. Fetch student's learning history
        stmt_hist = (
            select(LearningHistory)
            .where(
                LearningHistory.external_identity_id == student_id,
                LearningHistory.created_at >= since_date,
            )
            .order_by(LearningHistory.created_at.asc())
        )
        res_hist = await self.session.execute(stmt_hist)
        history_entries = list(res_hist.scalars().all())

        # 3. Fetch student's content masteries
        stmt_mastery = select(StudentContentMastery).where(
            StudentContentMastery.external_identity_id == student_id
        )
        res_mastery = await self.session.execute(stmt_mastery)
        masteries = list(res_mastery.scalars().all())

        # Distinguish NEW STUDENT (has_data=False) from LOW PERFORMANCE
        has_history = len(history_entries) > 0
        has_mastery_activity = any(m.questions_answered > 0 for m in masteries)
        has_data = has_history or has_mastery_activity

        if not has_data:
            welcome_msg = "Vamos começar! Você ainda não possui histórico suficiente. Resolva algumas questões para construirmos seu perfil."
        else:
            welcome_msg = "Veja como está sua evolução no aprendizado."

        # Summary Stats Calculation
        total_answered = len(history_entries)
        total_correct = sum(1 for e in history_entries if e.is_correct is True)
        overall_avg = (total_correct / total_answered * 100.0) if total_answered > 0 else 0.0

        mastered_count = sum(1 for m in masteries if float(m.mastery_score) >= 70.0)

        # Streak calculation (active days)
        unique_days = {e.created_at.date() for e in history_entries}
        streak_days = max(1, len(unique_days)) if has_data else 0

        summary = {
            "overall_average": round(overall_avg, 1),
            "contents_mastered": mastered_count,
            "questions_answered": total_answered,
            "questions_correct": total_correct,
            "streak_days": streak_days,
        }

        # Action Plan Breakdown
        action_plan_items: list[dict[str, Any]] = []
        needs_imp: list[dict[str, Any]] = []
        in_dev: list[dict[str, Any]] = []
        consolidated: list[dict[str, Any]] = []

        for m in masteries:
            node = await self.session.get(CatalogNode, m.content_node_id)
            node_name = node.name if node else "Conteúdo"
            score = float(m.mastery_score)

            item = {
                "content_node_id": str(m.content_node_id),
                "content_name": node_name,
                "mastery_score": round(score, 1),
                "current_level": m.current_level,
                "status_label": self._get_status_label(score),
            }
            action_plan_items.append(item)

            if score < 50.0:
                needs_imp.append(item)
            elif score < 70.0:
                in_dev.append(item)
            else:
                consolidated.append(item)

        action_plan = {
            "needs_improvement": needs_imp,
            "in_development": in_dev,
            "consolidated": consolidated,
        }

        # Active Recommendation Generation and Step Resolution
        resolved_recs = await self.recommendation_engine.generate_and_resolve_recommendations(
            student_id=student_id,
            institution_id=institution_id,
            classroom_id=classroom_id,
            limit=1,
        )

        active_rec_step = None
        if resolved_recs:
            r = resolved_recs[0]
            c_node_id = uuid.UUID(r["content_node_id"])

            # Build 4-step sequence
            steps = []

            # Step 1: Material
            has_mat = r.get("has_material", False)
            p_res = r.get("primary_resource")
            steps.append({
                "step_number": 1,
                "title": "Revisar Material Teórico",
                "step_type": "MATERIAL",
                "status": "in_progress" if has_mat else "not_available",
                "description": f"Acesse a apostila ou resumo de {r['content_name']}" if has_mat else "Nenhum material cadastrado para este conteúdo",
                "resource_id": p_res.get("resource_id") if p_res else None,
            })

            # Step 2: Video
            video_res = await self.video_engine.recommend_video_for_student(
                student_id=student_id,
                content_node_id=c_node_id,
                institution_id=institution_id,
            )
            has_vid = video_res.get("status") == "OK"
            steps.append({
                "step_number": 2,
                "title": "Assistir Vídeo Recomendado",
                "step_type": "VIDEO",
                "status": "pending" if has_vid else "not_available",
                "description": video_res["title"] if has_vid else "Nenhum vídeo cadastrado para este conteúdo",
                "resource_id": video_res.get("video_resource_id") if has_vid else None,
            })

            # Step 3: Practice Questions
            has_q = r.get("has_questions", False)
            q_count = len(r.get("practice_questions", []))
            steps.append({
                "step_number": 3,
                "title": f"Resolver questões de nível {r['recommended_difficulty']}",
                "step_type": "PRACTICE",
                "status": "pending" if has_q else "not_available",
                "description": f"Praticar {q_count} questão(ões) de nível {r['recommended_difficulty']}" if has_q else "Nenhuma questão disponível neste nível",
                "question_version_id": r["practice_questions"][0]["question_version_id"] if has_q else None,
            })

            # Step 4: Re-evaluate
            steps.append({
                "step_number": 4,
                "title": "Reavaliar Domínio",
                "step_type": "REEVALUATE",
                "status": "pending",
                "description": "Atualizar medição de maestria após a prática",
            })

            active_rec_step = {
                "recommendation_id": r["recommendation_id"],
                "content_node_id": r["content_node_id"],
                "content_name": r["content_name"],
                "mastery_score": r["mastery_score"] or 0.0,
                "recommended_difficulty": r["recommended_difficulty"],
                "context_source": r["context_source"],
                "reason": r["reason"],
                "steps": steps,
                "primary_resource": p_res,
                "practice_questions_count": q_count,
            }

        return {
            "student_id": student_id,
            "time_period": time_period,
            "has_data": has_data,
            "welcome_message": welcome_msg,
            "summary": summary,
            "active_recommendation": active_rec_step,
            "action_plan": action_plan,
            "mastery_breakdown": action_plan_items,
        }

    async def get_evolution(
        self,
        *,
        student_id: str,
        time_period: str = "academic_year",
    ) -> dict[str, Any]:
        """Builds student evolution timeline and performance stats."""
        now = datetime.now(timezone.utc)
        since_date = self._get_period_start_date(time_period, now)

        stmt_hist = (
            select(LearningHistory)
            .where(
                LearningHistory.external_identity_id == student_id,
                LearningHistory.created_at >= since_date,
            )
            .order_by(LearningHistory.created_at.asc())
        )
        res_hist = await self.session.execute(stmt_hist)
        entries = list(res_hist.scalars().all())

        has_data = len(entries) > 0
        total_answered = len(entries)
        total_correct = sum(1 for e in entries if e.is_correct is True)
        total_incorrect = sum(1 for e in entries if e.is_correct is False)
        accuracy = (total_correct / total_answered * 100.0) if total_answered > 0 else 0.0

        # Group entries by day for evolution chart
        daily_groups: dict[str, list[LearningHistory]] = {}
        for e in entries:
            day_str = e.created_at.strftime("%d/%m")
            daily_groups.setdefault(day_str, []).append(e)

        overall_evolution = []
        for day, group in daily_groups.items():
            g_ans = len(group)
            g_corr = sum(1 for e in group if e.is_correct is True)
            g_avg = (g_corr / g_ans * 100.0) if g_ans > 0 else 0.0
            overall_evolution.append({
                "date_label": day,
                "average_score": round(g_avg, 1),
                "questions_answered": g_ans,
                "questions_correct": g_corr,
            })

        # Content evolution
        stmt_mastery = select(StudentContentMastery).where(
            StudentContentMastery.external_identity_id == student_id
        )
        res_m = await self.session.execute(stmt_mastery)
        masteries = list(res_m.scalars().all())

        content_evolution = []
        for m in masteries:
            node = await self.session.get(CatalogNode, m.content_node_id)
            score = float(m.mastery_score)
            content_evolution.append({
                "content_node_id": str(m.content_node_id),
                "content_name": node.name if node else "Conteúdo",
                "initial_score": 0.0,
                "current_score": round(score, 1),
                "progress_delta": round(score, 1),
            })

        return {
            "student_id": student_id,
            "time_period": time_period,
            "has_data": has_data,
            "overall_evolution": overall_evolution,
            "content_evolution": content_evolution,
            "accuracy_percentage": round(accuracy, 1),
            "total_answered": total_answered,
            "total_correct": total_correct,
            "total_incorrect": total_incorrect,
        }

    async def get_learning_path(
        self,
        *,
        student_id: str,
        institution_id: str | None = None,
        classroom_id: str | None = None,
    ) -> dict[str, Any]:
        """Builds active step-by-step learning path for student."""
        recs = await self.recommendation_engine.generate_and_resolve_recommendations(
            student_id=student_id,
            institution_id=institution_id,
            classroom_id=classroom_id,
            limit=1,
        )

        if not recs:
            return {
                "student_id": student_id,
                "content_node_id": str(uuid.uuid4()),
                "content_name": "Sem conteúdo no momento",
                "current_mastery_score": 0.0,
                "recommended_difficulty": "EASY",
                "context_source": "AUTONOMOUS",
                "reason": "Nenhuma recomendação pendente.",
                "steps": [],
                "active_step_index": 0,
            }

        r = recs[0]
        c_node_id = uuid.UUID(r["content_node_id"])

        steps = []
        has_mat = r.get("has_material", False)
        p_res = r.get("primary_resource")

        steps.append({
            "step_number": 1,
            "title": "Revisar Material Teórico",
            "step_type": "MATERIAL",
            "status": "in_progress" if has_mat else "not_available",
            "description": f"Estudar material teórico de {r['content_name']}" if has_mat else "Nenhum material disponível",
            "resource_id": p_res.get("resource_id") if p_res else None,
        })

        video_res = await self.video_engine.recommend_video_for_student(
            student_id=student_id,
            content_node_id=c_node_id,
            institution_id=institution_id,
        )
        has_vid = video_res.get("status") == "OK"

        steps.append({
            "step_number": 2,
            "title": "Assistir Vídeo Recomendado",
            "step_type": "VIDEO",
            "status": "pending" if has_vid else "not_available",
            "description": video_res["title"] if has_vid else "Nenhum vídeo disponível",
            "resource_id": video_res.get("video_resource_id") if has_vid else None,
        })

        has_q = r.get("has_questions", False)
        q_count = len(r.get("practice_questions", []))
        steps.append({
            "step_number": 3,
            "title": f"Praticar {q_count} Questões ({r['recommended_difficulty']})",
            "step_type": "PRACTICE",
            "status": "pending" if has_q else "not_available",
            "description": f"Resolver questões de nível {r['recommended_difficulty']}",
            "question_version_id": r["practice_questions"][0]["question_version_id"] if has_q else None,
        })

        steps.append({
            "step_number": 4,
            "title": "Reavaliar Domínio",
            "step_type": "REEVALUATE",
            "status": "pending",
            "description": "Medição de maestria atualizada pós-treino",
        })

        return {
            "student_id": student_id,
            "content_node_id": r["content_node_id"],
            "content_name": r["content_name"],
            "current_mastery_score": r["mastery_score"] or 0.0,
            "recommended_difficulty": r["recommended_difficulty"],
            "context_source": r["context_source"],
            "reason": r["reason"],
            "steps": steps,
            "active_step_index": 0,
        }

    # Helper methods
    @staticmethod
    def _get_period_start_date(period: str, now: datetime) -> datetime:
        p = period.lower()
        if p == "last_30_days":
            return now - timedelta(days=30)
        elif p in ("bimester", "bimestre"):
            return now - timedelta(days=60)
        elif p in ("semester", "semestre"):
            return now - timedelta(days=180)
        else:  # academic_year (default)
            return datetime(now.year, 1, 1, tzinfo=timezone.utc)

    @staticmethod
    def _get_status_label(score: float) -> str:
        if score < 50.0:
            return "Precisa melhorar"
        elif score < 70.0:
            return "Em desenvolvimento"
        else:
            return "Domínio consolidado"
