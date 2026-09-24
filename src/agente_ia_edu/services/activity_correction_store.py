"""PHASE 18 - deterministic correction of a COMPLETED activity attempt.

    execution   = ActivityAttempt + ActivityAnswer          (PHASE 17)
    correction  = ActivityResult + ActivityResultItem        (this module)
    domain map / trilha / TRI / notas                        = FUTURE - NOT here

For every item with a known content node (ContentQuestionLink) and a known
difficulty (QuestionVersion.recommended_difficulty), correction also writes a
LearningHistory row (activity_type=OFFICIAL_ASSESSMENT) - the evidence source
DomainMapService reads for the student's domain-map view. Items without
either are skipped, not backfilled with invented data. This is the only
bridge to LearningHistory added here; the domain-map READ side, trilha, TRI
and notas remain future work elsewhere.

"A correção é determinística. A IA NÃO participa. O gabarito congelado é a
autoridade."

For every frozen ``AssessmentItem`` (in its frozen position order) the student's
current ``ActivityAnswer.selected_option`` is compared to
``AssessmentItem.frozen_correct_option`` - the official answer key snapshotted at
list-publication time (PHASE 15). Nothing here reads the live official key,
infers a key from question text, or consults PedagogicalClassification. No
official table is written. The raw per-question outcome is persisted so later
phases can analyse by content / discipline / difficulty without re-correcting.

``activity_results.attempt_id`` is UNIQUE -> correction is idempotent: a second
call returns the existing result (in-process or cross-process, via the DB
constraint). The whole result + its items are written in ONE transaction.

Authorisation reuses the PHASE 16 rule verbatim
(``ActivityAssignmentStore.resolve_student_assignment``) - a student only ever
sees their own result; no new manager/teacher access is added.

AI-agnostic: imports no provider / OpenAI SDK.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models.assessments import (
    ActivityAnswer,
    ActivityAttempt,
    ActivityResult,
    ActivityResultItem,
)
from agente_ia_edu.db.models.catalog import ContentQuestionLink
from agente_ia_edu.db.models.learning_path import LearningHistory
from agente_ia_edu.db.models.official import BookletQuestion, QuestionOption, QuestionVersion
from agente_ia_edu.services.activity_assignment_store import (
    ActivityAssignmentStore,
    AssignmentAuthError,
    AssignmentNotFound,
)
from agente_ia_edu.services.learning_path_policies import ActivityType
from agente_ia_edu.services.question_list_store import (
    ListNotFoundError,
    ListStateError,
    Requester,
)

STATUS_COMPLETED = "COMPLETED"


class CorrectionError(ValueError):
    """422 - malformed request."""


class CorrectionNotFound(LookupError):
    """404."""


class CorrectionAuthError(PermissionError):
    """403."""


class CorrectionStateError(RuntimeError):
    """409 - attempt not COMPLETED."""

    def __init__(self, message: str, *, payload: dict | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


class CorrectionSnapshotError(RuntimeError):
    """409 - the activity snapshot is inconsistent; correction fails closed."""

    def __init__(self, reason: str, *, payload: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.payload = payload or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class ActivityCorrectionStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._assignments = ActivityAssignmentStore(session)

    # -- shared resolution (PHASE 16 rule) --------------------------

    async def _resolve(self, assignment_id: UUID, *, requester: Requester):
        try:
            row = await self._assignments.resolve_student_assignment(
                assignment_id, requester=requester)
        except AssignmentNotFound as exc:
            raise CorrectionNotFound(str(assignment_id)) from exc
        except AssignmentAuthError as exc:
            raise CorrectionAuthError(str(exc)) from exc
        try:
            assessment, version = await self._assignments._load_list(row.assessment_id)
        except (ListNotFoundError, ListStateError) as exc:  # pragma: no cover
            raise CorrectionNotFound(str(assignment_id)) from exc
        return row, assessment, version

    async def _load_attempt(self, assignment_id: UUID, student_id: str) -> ActivityAttempt | None:
        return (await self._session.execute(
            select(ActivityAttempt).where(
                ActivityAttempt.assignment_id == assignment_id,
                ActivityAttempt.student_external_id == student_id,
            )
        )).scalar_one_or_none()

    async def _load_result(self, attempt_id: UUID) -> ActivityResult | None:
        return (await self._session.execute(
            select(ActivityResult).where(ActivityResult.attempt_id == attempt_id)
            .options()
        )).scalar_one_or_none()

    # -- snapshot integrity (spec s5 - fail closed) ---------------

    def _validate_snapshot(self, assignment, version, answers) -> None:
        if str(assignment.assessment_version_id) != str(version.id):
            raise CorrectionSnapshotError(
                "assessment_version_id da atividade não corresponde à versão publicada",
                payload={"expected": str(assignment.assessment_version_id),
                         "found": str(version.id)})
        items = sorted(version.items, key=lambda it: it.position)
        if not items:
            raise CorrectionSnapshotError("a versão publicada não possui questões")
        if assignment.question_count and len(items) != assignment.question_count:
            raise CorrectionSnapshotError(
                "question_count congelado difere do número de itens da versão",
                payload={"frozen": assignment.question_count, "found": len(items)})
        positions = [it.position for it in items]
        if positions != list(range(1, len(items) + 1)):
            raise CorrectionSnapshotError(
                "as posições das questões não são contíguas 1..N",
                payload={"positions": positions})
        missing_key = [it.position for it in items if it.frozen_correct_option_id is None]
        if missing_key:
            raise CorrectionSnapshotError(
                "gabarito congelado ausente para uma ou mais questões",
                payload={"positions_without_key": missing_key})
        item_qvs = {str(it.question_version_id) for it in items}
        stray = sorted({str(a.question_version_id) for a in answers} - item_qvs)
        if stray:
            raise CorrectionSnapshotError(
                "há respostas para questões que não pertencem a esta atividade",
                payload={"question_version_ids": stray})

    # -- correction (deterministic, one transaction) ------------

    async def correct(self, assignment_id: UUID, *, requester: Requester) -> dict:
        assignment, assessment, version = await self._resolve(assignment_id, requester=requester)
        student_id = requester.external_user_id
        attempt = await self._load_attempt(assignment.id, student_id)
        if attempt is None:
            raise CorrectionStateError("não há tentativa para corrigir",
                                       payload={"status": "NOT_STARTED"})
        if attempt.status != STATUS_COMPLETED:
            raise CorrectionStateError(
                "somente uma tentativa COMPLETED pode ser corrigida",
                payload={"status": attempt.status})

        # capture the display context BEFORE any commit expires these instances
        activity_ctx = {
            "assignment_id": str(assignment.id),
            "assessment_id": str(assignment.assessment_id),
            "assessment_version_id": str(assignment.assessment_version_id),
            "title": assessment.title,
        }
        # Same reason: the race handler below reads this AFTER a rollback(),
        # which expires `attempt` just like a commit does - a bare `attempt.id`
        # there would need an implicit reload outside any await, raising
        # MissingGreenlet instead of gracefully returning the competitor's row.
        attempt_id = attempt.id

        existing = await self._load_result(attempt.id)
        if existing is not None:
            return await self._result_dict(activity_ctx, existing)

        items = sorted(version.items, key=lambda it: it.position)
        answers = (await self._session.execute(
            select(ActivityAnswer).where(ActivityAnswer.attempt_id == attempt.id)
        )).scalars().all()
        self._validate_snapshot(assignment, version, answers)

        # batched lookups - NO per-question query
        frozen_ids = {it.frozen_correct_option_id for it in items if it.frozen_correct_option_id}
        opt_key = dict((await self._session.execute(
            select(QuestionOption.id, QuestionOption.option_key)
            .where(QuestionOption.id.in_(frozen_ids))
        )).all()) if frozen_ids else {}
        qv_ids = [it.question_version_id for it in items]
        official_number = {}
        for vid, num in (await self._session.execute(
            select(BookletQuestion.question_version_id, BookletQuestion.official_number)
            .where(BookletQuestion.question_version_id.in_(qv_ids))
        )).all():
            official_number.setdefault(vid, num)

        # batched, same "no per-question query" discipline as above - content
        # node + difficulty for every item this attempt could produce
        # LearningHistory evidence for (domain-map reads LearningHistory, not
        # ActivityResult - see module docstring).
        content_node_by_qv: dict[UUID, UUID] = {}
        for vid, node_id in (await self._session.execute(
            select(ContentQuestionLink.question_version_id, ContentQuestionLink.content_node_id)
            .where(ContentQuestionLink.question_version_id.in_(qv_ids))
        )).all():
            content_node_by_qv.setdefault(vid, node_id)
        difficulty_by_qv = dict((await self._session.execute(
            select(QuestionVersion.id, QuestionVersion.recommended_difficulty)
            .where(QuestionVersion.id.in_(qv_ids), QuestionVersion.recommended_difficulty.isnot(None))
        )).all())

        ans_by_qv = {a.question_version_id: a for a in answers}

        correct = incorrect = unanswered = answered = 0
        result_items: list[ActivityResultItem] = []
        history_rows: list[LearningHistory] = []
        for it in items:
            correct_key = opt_key.get(it.frozen_correct_option_id)
            a = ans_by_qv.get(it.question_version_id)
            student_key = a.selected_option_key if (a and a.selected_option_id is not None) else None
            is_answered = student_key is not None
            if not is_answered:
                unanswered += 1
                is_correct = False
            else:
                answered += 1
                is_correct = (student_key == correct_key)
                if is_correct:
                    correct += 1
                else:
                    incorrect += 1
            result_items.append(ActivityResultItem(
                question_version_id=it.question_version_id,
                position=it.position,
                official_number=official_number.get(it.question_version_id),
                selected_option_key=student_key,
                correct_option_key=correct_key,
                is_correct=is_correct,
                answered=is_answered,
            ))

            content_node_id = content_node_by_qv.get(it.question_version_id)
            difficulty_level = difficulty_by_qv.get(it.question_version_id)
            if content_node_id is not None and difficulty_level is not None:
                history_rows.append(LearningHistory(
                    external_identity_id=student_id,
                    activity_type=ActivityType.OFFICIAL_ASSESSMENT.value,
                    question_version_id=it.question_version_id,
                    selected_option_id=a.selected_option_id if a else None,
                    difficulty_level=difficulty_level,
                    is_correct=is_correct if is_answered else None,
                    content_node_id=content_node_id,
                ))

        result = ActivityResult(
            attempt_id=attempt.id,
            assignment_id=assignment.id,
            student_external_id=student_id,
            assessment_version_id=version.id,
            selection_fingerprint=assignment.selection_fingerprint,
            question_count=len(items),
            answered_count=answered,
            correct_count=correct,
            incorrect_count=incorrect,
            unanswered_count=unanswered,
            completion_status=attempt.status,
            started_at=attempt.started_at,
            completed_at=attempt.completed_at,
            corrected_at=_now(),
        )
        result.items = result_items
        self._session.add(result)
        if history_rows:
            self._session.add_all(history_rows)
        try:
            # flush() - not just commit() - is where the UNIQUE(attempt_id)
            # violation actually surfaces (SQLite and PostgreSQL both check
            # UNIQUE at INSERT time, not deferred to COMMIT), so it has to be
            # inside this try too or a genuine concurrent-correction race
            # crashes correct() instead of falling back to the winner's row.
            await self._session.flush()
            new_id = result.id
            await self._session.commit()
        except IntegrityError:
            # a concurrent corrector won the UNIQUE(attempt_id) race
            await self._session.rollback()
            existing = await self._load_result(attempt_id)
            if existing is None:  # pragma: no cover - re-raise if it was a different violation
                raise
            return await self._result_dict(activity_ctx, existing)

        fresh = (await self._session.execute(
            select(ActivityResult).where(ActivityResult.id == new_id)
        )).scalar_one()
        return await self._result_dict(activity_ctx, fresh)

    # -- read (student's own result only) -----------------------

    async def get_result(self, assignment_id: UUID, *, requester: Requester) -> dict:
        assignment, assessment, _version = await self._resolve(assignment_id, requester=requester)
        activity_ctx = {
            "assignment_id": str(assignment.id),
            "assessment_id": str(assignment.assessment_id),
            "assessment_version_id": str(assignment.assessment_version_id),
            "title": assessment.title,
        }
        attempt = await self._load_attempt(assignment.id, requester.external_user_id)
        if attempt is None:
            raise CorrectionNotFound(str(assignment_id))
        result = await self._load_result(attempt.id)
        if result is None:
            raise CorrectionNotFound(str(assignment_id))
        return await self._result_dict(activity_ctx, result)

    async def _result_dict(self, activity_ctx: dict, result: ActivityResult) -> dict:
        rows = (await self._session.execute(
            select(ActivityResultItem).where(ActivityResultItem.result_id == result.id)
            .order_by(ActivityResultItem.position)
        )).scalars().all()
        qc = result.question_count or 0
        pct = round((result.correct_count / qc) * 100, 1) if qc else 0.0
        return {
            "result": {
                "id": str(result.id),
                "attempt_id": str(result.attempt_id),
                "assignment_id": str(result.assignment_id),
                "assessment_version_id": str(result.assessment_version_id),
                "student_external_id": result.student_external_id,
                "selection_fingerprint": result.selection_fingerprint,
                "question_count": result.question_count,
                "answered_count": result.answered_count,
                "correct_count": result.correct_count,
                "incorrect_count": result.incorrect_count,
                "unanswered_count": result.unanswered_count,
                "aproveitamento_percent": pct,
                "completion_status": result.completion_status,
                "started_at": _iso(result.started_at),
                "completed_at": _iso(result.completed_at),
                "corrected_at": _iso(result.corrected_at),
            },
            "activity": activity_ctx,
            "items": [
                {
                    "position": r.position,
                    "question_version_id": str(r.question_version_id),
                    "official_number": r.official_number,
                    "status": ("CORRECT" if r.is_correct
                               else ("UNANSWERED" if not r.answered else "INCORRECT")),
                    "answered": r.answered,
                    "is_correct": r.is_correct,
                    "selected_option_key": r.selected_option_key,
                    "correct_option_key": r.correct_option_key,   # released ONLY here, post-correction
                    "resolution": "em breve",
                }
                for r in rows
            ],
            "answer_key_visible": True,
        }


__all__ = [
    "ActivityCorrectionStore",
    "CorrectionAuthError",
    "CorrectionError",
    "CorrectionNotFound",
    "CorrectionSnapshotError",
    "CorrectionStateError",
    "STATUS_COMPLETED",
]
