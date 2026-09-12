"""PHASE 22 - Adaptive Practice Engine (closes the first real adaptive loop).

    learning path  = AdaptiveLearningPathService                (PHASE 21, decision)
    practice       = this service                               (this phase, execution)
    -> reuses: QuestionBankService/QuestionSelection (PHASE 12/14) for selection,
              QuestionListStore (PHASE 15) to build the list,
              ActivityAssignmentStore (PHASE 16) to distribute it (origin=PRACTICE),
              the PHASE 17 Player, PHASE 18 Correction, PHASE 19 Analysis,
              PHASE 20 Domain Map (origin_breakdown["PRACTICE"]), PHASE 21 path.

RECOMMENDATION -> PRACTICE -> ANSWER -> CORRECTION -> RESULT -> DOMAIN -> NEW PATH.

A practice is an ordinary EXERCISE_LIST Activity distributed to the STUDENT
themself, tagged ``origin = PRACTICE`` on the ActivityAssignment metadata so its
evidence is kept SEPARATE from OFFICIAL_ACTIVITY in the Domain Map. No new
Question Bank, no question copies, no new player, no new correction. No AI.

Selection is deterministic (``PracticeSelectionPolicy``): ACTIVE curriculum-v2
questions for the exact ``content_code``, definitive classification preferred,
visual-dependency and protected questions excluded, and the student's most
recently practised questions de-prioritised (but re-allowed if the pool is
small). It never invents questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models.assessments import (
    ActivityAssignment,
    ActivityAttempt,
    ActivityResult,
    ActivityResultItem,
    Assessment,
    AssessmentVersion,
)
from agente_ia_edu.db.models.catalog import CatalogNode
from agente_ia_edu.services.activity_assignment_store import ActivityAssignmentStore
from agente_ia_edu.services.curriculum_domain_map import ORIGIN_PRACTICE
from agente_ia_edu.services.list_generator import ListConfiguration
from agente_ia_edu.services.question_bank import QuestionBankFilters, QuestionBankService
from agente_ia_edu.services.question_list_store import (
    LIST_MATERIAL_TYPE,
    QuestionListStore,
    Requester,
)

# practice modes - only PRACTICE_CONTENT is executed in this phase; the rest are
# reserved contract values a later phase can wire without a new endpoint.
MODE_CONTENT = "PRACTICE_CONTENT"
MODE_REVIEW = "PRACTICE_REVIEW"
MODE_PREREQUISITE = "PRACTICE_PREREQUISITE"
MODE_MIXED = "PRACTICE_MIXED"
SUPPORTED_MODES = (MODE_CONTENT,)
CONTRACT_MODES = (MODE_CONTENT, MODE_REVIEW, MODE_PREREQUISITE, MODE_MIXED)

ALLOWED_COUNTS = (5, 10, 15, 20)
MAX_QUESTIONS = 20
MIN_QUESTIONS = 1
RECENT_EXCLUDE_DEFAULT = 30

# practice lifecycle - mapped from the reused ActivityAttempt / correction state
STATE_CREATED = "PRACTICE_CREATED"
STATE_IN_PROGRESS = "PRACTICE_IN_PROGRESS"
STATE_COMPLETED = "PRACTICE_COMPLETED"
STATE_CORRECTED = "PRACTICE_CORRECTED"


class PracticeError(ValueError):
    """422."""

    def __init__(self, message: str, *, payload: dict | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


class PracticeNotFound(LookupError):
    """404."""


class PracticeAuthError(PermissionError):
    """403."""


@dataclass(frozen=True)
class PracticeSelectionPolicy:
    """Deterministic question-selection strategy. Swap for a different policy
    later without touching the service."""

    prefer_definitive: bool = True          # exclude NEEDS_REVIEW / FORCED_CLOSURE / LOW
    exclude_visual_dependency: bool = True
    exclude_protected: bool = True
    recent_exclude: int = RECENT_EXCLUDE_DEFAULT

    @classmethod
    def default(cls) -> "PracticeSelectionPolicy":
        return cls()

    def as_dict(self) -> dict:
        return {
            "PREFER_DEFINITIVE": self.prefer_definitive,
            "EXCLUDE_VISUAL_DEPENDENCY": self.exclude_visual_dependency,
            "EXCLUDE_PROTECTED": self.exclude_protected,
            "RECENT_EXCLUDE": self.recent_exclude,
        }

    def rank(self, items: list) -> list:
        """Stable, deterministic order: official_number, then question_version_id."""
        return sorted(items, key=lambda it: (
            it.official_number if it.official_number is not None else 1_000_000,
            str(it.question_version_id),
        ))

    def choose(self, ranked_vids: list[str], *, count: int,
               recent_ids: set[str]) -> tuple[list[str], int]:
        """Pick ``count`` ids, de-prioritising the student's recent questions but
        re-allowing them (in order) if the fresh pool is too small. Returns
        (selected, reused_recent_count)."""
        fresh = [v for v in ranked_vids if v not in recent_ids]
        recent = [v for v in ranked_vids if v in recent_ids]
        picked = fresh[:count]
        reused = 0
        if len(picked) < count:
            fill = recent[: count - len(picked)]
            reused = len(fill)
            picked = picked + fill
        return picked, reused


class AdaptivePracticeService:
    """Deterministic. AI-agnostic. Reuses PHASES 12-21; adds no table."""

    def __init__(self, session: AsyncSession,
                 policy: PracticeSelectionPolicy | None = None) -> None:
        self._session = session
        self._bank = QuestionBankService(session)
        self._lists = QuestionListStore(session)
        self._assignments = ActivityAssignmentStore(session)
        self.policy = policy or PracticeSelectionPolicy.default()

    # ---- create ----------------------------------------------------

    async def create_practice(self, student_external_id: str, *, requester: Requester,
                              content_code: str, mode: str = MODE_CONTENT,
                              question_count: int = 10) -> dict:
        self._authz_self(student_external_id, requester)
        mode = (mode or MODE_CONTENT).upper()
        if mode not in SUPPORTED_MODES:
            raise PracticeError(
                f"modo de prática ainda não disponível: {mode!r} (use {MODE_CONTENT}).")
        try:
            requested = int(question_count)
        except (TypeError, ValueError):
            raise PracticeError("question_count inválido")
        if requested < MIN_QUESTIONS:
            raise PracticeError(f"question_count deve ser >= {MIN_QUESTIONS}")
        requested = min(requested, MAX_QUESTIONS)          # clamp to the safe maximum

        node = (await self._session.execute(
            select(CatalogNode).where(CatalogNode.code == content_code,
                                      CatalogNode.active.is_(True))
        )).scalar_one_or_none()
        if node is None or (node.node_type or "").upper() not in ("CONTENT", "SUBCONTENT"):
            raise PracticeError(f"conteúdo curricular inválido: {content_code!r}")
        content_name = node.name

        # -- select official questions for this content (batched) --
        await self._bank._load_catalog()
        filters = QuestionBankFilters(
            content_code=content_code,
            classification_state="CLASSIFIED" if self.policy.prefer_definitive else "ANY_CLASSIFIED",
        )
        page = await self._bank.list_questions(filters, page=1, page_size=200,
                                               order_by="official_number", order_direction="asc")
        items = list(page.items)
        excluded_visual = excluded_protected = 0
        kept = []
        for it in items:
            if self.policy.exclude_visual_dependency and it.has_visual_dependency:
                excluded_visual += 1
                continue
            if self.policy.exclude_protected and it.is_protected:
                excluded_protected += 1
                continue
            kept.append(it)
        available = len(kept)

        recent_ids = await self._recent_question_ids(student_external_id, self.policy.recent_exclude)
        ranked = [str(it.question_version_id) for it in self.policy.rank(kept)]
        selected, reused_recent = self.policy.choose(
            ranked, count=requested, recent_ids=recent_ids)

        selection_report = {
            "available_questions": available,
            "requested_questions": requested,
            "selected_questions": len(selected),
            "excluded_visual_dependency": excluded_visual,
            "excluded_protected": excluded_protected,
            "reused_recent_questions": reused_recent,
            "recent_pool_excluded": len(recent_ids),
            "policy": self.policy.as_dict(),
        }
        # Insufficient bank: never invent questions and never silently shrink the
        # practice. Report the three counts and the standard explanation so the
        # student can pick a smaller size (spec s10 / s32).
        if len(selected) < requested:
            selection_report["sufficient"] = False
            raise PracticeError(
                "Não há questões suficientes disponíveis para esta prática.",
                payload=selection_report)
        selection_report["sufficient"] = True

        # -- build the list (reuse PHASE 15) + distribute to the student (PHASE 16) --
        owner = Requester(external_user_id=student_external_id,
                          school_id=requester.school_id, role=requester.role,
                          is_platform_admin=requester.is_platform_admin)
        config = ListConfiguration(
            title=f"Prática — {content_name}",
            instructions="Prática de estudo. Este resultado não é uma nota escolar.",
            answer_key_presentation="KEY_AT_END",
        )
        summary = await self._lists.create(
            configuration=config,
            question_version_ids=[UUID(v) for v in selected],
            requester=owner,
            institution_id=str(requester.school_id) if requester.school_id else None,
        )
        list_id = UUID(str(summary.id))
        await self._lists.finalize(list_id, requester=owner)

        view, _existed = await self._assignments.create(
            list_id, requester=owner,
            target_type="STUDENT", target_id=student_external_id,
            available_from="2000-01-01T00:00:00Z",
            origin=ORIGIN_PRACTICE,
            extra_metadata={"practice": True, "mode": mode, "content_code": content_code},
        )
        return {
            "practice_id": view.id,
            "assignment_id": view.id,
            "student_external_id": student_external_id,
            "mode": mode,
            "origin": ORIGIN_PRACTICE,
            "content_code": content_code,
            "content_name": content_name,
            "state": STATE_CREATED,
            "question_count": len(selected),
            "availability": view.availability,
            "selection": selection_report,
            "ai_used": False,
        }

    # ---- read ------------------------------------------------------

    async def list_practices(self, student_external_id: str, *, requester: Requester) -> dict:
        self._authz_self(student_external_id, requester)
        rows = (await self._session.execute(
            select(ActivityAssignment, Assessment.title)
            .join(Assessment, Assessment.id == ActivityAssignment.assessment_id)
            .where(ActivityAssignment.target_type == "STUDENT",
                   ActivityAssignment.target_id == student_external_id)
            .order_by(ActivityAssignment.created_at.desc())
        )).all()
        practices = [r for r in rows if ((r[0].metadata_ or {}).get("practice") is True)]
        assignment_ids = [r[0].id for r in practices]
        state_by_assignment = await self._states(assignment_ids, student_external_id)
        return {
            "student_external_id": student_external_id,
            "items": [self._summary(a, title, state_by_assignment.get(a.id, STATE_CREATED))
                      for a, title in practices],
        }

    async def get_practice(self, student_external_id: str, practice_id: UUID, *,
                           requester: Requester) -> dict:
        self._authz_self(student_external_id, requester)
        row = (await self._session.execute(
            select(ActivityAssignment, Assessment.title)
            .join(Assessment, Assessment.id == ActivityAssignment.assessment_id)
            .where(ActivityAssignment.id == practice_id)
        )).first()
        if row is None:
            raise PracticeNotFound(str(practice_id))
        a, title = row
        if (a.target_type != "STUDENT" or a.target_id != student_external_id
                or (a.metadata_ or {}).get("practice") is not True):
            raise PracticeAuthError("this practice does not belong to you")
        state_by = await self._states([a.id], student_external_id)
        detail = self._summary(a, title, state_by.get(a.id, STATE_CREATED))
        result = (await self._session.execute(
            select(ActivityResult).where(
                ActivityResult.assignment_id == a.id,
                ActivityResult.student_external_id == student_external_id)
        )).scalar_one_or_none()
        if result is not None:
            qc = result.question_count or 0
            detail["result"] = {
                "question_count": result.question_count,
                "answered_count": result.answered_count,
                "correct_count": result.correct_count,
                "incorrect_count": result.incorrect_count,
                "unanswered_count": result.unanswered_count,
                "aproveitamento_percent": round((result.correct_count / qc) * 100, 1) if qc else 0.0,
                "corrected_at": result.corrected_at.isoformat() if result.corrected_at else None,
            }
        return detail

    # ---- helpers -------------------------------------------------

    @staticmethod
    def _authz_self(student_external_id: str, requester: Requester) -> None:
        if requester.external_user_id != student_external_id and not requester.is_platform_admin:
            raise PracticeAuthError("a student can only create/read their own practice")

    async def _recent_question_ids(self, student_external_id: str, limit: int) -> set[str]:
        if limit <= 0:
            return set()
        rows = (await self._session.execute(
            select(ActivityResultItem.question_version_id, ActivityResult.completed_at)
            .join(ActivityResult, ActivityResult.id == ActivityResultItem.result_id)
            .where(ActivityResult.student_external_id == student_external_id)
            .order_by(ActivityResult.completed_at.desc())
            .limit(max(limit * 4, 60))
        )).all()
        out: list[str] = []
        seen: set[str] = set()
        for vid, _ in rows:
            v = str(vid)
            if v not in seen:
                seen.add(v)
                out.append(v)
            if len(out) >= limit:
                break
        return set(out)

    async def _states(self, assignment_ids: list, student_external_id: str) -> dict:
        if not assignment_ids:
            return {}
        att = {a: st for a, st in (await self._session.execute(
            select(ActivityAttempt.assignment_id, ActivityAttempt.status)
            .where(ActivityAttempt.assignment_id.in_(assignment_ids),
                   ActivityAttempt.student_external_id == student_external_id)
        )).all()}
        corrected = set((await self._session.execute(
            select(ActivityResult.assignment_id).where(
                ActivityResult.assignment_id.in_(assignment_ids),
                ActivityResult.student_external_id == student_external_id)
        )).scalars().all())
        out = {}
        for aid_ in assignment_ids:
            if aid_ in corrected:
                out[aid_] = STATE_CORRECTED
            elif att.get(aid_) == "COMPLETED":
                out[aid_] = STATE_COMPLETED
            elif att.get(aid_) == "IN_PROGRESS":
                out[aid_] = STATE_IN_PROGRESS
            else:
                out[aid_] = STATE_CREATED
        return out

    @staticmethod
    def _summary(a: ActivityAssignment, title: str, state: str) -> dict:
        md = a.metadata_ or {}
        return {
            "practice_id": str(a.id),
            "assignment_id": str(a.id),
            "title": title,
            "mode": md.get("mode", MODE_CONTENT),
            "content_code": md.get("content_code"),
            "origin": md.get("origin", ORIGIN_PRACTICE),
            "question_count": a.question_count,
            "state": state,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }


__all__ = [
    "ALLOWED_COUNTS",
    "AdaptivePracticeService",
    "MAX_QUESTIONS",
    "MODE_CONTENT",
    "PracticeAuthError",
    "PracticeError",
    "PracticeNotFound",
    "PracticeSelectionPolicy",
    "STATE_CORRECTED",
    "STATE_CREATED",
]
