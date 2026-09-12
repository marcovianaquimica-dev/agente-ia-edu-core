"""PHASE 16 - distribution of a PUBLISHED question list to recipients.

Separation of concerns:
    list content  = Assessment / AssessmentVersion / AssessmentItem (PHASE 15)
    distribution  = ActivityAssignment                              (this module)
    execution     = attempts / responses                            (PHASE 17 - NOT here)

Only a list that is PUBLISHED, in the caller's tenant, created/managed by an
authorised user and has >= 1 question can be distributed. A distribution freezes
``assessment_version_id`` + ``selection_fingerprint`` + ``question_count`` +
``answer_key_presentation`` so it can be shown to a student without ever
rebuilding the list from a mutable selection.

Recipients are institutional references (STUDENT | CLASS + target_id), resolved
against the existing ``user_school_links`` scope rows - students are never
copied into the assignment. GRADE distribution is intentionally rejected: no
grade -> roster relation exists in the current schema.

AI-agnostic: imports no provider / OpenAI SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models import ActivityAssignment
from agente_ia_edu.db.models.admin import UserSchoolLink
from agente_ia_edu.db.models.assessments import Assessment, AssessmentVersion
from agente_ia_edu.services.question_list_store import (
    LIST_MATERIAL_TYPE,
    ListAuthorizationError,
    ListNotFoundError,
    ListStateError,
    Requester,
)

TARGET_STUDENT = "STUDENT"
TARGET_CLASS = "CLASS"
SUPPORTED_TARGET_TYPES = (TARGET_STUDENT, TARGET_CLASS)
STATUS_ACTIVE = "ACTIVE"
STATUS_CLOSED = "CLOSED"
STATUS_CANCELLED = "CANCELLED"
_MANAGE_STATUSES = {STATUS_ACTIVE, STATUS_CLOSED, STATUS_CANCELLED}

AVAIL_OPEN = "DISPONIVEL"
AVAIL_WAITING = "AGUARDANDO"
AVAIL_CLOSED = "ENCERRADA"

_STUDENT_ROLES = {"STUDENT", "ALUNO"}
_PRIVILEGED = {"COORDINATION", "COORDINATOR", "COORDENACAO", "DIRECTOR", "DIRETOR",
               "PRINCIPAL", "ADMIN", "MANAGER", "GESTOR"}


class AssignmentError(ValueError):
    """422."""


class AssignmentNotFound(LookupError):
    """404."""


class AssignmentAuthError(PermissionError):
    """403."""


class AssignmentStateError(ValueError):
    """409."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssignmentError(f"data inválida: {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def availability(row: ActivityAssignment, *, at: datetime | None = None) -> str:
    at = at or _now()
    if row.status != STATUS_ACTIVE:
        return AVAIL_CLOSED
    if row.available_from and at < _as_aware(row.available_from):
        return AVAIL_WAITING
    if row.due_at and at > _as_aware(row.due_at):
        return AVAIL_CLOSED
    return AVAIL_OPEN


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class AssignmentView:
    id: str
    assessment_id: str
    assessment_version_id: str
    list_id: str
    title: str
    school_id: str | None
    created_by_external_id: str | None
    target_type: str
    target_id: str
    available_from: str | None
    due_at: str | None
    status: str
    availability: str
    selection_fingerprint: str | None
    question_count: int
    answer_key_presentation: str | None
    created_at: str
    updated_at: str

    @staticmethod
    def of(row: ActivityAssignment, *, title: str) -> "AssignmentView":
        return AssignmentView(
            id=str(row.id), assessment_id=str(row.assessment_id),
            assessment_version_id=str(row.assessment_version_id),
            list_id=str(row.assessment_id), title=title,
            school_id=str(row.school_id) if row.school_id else None,
            created_by_external_id=row.created_by_external_id,
            target_type=row.target_type, target_id=row.target_id,
            available_from=row.available_from.isoformat() if row.available_from else None,
            due_at=row.due_at.isoformat() if row.due_at else None,
            status=row.status, availability=availability(row),
            selection_fingerprint=row.selection_fingerprint,
            question_count=row.question_count,
            answer_key_presentation=row.answer_key_presentation,
            created_at=row.created_at.isoformat() if row.created_at else "",
            updated_at=row.updated_at.isoformat() if row.updated_at else "",
        )


class ActivityAssignmentStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- list access helpers (reuse the PHASE 15 shape) ----------------

    async def _load_list(self, list_id: UUID) -> tuple[Assessment, AssessmentVersion]:
        row = (await self._session.execute(
            select(Assessment).where(Assessment.id == list_id)
            .options(selectinload(Assessment.versions).selectinload(AssessmentVersion.items))
        )).unique().scalar_one_or_none()
        if row is None or row.material_type != LIST_MATERIAL_TYPE:
            raise ListNotFoundError(str(list_id))
        version = row.versions[0] if row.versions else None
        if version is None:
            raise ListNotFoundError(str(list_id))
        return row, version

    @staticmethod
    def _can_manage_list(assessment: Assessment, who: Requester) -> bool:
        if assessment.owner_external_id == who.external_user_id or who.is_platform_admin:
            return True
        # a privileged role in the SAME school may also distribute (PHASE 16 s10)
        return (who.is_privileged and assessment.school_id and who.school_id
                and str(assessment.school_id) == str(who.school_id))

    def _can_view_assignment(self, assessment: Assessment, who: Requester) -> bool:
        return self._can_manage_list(assessment, who)

    # -- create -----------------------------------------------------

    async def create(
        self, list_id: UUID, *, requester: Requester,
        target_type: str, target_id: str,
        available_from: str | datetime | None = None,
        due_at: str | datetime | None = None,
        academic_year: str | None = None,
        origin: str | None = None,
        extra_metadata: dict | None = None,
    ) -> tuple[AssignmentView, bool]:
        assessment, version = await self._load_list(list_id)
        if not self._can_manage_list(assessment, requester):
            raise ListAuthorizationError("not authorised to distribute this list")
        if assessment.school_id and requester.school_id \
                and str(assessment.school_id) != str(requester.school_id) \
                and not requester.is_platform_admin:
            raise ListAuthorizationError("cross-tenant distribution is not allowed")
        if version.status != "published":
            raise ListStateError("only a PUBLISHED (finalizada) list can be distributed")
        if not version.items:
            raise ListStateError("cannot distribute an empty list")

        tt = (target_type or "").upper()
        if tt == "GRADE":
            raise AssignmentError(
                "distribuição por série ainda não é suportada: não há relação série -> "
                "turma/aluno no modelo atual (apenas escopos em user_school_links)."
            )
        if tt not in SUPPORTED_TARGET_TYPES:
            raise AssignmentError(f"target_type inválido: {target_type!r} (use STUDENT ou CLASS).")
        tid = (target_id or "").strip()
        if not tid:
            raise AssignmentError("target_id é obrigatório.")

        av_from = _parse_dt(available_from)
        due = _parse_dt(due_at)
        if av_from and due and due < av_from:
            raise AssignmentError("due_at não pode ser anterior a available_from.")

        # PHASE 16 s15 - duplicate control: one ACTIVE distribution of this
        # published version to this recipient.
        existing = (await self._session.execute(
            select(ActivityAssignment).where(
                ActivityAssignment.assessment_version_id == version.id,
                ActivityAssignment.target_type == tt,
                ActivityAssignment.target_id == tid,
                ActivityAssignment.status == STATUS_ACTIVE,
            )
        )).scalar_one_or_none()
        if existing is not None:
            return AssignmentView.of(existing, title=assessment.title), True

        md = assessment.metadata_ or {}
        title = assessment.title  # capture before commit expires the instance
        assignment_md: dict = {}
        if academic_year:
            assignment_md["academic_year"] = academic_year
        if origin:                       # e.g. "PRACTICE" - keeps practice evidence
            assignment_md["origin"] = origin   # separable from OFFICIAL_ACTIVITY in the Domain Map
        if extra_metadata:
            assignment_md.update(extra_metadata)
        row = ActivityAssignment(
            assessment_id=assessment.id,
            assessment_version_id=version.id,
            school_id=assessment.school_id,
            created_by_external_id=requester.external_user_id,
            target_type=tt, target_id=tid,
            available_from=av_from, due_at=due, status=STATUS_ACTIVE,
            selection_fingerprint=md.get("selection_fingerprint"),
            question_count=len(version.items),
            answer_key_presentation=md.get("answer_key_presentation"),
            metadata_=assignment_md or None,
        )
        self._session.add(row)
        await self._session.flush()
        new_id = row.id
        await self._session.commit()
        fresh = (await self._session.execute(
            select(ActivityAssignment).where(ActivityAssignment.id == new_id)
        )).scalar_one()
        return AssignmentView.of(fresh, title=title), False

    # -- read (professor / coordination) ---------------------------

    async def list_for_list(self, list_id: UUID, *, requester: Requester) -> list[AssignmentView]:
        assessment, _version = await self._load_list(list_id)
        if not self._can_view_assignment(assessment, requester):
            raise ListAuthorizationError("not authorised to view this list's distributions")
        rows = list((await self._session.scalars(
            select(ActivityAssignment)
            .where(ActivityAssignment.assessment_id == assessment.id)
            .order_by(ActivityAssignment.created_at.desc())
        )).all())
        return [AssignmentView.of(r, title=assessment.title) for r in rows]

    async def get(self, assignment_id: UUID, *, requester: Requester) -> AssignmentView:
        row = await self._session.get(ActivityAssignment, assignment_id)
        if row is None:
            raise AssignmentNotFound(str(assignment_id))
        assessment = await self._session.get(Assessment, row.assessment_id)
        if assessment is None or not self._can_view_assignment(assessment, requester):
            raise AssignmentAuthError("not authorised to view this distribution")
        return AssignmentView.of(row, title=assessment.title)

    async def update(
        self, assignment_id: UUID, *, requester: Requester,
        available_from: str | datetime | None = ...,
        due_at: str | datetime | None = ...,
        status: str | None = None,
    ) -> AssignmentView:
        row = await self._session.get(ActivityAssignment, assignment_id)
        if row is None:
            raise AssignmentNotFound(str(assignment_id))
        assessment = await self._session.get(Assessment, row.assessment_id)
        if assessment is None or not self._can_manage_list(assessment, requester):
            raise AssignmentAuthError("only an authorised manager can edit this distribution")
        if row.status == STATUS_CANCELLED:
            raise AssignmentStateError("a cancelled distribution cannot be edited")
        if available_from is not ...:
            row.available_from = _parse_dt(available_from)
        if due_at is not ...:
            row.due_at = _parse_dt(due_at)
        if row.available_from and row.due_at and row.due_at < row.available_from:
            raise AssignmentError("due_at não pode ser anterior a available_from.")
        if status is not None:
            new = status.upper()
            if new not in {STATUS_ACTIVE, STATUS_CLOSED}:
                raise AssignmentError("status só pode ser ACTIVE ou CLOSED via edição (use DELETE para cancelar).")
            row.status = new
        row.updated_at = _now()
        title = assessment.title
        await self._session.commit()
        fresh = (await self._session.execute(
            select(ActivityAssignment).where(ActivityAssignment.id == assignment_id)
        )).scalar_one()
        return AssignmentView.of(fresh, title=title)

    async def cancel(self, assignment_id: UUID, *, requester: Requester) -> AssignmentView:
        row = await self._session.get(ActivityAssignment, assignment_id)
        if row is None:
            raise AssignmentNotFound(str(assignment_id))
        assessment = await self._session.get(Assessment, row.assessment_id)
        if assessment is None or not self._can_manage_list(assessment, requester):
            raise AssignmentAuthError("only an authorised manager can cancel this distribution")
        title = assessment.title
        if row.status != STATUS_CANCELLED:
            row.status = STATUS_CANCELLED
            row.updated_at = _now()
            await self._session.commit()
        fresh = (await self._session.execute(
            select(ActivityAssignment).where(ActivityAssignment.id == assignment_id)
        )).scalar_one()
        return AssignmentView.of(fresh, title=title)

    async def distribution_summary(self, list_id: UUID, *, requester: Requester) -> dict:
        views = await self.list_for_list(list_id, requester=requester)
        active = [v for v in views if v.status == STATUS_ACTIVE]
        return {
            "count": len(views),
            "active": len(active),
            "cancelled": sum(1 for v in views if v.status == STATUS_CANCELLED),
            "closed": sum(1 for v in views if v.status == STATUS_CLOSED),
            "last_distributed_at": max((v.created_at for v in views), default=None),
        }

    # -- read (student - visibility only) -------------------------

    async def _student_scope(self, student_external_id: str) -> tuple[set[str], set[str]]:
        """Return (school_ids, classroom_ids) the student is currently linked to."""
        rows = list((await self._session.scalars(
            select(UserSchoolLink).where(
                UserSchoolLink.external_user_id == student_external_id,
                UserSchoolLink.active.is_(True),
            )
        )).all())
        student_rows = [r for r in rows if (r.role or "").upper() in _STUDENT_ROLES]
        schools = {str(r.school_id) for r in student_rows if r.school_id}
        classes = {
            r.scope_external_id for r in student_rows
            if (r.scope_type or "").upper() == "CLASSROOM" and r.scope_external_id
        }
        return schools, classes

    async def student_activities(self, *, requester: Requester) -> list[dict]:
        student_id = requester.external_user_id
        schools, classes = await self._student_scope(student_id)
        conds = [and_(ActivityAssignment.target_type == TARGET_STUDENT,
                      ActivityAssignment.target_id == student_id)]
        if classes:
            conds.append(and_(ActivityAssignment.target_type == TARGET_CLASS,
                              ActivityAssignment.target_id.in_(classes)))
        q = (
            select(ActivityAssignment)
            .where(ActivityAssignment.status != STATUS_CANCELLED, or_(*conds))
            .order_by(ActivityAssignment.created_at.desc())
        )
        rows = list((await self._session.scalars(q)).all())
        if schools:
            rows = [r for r in rows if r.school_id is None or str(r.school_id) in schools]
        if not rows:
            return []
        assessment_ids = {r.assessment_id for r in rows}
        assessments = {
            a.id: a for a in (await self._session.scalars(
                select(Assessment).where(Assessment.id.in_(assessment_ids))
            )).all()
        }
        out = []
        for r in rows:
            a = assessments.get(r.assessment_id)
            out.append({
                "assignment_id": str(r.id),
                "list_id": str(r.assessment_id),
                "assessment_version_id": str(r.assessment_version_id),
                "title": a.title if a else "(atividade)",
                "author_external_id": a.created_by_external_identity if a else None,
                "question_count": r.question_count,
                "available_from": r.available_from.isoformat() if r.available_from else None,
                "due_at": r.due_at.isoformat() if r.due_at else None,
                "status": r.status,
                "availability": availability(r),
                "target_type": r.target_type,
            })
        return out

    async def resolve_student_assignment(
        self, assignment_id: UUID, *, requester: Requester
    ) -> ActivityAssignment:
        """Return the assignment row IFF the caller is an authorised recipient
        (direct STUDENT target, or a member of the CLASS target, in the right
        tenant) and it is not CANCELLED. Shared by the PHASE 16 entry screen and
        the PHASE 17 activity player - one authorisation rule, no parallel authz.
        """
        row = await self._session.get(ActivityAssignment, assignment_id)
        if row is None or row.status == STATUS_CANCELLED:
            raise AssignmentNotFound(str(assignment_id))
        schools, classes = await self._student_scope(requester.external_user_id)
        is_recipient = (
            (row.target_type == TARGET_STUDENT and row.target_id == requester.external_user_id)
            or (row.target_type == TARGET_CLASS and row.target_id in classes)
        )
        if not is_recipient or (row.school_id and schools and str(row.school_id) not in schools):
            raise AssignmentAuthError("this activity is not assigned to you")
        return row

    async def student_activity_detail(self, assignment_id: UUID, *, requester: Requester) -> dict:
        row = await self.resolve_student_assignment(assignment_id, requester=requester)
        assessment = await self._session.get(Assessment, row.assessment_id)
        return {
            "assignment_id": str(row.id),
            "title": assessment.title if assessment else "(atividade)",
            "author_external_id": assessment.created_by_external_identity if assessment else None,
            "question_count": row.question_count,
            "available_from": row.available_from.isoformat() if row.available_from else None,
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "status": row.status,
            "availability": availability(row),
            "can_start": availability(row) == AVAIL_OPEN,
            "entry_screen": {
                "heading": assessment.title if assessment else "Atividade",
                "action_label": "Iniciar atividade",
                "note": "A resolução da atividade será disponibilizada em breve.",
            },
        }


__all__ = [
    "AVAIL_CLOSED",
    "AVAIL_OPEN",
    "AVAIL_WAITING",
    "ActivityAssignmentStore",
    "AssignmentAuthError",
    "AssignmentError",
    "AssignmentNotFound",
    "AssignmentStateError",
    "AssignmentView",
    "STATUS_ACTIVE",
    "STATUS_CANCELLED",
    "STATUS_CLOSED",
    "SUPPORTED_TARGET_TYPES",
    "TARGET_CLASS",
    "TARGET_STUDENT",
    "availability",
]
