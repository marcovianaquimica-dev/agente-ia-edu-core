"""R2 - EssayPrompt/PromptMaterial/PromptAssignment: the "manage a proposal"
side of R2 (spec §6, "Gerenciar proposta"). Authorization (role check,
never-trust-the-body) lives in the route layer (Task 6); this service only
enforces the entity-level rules the database can't express as a CHECK
constraint - a prompt only accepts materials while DRAFT, and an assignment
requires its class to actually belong to the prompt's own school.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Class, EssayPrompt, PromptAssignment, PromptMaterial


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EssayProposalService:
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
    ) -> EssayPrompt:
        prompt = EssayPrompt(
            id=uuid.uuid4(),
            school_id=school_id,
            title=title,
            statement=statement,
            year=year,
            status="DRAFT",
            created_by_external_identity=created_by_external_identity,
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
        prompt = await self.session.get(EssayPrompt, essay_prompt_id)
        if prompt is None or prompt.school_id != school_id:
            raise ValueError(f"EssayPrompt not found in school {school_id}: {essay_prompt_id}")
        if prompt.status != "DRAFT":
            raise ValueError(
                f"EssayPrompt {essay_prompt_id} is {prompt.status}, not DRAFT - "
                "material can only be added before the first assignment."
            )
        if material_type == "TEXT" and not content:
            raise ValueError("material_type=TEXT requires content")
        if material_type == "IMAGE" and not storage_uri:
            raise ValueError("material_type=IMAGE requires storage_uri")
        if material_type not in ("TEXT", "IMAGE"):
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
        await self.session.flush()
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
        prompt = await self.session.get(EssayPrompt, essay_prompt_id)
        if prompt is None or prompt.school_id != school_id:
            raise ValueError(f"EssayPrompt not found in school {school_id}: {essay_prompt_id}")

        klass = await self.session.get(Class, class_id)
        if klass is None or klass.school_id != school_id:
            raise ValueError(f"Class not found in school {school_id}: {class_id}")

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


__all__ = ["EssayProposalService"]
