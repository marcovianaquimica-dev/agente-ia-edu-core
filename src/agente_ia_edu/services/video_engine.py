from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models import (
    CatalogNode,
    EducationalResource,
    PedagogicalContext,
    PedagogicalRecommendation,
    StudentContentMastery,
    VideoInteractionEvent,
    VideoResourceDetail,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.learning_path_policies import DifficultyLevel

logger = logging.getLogger(__name__)


class VideoFeedbackType:
    LIKED = "LIKED"
    DISLIKED = "DISLIKED"


class VideoFeedbackReason:
    TOO_FAST = "TOO_FAST"
    TOO_SLOW = "TOO_SLOW"
    TOO_BASIC = "TOO_BASIC"
    TOO_ADVANCED = "TOO_ADVANCED"
    NOT_CLEAR = "NOT_CLEAR"
    TOO_MUCH_THEORY = "TOO_MUCH_THEORY"
    NEEDS_EXAMPLES = "NEEDS_EXAMPLES"
    NEEDS_QUESTIONS = "NEEDS_QUESTIONS"
    OTHER = "OTHER"


@dataclass
class VideoRecommendationPolicy:
    """Configurable, deterministic policy for ranking video candidates."""

    direct_node_boost: float = 35.0          # Video linked directly to target content
    subcontent_boost: float = 25.0           # Video linked to subcontent
    difficulty_match_boost: float = 20.0     # Recommended level matches student difficulty level
    school_video_boost: float = 25.0         # Video owned by student's school/institution
    author_video_boost: float = 20.0         # Video created by teacher/author
    platform_video_boost: float = 15.0       # Official platform video (Química do ENEM)
    duration_sweet_spot_boost: float = 10.0   # Video duration between 3 and 15 mins (180s - 900s)
    duration_penalty: float = -15.0          # Excessively long video (> 30 mins) for low mastery
    unwatched_boost: float = 15.0            # Student hasn't watched this video yet
    in_progress_boost: float = 20.0          # Student started video and should finish
    completed_penalty: float = -30.0         # Student completed video, favor fresh videos

    # Feedback weight adjustments
    liked_boost: float = 30.0
    disliked_penalty: float = -60.0
    too_basic_penalty: float = -40.0         # If student found previous video too basic
    too_advanced_penalty: float = -40.0      # If student found previous video too advanced
    too_fast_penalty: float = -30.0          # If student flagged fast explanations
    prefers_examples_boost: float = 25.0     # If student prefers videos with examples
    excluded_video_penalty: float = -1000.0  # Exclude current video when "Quero outro" is requested

    def rank_videos(
        self,
        videos: list[dict[str, Any]],
        *,
        target_node_id: uuid.UUID | None = None,
        target_difficulty: str | None = None,
        requester_institution_id: str | None = None,
        watched_video_ids: set[uuid.UUID] | None = None,
        video_progress: dict[uuid.UUID, float] | None = None,
        excluded_video_ids: set[uuid.UUID] | None = None,
        user_feedback: dict[uuid.UUID, dict[str, Any]] | None = None,
        student_preferences: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Rank candidate videos deterministically by score, using resource_id as tie-breaker."""
        watched_video_ids = watched_video_ids or set()
        video_progress = video_progress or {}
        excluded_video_ids = excluded_video_ids or set()
        user_feedback = user_feedback or {}
        student_preferences = student_preferences or {}

        scored = []
        for v in videos:
            res_id = uuid.UUID(v["resource_id"])
            if res_id in excluded_video_ids:
                continue

            score = 0.0

            # 1. Direct content matching
            if target_node_id and v.get("content_node_id") == str(target_node_id):
                score += self.direct_node_boost
            elif v.get("is_subcontent_match"):
                score += self.subcontent_boost

            # 2. Origin & Ownership
            if requester_institution_id and v.get("owner_external_id") == requester_institution_id:
                score += self.school_video_boost

            origin = (v.get("origin_type") or "").upper()
            if origin in ("AUTHOR", "SCHOOL"):
                score += self.author_video_boost
            elif origin == "PLATFORM":
                score += self.platform_video_boost

            # 3. Difficulty Match
            rec_level = v.get("recommended_level")
            if target_difficulty and rec_level == target_difficulty:
                score += self.difficulty_match_boost

            # 4. Duration Optimization
            video_detail = v.get("video_detail") or {}
            dur = video_detail.get("duration_seconds")
            if dur:
                if 180 <= dur <= 900:  # 3-15 minutes
                    score += self.duration_sweet_spot_boost
                elif dur > 1800 and target_difficulty == "EASY":  # > 30 mins for EASY level
                    score += self.duration_penalty

            # 5. Watch Status / History
            prog = video_progress.get(res_id, 0.0)
            if prog >= 100.0:
                score += self.completed_penalty
            elif prog > 0.0:
                score += self.in_progress_boost
            elif res_id not in watched_video_ids:
                score += self.unwatched_boost

            # 6. Direct Video Feedback adjustments
            if res_id in user_feedback:
                fb = user_feedback[res_id]
                fb_type = (fb.get("type") or "").upper()
                fb_reason = (fb.get("reason") or "").upper()

                if fb_type == VideoFeedbackType.LIKED:
                    score += self.liked_boost
                elif fb_type == VideoFeedbackType.DISLIKED:
                    score += self.disliked_penalty

                if fb_reason in (VideoFeedbackReason.TOO_BASIC, VideoFeedbackReason.TOO_ADVANCED):
                    score += self.too_basic_penalty
                elif fb_reason == VideoFeedbackReason.TOO_FAST:
                    score += self.too_fast_penalty

            # 7. Student Preference Evidence Signals (behavioral trends)
            title_desc = f"{v.get('title', '')} {v.get('description', '')}".lower()

            if student_preferences.get(VideoFeedbackReason.TOO_FAST, 0) > 0:
                if dur and dur < 300:  # Fast / short video
                    score += self.too_fast_penalty

            if student_preferences.get(VideoFeedbackReason.TOO_BASIC, 0) > 0:
                if rec_level == "EASY":
                    score += self.too_basic_penalty

            if student_preferences.get(VideoFeedbackReason.TOO_ADVANCED, 0) > 0:
                if rec_level == "HARD":
                    score += self.too_basic_penalty

            if student_preferences.get(VideoFeedbackReason.NEEDS_EXAMPLES, 0) > 0:
                if "exemplo" in title_desc or "exercício" in title_desc:
                    score += self.prefers_examples_boost

            item = dict(v)
            item["video_score"] = score
            scored.append(item)

        # Deterministic sort: score descending, then resource_id ascending (tie-breaker)
        scored.sort(key=lambda x: (-x["video_score"], x["resource_id"]))
        return scored


class VideoRecommendationEngine:
    """Specialized engine for finding, ranking, and explaining video recommendations."""

    def __init__(
        self,
        session: AsyncSession,
        knowledge_service: KnowledgeService,
        policy: VideoRecommendationPolicy | None = None,
    ):
        self.session = session
        self.knowledge_service = knowledge_service
        self.policy = policy or VideoRecommendationPolicy()

    async def recommend_video_for_student(
        self,
        *,
        student_id: str,
        content_node_id: uuid.UUID,
        institution_id: str | None = None,
        excluded_video_ids: set[uuid.UUID] | None = None,
    ) -> dict[str, Any]:
        """Finds and ranks video candidates for a student on a specific content node."""
        excluded_video_ids = excluded_video_ids or set()

        node = await self.session.get(CatalogNode, content_node_id)
        if not node:
            return {
                "status": "NO_VIDEO_AVAILABLE",
                "reason": "Conteúdo não encontrado no catálogo.",
                "video": None,
            }

        # 1. Determine student mastery & difficulty
        stmt_mastery = select(StudentContentMastery).where(
            StudentContentMastery.external_identity_id == student_id,
            StudentContentMastery.content_node_id == content_node_id,
        )
        res_mastery = await self.session.execute(stmt_mastery)
        mastery = res_mastery.scalar_one_or_none()
        mastery_score = float(mastery.mastery_score) if mastery else 0.0

        if mastery_score < 50.0:
            target_difficulty = DifficultyLevel.EASY.value
        elif mastery_score < 70.0:
            target_difficulty = DifficultyLevel.MEDIUM.value
        else:
            target_difficulty = DifficultyLevel.HARD.value

        # 2. Fetch video resources via Knowledge Layer
        raw_resources = await self.knowledge_service.find_resources_by_content(
            node.name,
            resource_type="VIDEO",
            requester_institution_id=institution_id,
        )

        if not raw_resources:
            return {
                "status": "NO_VIDEO_AVAILABLE",
                "reason": f"Nenhum vídeo disponível no catálogo para o conteúdo '{node.name}'.",
                "content_node_id": str(content_node_id),
                "recommended_difficulty": target_difficulty,
                "video": None,
            }

        # 3. Fetch student history (events, progress & feedback)
        watched_ids, video_progress, user_feedback, preferences = await self._fetch_student_video_history(student_id)

        # 4. Rank videos deterministically
        ranked_videos = self.policy.rank_videos(
            raw_resources,
            target_node_id=content_node_id,
            target_difficulty=target_difficulty,
            requester_institution_id=institution_id,
            watched_video_ids=watched_ids,
            video_progress=video_progress,
            excluded_video_ids=excluded_video_ids,
            user_feedback=user_feedback,
            student_preferences=preferences,
        )

        if not ranked_videos:
            return {
                "status": "NO_VIDEO_AVAILABLE",
                "reason": f"Todos os vídeos disponíveis para '{node.name}' foram excluídos ou assistidos.",
                "content_node_id": str(content_node_id),
                "recommended_difficulty": target_difficulty,
                "video": None,
            }

        best = ranked_videos[0]
        stmt = (
            select(EducationalResource)
            .where(EducationalResource.id == uuid.UUID(best["resource_id"]))
            .options(selectinload(EducationalResource.video_detail))
        )
        res_res = await self.session.execute(stmt)
        res_obj = res_res.scalar_one_or_none()

        if not res_obj:
            return {
                "status": "NO_VIDEO_AVAILABLE",
                "reason": "Erro ao carregar detalhes do recurso de vídeo.",
                "video": None,
            }

        # Detailed audit explanation
        dur_str = f" ({res_obj.video_detail.duration_seconds // 60} min)" if res_obj.video_detail and res_obj.video_detail.duration_seconds else ""
        explanation = (
            f"Recomendamos o vídeo '{res_obj.title}'{dur_str} porque ele está diretamente "
            f"relacionado a '{node.name}', possui nível {target_difficulty} adequado ao seu "
            f"domínio atual ({mastery_score:.1f}%) e apresenta a melhor pontuação pedagógica."
        )

        return {
            "status": "OK",
            "video_resource_id": str(res_obj.id),
            "title": res_obj.title,
            "platform": res_obj.video_detail.platform if res_obj.video_detail else "PLATFORM",
            "external_video_id": res_obj.video_detail.external_video_id if res_obj.video_detail else None,
            "duration_seconds": res_obj.video_detail.duration_seconds if res_obj.video_detail else None,
            "source_url": res_obj.source_url,
            "score": best["video_score"],
            "rank": 1,
            "content_node_id": str(content_node_id),
            "recommended_difficulty": target_difficulty,
            "reason": explanation,
            "context_source": "AUTONOMOUS",
            "audit_factors": {
                "matched_content": node.name,
                "mastery_score": mastery_score,
                "target_difficulty": target_difficulty,
                "origin_type": res_obj.origin_type,
                "visibility_scope": res_obj.visibility_scope,
                "owner_external_id": res_obj.owner_external_id,
                "student_preferences": preferences,
            },
            "candidate_count": len(ranked_videos),
            "video_object": res_obj,
        }

    async def request_another_video(
        self,
        *,
        student_id: str,
        content_node_id: uuid.UUID,
        current_video_id: uuid.UUID,
        feedback_type: str | None = None,
        feedback_reason: str | None = None,
        institution_id: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Request another video for a content, recording feedback and excluding the current video."""
        if feedback_type or feedback_reason:
            from agente_ia_edu.services.recommendation import ResourceTrackingService
            tracking = ResourceTrackingService(self.session)
            await tracking.record_interaction(
                student_id=student_id,
                resource_id=current_video_id,
                action_type="FEEDBACK",
                content_node_id=content_node_id,
                feedback_type=feedback_type,
                feedback_reason=feedback_reason,
                event_id=event_id,
            )

        return await self.recommend_video_for_student(
            student_id=student_id,
            content_node_id=content_node_id,
            institution_id=institution_id,
            excluded_video_ids={current_video_id},
        )

    async def get_student_video_progress(
        self,
        *,
        student_id: str,
        resource_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Query student progress and feedback on a specific video."""
        stmt = (
            select(VideoInteractionEvent)
            .where(
                VideoInteractionEvent.student_id == student_id,
                VideoInteractionEvent.resource_id == resource_id,
            )
            .order_by(VideoInteractionEvent.created_at.desc())
        )
        res = await self.session.execute(stmt)
        events = res.scalars().all()

        if not events:
            return {
                "student_id": student_id,
                "resource_id": str(resource_id),
                "status": "UNWATCHED",
                "progress_percentage": 0.0,
                "last_interaction_at": None,
                "feedback_type": None,
                "feedback_reason": None,
            }

        max_prog = 0.0
        latest_fb_type = None
        latest_fb_reason = None
        last_at = events[0].created_at.isoformat()

        for e in events:
            if e.progress_percentage is not None:
                max_prog = max(max_prog, float(e.progress_percentage))
            if e.event_type == "FEEDBACK" and not latest_fb_type:
                latest_fb_type = e.feedback_type
                latest_fb_reason = e.feedback_reason

        if max_prog >= 100.0 or any(e.event_type == "COMPLETED" for e in events):
            status = "COMPLETED"
            max_prog = 100.0
        elif max_prog > 0.0 or any(e.event_type in ("OPENED", "STARTED", "PROGRESS") for e in events):
            status = "IN_PROGRESS"
        else:
            status = "UNWATCHED"

        return {
            "student_id": student_id,
            "resource_id": str(resource_id),
            "status": status,
            "progress_percentage": max_prog,
            "last_interaction_at": last_at,
            "feedback_type": latest_fb_type,
            "feedback_reason": latest_fb_reason,
        }

    async def _fetch_student_video_history(
        self,
        student_id: str,
    ) -> tuple[
        set[uuid.UUID],
        dict[uuid.UUID, float],
        dict[uuid.UUID, dict[str, Any]],
        dict[str, int],
    ]:
        """Fetch previously watched videos, progress, feedback, and derived preferences for student."""
        # 1. Query VideoInteractionEvent DB table
        stmt_events = (
            select(VideoInteractionEvent)
            .where(VideoInteractionEvent.student_id == student_id)
            .order_by(VideoInteractionEvent.created_at.desc())
        )
        res_events = await self.session.execute(stmt_events)
        events = res_events.scalars().all()

        watched_ids: set[uuid.UUID] = set()
        video_progress: dict[uuid.UUID, float] = {}
        user_feedback: dict[uuid.UUID, dict[str, Any]] = {}
        preferences: dict[str, int] = {}

        for e in events:
            res_id = e.resource_id
            watched_ids.add(res_id)

            if e.progress_percentage is not None:
                curr = video_progress.get(res_id, 0.0)
                video_progress[res_id] = max(curr, float(e.progress_percentage))

            if e.event_type == "FEEDBACK":
                if res_id not in user_feedback:
                    user_feedback[res_id] = {
                        "type": e.feedback_type,
                        "reason": e.feedback_reason,
                    }
                if e.feedback_reason:
                    reason_key = e.feedback_reason.upper()
                    preferences[reason_key] = preferences.get(reason_key, 0) + 1

        # 2. Query PedagogicalRecommendation as fallback
        stmt_recs = (
            select(PedagogicalRecommendation)
            .where(PedagogicalRecommendation.student_id == student_id)
        )
        res_recs = await self.session.execute(stmt_recs)
        recs = res_recs.scalars().all()

        for r in recs:
            if r.resource_id:
                watched_ids.add(r.resource_id)

            meta = r.metadata_ or {}
            interactions = meta.get("interactions", [])
            for inter in interactions:
                res_id_str = inter.get("resource_id")
                if res_id_str:
                    u_id = uuid.UUID(res_id_str)
                    watched_ids.add(u_id)
                    if inter.get("progress") is not None:
                        curr = video_progress.get(u_id, 0.0)
                        video_progress[u_id] = max(curr, float(inter["progress"]))
                    if inter.get("action") == "FEEDBACK" and u_id not in user_feedback:
                        fb_t = inter.get("feedback_type")
                        fb_r = inter.get("feedback_reason")
                        user_feedback[u_id] = {"type": fb_t, "reason": fb_r}
                        if fb_r:
                            r_key = fb_r.upper()
                            preferences[r_key] = preferences.get(r_key, 0) + 1

        return watched_ids, video_progress, user_feedback, preferences

