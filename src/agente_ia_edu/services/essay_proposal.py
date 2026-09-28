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

from ..db.models import Class, EssayPrompt, PromptAssignment, PromptMaterial
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
