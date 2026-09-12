"""PHASE 17 - student activity player: execution state for a distributed activity.

    list content   = Assessment / AssessmentVersion / AssessmentItem   (PHASE 15)
    distribution   = ActivityAssignment                                (PHASE 16)
    execution      = ActivityAttempt + ActivityAnswer                  (this module)
    correction     = FUTURE - NOT here

"O aluno executa a atividade; o sistema registra o estado da execução."

This module records ONLY execution state: which questions the student has an
answer for and what that current answer is, plus the attempt lifecycle
(IN_PROGRESS -> COMPLETED). It computes NO score, percentage, correctness,
ranking or pedagogical result, and it NEVER returns the answer key to the
student. The frozen PHASE 16 snapshot (assessment_version_id + selection order +
question_count + selection_fingerprint) is the single source of truth for the
question set and its order - the activity is never rebuilt from a mutable
selection.

Authorisation reuses the PHASE 16 rule verbatim
(``ActivityAssignmentStore.resolve_student_assignment``) - no parallel authz.

AI-agnostic: imports no provider / OpenAI SDK.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models.assessments import (
    ActivityAnswer,
    ActivityAssignment,
    ActivityAttempt,
)
from agente_ia_edu.db.models.official import QuestionOption, QuestionVersion
from agente_ia_edu.services.activity_assignment_store import (
    AVAIL_OPEN,
    ActivityAssignmentStore,
    AssignmentAuthError,
    AssignmentNotFound,
    availability,
)
from agente_ia_edu.services.question_bank import QuestionBankService
from agente_ia_edu.services.question_list_store import (
    ListNotFoundError,
    ListStateError,
    Requester,
)

STATUS_NOT_STARTED = "NOT_STARTED"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETED = "COMPLETED"


class PlayerError(ValueError):
    """422 - a malformed request (unknown question, bad option, ...)."""


class PlayerNotFound(LookupError):
    """404."""


class PlayerAuthError(PermissionError):
    """403."""


class PlayerStateError(RuntimeError):
    """409 - not available / already completed / incomplete finalisation."""

    def __init__(self, message: str, *, payload: dict | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class ActivityPlayerStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._assignments = ActivityAssignmentStore(session)
        self._bank = QuestionBankService(session)

    # -- shared resolution -------------------------------------------------

    async def _resolve(self, assignment_id: UUID, *, requester: Requester):
        """(assignment_row, assessment, version, availability_label) for an
        authorised student recipient. Raises the PHASE 16 auth errors, remapped
        to this module's types by the route layer."""
        try:
            row = await self._assignments.resolve_student_assignment(
                assignment_id, requester=requester)
        except AssignmentNotFound as exc:
            raise PlayerNotFound(str(assignment_id)) from exc
        except AssignmentAuthError as exc:
            raise PlayerAuthError(str(exc)) from exc
        try:
            assessment, version = await self._assignments._load_list(row.assessment_id)
        except (ListNotFoundError, ListStateError) as exc:  # pragma: no cover - snapshot broken
            raise PlayerNotFound(str(assignment_id)) from exc
        return row, assessment, version, availability(row)

    async def _load_attempt(self, assignment_id: UUID, student_id: str) -> ActivityAttempt | None:
        return (await self._session.execute(
            select(ActivityAttempt).where(
                ActivityAttempt.assignment_id == assignment_id,
                ActivityAttempt.student_external_id == student_id,
            )
        )).scalar_one_or_none()

    # -- start -----------------------------------------------------------

    async def start(self, assignment_id: UUID, *, requester: Requester) -> dict:
        row, assessment, version, avail = await self._resolve(assignment_id, requester=requester)
        student_id = requester.external_user_id

        attempt = await self._load_attempt(row.id, student_id)
        if attempt is not None:
            if attempt.status == STATUS_COMPLETED:
                # no new attempt, no reopen in this phase
                return await self._state(assignment_id, requester=requester)
            # idempotent: reuse the IN_PROGRESS attempt, just touch it
            attempt.last_activity_at = _now()
            if attempt.started_at is None:
                attempt.started_at = _now()
            if attempt.status != STATUS_IN_PROGRESS:
                attempt.status = STATUS_IN_PROGRESS
            await self._session.commit()
            return await self._state(assignment_id, requester=requester)

        if avail != AVAIL_OPEN:
            raise PlayerStateError(
                "a atividade não está disponível para início",
                payload={"availability": avail})
        if not version.items:
            raise PlayerStateError("a atividade não possui questões")

        now = _now()
        attempt = ActivityAttempt(
            assignment_id=row.id, student_external_id=student_id,
            status=STATUS_IN_PROGRESS, started_at=now, last_activity_at=now,
            current_position=1)
        self._session.add(attempt)
        try:
            await self._session.commit()
        except IntegrityError:
            # a concurrent start won the UNIQUE(assignment_id, student) race
            await self._session.rollback()
        return await self._state(assignment_id, requester=requester)

    # -- state ---------------------------------------------------------

    async def get_state(self, assignment_id: UUID, *, requester: Requester) -> dict:
        return await self._state(assignment_id, requester=requester)

    async def _question_payload(self, version) -> list[dict]:
        ordered = sorted(version.items, key=lambda it: it.position)
        vids = [it.question_version_id for it in ordered]
        # ONE batched bank load - no N+1
        items = await self._bank.get_questions_by_version_ids(vids)
        by_vid = {it.question_version_id: it for it in items}
        out: list[dict] = []
        for it in ordered:
            q = by_vid.get(it.question_version_id)
            options = [
                {"key": o.key, "position": o.position, "text": o.text}   # NEVER is_valid_option
                for o in sorted(q.options, key=lambda o: o.position)
            ] if q else []
            out.append({
                "position": it.position,
                "question_version_id": str(it.question_version_id),
                "question_id": str(q.question_id) if q else None,
                "year": q.year if q else None,
                "official_number": q.official_number if q else None,
                "enem_area": q.enem_area if q else None,
                "statement": (q.statement or q.canonical_text) if q else "",
                "options": options,
            })
        return out

    async def _state(self, assignment_id: UUID, *, requester: Requester) -> dict:
        row, assessment, version, avail = await self._resolve(assignment_id, requester=requester)
        attempt = await self._load_attempt(row.id, requester.external_user_id)

        questions = await self._question_payload(version)
        total = len(questions)

        answers: dict[str, dict] = {}
        if attempt is not None:
            rows = (await self._session.execute(
                select(ActivityAnswer).where(ActivityAnswer.attempt_id == attempt.id)
            )).scalars().all()
            answers = {
                str(a.question_version_id): {
                    "selected_option": a.selected_option_key,
                    "answered_at": _iso(a.answered_at),
                }
                for a in rows if a.selected_option_id is not None
            }

        for q in questions:
            a = answers.get(q["question_version_id"])
            q["answered"] = a is not None
            q["selected_option"] = a["selected_option"] if a else None
            q["answered_at"] = a["answered_at"] if a else None

        answered_count = sum(1 for q in questions if q["answered"])
        pending_count = total - answered_count
        pending_positions = [q["position"] for q in questions if not q["answered"]]

        status = attempt.status if attempt is not None else STATUS_NOT_STARTED
        current_position = None
        if attempt is not None:
            current_position = attempt.current_position
        if not current_position:
            current_position = (pending_positions[0] if pending_positions else 1) if total else None

        return {
            "activity": {
                "assignment_id": str(row.id),
                "assessment_id": str(row.assessment_id),
                "assessment_version_id": str(row.assessment_version_id),
                "title": assessment.title,
                "instructions": assessment.description,
                "author_external_id": assessment.owner_external_id,
                "availability": avail,
                "available_from": _iso(row.available_from),
                "due_at": _iso(row.due_at),
                "selection_fingerprint": row.selection_fingerprint,
                "question_count": row.question_count,
            },
            "attempt": {
                "id": str(attempt.id) if attempt is not None else None,
                "status": status,
                "started_at": _iso(attempt.started_at) if attempt is not None else None,
                "last_activity_at": _iso(attempt.last_activity_at) if attempt is not None else None,
                "completed_at": _iso(attempt.completed_at) if attempt is not None else None,
            },
            "status": status,
            "editable": status == STATUS_IN_PROGRESS,
            "total_questions": total,
            "answered_count": answered_count,
            "pending_count": pending_count,
            "pending_positions": pending_positions,
            "current_position": current_position,
            "questions": questions,
            "answer_key_visible": False,
        }

    # -- save answer (autosave) --------------------------------------

    async def save_answer(
        self, assignment_id: UUID, question_version_id: UUID, *,
        requester: Requester, selected_option: str | None,
    ) -> dict:
        row, assessment, version, avail = await self._resolve(assignment_id, requester=requester)
        attempt = await self._load_attempt(row.id, requester.external_user_id)
        if attempt is None:
            raise PlayerStateError("inicie a atividade antes de responder",
                                   payload={"status": STATUS_NOT_STARTED})
        if attempt.status == STATUS_COMPLETED:
            raise PlayerStateError("a atividade já foi finalizada e não pode ser alterada",
                                   payload={"status": STATUS_COMPLETED})
        if avail != AVAIL_OPEN:
            raise PlayerStateError("a atividade não está disponível para edição",
                                   payload={"availability": avail})

        item = next((it for it in version.items
                     if str(it.question_version_id) == str(question_version_id)), None)
        if item is None:
            raise PlayerError("esta questão não pertence a esta atividade")

        key = (selected_option or "").strip().upper() or None
        option_id = None
        if key is not None:
            opt = (await self._session.execute(
                select(QuestionOption).where(
                    QuestionOption.question_version_id == item.question_version_id,
                    func.upper(QuestionOption.option_key) == key,
                )
            )).scalar_one_or_none()
            if opt is None:
                raise PlayerError(f"alternativa inválida: {selected_option!r}")
            option_id = opt.id
            key = opt.option_key

        now = _now()
        answer = (await self._session.execute(
            select(ActivityAnswer).where(
                ActivityAnswer.attempt_id == attempt.id,
                ActivityAnswer.question_version_id == item.question_version_id,
            )
        )).scalar_one_or_none()
        if answer is None:
            answer = ActivityAnswer(
                attempt_id=attempt.id, question_version_id=item.question_version_id,
                selected_option_id=option_id, selected_option_key=key, answered_at=now)
            self._session.add(answer)
        else:
            answer.selected_option_id = option_id
            answer.selected_option_key = key
            answer.answered_at = now
        attempt.last_activity_at = now
        attempt.current_position = item.position
        try:
            await self._session.commit()
        except IntegrityError:
            # concurrent first-write of the same (attempt, question) - retry as update
            await self._session.rollback()
            answer = (await self._session.execute(
                select(ActivityAnswer).where(
                    ActivityAnswer.attempt_id == attempt.id,
                    ActivityAnswer.question_version_id == item.question_version_id,
                )
            )).scalar_one()
            answer.selected_option_id = option_id
            answer.selected_option_key = key
            answer.answered_at = now
            await self._session.commit()

        state = await self._state(assignment_id, requester=requester)
        return {
            "saved": True,
            "question_version_id": str(item.question_version_id),
            "position": item.position,
            "selected_option": key,
            "answered_at": _iso(now),
            "answered_count": state["answered_count"],
            "pending_count": state["pending_count"],
            "total_questions": state["total_questions"],
            "status": state["status"],
        }

    # -- lightweight "last viewed question" -------------------------

    async def set_current_position(
        self, assignment_id: UUID, position: int, *, requester: Requester
    ) -> dict:
        row, _assessment, version, _avail = await self._resolve(assignment_id, requester=requester)
        attempt = await self._load_attempt(row.id, requester.external_user_id)
        if attempt is None or attempt.status != STATUS_IN_PROGRESS:
            raise PlayerStateError("a atividade não está em andamento")
        total = len(version.items)
        if not (1 <= int(position) <= total):
            raise PlayerError(f"posição fora do intervalo 1..{total}")
        attempt.current_position = int(position)
        attempt.last_activity_at = _now()
        await self._session.commit()
        return {"current_position": int(position)}

    # -- complete (BACKEND is the authority) -----------------------

    async def complete(self, assignment_id: UUID, *, requester: Requester) -> dict:
        row, assessment, version, avail = await self._resolve(assignment_id, requester=requester)
        attempt = await self._load_attempt(row.id, requester.external_user_id)
        if attempt is None:
            raise PlayerStateError("inicie a atividade antes de finalizar",
                                   payload={"status": STATUS_NOT_STARTED})
        if attempt.status == STATUS_COMPLETED:
            return await self._state(assignment_id, requester=requester)

        total = len(version.items)
        answered = int(await self._session.scalar(
            select(func.count()).select_from(ActivityAnswer).where(
                ActivityAnswer.attempt_id == attempt.id,
                ActivityAnswer.selected_option_id.is_not(None),
            )
        ))
        if answered < total:
            answered_vids = set((await self._session.execute(
                select(ActivityAnswer.question_version_id).where(
                    ActivityAnswer.attempt_id == attempt.id,
                    ActivityAnswer.selected_option_id.is_not(None),
                )
            )).scalars().all())
            pending = [it.position for it in sorted(version.items, key=lambda x: x.position)
                       if it.question_version_id not in answered_vids]
            raise PlayerStateError(
                f"você ainda não respondeu {total - answered} questão(ões)",
                payload={"pending_count": total - answered, "pending_positions": pending,
                         "answered_count": answered, "total_questions": total})

        now = _now()
        attempt.status = STATUS_COMPLETED
        attempt.completed_at = now
        attempt.last_activity_at = now
        await self._session.commit()
        return await self._state(assignment_id, requester=requester)


__all__ = [
    "ActivityPlayerStore",
    "PlayerAuthError",
    "PlayerError",
    "PlayerNotFound",
    "PlayerStateError",
    "STATUS_COMPLETED",
    "STATUS_IN_PROGRESS",
    "STATUS_NOT_STARTED",
]
