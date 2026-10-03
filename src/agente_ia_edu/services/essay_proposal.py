"""R2 - EssayPrompt/PromptMaterial/PromptAssignment: the "manage a proposal"
side of R2 (spec §6, "Gerenciar proposta"). Authorization (role check,
never-trust-the-body) lives in the route layer (Task 6); this service only
enforces the entity-level rules the database can't express as a CHECK
constraint - a prompt only accepts materials while DRAFT, and an assignment
requires its class to actually belong to the prompt's own school.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Class, EssayPrompt, GradeLevel, Person, PromptAssignment, PromptAssignmentLog,
    PromptMaterial, Student, StudentEnrollment,
)
from .institution_settings import InstitutionSettingsService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite (test-only; production is Postgres with DateTime(timezone=True))
    hands back naive datetimes on a fresh read, since it has no real
    timezone-aware storage - normalize before ever subtracting from
    _utcnow(), or that raises TypeError on SQLite while working fine on
    Postgres."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class EssayProposalService:
    # How long a soft-deleted proposal stays in the "Lixeira" and restorable
    # before it silently drops out of the trash listing - see
    # db/models/essay_proposal.py's EssayPrompt.deleted_at docstring. Nothing
    # is ever hard-deleted by this service: past this window the row is just
    # no longer offered back, exactly what "guardado por 30 dias" asked for.
    TRASH_RETENTION_DAYS = 30

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_prompt(
        self,
        *,
        school_id: uuid.UUID,
        title: str,
        statement: str,
        year: int,
        created_by_external_identity: str,
        is_free_theme: bool = False,
    ) -> EssayPrompt:
        prompt = EssayPrompt(
            id=uuid.uuid4(),
            school_id=school_id,
            title=title,
            statement=statement,
            year=year,
            status="DRAFT",
            created_by_external_identity=created_by_external_identity,
            is_free_theme=is_free_theme,
        )
        self.session.add(prompt)
        await self.session.flush()
        return prompt

    async def add_material(
        self,
        *,
        school_id: uuid.UUID,
        essay_prompt_id: uuid.UUID,
        material_type: str,
        position: int,
        content: str | None = None,
        storage_uri: str | None = None,
    ) -> PromptMaterial:
        prompt = await self._active_prompt_or_raise(school_id=school_id, essay_prompt_id=essay_prompt_id)
        if prompt.status != "DRAFT":
            raise ValueError(
                f"EssayPrompt {essay_prompt_id} is {prompt.status}, not DRAFT - "
                "material can only be added before the first assignment."
            )
        if material_type == "TEXT" and not content:
            raise ValueError("material_type=TEXT requires content")
        if material_type in ("IMAGE", "FILE") and not storage_uri:
            raise ValueError(f"material_type={material_type} requires storage_uri")
        if material_type not in ("TEXT", "IMAGE", "FILE"):
            raise ValueError(f"Unknown material_type: {material_type!r}")

        material = PromptMaterial(
            id=uuid.uuid4(),
            essay_prompt_id=essay_prompt_id,
            material_type=material_type,
            content=content,
            storage_uri=storage_uri,
            position=position,
        )
        self.session.add(material)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ValueError(
                f"EssayPrompt {essay_prompt_id} already has a material at position {position}"
            ) from exc
        return material

    async def create_assignment(
        self,
        *,
        school_id: uuid.UUID,
        essay_prompt_id: uuid.UUID,
        class_id: uuid.UUID,
        assigned_by_external_identity: str,
        due_at: datetime | None = None,
        validation_enabled: bool = True,
    ) -> PromptAssignment:
        prompt = await self._active_prompt_or_raise(school_id=school_id, essay_prompt_id=essay_prompt_id)

        klass = await self.session.get(Class, class_id)
        if klass is None or klass.school_id != school_id:
            raise ValueError(f"Class not found in school {school_id}: {class_id}")

        if not validation_enabled:
            settings = await InstitutionSettingsService(self.session).get_settings(school_id)
            if not settings.validation_teacher_can_disable:
                raise ValueError(
                    "This school does not allow disabling teacher review per "
                    "proposal (validation_teacher_can_disable=False) - "
                    "validation_enabled must stay True."
                )

        assignment = PromptAssignment(
            id=uuid.uuid4(),
            school_id=school_id,
            essay_prompt_id=essay_prompt_id,
            class_id=class_id,
            assigned_by_external_identity=assigned_by_external_identity,
            due_at=due_at,
            validation_enabled=validation_enabled,
            status="OPEN",
        )
        self.session.add(assignment)

        if prompt.status == "DRAFT":
            prompt.status = "ACTIVE"

        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ValueError(
                f"EssayPrompt {essay_prompt_id} is already assigned to class {class_id}"
            ) from exc
        return assignment

    async def create_assignments_bulk(
        self,
        *,
        school_id: uuid.UUID,
        essay_prompt_id: uuid.UUID,
        class_ids: list[uuid.UUID],
        assigned_by_external_identity: str,
        due_at: datetime | None = None,
        validation_enabled: bool = True,
    ) -> tuple[list[dict], dict[uuid.UUID, str]]:
        """Best-effort per class, mirroring EssayCorrectionService.bulk_approve
        - one class already assigned to this prompt (IntegrityError ->
        ValueError inside create_assignment) must never block the rest of
        the batch. create_assignment() itself calls session.rollback() on
        that conflict, which - inside a shared multi-class loop - would
        ALSO discard any earlier class in this same batch that was flushed
        but not yet committed; committing after every individual success
        keeps each class's rollback blast radius to itself. The field
        values are captured into a plain dict before that commit (not the
        ORM object itself), since expire_on_commit=True in production
        would otherwise force a lazy re-select on the next attribute read -
        the same MissingGreenlet hazard every other route+commit pair in
        this codebase already works around by building the response before
        committing."""
        assigned: list[dict] = []
        failures: dict[uuid.UUID, str] = {}
        for class_id in class_ids:
            try:
                assignment = await self.create_assignment(
                    school_id=school_id, essay_prompt_id=essay_prompt_id, class_id=class_id,
                    assigned_by_external_identity=assigned_by_external_identity,
                    due_at=due_at, validation_enabled=validation_enabled,
                )
                assigned.append({
                    "id": assignment.id, "school_id": assignment.school_id,
                    "essay_prompt_id": assignment.essay_prompt_id, "class_id": assignment.class_id,
                    "status": assignment.status, "validation_enabled": assignment.validation_enabled,
                })
                await self.session.commit()
            except ValueError as exc:
                failures[class_id] = str(exc)
        return assigned, failures

    async def resolve_assignment_targets(
        self, *, school_id: uuid.UUID, class_ids: list[uuid.UUID],
        grade_level_ids: list[uuid.UUID], student_ids: list[uuid.UUID],
    ) -> dict:
        """Expande serie em turmas reais (Class.grade_level_id), junta com
        as turmas escolhidas direto sem duplicar, e resolve os nomes pra um
        retrato pronto pro PromptAssignmentLog."""
        final_class_ids: set[uuid.UUID] = set(class_ids)
        series_summary = []
        if grade_level_ids:
            grade_classes = (await self.session.execute(
                select(Class.id, Class.name, Class.grade_level_id)
                .where(
                    Class.school_id == school_id,
                    Class.grade_level_id.in_(grade_level_ids),
                )
            )).all()
            by_grade: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
            for class_id, class_name, grade_level_id in grade_classes:
                final_class_ids.add(class_id)
                by_grade.setdefault(grade_level_id, []).append((class_id, class_name))
            grade_rows = (await self.session.execute(
                select(GradeLevel.id, GradeLevel.name).where(GradeLevel.id.in_(grade_level_ids))
            )).all()
            grade_names = {gid: name for gid, name in grade_rows}
            for grade_id, classes in by_grade.items():
                series_summary.append({
                    "grade_level_id": str(grade_id),
                    "name": grade_names.get(grade_id, str(grade_id)),
                    "turmas_expandidas": [
                        {"class_id": str(cid), "name": cname} for cid, cname in classes
                    ],
                })

        class_names: dict[uuid.UUID, str] = {}
        if final_class_ids:
            rows = (await self.session.execute(
                select(Class.id, Class.name).where(Class.id.in_(final_class_ids))
            )).all()
            class_names = {cid: name for cid, name in rows}

        student_names: dict[uuid.UUID, str] = {}
        if student_ids:
            rows = (await self.session.execute(
                select(Student.id, Person.full_name)
                .join(Person, Person.id == Student.person_id)
                .where(Student.id.in_(student_ids))
            )).all()
            student_names = {sid: name for sid, name in rows}

        # "turmas" lista TODAS as turmas finais (as escolhidas direto +
        # as expandidas de serie), uma vez cada, com o nome resolvido -
        # final_class_ids ja e a uniao sem duplicar (e um set).
        target_summary = {
            "turmas": [
                {"class_id": str(cid), "name": class_names.get(cid, str(cid))}
                for cid in sorted(final_class_ids, key=str)
            ],
            "series": series_summary,
            "alunos": [
                {"student_id": str(sid), "name": student_names.get(sid, str(sid))}
                for sid in sorted(student_ids, key=str)
            ],
        }

        return {
            "class_ids": final_class_ids,
            "student_ids": set(student_ids),
            "target_summary": target_summary,
        }

    async def create_assignments_combined(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID,
        class_ids: list[uuid.UUID], grade_level_ids: list[uuid.UUID],
        student_ids: list[uuid.UUID], assigned_by_external_identity: str,
        due_at: datetime | None = None, validation_enabled: bool = True,
    ) -> dict:
        """Atribui a proposta ao publico combinado (turmas + series
        expandidas + alunos especificos) numa chamada so. Idempotente: um
        alvo que ja tinha essa proposta e pulado silenciosamente (pre-
        consulta ANTES de inserir, nunca deixa o IntegrityError da unique
        estourar), mas o log registra a tentativa inteira - inclusive os
        alvos que ja existiam - porque ele retrata a ACAO do professor, nao
        so o que mudou no banco."""
        if not class_ids and not grade_level_ids and not student_ids:
            raise ValueError("Escolha pelo menos uma serie, turma ou aluno.")

        prompt = await self._active_prompt_or_raise(
            school_id=school_id, essay_prompt_id=essay_prompt_id
        )
        resolved = await self.resolve_assignment_targets(
            school_id=school_id, class_ids=class_ids,
            grade_level_ids=grade_level_ids, student_ids=student_ids,
        )
        final_class_ids = resolved["class_ids"]
        final_student_ids = resolved["student_ids"]

        existing_classes = set()
        if final_class_ids:
            rows = (await self.session.execute(
                select(PromptAssignment.class_id).where(
                    PromptAssignment.essay_prompt_id == essay_prompt_id,
                    PromptAssignment.class_id.in_(final_class_ids),
                )
            )).scalars().all()
            existing_classes = set(rows)
        existing_students = set()
        if final_student_ids:
            rows = (await self.session.execute(
                select(PromptAssignment.student_id).where(
                    PromptAssignment.essay_prompt_id == essay_prompt_id,
                    PromptAssignment.student_id.in_(final_student_ids),
                )
            )).scalars().all()
            existing_students = set(rows)

        created_count = 0
        for class_id in final_class_ids - existing_classes:
            self.session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=school_id, essay_prompt_id=essay_prompt_id,
                class_id=class_id, student_id=None,
                assigned_by_external_identity=assigned_by_external_identity,
                due_at=due_at, validation_enabled=validation_enabled, status="OPEN",
            ))
            created_count += 1
        for student_id in final_student_ids - existing_students:
            self.session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=school_id, essay_prompt_id=essay_prompt_id,
                class_id=None, student_id=student_id,
                assigned_by_external_identity=assigned_by_external_identity,
                due_at=due_at, validation_enabled=validation_enabled, status="OPEN",
            ))
            created_count += 1

        already_assigned_count = len(existing_classes) + len(existing_students)

        if prompt.status == "DRAFT" and created_count > 0:
            prompt.status = "ACTIVE"

        log = PromptAssignmentLog(
            id=uuid.uuid4(), school_id=school_id, essay_prompt_id=essay_prompt_id,
            assigned_by_external_identity=assigned_by_external_identity,
            target_summary=resolved["target_summary"],
        )
        self.session.add(log)
        await self.session.flush()

        return {
            "created_count": created_count,
            "already_assigned_count": already_assigned_count,
            "log_id": log.id,
        }

    async def search_students_for_assignment(
        self, *, school_id: uuid.UUID, class_ids: list[uuid.UUID], query: str,
    ) -> list[tuple[uuid.UUID, str, str | None, str]]:
        """(student_id, full_name, document_number, class_name) de alunos
        ativos nas turmas dadas (o chamador ja resolveu quais turmas o
        professor esta autorizado a ver, via
        TeacherPortalService.list_teacher_classrooms - este metodo nao
        resolve autorizacao, so filtra pelo escopo que recebe) cujo nome
        bate com a busca."""
        if not class_ids:
            return []
        q_clean = query.strip().lower()
        rows = (await self.session.execute(
            select(
                Student.id, Person.full_name, Person.document_number, Class.name,
            )
            .join(Person, Person.id == Student.person_id)
            .join(StudentEnrollment, StudentEnrollment.student_id == Student.id)
            .join(Class, Class.id == StudentEnrollment.class_id)
            .where(
                StudentEnrollment.school_id == school_id,
                StudentEnrollment.class_id.in_(class_ids),
                StudentEnrollment.status == "ACTIVE",
            )
            .order_by(Person.full_name)
        )).all()
        return [
            (student_id, full_name, document_number, class_name)
            for student_id, full_name, document_number, class_name in rows
            if not q_clean or q_clean in full_name.lower()
        ]

    async def list_assignment_log(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID,
    ) -> list[PromptAssignmentLog]:
        result = await self.session.execute(
            select(PromptAssignmentLog)
            .where(
                PromptAssignmentLog.school_id == school_id,
                PromptAssignmentLog.essay_prompt_id == essay_prompt_id,
            )
            .order_by(PromptAssignmentLog.created_at.desc())
        )
        return list(result.scalars().all())

    async def soft_delete_prompt(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID,
    ) -> EssayPrompt:
        """Moves a proposal to the "Lixeira" - sets deleted_at only, nothing
        else changes, so every material/assignment/submission/correction
        under it stays exactly as it was if the teacher restores it."""
        prompt = await self._active_prompt_or_raise(school_id=school_id, essay_prompt_id=essay_prompt_id)
        prompt.deleted_at = _utcnow()
        await self.session.flush()
        return prompt

    async def restore_prompt(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID,
    ) -> EssayPrompt:
        prompt = await self.session.get(EssayPrompt, essay_prompt_id)
        if prompt is None or prompt.school_id != school_id:
            raise ValueError(f"EssayPrompt not found in school {school_id}: {essay_prompt_id}")
        if prompt.deleted_at is None:
            raise ValueError(f"EssayPrompt {essay_prompt_id} is not in the trash")
        if _utcnow() - _as_aware_utc(prompt.deleted_at) > timedelta(days=self.TRASH_RETENTION_DAYS):
            raise ValueError(
                f"EssayPrompt {essay_prompt_id} was deleted more than "
                f"{self.TRASH_RETENTION_DAYS} days ago and can no longer be restored"
            )
        prompt.deleted_at = None
        await self.session.flush()
        return prompt

    async def list_trash(self, *, school_id: uuid.UUID) -> list[EssayPrompt]:
        """Only proposals still inside the retention window - one that aged
        out simply stops appearing here (never hard-deleted, see
        TRASH_RETENTION_DAYS's docstring), so the teacher can't "restore"
        something the UI no longer offers."""
        cutoff = _utcnow() - timedelta(days=self.TRASH_RETENTION_DAYS)
        result = await self.session.execute(
            select(EssayPrompt)
            .where(
                EssayPrompt.school_id == school_id,
                EssayPrompt.deleted_at.is_not(None),
                EssayPrompt.deleted_at >= cutoff,
            )
            .order_by(EssayPrompt.deleted_at.desc())
        )
        return list(result.scalars().all())

    async def _active_prompt_or_raise(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID,
    ) -> EssayPrompt:
        prompt = await self.session.get(EssayPrompt, essay_prompt_id)
        if prompt is None or prompt.school_id != school_id or prompt.deleted_at is not None:
            raise ValueError(f"EssayPrompt not found in school {school_id}: {essay_prompt_id}")
        return prompt


__all__ = ["EssayProposalService", "as_aware_utc"]

# Public alias - the route layer needs the same normalization to compute
# days_remaining from a freshly-read deleted_at.
as_aware_utc = _as_aware_utc
