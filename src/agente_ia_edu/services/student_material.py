"""PHASE 25 - Material Delivery & Study Integration (STUDENT-facing).

Turns the PHASE 23 material foundation into a real, tenant-safe, ordered
in-platform reading experience. Reuses every existing PHASE 23 model as-is -
no second material model, no copy of section/block content:
TheoryMaterial / TheoryMaterialVersion / MaterialSection / MaterialBlock /
MaterialExercise - plus MaterialAvailabilityService.visible_to_student()
(PHASE 25) for the tenant/visibility check. Only PUBLISHED versions are ever
served to a student (PHASE 23's versioning invariant: a published version is
never silently modified).

Writes exactly ONE thing: the student's own MaterialProgress row (position
only). It NEVER touches domain_content_mastery, ActivityResult(Item) or the
Adaptive Learning Path - reading a material is not evidence of mastery
(spec s10). Associated exercises (MaterialExercise) are never played here:
"Pratique o que você estudou" hands off to the EXISTING
AdaptivePracticeService.create_practice(content_code=...) - the exact same
entry point Trilha (PHASE 21) and Study Session (PHASE 24) already use, so no
second question-selection/player/correction mechanism is introduced.

Batched throughout (one query per collection, never per row) - safe as a
material grows to 50+ sections / 100+ blocks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    CatalogNode,
    MaterialBlock,
    MaterialExercise,
    MaterialProgress,
    MaterialSection,
    TheoryMaterial,
    TheoryMaterialVersion,
)
from .material_availability import MaterialAvailabilityService

PUBLISHED = "PUBLISHED"
# Deterministic reading-time heuristic (no AI, no real timing measurement) -
# documented explicitly wherever it is surfaced to the student as an estimate.
_MINUTES_PER_BLOCK = 1.5


class MaterialAccessError(PermissionError):
    """The requester cannot see this material (visibility_scope/school_id)."""


class MaterialNotFoundError(LookupError):
    """The material, or a PUBLISHED version of it, does not exist."""


def _uuid_or_none(value) -> UUID | None:
    return UUID(str(value)) if value else None


class StudentMaterialService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._availability = MaterialAvailabilityService(session)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    async def _published_version(self, material_id: UUID) -> TheoryMaterialVersion | None:
        return (await self._session.execute(
            select(TheoryMaterialVersion)
            .where(TheoryMaterialVersion.material_id == material_id,
                   TheoryMaterialVersion.status == PUBLISHED)
            .order_by(TheoryMaterialVersion.version_number.desc())
            .limit(1)
        )).scalars().first()

    async def _load_material_and_version(
        self, material_id: UUID, *, requester_school_id: str | None,
    ) -> tuple[TheoryMaterial, TheoryMaterialVersion]:
        material = await self._session.get(TheoryMaterial, material_id)
        if material is None:
            raise MaterialNotFoundError("material not found")
        if not self._availability.visible_to_student(
            material, requester_school_id=requester_school_id
        ):
            raise MaterialAccessError("material not visible to this student")
        version = await self._published_version(material_id)
        if version is None:
            raise MaterialNotFoundError("material has no published version")
        return material, version

    async def _content_codes(self, node_ids: list[UUID | None]) -> dict[UUID, str]:
        ids = [n for n in node_ids if n]
        if not ids:
            return {}
        rows = (await self._session.execute(
            select(CatalogNode.id, CatalogNode.code).where(CatalogNode.id.in_(ids))
        )).all()
        return {nid: code for nid, code in rows}

    async def _counts_for_versions(self, version_ids: list[UUID]) -> dict[UUID, dict[str, int]]:
        out = {vid: {"sections": 0, "blocks": 0, "questions": 0} for vid in version_ids}
        if not version_ids:
            return out
        for vid, n in (await self._session.execute(
            select(MaterialSection.material_version_id, func.count())
            .where(MaterialSection.material_version_id.in_(version_ids))
            .group_by(MaterialSection.material_version_id)
        )).all():
            out[vid]["sections"] = int(n)
        for vid, n in (await self._session.execute(
            select(MaterialBlock.material_version_id, func.count())
            .where(MaterialBlock.material_version_id.in_(version_ids))
            .group_by(MaterialBlock.material_version_id)
        )).all():
            out[vid]["blocks"] = int(n)
        for vid, n in (await self._session.execute(
            select(MaterialExercise.material_version_id, func.count())
            .where(MaterialExercise.material_version_id.in_(version_ids),
                   MaterialExercise.question_version_id.isnot(None))
            .group_by(MaterialExercise.material_version_id)
        )).all():
            out[vid]["questions"] = int(n)
        return out

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def list_materials(
        self, *, requester_school_id: str | None, content_code: str | None = None,
    ) -> list[dict]:
        node_id = None
        if content_code:
            node_id = (await self._session.execute(
                select(CatalogNode.id).where(CatalogNode.code == content_code)
            )).scalar_one_or_none()
            if node_id is None:
                return []

        school_uuid = _uuid_or_none(requester_school_id)
        visible = (TheoryMaterial.visibility_scope == "PUBLIC") | (
            (TheoryMaterial.visibility_scope == "SCHOOL")
            & (TheoryMaterial.school_id == school_uuid)
        ) if school_uuid else (TheoryMaterial.visibility_scope == "PUBLIC")

        q = (
            select(
                TheoryMaterial.id, TheoryMaterial.title, TheoryMaterial.description,
                TheoryMaterial.material_kind, TheoryMaterial.primary_content_node_id,
                TheoryMaterial.created_by_external_identity,
                TheoryMaterialVersion.id, TheoryMaterialVersion.version_number,
            )
            .join(TheoryMaterialVersion, TheoryMaterialVersion.material_id == TheoryMaterial.id)
            .where(TheoryMaterialVersion.status == PUBLISHED, visible)
        )
        if node_id is not None:
            sec_sub = select(MaterialSection.material_version_id).where(
                MaterialSection.content_node_id == node_id
            )
            q = q.where(
                (TheoryMaterial.primary_content_node_id == node_id)
                | (TheoryMaterialVersion.id.in_(sec_sub))
            )
        rows = (await self._session.execute(q)).all()

        # One material may have several PUBLISHED versions - keep only the
        # highest version_number per material (grouped in Python: one query,
        # no per-material lookup).
        chosen: dict[UUID, tuple] = {}
        for mid, title, desc, kind, node, author, vid, vnum in rows:
            cur = chosen.get(mid)
            if cur is None or vnum > cur[6]:
                chosen[mid] = (mid, title, desc, kind, node, author, vnum, vid)

        version_ids = [row[7] for row in chosen.values()]
        counts = await self._counts_for_versions(version_ids)
        codes = await self._content_codes([row[4] for row in chosen.values()])

        out = []
        for mid, title, desc, kind, node, author, vnum, vid in chosen.values():
            c = counts.get(vid, {"sections": 0, "blocks": 0, "questions": 0})
            out.append({
                "material_id": str(mid),
                "title": title,
                "description": desc,
                "material_kind": kind,
                "content_code": codes.get(node),
                "author": author,
                "version_id": str(vid),
                "version_number": vnum,
                "section_count": c["sections"],
                "block_count": c["blocks"],
                "exercise_count": c["questions"],
                "estimated_minutes": max(1, round(c["blocks"] * _MINUTES_PER_BLOCK)),
            })
        out.sort(key=lambda r: (r["title"] or "", r["material_id"]))
        return out

    async def get_material(self, material_id: UUID, *, requester_school_id: str | None) -> dict:
        material, version = await self._load_material_and_version(
            material_id, requester_school_id=requester_school_id
        )
        counts = (await self._counts_for_versions([version.id]))[version.id]
        codes = await self._content_codes([material.primary_content_node_id])
        return {
            "material_id": str(material.id),
            "title": material.title,
            "description": material.description,
            "material_kind": material.material_kind,
            "content_code": codes.get(material.primary_content_node_id),
            "author": material.created_by_external_identity,
            "version_id": str(version.id),
            "version_number": version.version_number,
            "introduction": version.introduction,
            "summary": version.summary,
            "published_at": version.published_at.isoformat() if version.published_at else None,
            "section_count": counts["sections"],
            "block_count": counts["blocks"],
            "exercise_count": counts["questions"],
            "estimated_minutes": max(1, round(counts["blocks"] * _MINUTES_PER_BLOCK)),
        }

    async def get_sections(self, material_id: UUID, *, requester_school_id: str | None) -> list[dict]:
        _material, version = await self._load_material_and_version(
            material_id, requester_school_id=requester_school_id
        )
        sections = (await self._session.execute(
            select(MaterialSection)
            .where(MaterialSection.material_version_id == version.id)
            .order_by(MaterialSection.position)
        )).scalars().all()
        blocks = (await self._session.execute(
            select(MaterialBlock)
            .where(MaterialBlock.material_version_id == version.id)
            .order_by(MaterialBlock.position)
        )).scalars().all()
        exercises = (await self._session.execute(
            select(MaterialExercise)
            .where(MaterialExercise.material_version_id == version.id)
            .order_by(MaterialExercise.position)
        )).scalars().all()

        blocks_by_section: dict[UUID, list] = {}
        for b in blocks:
            blocks_by_section.setdefault(b.section_id, []).append(b)
        exercises_by_section: dict[UUID | None, list] = {}
        for ex in exercises:
            exercises_by_section.setdefault(ex.section_id, []).append(ex)

        codes = await self._content_codes([s.content_node_id for s in sections])

        out = []
        for s in sections:
            out.append({
                "section_id": str(s.id),
                "section_type": s.section_type,
                "position": s.position,
                "title": s.title,
                "body": s.body,
                "content_code": codes.get(s.content_node_id),
                "curriculum_relation_type": s.curriculum_relation_type,
                "blocks": [
                    {
                        "block_id": str(b.id),
                        "block_type": b.block_type,
                        "position": b.position,
                        "title": b.title,
                        "body": b.body,
                        "metadata": b.metadata_,
                    }
                    for b in sorted(blocks_by_section.get(s.id, []), key=lambda b: b.position)
                ],
                "exercises": [
                    {
                        "exercise_id": str(ex.id),
                        "question_version_id": str(ex.question_version_id) if ex.question_version_id else None,
                        "relation_type": ex.relation_type,
                        "is_required": ex.is_required,
                        "points": float(ex.points) if ex.points is not None else None,
                    }
                    for ex in sorted(exercises_by_section.get(s.id, []), key=lambda e: e.position)
                ],
            })
        return out

    async def get_progress(
        self, material_id: UUID, student_external_id: str, *, requester_school_id: str | None,
    ) -> dict:
        _material, version = await self._load_material_and_version(
            material_id, requester_school_id=requester_school_id
        )
        row = (await self._session.execute(
            select(MaterialProgress).where(
                MaterialProgress.student_external_id == student_external_id,
                MaterialProgress.material_version_id == version.id,
            )
        )).scalars().first()
        if row is None:
            first_section = (await self._session.execute(
                select(MaterialSection.id)
                .where(MaterialSection.material_version_id == version.id)
                .order_by(MaterialSection.position)
                .limit(1)
            )).scalar_one_or_none()
            return {
                "material_id": str(material_id),
                "version_id": str(version.id),
                "started": False,
                "status": "NOT_STARTED",
                "current_section_id": str(first_section) if first_section else None,
                "current_block_id": None,
                "sections_completed": 0,
            }
        return {
            "material_id": str(material_id),
            "version_id": str(version.id),
            "started": True,
            "status": row.status,
            "current_section_id": str(row.current_section_id) if row.current_section_id else None,
            "current_block_id": str(row.current_block_id) if row.current_block_id else None,
            "sections_completed": row.sections_completed,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    async def save_progress(
        self,
        material_id: UUID,
        student_external_id: str,
        *,
        requester_school_id: str | None,
        current_section_id: UUID | None,
        current_block_id: UUID | None = None,
        completed: bool = False,
    ) -> dict:
        material, version = await self._load_material_and_version(
            material_id, requester_school_id=requester_school_id
        )
        section = None
        if current_section_id is not None:
            section = (await self._session.execute(
                select(MaterialSection).where(
                    MaterialSection.id == current_section_id,
                    MaterialSection.material_version_id == version.id,
                )
            )).scalars().first()
            if section is None:
                raise ValueError("current_section_id does not belong to this material version")
        if current_block_id is not None:
            block_ok = (await self._session.execute(
                select(MaterialBlock.id).where(
                    MaterialBlock.id == current_block_id,
                    MaterialBlock.material_version_id == version.id,
                )
            )).scalar_one_or_none()
            if block_ok is None:
                raise ValueError("current_block_id does not belong to this material version")

        row = (await self._session.execute(
            select(MaterialProgress).where(
                MaterialProgress.student_external_id == student_external_id,
                MaterialProgress.material_version_id == version.id,
            )
        )).scalars().first()
        status = "COMPLETED" if completed else "IN_PROGRESS"
        if row is None:
            row = MaterialProgress(
                student_external_id=student_external_id,
                material_id=material.id,
                material_version_id=version.id,
                current_section_id=current_section_id,
                current_block_id=current_block_id,
                sections_completed=section.position if section else 0,
                status=status,
            )
            self._session.add(row)
        else:
            row.current_section_id = current_section_id
            row.current_block_id = current_block_id
            row.sections_completed = max(row.sections_completed, section.position if section else 0)
            row.status = status
            row.updated_at = datetime.now(timezone.utc)
        await self._session.commit()
        return await self.get_progress(
            material_id, student_external_id, requester_school_id=requester_school_id
        )
